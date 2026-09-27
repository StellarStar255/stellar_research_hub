"""网页版的对话 agent：每轮提问跑一次 `claude -p`（用本机已登录的 Claude Code，不用 API Key），
工作目录是论文目录，能读 paper.pdf、在 experiments/ 里写实验代码、改 notes.md、联网查资料。
Claude 自己不能执行命令：实验代码由用户在网页上点「运行」执行（run_experiment），
输出和图再自动发回给 Claude 解读。
stream-json 输出折叠成对话记录，存在 <论文>/chats/<id>.json。

一条助手消息的 parts 依次是正文和工具步骤：
  {"type": "text", "text": ...}
  {"type": "step", "kind": read|run|write|note|search|fetch|view|tool, "label": ..., "detail": ...,
   "tool_id": ..., "status": running|done|error, "output": ...}
"""
import json
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from pathlib import Path

PROMPT_TEMPLATE = (Path(__file__).parent / "agent_prompt.md").read_text(encoding="utf-8")
TOPIC_PROMPT = (Path(__file__).parent / "topic_prompt.md").read_text(encoding="utf-8")
RH = str(Path(__file__).resolve().parent.parent / "rh")
CLAUDE = shutil.which("claude") or "/opt/homebrew/bin/claude"
MODEL = os.environ.get("RESEARCH_AGENT_MODEL")   # 例如 sonnet，回答更快
PYTHON = os.environ.get("RESEARCH_PYTHON") or shutil.which("python3") or "python3"

TOOLS = "Read,Write,Edit,Glob,Grep,WebSearch,WebFetch"
# 写文件只放行论文目录下的 experiments/ 和 notes.md（相对工作目录），别处一律拒绝
ALLOWED = ["Read", "Glob", "Grep", "WebSearch", "WebFetch",
           "Write(./experiments/**)", "Edit(./experiments/**)", "Write(./notes.md)", "Edit(./notes.md)"]
# 主题调研：只多放行两个专用命令（搜 arXiv / 下载论文），报告只能写 report.md
TOPIC_TOOLS = "Read,Write,Edit,Glob,Grep,Bash,WebSearch,WebFetch"
TOPIC_ALLOWED = ["Read", "Glob", "Grep", "WebSearch", "WebFetch",
                 "Write(./report.md)", "Edit(./report.md)", f"Bash({RH} search:*)", f"Bash({RH} cite:*)", f"Bash({RH} import:*)"]
PAPER_RE = re.compile(r"^PAPER (\S+)$", re.M)
RUN_RE = re.compile(r"\[\[RUN\s+(experiments/[\w./-]+\.py)\s*\]\]")

STYLES = {
    "tutor": "导师模式：一次只讲一个概念，讲完用一个小问题检验我是否真的理解，等我回答后再继续；我答错时先给提示而不是直接给答案。",
    "explain": "讲解模式：直接、完整、有条理地回答，先给一句话结论再展开，多用直觉类比和数值例子。",
    "skim": "速读模式：简洁，要点列表，每点一两句话；除非我追问，不展开推导。",
    "lab": "实验模式：尽量写可运行的小实验来回答，验证论文的主张，画图，并和论文里的数字对比。",
}

_lock = threading.RLock()
_procs = {}  # chat id -> Popen
_pending = {}  # chat id -> 还没落盘的流式文字片段
_runs = {}     # chat id -> threading.Event（正在跑实验；置位 = 用户点了停止）


def _chats_dir(paper):
    return Path(paper.root) / "chats"


def _path(paper, cid):
    if not re.fullmatch(r"[\w-]+", cid or ""):
        raise ValueError("bad chat id")
    return _chats_dir(paper) / f"{cid}.json"


def load(paper, cid):
    return json.loads(_path(paper, cid).read_text(encoding="utf-8"))


