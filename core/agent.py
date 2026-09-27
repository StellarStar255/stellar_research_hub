# -*- coding: utf-8 -*-
"""论文研究 agent：一轮对话 = 流式调用 Claude + 执行它要的工具，直到它答完。

设计要点
- 论文 PDF 作为 document 块放在第一条用户消息里（开启引用 → 回答能标出处页码），
  打 1 小时缓存断点；整段历史再用顶层自动缓存。多轮追问时论文不重复计费。
- 历史只追加不修改：系统提示词、工具列表、论文标题都在建会话时定死
  （见 Library.new_session）。中途改了前缀，缓存失效，历史里的 thinking 块
  也会被服务端拒绝。
- 历史里不存大块二进制：PDF 存成 {"type": "paper_document"}，实验图片存成
  {"type": "local_image", "path": 相对路径}，发请求前再展开（文件内容不变，展开结果逐字节一致）。
- 工具：run_python（在本机跑实验）、save_note（写进笔记）；可选服务端
  web_search / web_fetch（查相关工作）。
- 本模块不依赖 Qt：界面交互（确认运行代码、写笔记、显示流式文字）都通过
  Callbacks 注入，方便测试。
"""

import base64
import datetime
import inspect
import mimetypes
import os

from core import sandbox

DEFAULT_MODEL = "claude-opus-5"
MODEL_CHOICES = ["claude-opus-5", "claude-sonnet-5", "claude-fable-5-1", "claude-opus-5-5",
                 "claude-haiku-4-5"]
EFFORT_CHOICES = ["low", "medium", "high", "xhigh", "max"]
DEFAULT_EFFORT = "high"
MAX_TOKENS = 64000
MAX_TOOL_ROUNDS = 30          # 一轮提问里最多来回调用多少次工具，防止失控
MAX_PDF_BYTES = 30 * 1024 * 1024   # 请求体上限 32MB，留点余量给历史
API_TIMEOUT_SECONDS = 600.0
API_MAX_RETRIES = 2
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# 每百万 token 价格（输入, 输出），用于在界面上粗估花费；缓存读 0.1×，写 1.25×
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-4-5": (1.0, 5.0),
}


class AgentError(Exception):
    """给用户看的错误（已经是人话）。"""


# ---------------------------------------------------------------------------
# 学习风格 & 快捷提问
# ---------------------------------------------------------------------------

STYLES = {
    "tutor": ("导师（苏格拉底式）",
              "你是耐心的导师。先弄清我已经懂什么，再一步一步讲；每次只讲一个概念，"
              "讲完用一个小问题检验我是否真的理解，等我回答后再给反馈、继续往下。"
              "我答错时不要直接给答案，先给提示。不要一次把所有内容倒给我。"),
    "explain": ("讲解（直接解释）",
                "直接、完整、有条理地回答。先给一句话结论，再展开；多用直觉类比、"
                "具体数值例子和“为什么要这样做”的动机说明。"),
    "skim": ("速读（抓重点）",
             "简洁优先：要点列表，每点一两句话。优先回答“解决什么问题、核心想法、"
             "关键结果、值不值得细读”。除非我追问，不展开推导。"),
    "lab": ("实验（动手验证）",
            "尽量用可运行的小实验来回答：写最小复现、用玩具数据验证论文的主张、画图、"
            "和论文里的数字对比。先说明实验设计和预期，再运行，最后解读结果和它与论文的差异。"),
}
DEFAULT_STYLE = "tutor"

