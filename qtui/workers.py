# -*- coding: utf-8 -*-
"""后台线程：agent 回合、arXiv 下载。

agent 回合里有两件事必须回到主线程做：确认运行代码（弹窗）和写笔记（笔记编辑器
在主线程）。做法是发信号 + threading.Event 等主线程回填结果；用户点「停止」时
也会把 Event 置位，不会卡死在等待上。
"""

import threading

from PyQt6.QtCore import QThread, pyqtSignal

from core import agent, arxiv


class _Box:
    """主线程回填结果用的小盒子。"""

    def __init__(self):
        self.event = threading.Event()
        self.result = None

    def set(self, result):
        self.result = result
        self.event.set()


class AgentWorker(QThread):
    text_delta = pyqtSignal(str)
    thinking_delta = pyqtSignal(str)
    status = pyqtSignal(str)
    history_changed = pyqtSignal()
    tool_started = pyqtSignal(str, object)          # name, input
    tool_done = pyqtSignal(str, str, list)          # name, 结果文字, 图片路径
    approval_needed = pyqtSignal(str, str, object)  # purpose, code, box → (bool, reason)
    note_requested = pyqtSignal(str, str, object)   # title, content, box → str
    finished_turn = pyqtSignal(str)                 # "done" / "cancelled"
    failed = pyqtSignal(str, bool)                  # 错误说明, 用户这句话是否已回滚（要放回输入框）

    def __init__(self, session, user_text, settings, parent=None):
        super().__init__(parent)
        self._session = session
        self._user_text = user_text
        self._settings = dict(settings)
        self._cancel = threading.Event()
        self._pending = None

    # ---- 主线程调用 ----
    def cancel(self):
        self._cancel.set()
        box = self._pending
        if box is not None:
            box.set(None)

    # ---- 线程内 ----
    def _ask_main(self, signal, *args):
        box = _Box()
        self._pending = box
        signal.emit(*args, box)
        while not box.event.wait(0.1):
            if self._cancel.is_set():
                break
        self._pending = None
        return box.result

    def run(self):
        worker = self

        class _Cb(agent.Callbacks):
            def on_text(self, delta):
                worker.text_delta.emit(delta)

            def on_thinking(self, delta):
                worker.thinking_delta.emit(delta)

            def on_status(self, text):
                worker.status.emit(text)

            def on_tool_start(self, name, tool_input):
                worker.tool_started.emit(name, tool_input)

            def on_tool_done(self, name, result_text, images):
                worker.tool_done.emit(name, result_text, list(images))

            def on_history_changed(self):
                worker.history_changed.emit()

            def cancelled(self):
                return worker._cancel.is_set()

            def approve_code(self, purpose, code):
                if worker._settings.get("auto_run"):
                    return True, ""
                res = worker._ask_main(worker.approval_needed, purpose, code)
                if res is None:
                    return False, "用户中止了本轮"
                return res

            def save_note(self, title, content):
                res = worker._ask_main(worker.note_requested, title, content)
                return res or "笔记没有写入（用户中止）"

        n_before = len(self._session.messages)
        try:
            client = agent.make_client(self._settings)
            outcome = agent.run_turn(client, self._session, self._user_text, self._settings, _Cb())
        except agent.AgentError as e:
            self.failed.emit(str(e), len(self._session.messages) == n_before)
            return
        except Exception as e:       # noqa: BLE001 —— 线程里的异常不能漏到 Qt 导致退出
            self.failed.emit(f"{type(e).__name__}: {e}", len(self._session.messages) == n_before)
            return
        self.finished_turn.emit(outcome)


class ImportWorker(QThread):
    progress = pyqtSignal(int, int)
    done = pyqtSignal(str, dict)       # 临时 PDF 路径, meta
    failed = pyqtSignal(str)

    def __init__(self, text, parent=None):
        super().__init__(parent)
        self._text = text
        self._cancel = threading.Event()

    def cancel(self):
        self._cancel.set()

    def run(self):
        try:
            path, meta = arxiv.fetch(self._text,
                                     progress=lambda d, t: self.progress.emit(d, t),
                                     cancelled=self._cancel.is_set)
        except InterruptedError:
            self.failed.emit("")
            return
        except Exception as e:           # noqa: BLE001
            self.failed.emit(arxiv.friendly_error(e))
            return
        self.done.emit(path, meta)


# 窗口关掉时还没跑完的线程：Python 引用一断，sip 会销毁运行中的 QThread
# 直接让进程 abort。先攥住，跑完再放手。
_DETACHED = set()


def detach(worker):
    if worker is None or not worker.isRunning():
        return
    _DETACHED.add(worker)
    worker.finished.connect(lambda: _DETACHED.discard(worker))
