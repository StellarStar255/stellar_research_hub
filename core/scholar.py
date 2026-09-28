# -*- coding: utf-8 -*-
"""论文影响力：从 Semantic Scholar 查引用数、高影响引用数、发表会议/期刊。

- 按 arXiv 编号批量查（一次请求），结果缓存 7 天（~/.stellar_research_hub/scholar_cache.json）
- 不注册的免费额度是所有匿名用户共用的，经常 429：自动重试几次，还不行就返回空，
  调用方如实显示「查不到」，绝不编数字
- 设了环境变量 SEMANTIC_SCHOLAR_API_KEY（免费申请）就走独享额度，稳定得多

（OpenAlex 试过：arXiv 版本和正式发表版本是两条记录，引用数严重偏低，不用。）
"""

import json
import os
import time
import urllib.error
import urllib.request

from core import arxiv, paths
from core.library import strip_arxiv_version

API = ("https://api.semanticscholar.org/graph/v1/paper/batch"
       "?fields=title,year,venue,citationCount,influentialCitationCount")
CACHE_TTL = 7 * 24 * 3600
RETRY_WAITS = (3,)
# 被限流后这段时间内不再请求，直接报「查不到」：否则每次搜索都要白等重试，规划阶段十几次搜索会慢好几分钟
THROTTLE_COOLDOWN = 90


def _throttle_path():
    return os.path.join(paths.CONFIG_DIR, "scholar_throttled")


def _recently_throttled():
    try:
        return time.time() - os.path.getmtime(_throttle_path()) < THROTTLE_COOLDOWN
    except OSError:
        return False


def _mark_throttled():
    try:
        os.makedirs(paths.CONFIG_DIR, exist_ok=True)
        with open(_throttle_path(), "w") as fh:
            fh.write(str(time.time()))
    except OSError:
        pass


def _cache_path():
    return os.path.join(paths.CONFIG_DIR, "scholar_cache.json")


def _load_cache():
    try:
        with open(_cache_path(), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {}


def _save_cache(cache):
    try:
        os.makedirs(os.path.dirname(_cache_path()), exist_ok=True)
        tmp = _cache_path() + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(cache, fh, ensure_ascii=False)
        os.replace(tmp, _cache_path())
    except OSError:
        pass


def _post(ids):
    req = urllib.request.Request(API, data=json.dumps({"ids": ids}).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "User-Agent": arxiv.USER_AGENT})
    key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if key:
        req.add_header("x-api-key", key)
    with urllib.request.urlopen(req, timeout=20, context=arxiv._ssl_context()) as resp:
        return json.load(resp)


def lookup(arxiv_ids, sleep=time.sleep):
    """{不带版本号的 arXiv 编号: {"citations", "influential", "venue", "year"}}。
    查不到的编号不在结果里；第二个返回值是失败原因（成功时为 None）。"""
    bases = list(dict.fromkeys(strip_arxiv_version(i) for i in arxiv_ids if i))
    cache = _load_cache()
    now = time.time()
    out = {b: cache[b]["data"] for b in bases if b in cache and now - cache[b].get("at", 0) < CACHE_TTL}
    missing = [b for b in bases if b not in out]
    if not missing:
        return out, None
    if _recently_throttled() and not os.environ.get("SEMANTIC_SCHOLAR_API_KEY"):
        return out, "Semantic Scholar 限流（免费额度大家共用），稍后再查"
    error = None
    data = None
    for wait in (0,) + RETRY_WAITS:
        if wait:
            sleep(wait)
        try:
            data = _post(["ARXIV:" + b for b in missing])
            error = None
            break
        except urllib.error.HTTPError as e:
            error = "Semantic Scholar 限流（免费额度大家共用），稍后再查" if e.code == 429 else f"HTTP {e.code}"
            if e.code != 429:
                break
        except Exception as e:           # noqa: BLE001 —— 网络问题都算查不到
            error = arxiv.friendly_error(e)
            break
    if data is None and error and "限流" in error:
        _mark_throttled()
    if data is not None:
        for b, item in zip(missing, data):
            if not item:
                continue
            rec = {"citations": item.get("citationCount"), "influential": item.get("influentialCitationCount"),
                   "venue": item.get("venue") or "", "year": item.get("year")}
            out[b] = rec
            cache[b] = {"at": now, "data": rec}
        _save_cache(cache)
    return out, error


def describe(rec, published=""):
    """一条记录 → 「引用 612（高影响 80，约 205/年）· ICLR」。"""
    if not rec or rec.get("citations") is None:
        return "引用数：Semantic Scholar 没有收录"
    parts = [f"引用 {rec['citations']}"]
    extra = []
    if rec.get("influential"):
        extra.append(f"高影响 {rec['influential']}")
    year = rec.get("year") or (int(published[:4]) if published[:4].isdigit() else None)
    if year:
        age = max(0.5, time.localtime().tm_year - year + 0.5)
        extra.append(f"约 {rec['citations'] / age:.0f}/年")
    if extra:
        parts[0] += "（" + "，".join(extra) + "）"
    if rec.get("venue"):
        parts.append(rec["venue"])
    return " · ".join(parts)
