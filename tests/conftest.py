"""测试环境：Qt 无头运行、配置 / 论文库 / 钥匙串全部隔离到临时目录。

必须在任何 Qt 模块导入前设置平台插件。
"""
import os
import sys

import PyQt6

_plugins = os.path.join(os.path.dirname(PyQt6.__file__), 'Qt6', 'plugins', 'platforms')
if os.path.isdir(_plugins):
    os.environ.setdefault('QT_QPA_PLATFORM_PLUGIN_PATH', _plugins)
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def isolated_config(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_HUB_HOME", str(tmp_path / "home"))
    from core import paths
    monkeypatch.setattr(paths, "LIBRARY_DIR", str(tmp_path / "home" / "library"))
    from qtui import settings
    monkeypatch.setattr(settings, "SETTINGS_FILE_OVERRIDE", str(tmp_path / "settings.ini"))
    monkeypatch.setattr(settings, "keyring", None)


_APP = []


def _app():
    """QApplication 必须一直被引用着，否则会被回收，之后的 Qt 调用直接 abort。"""
    from PyQt6.QtWidgets import QApplication
    if not _APP:
        _APP.append(QApplication.instance() or QApplication([]))
    return _APP[0]


@pytest.fixture(scope="session")
def qapp():
    return _app()


def make_pdf(path, pages=2, title=None):
    """用 Qt 生成一个真的小 PDF（带文字），测试导入 / 阅读器用。"""
    from PyQt6.QtGui import QPageSize, QPainter, QPdfWriter
    _app()
    w = QPdfWriter(str(path))
    w.setPageSize(QPageSize(QPageSize.PageSizeId.A4))
    if title:
        w.setTitle(title)
    p = QPainter(w)
    for i in range(pages):
        if i:
            w.newPage()
        p.drawText(200, 400, f"Page {i + 1}: attention is all you need")
    p.end()
    return str(path)