def _save(paper, chat):
    d = _chats_dir(paper)
    d.mkdir(parents=True, exist_ok=True)
    p = _path(paper, chat["id"])
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(chat, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def list_chats(paper):
    d = _chats_dir(paper)
    if not d.exists():
        return []
    out = []
    for f in sorted(d.glob("*.json"), key=lambda f: f.stat().st_mtime, reverse=True):
        try:
            c = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({"id": c["id"], "title": c["title"], "updated": c.get("updated"),
                    "running": is_running(c["id"])})
    return out


def delete(paper, cid):
    stop(cid)
    _path(paper, cid).unlink(missing_ok=True)


def is_running(cid):
    p = _procs.get(cid)
    return (p is not None and p.poll() is None) or cid in _runs


def build_prompt(paper, style):
    if paper.kind == "topic":
        return _topic_prompt(paper)
    meta = paper.meta
    info = [f"标题：{paper.title}"]
    if meta.get("authors"):
        info.append("作者：" + ", ".join(meta["authors"][:8]))
    if meta.get("arxiv_id"):
        info.append("arXiv：" + meta["arxiv_id"])
    return (PROMPT_TEMPLATE
            .replace("{PAPER_INFO}", "\n".join(info))
            .replace("{STYLE}", STYLES.get(style, STYLES["tutor"]))
            .replace("{PYTHON}", PYTHON))


def _topic_prompt(topic):
    from core.library import Library
    lib = Library()
    papers = [lib.get(pid) for pid in topic.meta.get("papers", [])]
    lines = [f"- [{p.title}](paper:{p.id}) · PDF: {p.pdf_path}" for p in papers if p]
    have = ("已经为这个主题下载过的论文：\n" + "\n".join(lines)) if lines else "还没有为这个主题下载任何论文。"
    if topic.meta.get("plan_confirmed") is False:
        have += "\n\n**当前处于第一阶段**：调研计划还没被用户确认，只能搜索和查引用数，不能 import。"
    else:
        have += "\n\n用户已确认调研计划（或正在追问），可以 import。"
    return (TOPIC_PROMPT.replace("{TOPIC}", topic.title).replace("{TOPIC_ID}", topic.id)
            .replace("{TOPIC_PAPERS}", have).replace("{RH}", RH))


def send(paper, cid, text, style="tutor", auto=False):
    """开始一轮。返回对话 id（cid 为 None 时新建对话）。auto=True：程序代发的消息（实验结果），界面不显示成用户气泡。"""
    with _lock:
        if cid:
            chat = load(paper, cid)
            if is_running(cid):
                raise RuntimeError("上一个问题还在回答中")
        else:
            cid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
            chat = {"id": cid, "title": text.strip().splitlines()[0][:30], "claude_session": None,
                    "style": style if style in STYLES else "tutor",
                    "created": time.strftime("%Y-%m-%d %H:%M"), "messages": []}
        chat["messages"].append({"role": "user", "text": text, **({"auto": True} if auto else {})})
        chat["messages"].append({"role": "assistant", "parts": [], "draft": "", "status": "running",
                                 "started": time.time()})
        chat["updated"] = time.strftime("%Y-%m-%d %H:%M")
        _save(paper, chat)

        if paper.kind == "topic":
            tools, allowed = TOPIC_TOOLS, TOPIC_ALLOWED
        else:
            tools, allowed = TOOLS, ALLOWED
            os.makedirs(os.path.join(paper.root, "experiments", "figs"), exist_ok=True)
        cmd = [CLAUDE, "-p", text, "--output-format", "stream-json", "--verbose",
               "--include-partial-messages", "--system-prompt", build_prompt(paper, chat["style"]),
               "--tools", tools, "--allowedTools", *allowed, "--strict-mcp-config"]
        if MODEL:
            cmd += ["--model", MODEL]
        if chat.get("claude_session"):
            cmd += ["--resume", chat["claude_session"]]
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)
        proc = subprocess.Popen(cmd, cwd=paper.root, env=env, stdin=subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True, bufsize=1, start_new_session=True)
        _procs[cid] = proc
    threading.Thread(target=_pump, args=(paper, cid, proc), daemon=True).start()
    return cid


def stop(cid):
    ev = _runs.get(cid)
    if ev is not None:
        ev.set()
    p = _procs.get(cid)
    if p and p.poll() is None:
        os.killpg(p.pid, signal.SIGTERM)


def _update(paper, cid, fn):
    with _lock:
        chat = load(paper, cid)
        fn(chat, chat["messages"][-1])
        _save(paper, chat)


def _rel(path, root):
    try:
        return os.path.relpath(path, root)
    except ValueError:
        return path


