# -*- coding: utf-8 -*-
"""论文库：每篇论文一个目录，对话 / 笔记 / 实验都放在里面，方便整个拷走。

    library/<paper_id>/
        meta.json          标题、作者、摘要、arXiv 号……
        paper.pdf          论文原件（导入后不再改动：对话里的 PDF 必须逐字节不变，
                           否则提示缓存失效、历史里的 thinking 块也会被服务端拒绝）
        notes.md           学习笔记（用户手写 + Claude 通过 save_note 追加）
        sessions/<sid>.json  一次对话（完整的 API 消息历史）
        experiments/       Claude 跑实验代码的工作目录
            .runs/         每次运行的代码存档
            .artifacts/    每次运行产生的图（按 tool_use_id 固定下来，历史回放时字节不变）
"""

import datetime
import hashlib
import json
import os
import re
import shutil
import uuid

from core import paths

META_FILE = "meta.json"
PDF_FILE = "paper.pdf"
NOTES_FILE = "notes.md"
SESSIONS_DIR = "sessions"
EXPERIMENTS_DIR = "experiments"


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _write_json(path, data):
    """先写临时文件再替换：写到一半崩溃也不会留下半个 JSON 把历史弄丢。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def _slug(text, max_len=40):
    text = re.sub(r"[^\w\-.]+", "-", text or "", flags=re.UNICODE).strip("-.")
    return text[:max_len] or "paper"


def _file_sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------------------
# 论文
# ---------------------------------------------------------------------------

class Paper:
    kind = "paper"

    def __init__(self, root, meta):
        self.root = root
        self.meta = meta

    @property
    def id(self):
        return self.meta["id"]

    @property
    def title(self):
        return self.meta.get("title") or self.id

    @property
    def pdf_path(self):
        return os.path.join(self.root, PDF_FILE)

    @property
    def notes_path(self):
        return os.path.join(self.root, NOTES_FILE)

    @property
    def sessions_dir(self):
        return os.path.join(self.root, SESSIONS_DIR)

    @property
    def experiments_dir(self):
        d = os.path.join(self.root, EXPERIMENTS_DIR)
        os.makedirs(d, exist_ok=True)
        return d

    def display_subtitle(self):
        bits = []
        if self.meta.get("arxiv_id"):
            bits.append("arXiv:" + self.meta["arxiv_id"])
        if self.meta.get("published"):
            bits.append(self.meta["published"][:4])
        authors = self.meta.get("authors") or []
        if authors:
            bits.append(authors[0] + (" 等" if len(authors) > 1 else ""))
        return " · ".join(bits)

    def save_meta(self):
        _write_json(os.path.join(self.root, META_FILE), self.meta)

    def rename(self, title):
        # 只改显示名；已有对话在创建时存了一份标题快照，不受影响
        self.meta["title"] = title.strip() or self.meta.get("title")
        self.save_meta()

    # ---- 笔记 ----
    def read_notes(self):
        try:
            with open(self.notes_path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def write_notes(self, text):
        with open(self.notes_path, "w", encoding="utf-8") as fh:
            fh.write(text)

    # ---- 对话 ----
    def list_sessions(self):
        """按最近更新倒序。"""
        out = []
        if not os.path.isdir(self.sessions_dir):
            return out
        for name in os.listdir(self.sessions_dir):
            if not name.endswith(".json"):
                continue
            data = _read_json(os.path.join(self.sessions_dir, name))
            if isinstance(data, dict) and data.get("id"):
                out.append(Session(self, data))
        out.sort(key=lambda s: s.data.get("updated_at", ""), reverse=True)
        return out

    def new_session(self, style, background="", web_tools=False):
        """style / background / web_tools / 标题在创建时定下来，整场对话不变——
        系统提示词和工具列表中途一改，前面的提示缓存和 thinking 块就全作废了。"""
        sid = datetime.datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
        data = {
            "id": sid,
            "title": "",
            "created_at": _now(),
            "updated_at": _now(),
            "style": style,
            "background": background,
            "web_tools": bool(web_tools),
            "paper_title": self.title,
            "messages": [],
            "usage": {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
        }
        return Session(self, data)


class Session:
    def __init__(self, paper, data):
        self.paper = paper
        self.data = data

    @property
    def id(self):
        return self.data["id"]

    @property
    def messages(self):
        return self.data["messages"]

    @property
    def path(self):
        return os.path.join(self.paper.sessions_dir, self.id + ".json")

    def display_title(self):
        if self.data.get("title"):
            return self.data["title"]
        # 用第一句用户提问当标题
        for m in self.messages:
            if m.get("role") != "user" or not isinstance(m.get("content"), list):
                continue
            for b in m["content"]:
                if b.get("type") == "text" and b.get("text", "").strip():
                    t = b["text"].strip().splitlines()[0]
                    return t[:30] + ("…" if len(t) > 30 else "")
        return "新对话 " + self.data.get("created_at", "")[5:16].replace("T", " ")

    def add_usage(self, usage):
        u = self.data.setdefault("usage", {})
        for k in ("input", "output", "cache_read", "cache_write"):
            u[k] = u.get(k, 0) + int(usage.get(k, 0) or 0)

    def save(self):
        self.data["updated_at"] = _now()
        _write_json(self.path, self.data)

    def delete(self):
        try:
            os.remove(self.path)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# 库
# ---------------------------------------------------------------------------

class Library:
    def __init__(self, root=None):
        self.root = root or paths.LIBRARY_DIR
        os.makedirs(self.root, exist_ok=True)

    def list_papers(self):
        """按添加时间倒序。"""
        out = []
        for name in os.listdir(self.root):
            d = os.path.join(self.root, name)
            meta = _read_json(os.path.join(d, META_FILE))
            if isinstance(meta, dict) and meta.get("id") and os.path.exists(os.path.join(d, PDF_FILE)):
                out.append(Paper(d, meta))
        out.sort(key=lambda p: p.meta.get("added_at", ""), reverse=True)
        return out

    def get(self, paper_id):
        d = os.path.join(self.root, paper_id)
        meta = _read_json(os.path.join(d, META_FILE))
        if isinstance(meta, dict) and meta.get("id"):
            return Paper(d, meta)
        return None

    def find_duplicate(self, pdf_path=None, arxiv_id=None):
        """同一篇论文（arXiv 号相同，或 PDF 内容相同）已在库里时返回它。"""
        sha = _file_sha1(pdf_path) if pdf_path else None
        base_id = strip_arxiv_version(arxiv_id) if arxiv_id else None
        for p in self.list_papers():
            if base_id and strip_arxiv_version(p.meta.get("arxiv_id") or "") == base_id:
                return p
            if sha and p.meta.get("sha1") == sha:
                return p
        return None

    def add_pdf(self, pdf_path, meta=None):
        """把 PDF 拷进库，返回 Paper。meta 可带 title/authors/abstract/arxiv_id 等。"""
        meta = dict(meta or {})
        if not os.path.isfile(pdf_path):
            raise FileNotFoundError(pdf_path)
        with open(pdf_path, "rb") as fh:
            if fh.read(5) != b"%PDF-":
                raise ValueError("不是 PDF 文件: " + os.path.basename(pdf_path))
        title = meta.get("title") or os.path.splitext(os.path.basename(pdf_path))[0]
        if meta.get("arxiv_id"):
            base = "arxiv-" + _slug(meta["arxiv_id"].replace("/", "_"))
        else:
            base = _slug(title)
        pid = base
        n = 2
        while os.path.exists(os.path.join(self.root, pid)):
            pid = f"{base}-{n}"
            n += 1
        root = os.path.join(self.root, pid)
        os.makedirs(root)
        try:
            shutil.copyfile(pdf_path, os.path.join(root, PDF_FILE))
            meta.update({
                "id": pid,
                "title": title,
                "added_at": _now(),
                "source_file": os.path.abspath(pdf_path),
                "sha1": _file_sha1(os.path.join(root, PDF_FILE)),
            })
            paper = Paper(root, meta)
            paper.save_meta()
        except Exception:
            shutil.rmtree(root, ignore_errors=True)
            raise
        return paper

    def delete(self, paper):
        shutil.rmtree(paper.root, ignore_errors=True)


def strip_arxiv_version(arxiv_id):
    return re.sub(r"v\d+$", "", arxiv_id or "")
