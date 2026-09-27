# -*- coding: utf-8 -*-
"""跑 Claude 写的实验代码：独立子进程，工作目录固定在论文的 experiments/ 下。

这不是安全沙箱——代码以当前用户权限运行，能读写文件、联网。所以默认每次
运行前都要用户确认（设置里可以改成自动运行）。这里负责的是：
- 超时 / 用户中止时杀掉进程
- 输出截断（太长的日志只留头尾，别把上下文塞满）
- matplotlib 用 Agg 后端；plt.show() 自动存成图片
- 运行后新增 / 改动的图片拷到 .artifacts/<run_id>_N.png 固定下来，
  作为 image 块回传给 Claude（它能看到自己画的图），界面上也从这里显示
"""

import os
import shutil
import subprocess
import sys
import time

RUNS_DIR = ".runs"
ARTIFACTS_DIR = ".artifacts"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp")
MAX_OUTPUT_CHARS = 12000
MAX_IMAGES = 4
MAX_IMAGE_BYTES = 3_500_000      # base64 后要低于 5MB 的图片上限

# 运行器：先装好 matplotlib 的钩子，再以 experiment.py 的名义执行用户代码
# （报错行号和代码对得上）。__file__ 指向存档的代码文件。
_RUNNER = r'''
import os, sys
_code_path = sys.argv[1]
os.environ.setdefault("MPLBACKEND", "Agg")
try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as _plt
    _counter = [0]
    def _auto_show(*a, **k):
        for num in _plt.get_fignums():
            _counter[0] += 1
            name = "figure_%d.png" % _counter[0]
            _plt.figure(num).savefig(name, dpi=110, bbox_inches="tight")
            print("[已保存图像 %s]" % name)
        _plt.close("all")
    _plt.show = _auto_show
except Exception:
    pass
sys.argv = [_code_path]
with open(_code_path, encoding="utf-8") as _fh:
    _src = _fh.read()
_g = {"__name__": "__main__", "__file__": _code_path}
exec(compile(_src, "experiment.py", "exec"), _g)
'''


class RunResult:
    def __init__(self, exit_code, output, images, duration, timed_out=False, cancelled=False,
                 code_path=""):
        self.exit_code = exit_code
        self.output = output
        self.images = images            # 固定下来的图片绝对路径列表
        self.duration = duration
        self.timed_out = timed_out
        self.cancelled = cancelled
        self.code_path = code_path

    def summary_text(self):
        if self.cancelled:
            head = "用户中止了运行。"
        elif self.timed_out:
            head = f"运行超时（{self.duration:.0f} 秒）被终止。"
        else:
            head = f"退出码 {self.exit_code}，用时 {self.duration:.1f} 秒。"
        parts = [head]
        parts.append("输出：\n" + (self.output if self.output.strip() else "（没有输出）"))
        if self.images:
            parts.append("生成的图片：" + ", ".join(os.path.basename(p) for p in self.images)
                         + "（附在下面）")
        return "\n".join(parts)


def truncate_output(text, limit=MAX_OUTPUT_CHARS):
    if len(text) <= limit:
        return text
    head = text[: limit * 2 // 3]
    tail = text[-limit // 3:]
    return f"{head}\n\n…（中间省略 {len(text) - len(head) - len(tail)} 个字符）…\n\n{tail}"


def _snapshot_images(workdir):
    snap = {}
    for dirpath, dirnames, filenames in os.walk(workdir):
        # 不看隐藏目录（.artifacts/.runs）和常见的大目录
        dirnames[:] = [d for d in dirnames
                       if not d.startswith(".") and d not in ("__pycache__", "node_modules", "venv")]
        for f in filenames:
            if f.lower().endswith(IMAGE_EXTS):
                p = os.path.join(dirpath, f)
                try:
                    st = os.stat(p)
                except OSError:
                    continue
                snap[p] = (st.st_mtime_ns, st.st_size)
    return snap


def run_python(code, workdir, run_id, python=None, timeout=120, cancelled=None):
    """运行一段代码，返回 RunResult。cancelled() 返回 True 时杀掉进程。"""
    python = python or sys.executable
    runs = os.path.join(workdir, RUNS_DIR)
    os.makedirs(runs, exist_ok=True)
    code_path = os.path.join(runs, f"{run_id}.py")
    with open(code_path, "w", encoding="utf-8") as fh:
        fh.write(code)
    runner_path = os.path.join(runs, "_runner.py")
    with open(runner_path, "w", encoding="utf-8") as fh:
        fh.write(_RUNNER)

    before = _snapshot_images(workdir)
    env = dict(os.environ)
    # 不要彩色回溯（3.13+ 默认开）：ANSI 转义码会原样进到给 Claude 的输出里
    env.update({"MPLBACKEND": "Agg", "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1",
                "PYTHON_COLORS": "0", "NO_COLOR": "1"})
    start = time.monotonic()
    timed_out = was_cancelled = False
    try:
        proc = subprocess.Popen(
            [python, runner_path, code_path], cwd=workdir, env=env,
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            start_new_session=(os.name != "nt"))
    except OSError as e:
        return RunResult(-1, f"无法启动 Python 解释器 {python}: {e}", [], 0.0, code_path=code_path)

    output = ""
    while True:
        try:
            output, _ = proc.communicate(timeout=0.2)
            break
        except subprocess.TimeoutExpired:
            if cancelled and cancelled():
                was_cancelled = True
            elif time.monotonic() - start > timeout:
                timed_out = True
            else:
                continue
            _kill(proc)
            try:
                output, _ = proc.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                output = ""
            break
    duration = time.monotonic() - start

    images = _collect_images(workdir, before, run_id)
    return RunResult(proc.returncode if proc.returncode is not None else -1,
                     truncate_output(output or ""), images, duration,
                     timed_out=timed_out, cancelled=was_cancelled, code_path=code_path)


def _kill(proc):
    try:
        if os.name != "nt":
            import signal
            os.killpg(proc.pid, signal.SIGKILL)   # 连同它起的子进程一起杀
        else:
            proc.kill()
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass


def _collect_images(workdir, before, run_id):
    after = _snapshot_images(workdir)
    changed = [p for p, sig in after.items() if before.get(p) != sig]
    changed.sort(key=lambda p: after[p][0])
    art_dir = os.path.join(workdir, ARTIFACTS_DIR)
    out = []
    for i, src in enumerate(changed[:MAX_IMAGES], 1):
        if os.path.getsize(src) > MAX_IMAGE_BYTES:
            continue
        os.makedirs(art_dir, exist_ok=True)
        ext = os.path.splitext(src)[1].lower()
        dst = os.path.join(art_dir, f"{run_id}_{i}{ext}")
        shutil.copyfile(src, dst)
        out.append(dst)
    return out
