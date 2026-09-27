"""本地网页：python -m web.server  ->  http://localhost:8767

不需要 API Key：对话走本机已登录的 Claude Code（claude -p）。
"""
import html
import json
import os
import re
import tempfile
import urllib.parse

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel

from core import arxiv, topics
from core.library import Library
from web import agent

PORT = int(os.environ.get("RESEARCH_HUB_PORT", "8767"))
app = FastAPI()
library = Library()


def _paper(pid):
    p = library.get(pid) if pid and "/" not in pid and ".." not in pid else None
    if p is None:
        raise HTTPException(404, "论文不存在")
    return p


def _ctx(kind, oid):
    """对话 / 笔记的归属：一篇论文或一个研究主题。"""
    if kind == "papers":
        return _paper(oid)
    if kind == "topics":
        t = topics.get(oid)
        if t is None:
            raise HTTPException(404, "主题不存在")
        return t
    raise HTTPException(404)


def _topic_json(t):
    return {"id": t.id, "title": t.title, "archived": bool(t.meta.get("archived")),
            "created": t.meta.get("created_at", "")[:10],
            "papers": [_paper_json(p) for p in (library.get(i) for i in t.meta.get("papers", [])) if p]}


def _paper_json(p):
    return {"id": p.id, "title": p.title, "subtitle": p.display_subtitle(),
            "abstract": p.meta.get("abstract", ""), "url": p.meta.get("url", ""),
            "archived": bool(p.meta.get("archived"))}


class UpdateReq(BaseModel):
    title: str | None = None
    archived: bool | None = None


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"),
                        headers={"Cache-Control": "no-store"})


# ---------------- 论文 ----------------

@app.get("/api/papers")
def papers():
    return [_paper_json(p) for p in library.list_papers()]


class ImportReq(BaseModel):
    text: str


@app.post("/api/papers/import")
def import_paper(req: ImportReq):
    text = req.text.strip()
    arxiv_id = arxiv.parse_arxiv_id(text)
    if arxiv_id:
        dup = library.find_duplicate(arxiv_id=arxiv_id)
        if dup:
            return _paper_json(dup)
    try:
        path, meta = arxiv.fetch(text)
    except Exception as e:           # noqa: BLE001
        raise HTTPException(400, arxiv.friendly_error(e))
    try:
        dup = library.find_duplicate(pdf_path=path, arxiv_id=meta.get("arxiv_id"))
        return _paper_json(dup or library.add_pdf(path, meta))
    finally:
        os.remove(path)


@app.post("/api/papers/upload")
async def upload(request: Request, name: str = "paper.pdf"):
    data = await request.body()
    if not data.startswith(b"%PDF-"):
        raise HTTPException(400, "不是 PDF 文件")
    title = os.path.splitext(os.path.basename(name))[0]
    fd, path = tempfile.mkstemp(suffix=".pdf")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        dup = library.find_duplicate(pdf_path=path)
        return _paper_json(dup or library.add_pdf(path, {"title": title}))
    finally:
        os.remove(path)


@app.put("/api/papers/{pid}")
def update_paper(pid: str, req: UpdateReq):
    p = _paper(pid)
    if req.title is not None and req.title.strip():
        p.meta["title"] = req.title.strip()[:200]
    if req.archived is not None:
        p.meta["archived"] = req.archived
    p.save_meta()
    return _paper_json(p)


@app.delete("/api/papers/{pid}")
def delete_paper(pid: str):
    p = _paper(pid)
    for c in agent.list_chats(p):
        agent.stop(c["id"])
    library.delete(p)
    return {"ok": True}


@app.get("/files/{pid}/{path:path}")
def files(pid: str, path: str):
    """论文目录下的文件（PDF、实验图片）。只许在论文目录内。"""
    p = _paper(pid)
    root = os.path.realpath(p.root)
    full = os.path.realpath(os.path.join(root, path))
    if not full.startswith(root + os.sep) or not os.path.isfile(full):
        raise HTTPException(404)
    return FileResponse(full)


# ---------------- 研究主题 ----------------

class TopicReq(BaseModel):
    title: str


@app.get("/api/topics")
def list_topics():
    return [_topic_json(t) for t in topics.list_topics()]


@app.post("/api/topics")
def create_topic(req: TopicReq):
    if not req.title.strip():
        raise HTTPException(400, "主题不能为空")
    t = topics.create(req.title)
    cid = agent.send(t, None, f"请调研这个主题：{req.title.strip()}")
    return {**_topic_json(t), "chat_id": cid}


@app.get("/api/topics/{tid}")
def get_topic(tid: str):
    return _topic_json(_ctx("topics", tid))


@app.put("/api/topics/{tid}")
def update_topic(tid: str, req: UpdateReq):
    t = _ctx("topics", tid)
    if req.title is not None and req.title.strip():
        t.meta["title"] = req.title.strip()[:120]
    if req.archived is not None:
        t.meta["archived"] = req.archived
    t.save()
    return _topic_json(t)


@app.delete("/api/topics/{tid}")
def delete_topic(tid: str):
    t = _ctx("topics", tid)
    for c in agent.list_chats(t):
        agent.stop(c["id"])
    topics.delete(t)
    return {"ok": True}


# ---------------- 笔记 / 报告 ----------------

