"""端到端冒烟：真窗口 + 假 Claude，走一遍 导入 → 提问 → 跑实验 → 写笔记 → 引用跳页。"""
import time

from PyQt6.QtCore import QUrl
from PyQt6.QtWidgets import QApplication

from core import agent
from core.library import Library
from qtui import settings
from tests.conftest import make_pdf
from tests.fakes import FakeClient, message, text, tool_use


def _wait(pred, timeout=20):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        QApplication.processEvents()
        if pred():
            return True
        time.sleep(0.02)
    return False


def test_full_flow(qapp, tmp_path, monkeypatch):
    from qtui.main_window import MainWindow
    settings.save_settings({"auto_run": True, "api_key": "sk-test"})
    client = FakeClient([
        message([text("核心是缩放点积注意力", [{"type": "page_location", "start_page_number": 2,
                                           "end_page_number": 3, "cited_text": "x",
                                           "document_index": 0}]),
                 text("。")]),
        message([tool_use("run_python", {"purpose": "验证", "code":
                 "import matplotlib.pyplot as plt\nplt.plot([1,2]); plt.savefig('f.png'); print('ok42')"},
                 id_="toolu_run1"),
                 tool_use("save_note", {"title": "注意力", "content": "除以 √d 防止 softmax 饱和"},
                          id_="toolu_note1")], stop="tool_use"),
        message([text("实验和笔记都完成了。")]),
    ])
    monkeypatch.setattr(agent, "make_client", lambda s: client)

    lib = Library(str(tmp_path / "lib"))
    w = MainWindow(library=lib)
    w.show()
    QApplication.processEvents()
    w.library_panel.import_pdfs([make_pdf(tmp_path / "attn.pdf", pages=3, title="Attention Is All You Need")])
    assert _wait(lambda: w.pdf.doc.pageCount() == 3)
    paper = w.library_panel.current_paper()
    assert paper.title == "Attention Is All You Need"          # 取自 PDF 元数据

    w.chat.send("这篇讲什么？")
    assert w.chat.is_busy()
    assert _wait(lambda: not w.chat.is_busy())
    html = w.chat.view.toMarkdown()
    assert "缩放点积注意力" in html and "page:2" in html

    # 点引用 → 阅读器跳到第 2 页
    w.chat._on_anchor(QUrl("page:2"))
    assert _wait(lambda: w.pdf.current_page() == 2)

    w.chat.send("做个实验并记笔记")
    assert _wait(lambda: not w.chat.is_busy())
    md = w.chat.view.toMarkdown()
    assert "ok42" in md and "实验和笔记都完成了" in md
    assert "除以 √d" in w.notes.editor.toPlainText()
    assert "除以 √d" in paper.read_notes()
    [s] = paper.list_sessions()
    assert len(s.messages) == 6
    # 会话下拉框里的标题取自第一句提问
    assert w.chat.session_combo.currentText().startswith("这篇讲什么")

    # 新对话：空会话、风格可选
    w.chat.new_session()
    assert w.chat._session.messages == [] and w.chat.style_combo.isEnabled()
    w.close()


def test_failed_request_restores_input(qapp, tmp_path, monkeypatch):
    from PyQt6.QtWidgets import QMessageBox
    from qtui.main_window import MainWindow
    monkeypatch.setattr(agent, "make_client", lambda s: (_ for _ in ()).throw(agent.AgentError("没 Key")))
    shown = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *a, **k: shown.append(a[2]))
    lib = Library(str(tmp_path / "lib"))
    lib.add_pdf(make_pdf(tmp_path / "a.pdf"))
    w = MainWindow(library=lib)
    assert _wait(lambda: w.library_panel.current_paper() is not None)
    w.chat.send("你好")
    assert _wait(lambda: not w.chat.is_busy())
    assert shown == ["没 Key"]
    assert w.chat.input.toPlainText() == "你好"
    assert w.library_panel.current_paper().list_sessions() == []
    w.close()