QUICK_PROMPTS = [
    ("三分钟速览", "用三分钟能读完的篇幅告诉我：这篇论文要解决什么问题、核心想法是什么、"
                "关键结果如何、我应该重点读哪几节。"),
    ("带我精读", "带我逐节精读这篇论文。先给出论文结构和建议的阅读路线，然后从第一个关键部分开始，"
             "每次只讲一部分，讲完问我是否继续。"),
    ("拆解核心方法", "把论文的核心方法和关键公式拆开讲：每个符号的含义、每一步为什么这样做、"
                "和最朴素的做法相比好在哪里。"),
    ("考考我", "根据我们到目前为止讨论的内容（还没讨论过就按论文核心内容）出 3 道检验理解的题，"
            "由浅到深。一次只出一题，等我回答后批改，再出下一题。"),
    ("设计小实验", "设计一个能在我电脑上几分钟内跑完的小实验，直观验证这篇论文的一个核心主张。"
              "先说明实验设计和预期结果，然后写代码运行，最后解读结果。"),
    ("批判性评价", "批判性地评价这篇论文：假设是否合理、实验是否充分、有哪些局限或可能的漏洞、"
              "哪些后续工作值得接着读。"),
    ("整理笔记", "把我们这次对话的要点整理成一份简洁的学习笔记（核心概念、关键公式的直觉、"
             "我容易搞错的地方、待解决的问题），用 save_note 存进笔记。"),
]


def build_system_prompt(session_data):
    style_key = session_data.get("style") or DEFAULT_STYLE
    style = STYLES.get(style_key, STYLES[DEFAULT_STYLE])[1]
    background = (session_data.get("background") or "").strip()
    web = session_data.get("web_tools")
    parts = [
        "你是一个帮助用户学习和研究学术论文的助手，嵌在一个桌面论文阅读应用里。"
        "用户会就同一篇论文反复提问、讨论、做实验。论文全文以 PDF 附在第一条消息里，"
        f"标题是《{session_data.get('paper_title') or '未命名'}》。",
        "## 回答风格\n" + style,
    ]
    if background:
        parts.append("## 用户的背景\n" + background + "\n据此调整讲解深度：不要解释用户显然已经会的东西，"
                     "也不要跳过用户可能缺的前置知识。")
    parts.append(
        "## 准确性\n"
        "- 讲论文内容时以论文原文为准，引用具体段落作为依据（界面会把引用显示成可点击的页码）。\n"
        "- 分清三件事：论文明确写了的、你基于论文的推断、论文之外的背景知识。后两者要说明。\n"
        "- 论文没写或你不确定的，直接说不确定，不要编造数字、实验设置或参考文献。")
    parts.append(
        "## 工具\n"
        "- run_python：在用户电脑上运行 Python 做实验（工作目录是这篇论文的 experiments 文件夹，"
        "文件会保留，后续运行可以复用）。适合：用玩具数据复现核心算法、数值验证公式、画图帮助理解、"
        "对比论文数字。代码要小而快（默认几分钟内跑完），先用 numpy/matplotlib 等常见库；"
        "需要额外的库时先检查能否 import，缺了就告诉用户怎么装，不要自己 pip install。"
        "画图用 matplotlib，调用 plt.savefig('名字.png') 或 plt.show() 都行，图会回传给你看。"
        "运行前用户可能要确认，被拒绝时尊重用户的决定。\n"
        "- save_note：把值得保留的内容写进这篇论文的学习笔记。用户要求记笔记、整理要点时使用；"
        "内容写成独立可读的 Markdown。不要未经要求频繁写笔记。"
        + ("\n- web_search / web_fetch：查找相关工作、后续论文、官方代码仓库等论文之外的信息，"
           "给出来源链接。" if web else ""))
    parts.append(
        "## 格式\n"
        "界面用 Markdown 显示回答，但不渲染 LaTeX。行内公式请写成易读的 Unicode 形式"
        "（如 softmax(QKᵀ/√dₖ)·V、∑ᵢ pᵢ log pᵢ）；较长的推导放进代码块，每一步配一句文字说明。"
        "回答用用户提问所用的语言。")
    return "\n\n".join(parts)