@app.get("/api/{kind}/{oid}/notes")
def get_notes(kind: str, oid: str):
    return {"text": _ctx(kind, oid).read_notes()}


class NotesReq(BaseModel):
    text: str


@app.put("/api/{kind}/{oid}/notes")
def put_notes(kind: str, oid: str, req: NotesReq):
    _ctx(kind, oid).write_notes(req.text)
    return {"ok": True}


# ---------------- 导出 ----------------

def _export_markdown(obj):
    """报告 / 笔记 → 可独立阅读的 Markdown：paper:<id> 链接换成论文网页，相对图片路径换成绝对路径。"""
    text = obj.read_notes()

    def paper_link(m):
        p = library.get(m.group(2)) if re.fullmatch(r"[\w.-]+", m.group(2)) else None
        url = (p.meta.get("url") if p else "") or ""
        return f"[{m.group(1)}]({url})" if url else m.group(1)

    text = re.sub(r"\[([^\]]+)\]\(paper:([^)#\s]+)[^)]*\)", paper_link, text)
    if getattr(obj, "kind", "") == "paper":
        text = re.sub(r"(!\[[^\]]*\]\()(?!https?:|/)([^)]+)\)",
                      lambda m: f"{m.group(1)}{os.path.join(obj.root, m.group(2))})", text)
    head = f"# {obj.title}\n\n" if not text.lstrip().startswith("# ") else ""
    return head + text


def _filename(obj, ext):
    name = re.sub(r'[\\/:*?"<>|\s]+', "_", obj.title).strip("_")[:60] or "report"
    return name + ("_综述" if obj.kind == "topic" else "_笔记") + ext


@app.get("/api/{kind}/{oid}/export.md")
def export_md(kind: str, oid: str):
    obj = _ctx(kind, oid)
    fn = urllib.parse.quote(_filename(obj, ".md"))
    return Response(_export_markdown(obj), media_type="text/markdown",
                    headers={"Content-Disposition": f"attachment; filename*=UTF-8''{fn}"})


@app.get("/print/{kind}/{oid}")
def print_page(kind: str, oid: str):
    """打印版：浏览器打开后自动弹出打印，选「存储为 PDF」即可。"""
    obj = _ctx(kind, oid)
    md = obj.read_notes()
    if kind == "papers":   # 相对图片路径走 /files
        md = re.sub(r"(!\[[^\]]*\]\()(?!https?:|/)([^)]+)\)",
                    lambda m: f"{m.group(1)}/files/{obj.id}/{m.group(2)})", md)
    md = re.sub(r"\]\(paper:([^)#\s]+)[^)]*\)",
                lambda m: "](" + ((library.get(m.group(1)).meta.get("url") or "#") if library.get(m.group(1)) else "#") + ")",
                md)
    with open(os.path.join(os.path.dirname(__file__), "static", "print.html"), encoding="utf-8") as fh:
        page = fh.read()
    page = page.replace("{{TITLE}}", html.escape(obj.title)).replace(
        "{{MARKDOWN_JSON}}", json.dumps(md, ensure_ascii=False).replace("</", "<\\/"))
    return HTMLResponse(page)


# ---------------- 对话 ----------------

class ChatReq(BaseModel):
    message: str
    chat_id: str | None = None
    style: str = "tutor"


@app.get("/api/{kind}/{oid}/chats")
def chats(kind: str, oid: str):
    return agent.list_chats(_ctx(kind, oid))


@app.post("/api/{kind}/{oid}/chats")
def send(kind: str, oid: str, req: ChatReq):
    if not req.message.strip():
        raise HTTPException(400, "问题不能为空")
    obj = _ctx(kind, oid)
    if kind == "topics" and req.chat_id:
        obj.confirm_plan()     # 看过计划后用户的任何回复（确认或修改意见）都算确认，之后允许下载
    try:
        return {"id": agent.send(obj, req.chat_id, req.message.strip(), req.style)}
    except (FileNotFoundError, ValueError):
        raise HTTPException(404, "对话不存在")
    except RuntimeError as e:
        raise HTTPException(409, str(e))


@app.get("/api/{kind}/{oid}/chats/{cid}")
def chat(kind: str, oid: str, cid: str):
    try:
        c = agent.load(_ctx(kind, oid), cid)
    except (FileNotFoundError, ValueError):
        raise HTTPException(404)
    c["running"] = agent.is_running(cid)
    return c


class RunReq(BaseModel):
    file: str


@app.post("/api/papers/{pid}/chats/{cid}/run")
def run(pid: str, cid: str, req: RunReq):
    try:
        agent.run_experiment(_paper(pid), cid, req.file)
    except FileNotFoundError:
        raise HTTPException(404, f"找不到 {req.file}")
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.post("/api/{kind}/{oid}/chats/{cid}/stop")
def stop(kind: str, oid: str, cid: str):
    agent.stop(cid)
    return {"ok": True}


@app.delete("/api/{kind}/{oid}/chats/{cid}")
def delete_chat(kind: str, oid: str, cid: str):
    agent.delete(_ctx(kind, oid), cid)
    return {"ok": True}


if __name__ == "__main__":
    agent.recover(library)
    uvicorn.run(app, host="127.0.0.1", port=PORT)
