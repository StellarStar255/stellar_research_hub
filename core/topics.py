# -*- coding: utf-8 -*-
"""研究主题：给一个主题，agent 自己找论文、下载、读、写综述。

    topics/<topic_id>/
        meta.json    {"id", "title", "created_at", "papers": [论文 id, ...]}
        report.md    综述报告（agent 写，用户也可以改）
        chats/       对话（和论文的对话格式相同）
"""

import datetime
import json
import os
import re
import shutil
import uuid

from core import paths


def topics_dir():
    return os.path.join(os.path.dirname(paths.LIBRARY_DIR), "topics")


class Topic:
    kind = "topic"

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
    def notes_path(self):
        return os.path.join(self.root, "report.md")

    def read_notes(self):
        try:
            with open(self.notes_path, encoding="utf-8") as fh:
                return fh.read()
        except OSError:
            return ""

    def write_notes(self, text):
        with open(self.notes_path, "w", encoding="utf-8") as fh:
            fh.write(text)

    def confirm_plan(self):
        if self.meta.get("plan_confirmed") is False:
            self.meta["plan_confirmed"] = True
            self.save()

    def add_paper(self, paper_id):
        if paper_id not in self.meta.setdefault("papers", []):
            self.meta["papers"].append(paper_id)
            self.save()

    def save(self):
        tmp = os.path.join(self.root, "meta.json.tmp")
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(self.meta, fh, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(self.root, "meta.json"))


def _valid_id(tid):
    return bool(re.fullmatch(r"[\w-]+", tid or ""))


def list_topics():
    d = topics_dir()
    out = []
    if os.path.isdir(d):
        for name in os.listdir(d):
            t = get(name)
            if t:
                out.append(t)
    out.sort(key=lambda t: t.meta.get("created_at", ""), reverse=True)
    return out


def get(tid):
    if not _valid_id(tid):
        return None
    root = os.path.join(topics_dir(), tid)
    try:
        with open(os.path.join(root, "meta.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
    except (OSError, ValueError):
        return None
    return Topic(root, meta)


def create(title):
    tid = datetime.datetime.now().strftime("t%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4]
    root = os.path.join(topics_dir(), tid)
    os.makedirs(root)
    t = Topic(root, {"id": tid, "title": title.strip()[:80],
                     "created_at": datetime.datetime.now().isoformat(timespec="seconds"), "papers": [],
                     # 新主题先出计划，用户确认后才允许下载（rh import 会检查）
                     "plan_confirmed": False})
    t.save()
    return t


def delete(topic):
    shutil.rmtree(topic.root, ignore_errors=True)
