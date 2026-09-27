import io
import subprocess
import sys
import urllib.error

from core import paths, scholar, topics


def _http_error(code):
    return urllib.error.HTTPError("u", code, "x", {}, io.BytesIO(b""))


def test_lookup_parses_caches_and_strips_versions(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CONFIG_DIR", str(tmp_path))
    calls = []

    def fake_post(ids):
        calls.append(ids)
        return [{"citationCount": 642, "influentialCitationCount": 68, "venue": "ICLR", "year": 2023}, None]
    monkeypatch.setattr(scholar, "_post", fake_post)
    out, err = scholar.lookup(["2309.00071v3", "9999.99999"])
    assert err is None and calls == [["ARXIV:2309.00071", "ARXIV:9999.99999"]]
    assert out["2309.00071"]["citations"] == 642 and "9999.99999" not in out
    # 第二次走缓存，不再请求
    out2, _ = scholar.lookup(["2309.00071"])
    assert out2["2309.00071"]["venue"] == "ICLR" and len(calls) == 1


def test_lookup_retries_on_429_then_reports(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "CONFIG_DIR", str(tmp_path))
    n = {"i": 0}

    def always_429(ids):
        n["i"] += 1
        raise _http_error(429)
    monkeypatch.setattr(scholar, "_post", always_429)
    waits = []
    out, err = scholar.lookup(["2309.00071"], sleep=waits.append)
    assert out == {} and "限流" in err
    assert n["i"] == 1 + len(scholar.RETRY_WAITS) and waits == list(scholar.RETRY_WAITS)


def test_describe():
    assert scholar.describe(None).startswith("引用数：")
    s = scholar.describe({"citations": 100, "influential": 5, "venue": "NeurIPS", "year": 2020})
    assert s.startswith("引用 100（高影响 5，约") and s.endswith("NeurIPS")


def test_import_refused_until_plan_confirmed(tmp_path, monkeypatch):
    home = tmp_path / "home"
    monkeypatch.setattr(paths, "LIBRARY_DIR", str(home / "library"))
    t = topics.create("测试主题")
    assert t.meta["plan_confirmed"] is False
    env = {"RESEARCH_HUB_HOME": str(home), "PATH": "/usr/bin:/bin"}
    r = subprocess.run([sys.executable, "rh.py", "import", "1706.03762", "--topic", t.id],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 1 and "还没确认" in r.stdout
    t.confirm_plan()
    assert topics.get(t.id).meta["plan_confirmed"] is True