def _tool_step(block, root):
    name = block.get("name")
    inp = block.get("input") or {}
    step = {"type": "step", "tool_id": block.get("id"), "status": "running", "kind": "tool",
            "label": name, "detail": ""}
    if name == "Read":
        f = _rel(inp.get("file_path", ""), root)
        if f.endswith("paper.pdf"):
            pages = inp.get("pages")
            pid = os.path.basename(os.path.dirname(inp.get("file_path", "")))
            if os.path.dirname(f) in ("", "."):
                step.update(kind="read", label="阅读论文" + (f" 第 {pages} 页" if pages else ""))
            else:
                step.update(kind="read", label="阅读" + (f" 第 {pages} 页" if pages else ""),
                            detail=_paper_title(pid), paper_id=pid)
        elif f.lower().endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
            step.update(kind="view", label="查看图片", detail=f)
        else:
            step.update(kind="read", label="读取", detail=f)
    elif name == "Bash":
        cmd = inp.get("command", "")
        m = re.match(r"\S*rh (search|cite|import)\s+(.*)", cmd)
        if m and m.group(1) == "search":
            step.update(kind="search", label="搜索 arXiv", detail=m.group(2).split(" -")[0].strip("\"' "))
        elif m and m.group(1) == "cite":
            step.update(kind="search", label="查引用数", detail=m.group(2))
        elif m:
            step.update(kind="import", label="下载论文", detail=m.group(2).split(" --")[0].strip("\"' "))
        else:
            step.update(kind="run", label="运行", detail=cmd[:300])
    elif name in ("Write", "Edit"):
        f = _rel(inp.get("file_path", ""), root)
        if f == "notes.md":
            step.update(kind="note", label="更新笔记")
        elif f == "report.md":
            step.update(kind="note", label="更新综述报告")
        else:
            step.update(kind="write", label="写代码" if f.endswith(".py") else "写文件", detail=f,
                        code=(inp.get("content") or inp.get("new_string") or "")[:6000])
    elif name == "WebSearch":
        step.update(kind="search", label="搜索", detail=inp.get("query", ""))
    elif name == "WebFetch":
        step.update(kind="fetch", label="读取网页", detail=inp.get("url", ""))
    elif name in ("Glob", "Grep"):
        step.update(kind="tool", label="查找文件", detail=inp.get("pattern", ""))
    return step


def _paper_title(pid):
    from core.library import Library
    p = Library().get(pid) if re.fullmatch(r"[\w.-]+", pid or "") else None
    return p.title if p else pid


def _result_text(block):
    c = block.get("content")
    if isinstance(c, list):
        return "\n".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return str(c or "")


def _handle(chat, msg, ev, root):
    t = ev.get("type")
    if t == "system" and ev.get("subtype") == "init":
        chat["claude_session"] = ev.get("session_id")
    elif t == "stream_event":
        e = ev.get("event") or {}
        if e.get("type") == "content_block_delta" and (e.get("delta") or {}).get("type") == "text_delta":
            msg["draft"] += e["delta"]["text"]
    elif t == "assistant":
        for b in (ev.get("message") or {}).get("content") or []:
            if b.get("type") == "text" and b.get("text", "").strip():
                msg["parts"].append({"type": "text", "text": b["text"]})
                msg["draft"] = ""
            elif b.get("type") == "tool_use":
                msg["draft"] = ""
                msg["parts"].append(_tool_step(b, root))
    elif t == "user":
        content = (ev.get("message") or {}).get("content")
        for b in content if isinstance(content, list) else []:
            if b.get("type") != "tool_result":
                continue
            for p in msg["parts"]:
                if p.get("tool_id") == b.get("tool_use_id"):
                    p["status"] = "error" if b.get("is_error") else "done"
                    if p.get("kind") == "import":
                        out = _result_text(b)
                        m = PAPER_RE.search(out)
                        if m:
                            p.update(paper_id=m.group(1), detail=_paper_title(m.group(1)))
                        else:
                            p["status"] = "error"
                            p["output"] = out[-500:]
                        continue
                    if p.get("kind") in ("run", "tool", "fetch") or b.get("is_error"):
                        out = _result_text(b)
                        p["output"] = out if len(out) < 4000 else out[:2500] + "\n…\n" + out[-1200:]
    elif t == "result":
        msg["draft"] = ""
        if ev.get("session_id"):
            chat["claude_session"] = ev["session_id"]
        if ev.get("total_cost_usd") is not None:
            msg["cost"] = ev["total_cost_usd"]
        if ev.get("is_error") or ev.get("subtype") != "success":
            msg["status"] = "error"
            msg["error"] = ev.get("result") or ev.get("subtype")
        else:
            msg["status"] = "done"
            if not any(p["type"] == "text" for p in msg["parts"]) and ev.get("result"):
                msg["parts"].append({"type": "text", "text": ev["result"]})


