"""Small stdio MCP bridge: Codex has a read-only sandbox; writes go through this allowlist."""
import base64
import contextlib
import io
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def tools(kind):
    specs = [
        ("Read", "读取项目文件、PDF（pages 如 1-10）、图片", {"file_path": {"type": "string"}, "pages": {"type": "string"}}, ["file_path"]),
        ("Write", "写完整文件；append=true 追加笔记。只能写 experiments/、notes.md 或主题 report.md", {"file_path": {"type": "string"}, "content": {"type": "string"}, "append": {"type": "boolean"}}, ["file_path", "content"]),
    ]
    if kind == "topic":
        specs += [
            ("Search", "搜索 arXiv 及引用数", {"query": {"type": "string"}, "n": {"type": "integer"}, "new": {"type": "boolean"}}, ["query"]),
            ("Cite", "查询 arXiv 论文引用数", {"ids": {"type": "array", "items": {"type": "string"}}}, ["ids"]),
            ("Import", "用户确认计划后下载论文到本主题", {"ref": {"type": "string"}}, ["ref"]),
        ]
    return [{"name": n, "description": d, "inputSchema": {"type": "object", "properties": p, "required": r, "additionalProperties": False}} for n, d, p, r in specs]


def file_path(root, value, kind, write=False):
    root = Path(root).resolve()
    p = (root / value).resolve()
    if write:
        rel = p.relative_to(root).as_posix()
        allowed = (rel == "report.md") if kind == "topic" else (rel == "notes.md" or rel.startswith("experiments/"))
        if not allowed:
            raise ValueError("该文件不允许写入")
    elif not p.is_relative_to(root):
        # Topics can read downloaded library PDFs, but not arbitrary home files.
        from core.library import Library
        library = Path(Library().root).resolve()
        if kind != "topic" or not p.is_relative_to(library) or p.name != "paper.pdf":
            raise ValueError("只能读取本项目或论文库中的 PDF")
    return p


_QT = None


def read_pdf(path, pages=""):
    global _QT
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QGuiApplication
    from PyQt6.QtPdf import QPdfDocument
    _QT = QGuiApplication.instance() or QGuiApplication([])
    doc = QPdfDocument(None)
    if doc.load(str(path)) != QPdfDocument.Error.None_:
        raise ValueError("无法读取 PDF")
    count = doc.pageCount()
    start, end = 1, min(count, 10)
    if pages:
        bounds = pages.split("-")
        start = int(bounds[0])
        end = int(bounds[-1])
    if not 1 <= start <= end <= count or end - start >= 20:
        raise ValueError(f"PDF 共 {count} 页，每次最多读取 20 页")
    content = [{"type": "text", "text": f"PDF 共 {count} 页，本次读取 {start}-{end} 页。"}]
    from PyQt6.QtCore import QBuffer, QIODevice, QSize
    for page in range(start - 1, end):
        text = doc.getAllText(page).text()
        content.append({"type": "text", "text": f"\n(p.{page + 1})\n{text}"})
        # Include the rendered page so formulas, diagrams and scanned pages remain visible.
        size = doc.pagePointSize(page)
        width = 1000
        img = doc.render(page, QSize(width, round(width * size.height() / size.width())))
        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        content.append({"type": "image", "mimeType": "image/png", "data": base64.b64encode(bytes(buf.data())).decode()})
    doc.close()
    return content


def call(root, kind, oid, name, args):
    if name not in {t["name"] for t in tools(kind)}:
        raise ValueError("未知工具")
    if name == "Read":
        p = file_path(root, args["file_path"], kind)
        if p.suffix.lower() == ".pdf":
            return read_pdf(p, args.get("pages", ""))
        mime = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}.get(p.suffix.lower())
        if mime:
            return [{"type": "image", "mimeType": mime, "data": base64.b64encode(p.read_bytes()).decode()}]
        output = p.read_text(encoding="utf-8")
    elif name == "Write":
        p = file_path(root, args["file_path"], kind, write=True)
        if kind == "topic":
            from core import topics
            topic = topics.get(oid)
            if topic is None or topic.meta.get("plan_confirmed") is False:
                raise ValueError("用户确认计划前不能写报告")
        p.parent.mkdir(parents=True, exist_ok=True)
        with p.open("a" if args.get("append") else "w", encoding="utf-8") as f:
            f.write(args["content"])
        output = f"已写入 {p.relative_to(Path(root).resolve())}"
    else:
        import rh
        capture = io.StringIO()
        with contextlib.redirect_stdout(capture):
            if name == "Search":
                code = rh.cmd_search(SimpleNamespace(query=args["query"], n=max(1, min(20, args.get("n", 10))), new=args.get("new", False)))
            elif name == "Cite":
                code = rh.cmd_cite(SimpleNamespace(ids=args["ids"]))
            else:
                from core import topics
                topic = topics.get(oid)
                if topic is None or topic.meta.get("plan_confirmed") is False:
                    raise ValueError("用户尚未确认调研计划")
                code = rh.cmd_import(SimpleNamespace(ref=args["ref"], topic=oid))
        output = capture.getvalue()
        if code:
            raise ValueError(output)
    return [{"type": "text", "text": output}]


def main():
    root, kind, oid = sys.argv[1:4]
    for line in sys.stdin:
        request = None
        try:
            request = json.loads(line)
            if "id" not in request:
                continue
            method = request.get("method")
            if method == "initialize":
                result = {"protocolVersion": request.get("params", {}).get("protocolVersion", "2024-11-05"),
                          "capabilities": {"tools": {}}, "serverInfo": {"name": "research", "version": "1.0"}}
            elif method == "tools/list":
                result = {"tools": tools(kind)}
            elif method == "tools/call":
                params = request["params"]
                try:
                    result = {"content": call(root, kind, oid, params["name"], params.get("arguments", {}))}
                except Exception as e:
                    result = {"isError": True, "content": [{"type": "text", "text": str(e)}]}
            elif method == "ping":
                result = {}
            else:
                print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32601, "message": "Unknown method"}}), flush=True)
                continue
            print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}), flush=True)
        except Exception as e:
            if isinstance(request, dict) and "id" in request:
                print(json.dumps({"jsonrpc": "2.0", "id": request["id"], "error": {"code": -32603, "message": str(e)}}), flush=True)


if __name__ == "__main__":
    main()
