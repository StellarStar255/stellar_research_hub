# -*- coding: utf-8 -*-
"""从 arXiv（或任意 PDF 链接）下载论文。只用标准库，HTTPS 证书优先用 certifi。

支持的输入：
    2502.16161 / 2502.16161v2 / arXiv:2502.16161
    https://arxiv.org/abs/2502.16161 、 https://arxiv.org/pdf/2502.16161v1 、 alphaxiv / hf papers 链接
    hep-th/9901001 （旧式编号）
    任意直接指向 PDF 的 http(s) 链接
"""

import os
import re
import ssl
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

API_URL = "https://export.arxiv.org/api/query?id_list={}"
PDF_URL = "https://arxiv.org/pdf/{}"
USER_AGENT = "ResearchHub/0.1 (paper study tool)"
TIMEOUT = 60
MAX_PDF_BYTES = 60 * 1024 * 1024

_NEW_ID = r"\d{4}\.\d{4,5}(?:v\d+)?"
_OLD_ID = r"[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?"
_ID_RE = re.compile(rf"(?<![\d.])({_NEW_ID}|{_OLD_ID})(?![\d])")
_ATOM = "{http://www.w3.org/2005/Atom}"


def parse_arxiv_id(text):
    """从用户输入里抠出 arXiv 编号；不是 arXiv 的输入返回 None。"""
    text = (text or "").strip()
    if not text:
        return None
    if text.lower().startswith(("http://", "https://")):
        host = urllib.parse.urlparse(text).netloc.lower()
        if not any(h in host for h in ("arxiv.org", "alphaxiv.org", "huggingface.co")):
            return None
        path = urllib.parse.urlparse(text).path
        if path.endswith(".pdf"):
            path = path[:-4]
        m = _ID_RE.search(path)
        return m.group(1) if m else None
    text = re.sub(r"^arxiv:\s*", "", text, flags=re.IGNORECASE)
    m = _ID_RE.fullmatch(text)
    return m.group(1) if m else None


def _ssl_context():
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:            # noqa: BLE001 —— 没装 certifi 就用系统证书
        return ssl.create_default_context()


def _open(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    return urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_context())


def friendly_error(exc):
    """网络异常 → 人话。"""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 404:
            return "找不到这篇论文（404），检查一下编号或链接"
        if exc.code == 429:
            return "arXiv 限流了（429），过一会儿再试"
        return f"服务器返回 HTTP {exc.code}"
    if isinstance(exc, urllib.error.URLError):
        reason = exc.reason
        if isinstance(reason, ssl.SSLError):
            return f"HTTPS 证书校验失败（可能是代理拦截）: {reason}"
        if "nodename" in str(reason) or "Name or service" in str(reason):
            return "域名解析失败，检查网络或代理"
        return f"网络连接失败: {reason}"
    if isinstance(exc, TimeoutError):
        return "连接超时，检查网络或代理"
    return str(exc)


def parse_atom(xml_bytes):
    """arXiv API 的 Atom 响应 → 第一条的 meta dict；没有条目返回 None。"""
    entries = parse_feed(xml_bytes)
    return entries[0] if entries else None


def parse_feed(xml_bytes):
    """arXiv API 的 Atom 响应 → meta dict 列表。"""
    root = ET.fromstring(xml_bytes)
    out = []
    for entry in root.findall(_ATOM + "entry"):
        meta = _parse_entry(entry)
        if meta:
            out.append(meta)
    return out


def _parse_entry(entry):
    entry_id = (entry.findtext(_ATOM + "id") or "").strip()
    title = " ".join((entry.findtext(_ATOM + "title") or "").split())
    if not entry_id or not title or "/api/errors" in entry_id:
        return None
    m = re.search(r"/abs/(.+)$", entry_id)
    return {
        "arxiv_id": m.group(1) if m else entry_id,
        "title": title,
        "authors": [" ".join((a.findtext(_ATOM + "name") or "").split())
                    for a in entry.findall(_ATOM + "author")],
        "abstract": " ".join((entry.findtext(_ATOM + "summary") or "").split()),
        "published": (entry.findtext(_ATOM + "published") or "")[:10],
        "url": entry_id,
    }


SEARCH_URL = ("https://export.arxiv.org/api/query?search_query={}&start=0&max_results={}"
              "&sortBy={}&sortOrder=descending")


def search(query, n=10, sort="relevance"):
    """在 arXiv 搜论文。query 按空格拆成词，要求都出现（标题/摘要等任意字段）。
    sort: relevance / submittedDate。"""
    words = [w for w in re.split(r"\s+", query.strip()) if w]
    if not words:
        return []
    q = "+AND+".join("all:" + urllib.parse.quote(w) for w in words)
    sort_by = "submittedDate" if sort in ("date", "submittedDate", "new") else "relevance"
    with _open(SEARCH_URL.format(q, max(1, min(50, n)), sort_by)) as resp:
        return parse_feed(resp.read())


def fetch_metadata(arxiv_id):
    with _open(API_URL.format(urllib.parse.quote(arxiv_id))) as resp:
        meta = parse_atom(resp.read())
    if meta is None:
        raise ValueError(f"arXiv 上没有找到 {arxiv_id}")
    return meta


def download_pdf(url, progress=None, cancelled=None):
    """下载到临时文件，返回路径（调用方负责删除）。

    progress(done_bytes, total_bytes_or_0)；cancelled() 返回 True 时中止。
    """
    fd, path = tempfile.mkstemp(suffix=".pdf", prefix="rh_")
    try:
        with _open(url) as resp, os.fdopen(fd, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            first = True
            while True:
                if cancelled and cancelled():
                    raise InterruptedError("已取消")
                chunk = resp.read(1 << 16)
                if not chunk:
                    break
                if first and not chunk.startswith(b"%PDF-"):
                    raise ValueError("链接返回的不是 PDF（可能需要登录或是网页）")
                first = False
                done += len(chunk)
                if done > MAX_PDF_BYTES:
                    raise ValueError("PDF 超过 60MB，太大了")
                out.write(chunk)
                if progress:
                    progress(done, total)
        if first:
            raise ValueError("下载到的文件是空的")
        return path
    except BaseException:
        try:
            os.remove(path)
        except OSError:
            pass
        raise


def fetch(text, progress=None, cancelled=None):
    """输入 arXiv 编号/链接/PDF 链接 → (临时 PDF 路径, meta)。"""
    arxiv_id = parse_arxiv_id(text)
    if arxiv_id:
        meta = fetch_metadata(arxiv_id)
        # 用 API 返回的带版本号的编号下载，保证 PDF 与元数据一致
        pdf = download_pdf(PDF_URL.format(meta["arxiv_id"]), progress, cancelled)
        return pdf, meta
    text = text.strip()
    if text.lower().startswith(("http://", "https://")):
        pdf = download_pdf(text, progress, cancelled)
        name = os.path.basename(urllib.parse.urlparse(text).path) or "paper"
        return pdf, {"title": os.path.splitext(urllib.parse.unquote(name))[0], "url": text}
    raise ValueError("认不出来：请输入 arXiv 编号（如 2502.16161）、arXiv 链接或 PDF 链接")
