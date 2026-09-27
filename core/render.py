# -*- coding: utf-8 -*-
"""对话历史 → Markdown（给 QTextBrowser.setMarkdown 显示）。

- 引用显示成可点击的页码链接 [p.3](page:3)，点了跳到阅读器对应页
- 实验代码 / 输出 / 图片内嵌显示
- thinking 摘要默认折叠成一行提示（设置里可以展开）
"""

import os
import urllib.parse

MAX_SHOWN_OUTPUT = 4000


def _fence(text, lang=""):
    """挑一个不会和内容冲突的围栏长度。"""
    ticks = "```"
    while ticks in text:
        ticks += "`"
    return f"{ticks}{lang}\n{text.rstrip()}\n{ticks}"


def _quote(text):
    return "\n".join("> " + line if line.strip() else ">" for line in text.strip().splitlines())


def _file_url(path):
    return "file://" + urllib.parse.quote(os.path.abspath(path))


def citation_suffix(citations):
    """一个文本块的引用 → ' [p.3](page:3) [p.5–6](page:5)'（按页去重）。"""
    seen = []
    for c in citations or []:
        if c.get("type") != "page_location":
            continue
        a = c.get("start_page_number")
        b = c.get("end_page_number")
        if a is None:
            continue
        # end_page_number 是开区间
        last = (b - 1) if isinstance(b, int) and b - 1 > a else a
        key = (a, last)
        if key not in seen:
            seen.append(key)
    out = []
    for a, last in seen:
        label = f"p.{a}" if last == a else f"p.{a}–{last}"
        out.append(f"[{label}](page:{a})")
    return (" " + " ".join(out)) if out else ""


def _tool_result_text(block):
    content = block.get("content")
    if isinstance(content, str):
        return content, []
    texts, images = [], []
    for c in content or []:
        if c.get("type") == "text":
            texts.append(c.get("text", ""))
        elif c.get("type") == "local_image":
            images.append(c.get("path", ""))
    return "\n".join(texts), images


def render_user(message, experiments_dir):
    """返回 (用户文字的 Markdown, 工具结果的 Markdown)；只有工具结果时前者为空。"""
    content = message.get("content")
    if isinstance(content, str):
        return content, ""
    texts = [b.get("text", "") for b in content if b.get("type") == "text"]
    results = []
    for b in content:
        if b.get("type") != "tool_result":
            continue
        text, images = _tool_result_text(b)
        if len(text) > MAX_SHOWN_OUTPUT:
            text = text[:MAX_SHOWN_OUTPUT] + "\n…（界面上只显示前一部分，完整输出已交给 Claude）"
        label = "⚠️ 结果" if b.get("is_error") else "📤 结果"
        results.append(f"{label}\n\n" + _fence(text))
        for rel in images:
            p = os.path.join(experiments_dir, rel)
            if os.path.exists(p):
                results.append(f"![{os.path.basename(rel)}]({_file_url(p)})")
    return "\n\n".join(texts), "\n\n".join(results)


def render_assistant(message, show_thinking=False):
    content = message.get("content")
    if isinstance(content, str):
        return content
    parts = []
    text_run = []

    def flush():
        if text_run:
            parts.append("".join(text_run))
            text_run.clear()

    for b in content:
        t = b.get("type")
        if t == "text":
            text_run.append(b.get("text", "") + citation_suffix(b.get("citations")))
        elif t == "thinking":
            flush()
            summary = (b.get("thinking") or "").strip()
            if summary and show_thinking:
                parts.append("💭 *思考摘要*\n\n" + _quote(summary))
        elif t == "tool_use":
            flush()
            args = b.get("input") or {}
            if b.get("name") == "run_python":
                purpose = args.get("purpose") or ""
                parts.append(f"🧪 **运行实验**{('：' + purpose) if purpose else ''}\n\n"
                             + _fence(args.get("code", ""), "python"))
            elif b.get("name") == "save_note":
                parts.append(f"📝 **写入笔记**：{args.get('title') or ''}")
            else:
                parts.append(f"🔧 {b.get('name')}")
        elif t == "server_tool_use":
            flush()
            args = b.get("input") or {}
            if b.get("name") == "web_search":
                parts.append(f"🔎 *搜索：{args.get('query', '')}*")
            elif b.get("name") == "web_fetch":
                parts.append(f"🌐 *读取：{args.get('url', '')}*")
        elif t == "web_search_tool_result":
            flush()
            res = b.get("content")
            if isinstance(res, list):
                links = [f"[{r.get('title') or r.get('url')}]({r.get('url')})"
                         for r in res[:5] if r.get("type") == "web_search_result"]
                if links:
                    parts.append("  ·  ".join(links))
    flush()
    return "\n\n".join(p for p in parts if p.strip())


USER_HEADER = "#### 🧑 你"
CLAUDE_HEADER = "#### 🤖 Claude"


def render_history(messages, experiments_dir, show_thinking=False):
    """一次提问 = 「你」一段 + 「Claude」一段；中间的工具来回并进 Claude 那段。"""
    sections = []        # [(who, [md, ...])]

    def add(who, md):
        if not md.strip():
            return
        if not sections or sections[-1][0] != who:
            sections.append((who, []))
        sections[-1][1].append(md)

    for m in messages:
        if m.get("role") == "user":
            text_md, results_md = render_user(m, experiments_dir)
            add("user", text_md)
            add("claude", results_md)
        elif m.get("role") == "assistant":
            add("claude", render_assistant(m, show_thinking))
    out = []
    for who, mds in sections:
        header = USER_HEADER if who == "user" else CLAUDE_HEADER
        block = header + "\n\n" + "\n\n".join(mds)
        out.append(("---\n\n" if who == "user" and out else "") + block)
    return "\n\n".join(out)