def tool_definitions(session_data, model):
    tools = [
        {
            "name": "run_python",
            "description": (
                "在用户的电脑上运行一段 Python 3 代码并返回输出（stdout+stderr）和生成的图片。"
                "工作目录是这篇论文的实验文件夹，文件会跨运行保留。没有 stdin，不要用 input()。"),
            "eager_input_streaming": True,
            "input_schema": {
                "type": "object",
                "properties": {
                    "purpose": {"type": "string",
                                "description": "一句话说明这段代码要验证/展示什么（给用户确认时看）"},
                    "code": {"type": "string", "description": "完整的 Python 代码"},
                },
                "required": ["purpose", "code"],
            },
        },
        {
            "name": "save_note",
            "description": "把一段 Markdown 追加到这篇论文的学习笔记里。",
            "input_schema": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "这条笔记的小标题"},
                    "content": {"type": "string", "description": "笔记正文（Markdown）"},
                },
                "required": ["title", "content"],
            },
        },
    ]
    if session_data.get("web_tools"):
        if model.startswith("claude-haiku"):
            tools += [{"type": "web_search_20250305", "name": "web_search", "max_uses": 5},
                      {"type": "web_fetch_20250910", "name": "web_fetch", "max_uses": 5}]
        else:
            tools += [{"type": "web_search_20260209", "name": "web_search", "max_uses": 5},
                      {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 5}]
    return tools


# ---------------------------------------------------------------------------
# 历史 ↔ API 消息
# ---------------------------------------------------------------------------

def paper_document_block(pdf_path, title):
    size = os.path.getsize(pdf_path)
    if size > MAX_PDF_BYTES:
        raise AgentError(f"PDF 有 {size / 1024 / 1024:.0f}MB，超过单次请求上限（约 30MB），"
                         "可以先用 PDF 工具压缩或只保留正文页")
    with open(pdf_path, "rb") as fh:
        data = base64.standard_b64encode(fh.read()).decode("ascii")
    return {
        "type": "document",
        "source": {"type": "base64", "media_type": "application/pdf", "data": data},
        "title": title,
        "citations": {"enabled": True},
        # 学习时常常停下来想一会儿再问，5 分钟缓存不够用
        "cache_control": {"type": "ephemeral", "ttl": "1h"},
    }