def _pump(paper, cid, proc):
    noise = []  # 非 JSON 输出（stderr 合并进来了）
    last_draft_save = 0.0
    for line in proc.stdout:
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            noise = (noise + [line.rstrip()])[-20:]
            continue
        if ev.get("type") == "stream_event":
            d = ((ev.get("event") or {}).get("delta") or {}).get("text")
            if not d:
                continue
            # 流式文字很碎：内存里攒着，最多每 0.3 秒落一次盘
            with _lock:
                _pending.setdefault(cid, []).append(d)
            if time.time() - last_draft_save < 0.3:
                continue
            last_draft_save = time.time()
            _update(paper, cid, lambda chat, msg: _flush_draft(cid, msg))
            continue
        _update(paper, cid, lambda chat, msg: (_flush_draft(cid, msg), _handle(chat, msg, ev, paper.root)))
    proc.wait()
    err = "\n".join(noise)

    def finish(chat, msg):
        _pending.pop(cid, None)
        msg["draft"] = ""
        for p in msg["parts"]:
            if p.get("status") == "running":
                p["status"] = "error"
        if msg["status"] == "running":
            msg["status"] = "stopped" if proc.returncode in (-15, 143) else "error"
            msg["error"] = (err or "").strip()[-500:] or f"exit {proc.returncode}"
        msg["elapsed"] = round(time.time() - msg.get("started", time.time()))

    _update(paper, cid, finish)
    _procs.pop(cid, None)



def _flush_draft(cid, msg):
    with _lock:
        chunks = _pending.pop(cid, None)
    if chunks:
        msg["draft"] += "".join(chunks)


def recover(library):
    """上次服务进程留下的「进行中」标成中断。"""
    from core import topics
    for paper in library.list_papers() + topics.list_topics():
        d = _chats_dir(paper)
        for f in d.glob("*.json") if d.exists() else []:
            try:
                chat = json.loads(f.read_text(encoding="utf-8"))
            except ValueError:
                continue
            msg = chat["messages"][-1] if chat["messages"] else {}
            if msg.get("status") == "running":
                msg["status"] = "error"
                msg["error"] = "服务重启，本轮被中断"
                _save(paper, chat)


# ---------------------------------------------------------------------------
# 实验：用户点「运行」才执行，结果自动发回给 Claude
# ---------------------------------------------------------------------------

def run_experiment(paper, cid, rel_file, timeout=300):
    """在论文目录下运行 experiments/ 里的一个 .py；跑完把输出和图发给 Claude 解读。"""
    from core import sandbox
    if not RUN_RE.fullmatch(f"[[RUN {rel_file}]]"):
        raise ValueError("只能运行 experiments/ 下的 .py 文件")
    root = os.path.realpath(paper.root)
    path = os.path.realpath(os.path.join(root, rel_file))
    if not path.startswith(os.path.join(root, "experiments") + os.sep) or not os.path.isfile(path):
        raise FileNotFoundError(rel_file)
    with _lock:
        chat = load(paper, cid)
        if is_running(cid):
            raise RuntimeError("还有任务在进行中")
        with open(path, encoding="utf-8") as fh:
            code = fh.read()
        chat["messages"].append({"role": "run", "file": rel_file, "code": code, "status": "running",
                                 "started": time.time()})
        _save(paper, chat)
        cancel = threading.Event()
        _runs[cid] = cancel

    def work():
        run_id = time.strftime("%m%d_%H%M%S")
        try:
            res = sandbox.run_python(code, root, run_id, python=PYTHON, timeout=timeout,
                                     cancelled=cancel.is_set)
        except Exception as e:           # noqa: BLE001
            res = None
            err = f"{type(e).__name__}: {e}"
        images = [os.path.relpath(p, root) for p in (res.images if res else [])]

        def done(chat):
            m = chat["messages"][-1]
            if res is None:
                m.update(status="error", output=err)
            else:
                m.update(status="stopped" if res.cancelled else ("done" if res.exit_code == 0 else "error"),
                         output=res.output, exit_code=res.exit_code, images=images,
                         elapsed=round(res.duration, 1))
        with _lock:
            chat = load(paper, cid)
            done(chat)
            _save(paper, chat)
            _runs.pop(cid, None)
        if res is None or res.cancelled:
            return
        followup = [f"我运行了 `{rel_file}`。", res.summary_text()]
        if images:
            followup.append("生成的图片（已经显示给我了，不要在回答里重复嵌入；需要的话用 Read 自己看）：\n"
                            + "\n".join(f"- {p}" for p in images))
        followup.append("请解读结果：说明了什么、和论文是否一致。出错的话修好代码，再让我运行。")
        send(paper, cid, "\n\n".join(followup), auto=True)

    threading.Thread(target=work, daemon=True).start()
