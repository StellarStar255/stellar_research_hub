#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Research Hub 入口：和 Claude 一问一答地读论文、做实验。

用法:
    python3 stellar_research_hub.py [论文.pdf]
"""

import argparse
import datetime
import os
import sys
import traceback

# 环境变量可能指向其他 Python 安装的 Qt 插件（版本不匹配会导致启动失败），
# 强制使用当前 PyQt6 自带的插件目录。打包后（PyInstaller）插件已内置，跳过。
if not getattr(sys, "frozen", False):
    import PyQt6
    _plugin_root = os.path.join(os.path.dirname(PyQt6.__file__), "Qt6", "plugins")
    os.environ["QT_PLUGIN_PATH"] = _plugin_root
    os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = os.path.join(_plugin_root, "platforms")

from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication

from core.paths import CRASH_LOG_PATH
from qtui.main_window import MainWindow
from version import __version__, APP_NAME

_BASE_DIR = (getattr(sys, "_MEIPASS", None)
             or os.path.dirname(os.path.abspath(__file__)))
ICON_PATH = os.path.join(_BASE_DIR, "assets", "research_hub_icon.png")


def _install_excepthook():
    """槽函数里未捕获的异常默认会让 PyQt6 调 qFatal() 直接终止进程。
    改为：写日志 + 弹窗，程序继续运行。"""
    from PyQt6.QtWidgets import QMessageBox

    state = {"showing": False}

    def hook(exc_type, exc_value, exc_tb):
        text = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        try:
            os.makedirs(os.path.dirname(CRASH_LOG_PATH), exist_ok=True)
            with open(CRASH_LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(f"==== {datetime.datetime.now():%Y-%m-%d %H:%M:%S} "
                         f"v{__version__}\n{text}\n")
        except OSError:
            pass
        try:
            sys.__stderr__ and sys.__stderr__.write(text)
        except Exception:
            pass
        if state["showing"] or QApplication.instance() is None:
            return
        state["showing"] = True
        try:
            tail = "\n".join(text.strip().splitlines()[-6:])
            QMessageBox.critical(
                None, "程序内部错误",
                f"发生了未预期的错误，操作可能未完成。详细日志：{CRASH_LOG_PATH}\n\n{tail}")
        except Exception:
            pass
        finally:
            state["showing"] = False

    sys.excepthook = hook


def main():
    parser = argparse.ArgumentParser(description=f"{APP_NAME} —— 和 Claude 一起读论文、做实验")
    parser.add_argument("file", nargs="?", default=None, help="启动时导入并打开的 PDF")
    args = parser.parse_args()

    _install_excepthook()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    if os.path.exists(ICON_PATH):
        app.setWindowIcon(QIcon(ICON_PATH))
    window = MainWindow(initial=args.file)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
