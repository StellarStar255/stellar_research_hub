import os
import time

from core import sandbox


def test_output_and_exit_code(tmp_path):
    r = sandbox.run_python("import sys\nprint('hi')\nsys.exit(3)", str(tmp_path), "r1")
    assert r.exit_code == 3 and "hi" in r.output
    assert os.path.exists(os.path.join(tmp_path, ".runs", "r1.py"))


def test_traceback_uses_experiment_filename(tmp_path):
    r = sandbox.run_python("x = 1\nraise ValueError('boom')", str(tmp_path), "r2")
    assert r.exit_code != 0
    assert 'File "experiment.py", line 2' in r.output and "boom" in r.output
    assert "\x1b[" not in r.output


def test_timeout_kills(tmp_path):
    t0 = time.monotonic()
    r = sandbox.run_python("import time\ntime.sleep(30)", str(tmp_path), "r3", timeout=1)
    assert r.timed_out and time.monotonic() - t0 < 10
    assert "超时" in r.summary_text()


def test_cancel_kills(tmp_path):
    start = time.monotonic()
    r = sandbox.run_python("import time\ntime.sleep(30)", str(tmp_path), "r4",
                           cancelled=lambda: time.monotonic() - start > 0.5)
    assert r.cancelled and "中止" in r.summary_text()


def test_images_collected_and_frozen(tmp_path):
    code = ("import matplotlib.pyplot as plt\n"
            "plt.plot([0, 1]); plt.savefig('a.png')\n"
            "plt.figure(); plt.plot([1, 0]); plt.show()\n")
    r = sandbox.run_python(code, str(tmp_path), "r5")
    assert r.exit_code == 0, r.output
    # a.png + plt.show() 把两张还开着的图各存一张
    names = sorted(os.path.basename(p) for p in r.images)
    assert names == ["r5_1.png", "r5_2.png", "r5_3.png"]
    assert "\x1b[" not in r.output
    # 再跑一次覆盖 a.png：新图另存，旧的 artifact 不变
    before = open(r.images[0], "rb").read()
    r2 = sandbox.run_python("import matplotlib.pyplot as plt\nplt.plot([5, 5]); plt.savefig('a.png')",
                            str(tmp_path), "r6")
    assert [os.path.basename(p) for p in r2.images] == ["r6_1.png"]
    assert open(r.images[0], "rb").read() == before


def test_unchanged_images_not_reported(tmp_path):
    (tmp_path / "old.png").write_bytes(b"\x89PNG fake")
    r = sandbox.run_python("print(1)", str(tmp_path), "r7")
    assert r.images == []


def test_truncate_output():
    s = "x" * 50000
    out = sandbox.truncate_output(s, 1000)
    assert len(out) < 1200 and "省略" in out


def test_bad_interpreter(tmp_path):
    r = sandbox.run_python("print(1)", str(tmp_path), "r8", python="/nonexistent/python")
    assert r.exit_code == -1 and "无法启动" in r.output
