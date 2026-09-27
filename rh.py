#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""主题研究 agent 用的命令行（agent 只被允许运行这两个子命令）：

    ./rh search "<关键词>" [-n 10] [--new]     在 arXiv 搜论文（附 Semantic Scholar 引用数 / 会议）
    ./rh cite <arXiv 编号> [<arXiv 编号> ...]   查已知论文的引用数 / 会议
    ./rh import <arXiv 编号或 PDF 链接> [--topic <主题 id>]   下载进论文库
                                                 （主题的调研计划用户确认前拒绝下载）
"""

import argparse
import os
import sys
import textwrap

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core import arxiv, scholar, topics  # noqa: E402
from core.library import Library  # noqa: E402


def cmd_search(args):
    try:
        results = arxiv.search(args.query, args.n, "date" if args.new else "relevance")
    except Exception as e:           # noqa: BLE001
        print("ERROR 搜索失败:", arxiv.friendly_error(e))
        return 1
    lib = Library()
    if not results:
        print("没有结果，换个关键词（用英文术语效果更好）")
        return 0
    cites, err = scholar.lookup([m["arxiv_id"] for m in results])
    if err:
        print(f"（引用数查询失败：{err}。可以稍后用 rh cite 重查）")
    for i, m in enumerate(results, 1):
        have = lib.find_duplicate(arxiv_id=m["arxiv_id"])
        authors = ", ".join(m["authors"][:3]) + (" 等" if len(m["authors"]) > 3 else "")
        print(f"[{i}] {m['arxiv_id']} · {m['published'][:7]} · {m['title']}"
              + ("  （已在库里）" if have else ""))
        print(f"    作者: {authors}")
        if not err or scholar_key(m) in cites:
            print("    " + scholar.describe(cites.get(scholar_key(m)), m["published"]))
        print(textwrap.indent(textwrap.shorten(m["abstract"], 420, placeholder=" …"), "    "))
    return 0


def scholar_key(meta):
    from core.library import strip_arxiv_version
    return strip_arxiv_version(meta["arxiv_id"])


def cmd_cite(args):
    ids = [arxiv.parse_arxiv_id(x) or x for x in args.ids]
    cites, err = scholar.lookup(ids)
    if err:
        print(f"（部分引用数查询失败：{err}）")
    from core.library import strip_arxiv_version
    for i in ids:
        rec = cites.get(strip_arxiv_version(i))
        if rec is None and err:
            print(f"{i}: 查不到")
        else:
            print(f"{i}: {scholar.describe(rec)}")
    return 0


def cmd_import(args):
    lib = Library()
    topic = topics.get(args.topic) if args.topic else None
    if topic and topic.meta.get("plan_confirmed") is False:
        print("ERROR 用户还没确认调研计划。先列出子方向和候选论文清单（附引用数和入选理由），"
              "单独一行写 [[CONFIRM]] 等用户确认，确认后才能下载。")
        return 1
    arxiv_id = arxiv.parse_arxiv_id(args.ref)
    paper = lib.find_duplicate(arxiv_id=arxiv_id) if arxiv_id else None
    if paper is None:
        try:
            path, meta = arxiv.fetch(args.ref)
        except Exception as e:       # noqa: BLE001
            print("ERROR 下载失败:", arxiv.friendly_error(e))
            return 1
        try:
            paper = lib.find_duplicate(pdf_path=path, arxiv_id=meta.get("arxiv_id")) or lib.add_pdf(path, meta)
        finally:
            os.remove(path)
    if topic:
        topic.add_paper(paper.id)
    print(f"PAPER {paper.id}")
    print(f"标题: {paper.title}")
    print(f"PDF: {paper.pdf_path}")
    return 0


def main():
    ap = argparse.ArgumentParser(prog="rh")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("search")
    s.add_argument("query")
    s.add_argument("-n", type=int, default=10)
    s.add_argument("--new", action="store_true", help="按提交时间排序（找最新进展）")
    s.set_defaults(fn=cmd_search)
    c = sub.add_parser("cite")
    c.add_argument("ids", nargs="+")
    c.set_defaults(fn=cmd_cite)
    i = sub.add_parser("import")
    i.add_argument("ref")
    i.add_argument("--topic")
    i.set_defaults(fn=cmd_import)
    args = ap.parse_args()
    sys.exit(args.fn(args))


if __name__ == "__main__":
    main()