def _image_block(abs_path):
    media = mimetypes.guess_type(abs_path)[0] or "image/png"
    with open(abs_path, "rb") as fh:
        data = base64.standard_b64encode(fh.read()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def expand_messages(messages, pdf_path, paper_title, experiments_dir):
    """把历史里的占位块展开成真正的 API 内容块（不修改原列表）。"""
    doc = None
    out = []
    for m in messages:
        content = m.get("content")
        if not isinstance(content, list):
            out.append(m)
            continue
        new_content = []
        for b in content:
            t = b.get("type")
            if t == "paper_document":
                if doc is None:
                    doc = paper_document_block(pdf_path, paper_title)
                new_content.append(doc)
            elif t == "tool_result" and isinstance(b.get("content"), list):
                inner = []
                for c in b["content"]:
                    if c.get("type") == "local_image":
                        p = os.path.join(experiments_dir, c["path"])
                        if os.path.exists(p):
                            inner.append(_image_block(p))
                        else:
                            inner.append({"type": "text", "text": f"（图片 {c['path']} 已被删除）"})
                    else:
                        inner.append(c)
                new_content.append({**b, "content": inner})
            else:
                new_content.append(b)
        out.append({**m, "content": new_content})
    return out


_PRE_FALLBACK_DROP = {"thinking", "redacted_thinking", "tool_use"}


def sanitize_assistant_content(blocks):
    """服务端 fallback 在输出中途换了模型时，按规则整理要写回历史的内容：
    最后一个 fallback 块之前的 thinking / tool_use、没配对结果的 server_tool_use 去掉；
    fallback 标记块本身也去掉（它只是审计标记）。"""
    last_fb = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    if last_fb < 0:
        return blocks
    result_ids = {b.get("tool_use_id") for b in blocks if str(b.get("type", "")).endswith("_tool_result")}
    out = []
    for i, b in enumerate(blocks):
        t = b.get("type")
        if t == "fallback":
            continue
        if i < last_fb:
            if t in _PRE_FALLBACK_DROP:
                continue
            if t == "server_tool_use" and b.get("id") not in result_ids:
                continue
            if t not in ("text", "server_tool_use") and not str(t).endswith("_tool_result"):
                continue
        out.append(b)
    return out


# ---------------------------------------------------------------------------
# 回调接口
# ---------------------------------------------------------------------------

class Callbacks:
    """界面层实现这些方法。默认实现什么都不做（测试时按需覆盖）。"""

    def on_text(self, delta): pass
    def on_thinking(self, delta): pass
    def on_status(self, text): pass
    def on_tool_start(self, name, tool_input): pass
    def on_tool_done(self, name, result_text, images): pass
    def on_history_changed(self): pass            # 历史追加了完整的一步，可以重绘 / 保存

    def cancelled(self):
        return False

    def approve_code(self, purpose, code):
        """返回 (是否运行, 拒绝理由)。"""
        return True, ""

    def save_note(self, title, content):
        """返回写入结果说明（给模型看）。"""
        return "已保存"


# ---------------------------------------------------------------------------
# 调用
# ---------------------------------------------------------------------------

def make_client(settings):
    try:
        import anthropic
    except ImportError:
        raise AgentError("未安装 anthropic SDK，请先执行: pip install anthropic")
    kwargs = {"timeout": API_TIMEOUT_SECONDS, "max_retries": API_MAX_RETRIES}
    if settings.get("api_key"):
        kwargs["api_key"] = settings["api_key"]
    if settings.get("base_url"):
        kwargs["base_url"] = settings["base_url"]
    return anthropic.Anthropic(**kwargs)


def use_server_fallback(client, model, base_url):
    """只对支持的模型开服务端 fallbacks；走第三方网关（base_url）或 SDK 太旧时不开。"""
    if base_url or not model.startswith(("claude-opus-5", "claude-fable")):
        return False
    try:
        params = inspect.signature(client.beta.messages.stream).parameters
    except (TypeError, ValueError, AttributeError):
        return False
    return "fallbacks" in params


def build_request(session, settings, client):
    """组装一次请求的参数（不含 messages 展开以外的副作用）。"""
    data = session.data
    model = settings.get("model") or DEFAULT_MODEL
    paper = session.paper
    params = {
        "model": model,
        "max_tokens": MAX_TOKENS,
        "system": build_system_prompt(data),
        "tools": tool_definitions(data, model),
        "messages": expand_messages(data["messages"], paper.pdf_path,
                                    data.get("paper_title") or paper.title,
                                    paper.experiments_dir),
        "cache_control": {"type": "ephemeral"},
    }
    if not model.startswith("claude-haiku"):
        params["thinking"] = {"type": "adaptive", "display": "summarized"}
        params["output_config"] = {"effort": settings.get("effort") or DEFAULT_EFFORT}
    betas = []
    if use_server_fallback(client, model, settings.get("base_url")):
        betas.append(FALLBACK_BETA)
        params["fallbacks"] = "default"
    params["betas"] = betas
    return params


def _friendly_api_error(e, model):
    import anthropic
    if isinstance(e, anthropic.AuthenticationError):
        return "API Key 无效或未配置（菜单 设置 → AI 设置…）"
    if isinstance(e, anthropic.PermissionDeniedError):
        return "这个 API Key 没有权限访问该模型或功能"
    if isinstance(e, anthropic.NotFoundError):
        return f"模型 {model} 不存在或无权访问"
    if isinstance(e, anthropic.RateLimitError):
        return "请求过于频繁或额度用尽，稍后再试"
    if isinstance(e, anthropic.BadRequestError):
        return f"请求被拒绝（400）: {e.message}"
    if isinstance(e, anthropic.APIStatusError):
        if e.status_code >= 500:
            return f"Anthropic 服务暂时不可用（{e.status_code}），稍后再试"
        return f"API 错误 {e.status_code}: {e.message}"
    if isinstance(e, anthropic.APITimeoutError):
        return "请求超时，检查网络后重试"
    if isinstance(e, anthropic.APIConnectionError):
        return f"网络连接失败，检查网络或代理: {e}"
    return str(e)


def _usage_dict(usage):
    return {
        "input": getattr(usage, "input_tokens", 0) or 0,
        "output": getattr(usage, "output_tokens", 0) or 0,
        "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
    }


def estimate_cost(usage, model):
    pin, pout = PRICES.get(model, PRICES[DEFAULT_MODEL])
    return (usage.get("input", 0) * pin + usage.get("cache_read", 0) * pin * 0.1
            + usage.get("cache_write", 0) * pin * 1.25 + usage.get("output", 0) * pout) / 1e6


def _stream_once(client, params, cb):
    """流式请求一次，返回 (final_message, 是否被用户中止)。"""
    with client.beta.messages.stream(**params) as stream:
        for event in stream:
            if cb.cancelled():
                return None, True
            et = getattr(event, "type", "")
            if et == "text":
                cb.on_text(event.text)
            elif et == "thinking":
                cb.on_thinking(event.thinking)
            elif et == "content_block_start":
                bt = getattr(event.content_block, "type", "")
                if bt == "tool_use":
                    cb.on_status(f"正在编写 {event.content_block.name} 的参数…")
                elif bt == "server_tool_use":
                    cb.on_status("正在联网查找…")
                elif bt == "thinking":
                    cb.on_status("思考中…")
                elif bt == "text":
                    cb.on_status("回答中…")
                elif bt == "fallback":
                    cb.on_status("主模型拒答，已自动换用备用模型继续…")
        return stream.get_final_message(), False


def run_turn(client, session, user_text, settings, cb, max_rounds=MAX_TOOL_ROUNDS):
    """用户问一句 → 跑完整个 agent 回合。历史直接追加到 session.messages 并保存。

    出错时：本轮还没写入任何助手消息就回滚（返回前把用户这句话从历史里去掉，
    界面把它放回输入框）；已经有进展就保留到最后一个完整的状态。
    返回 "done" / "cancelled"；出错抛 AgentError。
    """
    import anthropic

    messages = session.messages
    start_len = len(messages)
    content = []
    if not any(b.get("type") == "paper_document"
               for m in messages if isinstance(m.get("content"), list) for b in m["content"]):
        content.append({"type": "paper_document"})
    content.append({"type": "text", "text": user_text})
    messages.append({"role": "user", "content": content})

    def rollback_if_untouched():
        if not any(m.get("role") == "assistant" for m in messages[start_len:]):
            del messages[start_len:]

    model = settings.get("model") or DEFAULT_MODEL
    json_retries = 0
    rounds = 0
    try:
        while True:
            if cb.cancelled():
                rollback_if_untouched()
                return "cancelled"
            params = build_request(session, settings, client)
            try:
                final, was_cancelled = _stream_once(client, params, cb)
            except ValueError:
                # 流式工具参数是模型输出的 JSON，偶尔完全解析不了：重发这一步（有上限）
                json_retries += 1
                if json_retries > 2:
                    raise AgentError("模型返回的工具参数无法解析，已重试多次，请换个说法再问")
                continue
            except anthropic.APIError as e:
                raise AgentError(_friendly_api_error(e, model)) from e
            if was_cancelled:
                rollback_if_untouched()
                return "cancelled"
            json_retries = 0
            session.add_usage(_usage_dict(final.usage))

            stop = final.stop_reason
            if stop == "refusal":
                detail = ""
                sd = getattr(final, "stop_details", None)
                if sd is not None and getattr(sd, "explanation", None):
                    detail = "：" + sd.explanation
                raise AgentError("模型拒绝了这个请求" + detail)

            blocks = sanitize_assistant_content([b.to_dict(mode="json") for b in final.content])
            messages.append({"role": "assistant", "content": blocks})
            session.save()
            cb.on_history_changed()

            if stop == "pause_turn":
                continue   # 服务端工具（联网搜索）还没做完，原样续上
            tool_uses = [b for b in blocks if b.get("type") == "tool_use"]
            if not tool_uses:
                if stop == "max_tokens":
                    cb.on_status("回答太长被截断了，可以说“继续”")
                return "done"

            rounds += 1
            results = []
            for tu in tool_uses:
                if stop == "max_tokens":
                    results.append(_error_result(tu, "输出被截断，工具参数不完整，没有执行"))
                elif rounds > max_rounds:
                    results.append(_error_result(tu, "本轮工具调用次数已达上限，请先总结当前结论"))
                elif cb.cancelled():
                    results.append(_error_result(tu, "用户中止了本轮"))
                else:
                    results.append(_run_tool(tu, session, settings, cb))
            messages.append({"role": "user", "content": results})
            session.save()
            cb.on_history_changed()
            if stop == "max_tokens" or rounds > max_rounds or cb.cancelled():
                return "cancelled" if cb.cancelled() else "done"
    except Exception:
        rollback_if_untouched()
        if messages or os.path.exists(session.path):   # 新会话第一句就失败：不留空文件
            session.save()
        raise


def _error_result(tool_use, text):
    return {"type": "tool_result", "tool_use_id": tool_use["id"], "is_error": True,
            "content": [{"type": "text", "text": text}]}


def _run_tool(tool_use, session, settings, cb):
    name = tool_use.get("name")
    args = tool_use.get("input")
    # 流式工具参数由 SDK 宽松解析，可能缺字段 / 类型不对：先校验再执行
    if name == "run_python":
        if not (isinstance(args, dict) and isinstance(args.get("code"), str) and args["code"].strip()):
            return _error_result(tool_use, "参数无效：需要非空字符串 code")
        purpose = args.get("purpose") if isinstance(args.get("purpose"), str) else ""
        cb.on_tool_start(name, args)
        ok, reason = cb.approve_code(purpose, args["code"])
        if not ok:
            msg = "用户拒绝运行这段代码。" + (f"理由：{reason}" if reason else "")
            cb.on_tool_done(name, msg, [])
            return _error_result(tool_use, msg)
        cb.on_status("正在运行实验代码…")
        workdir = session.paper.experiments_dir
        run_id = datetime.datetime.now().strftime("%m%d_%H%M%S_") + tool_use["id"][-6:]
        res = sandbox.run_python(args["code"], workdir, run_id,
                                 python=settings.get("python") or None,
                                 timeout=int(settings.get("timeout") or 120),
                                 cancelled=cb.cancelled)
        text = res.summary_text()
        cb.on_tool_done(name, text, res.images)
        content = [{"type": "text", "text": text}]
        content += [{"type": "local_image", "path": os.path.relpath(p, workdir)} for p in res.images]
        out = {"type": "tool_result", "tool_use_id": tool_use["id"], "content": content}
        if res.exit_code != 0:
            out["is_error"] = True
        return out
    if name == "save_note":
        if not (isinstance(args, dict) and isinstance(args.get("content"), str)):
            return _error_result(tool_use, "参数无效：需要字符串 content")
        title = args.get("title") if isinstance(args.get("title"), str) else ""
        cb.on_tool_start(name, args)
        msg = cb.save_note(title, args["content"])
        cb.on_tool_done(name, msg, [])
        return {"type": "tool_result", "tool_use_id": tool_use["id"],
                "content": [{"type": "text", "text": msg}]}
    return _error_result(tool_use, f"未知工具 {name}")


def append_note(notes_text, title, content):
    """笔记追加格式：二级标题 + 时间 + 正文。"""
    stamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    head = f"## {title.strip()}" if title and title.strip() else "## 笔记"
    block = f"{head}\n*{stamp} · Claude*\n\n{content.strip()}\n"
    base = notes_text.rstrip()
    return (base + "\n\n" + block) if base else block
