# -*- coding: utf-8 -*-
"""主窗口：左论文库 | 中阅读器 | 右（对话 / 笔记 / 实验）。"""

import os

from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtGui import QAction, QDesktopServices, QKeySequence
from PyQt6.QtWidgets import QMainWindow, QMessageBox, QSplitter, QTabWidget

from core.library import Library
from qtui.chat_panel import ChatPanel
from qtui.library_panel import LibraryPanel
from qtui.pdf_panel import PdfPanel
from qtui.settings import SettingsDialog, load_settings, qsettings
from qtui.side_panels import ExperimentsPanel, NotesPanel
from version import APP_NAME, __version__


class MainWindow(QMainWindow):

    def __init__(self, initial=None, library=None):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1500, 920)
        self.setAcceptDrops(True)
        self._paper = None
        self._initial = initial

        self.library = library or Library()
        self.library_panel = LibraryPanel(self.library)
        self.pdf = PdfPanel()
        self.chat = ChatPanel()
        self.notes = NotesPanel()
        self.experiments = ExperimentsPanel()

        self.tabs = QTabWidget()
        self.tabs.addTab(self.chat, "对话")
        self.tabs.addTab(self.notes, "笔记")
        self.tabs.addTab(self.experiments, "实验")

        self.splitter = QSplitter(Qt.Orientation.Horizontal)
        self.splitter.addWidget(self.library_panel)
        self.splitter.addWidget(self.pdf)
        self.splitter.addWidget(self.tabs)
        self.splitter.setStretchFactor(0, 0)
        self.splitter.setStretchFactor(1, 3)
        self.splitter.setStretchFactor(2, 3)
        self.splitter.setSizes([230, 640, 630])
        self.setCentralWidget(self.splitter)

        self.library_panel.paper_selected.connect(self._on_paper_selected)
        self.library_panel.paper_renamed.connect(lambda p: self._update_title())
        self.chat.page_requested.connect(self.pdf.jump_to_page)
        self.chat.note_requested.connect(self._on_note_requested)
        self.chat.figures_created.connect(self._on_figures)
        self.chat.busy_changed.connect(self.library_panel.set_busy)
        self.chat.current_page_provider = self.pdf.current_page

        self._build_menus()
        self._restore_state()
        self.statusBar().showMessage(f"论文库：{self.library.root}")
        QTimer.singleShot(0, self._initial_load)

    # ------------------------------------------------------------------
    def _build_menus(self):
        mb = self.menuBar()
        m = mb.addMenu("文件")
        a = QAction("导入 PDF…", self)
        a.setShortcut(QKeySequence.StandardKey.Open)
        a.triggered.connect(self.library_panel.import_pdf_dialog)
        m.addAction(a)
        a = QAction("从 arXiv 导入…", self)
        a.setShortcut(QKeySequence("Ctrl+Shift+O"))
        a.triggered.connect(self.library_panel.import_arxiv_dialog)
        m.addAction(a)
        m.addSeparator()
        a = QAction("打开论文库文件夹", self)
        a.triggered.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(self.library.root)))
        m.addAction(a)
        m.addSeparator()
        a = QAction("退出", self)
        a.setShortcut(QKeySequence.StandardKey.Quit)
        a.triggered.connect(self.close)
        m.addAction(a)

        m = mb.addMenu("对话")
        a = QAction("新对话", self)
        a.setShortcut(QKeySequence.StandardKey.New)
        a.triggered.connect(self.chat.new_session)
        m.addAction(a)
        a = QAction("停止生成", self)
        a.setShortcut(QKeySequence("Esc"))
        a.setShortcutContext(Qt.ShortcutContext.WindowShortcut)
        a.triggered.connect(self.chat.stop)
        m.addAction(a)
        a = QAction("聚焦输入框", self)
        a.setShortcut(QKeySequence("Ctrl+L"))
        a.triggered.connect(self._focus_input)
        m.addAction(a)

        m = mb.addMenu("设置")
        a = QAction("设置…", self)
        a.setShortcut(QKeySequence.StandardKey.Preferences)
        a.setMenuRole(QAction.MenuRole.PreferencesRole)
        a.triggered.connect(self.open_settings)
        m.addAction(a)

        m = mb.addMenu("帮助")
        a = QAction("关于", self)
        a.setMenuRole(QAction.MenuRole.AboutRole)
        a.triggered.connect(self._about)
        m.addAction(a)

    def _focus_input(self):
        self.tabs.setCurrentWidget(self.chat)
        self.chat.input.setFocus()

    def open_settings(self):
        if SettingsDialog(self).exec():
            self.chat.render()

    def _about(self):
        st = load_settings()
        QMessageBox.about(
            self, "关于",
            f"<b>{APP_NAME}</b> v{__version__}<br><br>"
            "和 Claude 一问一答地读论文、做实验。<br>"
            f"论文库：{self.library.root}<br>当前模型：{st['model']}")

    # ------------------------------------------------------------------
    def _initial_load(self):
        if self._initial:
            if self._initial.lower().endswith(".pdf") and os.path.isfile(self._initial):
                self.library_panel.import_pdfs([self._initial])
                return
        last = qsettings().value("ui/last_paper")
        self.library_panel.refresh(select_id=last)
        if self.library_panel.current_paper() is None and self.library_panel.list.count():
            self.library_panel.list.setCurrentRow(0)
        if not self.library_panel.list.count():
            self.chat.set_paper(None)
            if not load_settings()["api_key"] and not os.environ.get("ANTHROPIC_API_KEY"):
                self.statusBar().showMessage("先在「设置 → 设置…」里填好 Anthropic API Key，然后导入一篇论文")

    def _on_paper_selected(self, paper):
        if paper is not None and self._paper is not None and paper.id == self._paper.id:
            self._paper = paper          # 同一篇（列表刷新了）：只更新对象，不重载对话
            self._update_title()
            return
        self.notes.save()
        self._paper = paper
        self.pdf.load(paper.pdf_path if paper else None)
        self.notes.set_paper(paper)
        self.experiments.set_root(paper.experiments_dir if paper else None)
        self.chat.set_paper(paper)
        if paper is not None:
            qsettings().setValue("ui/last_paper", paper.id)
        self._update_title()

    def _update_title(self):
        p = self.library_panel.current_paper()
        self.setWindowTitle(f"{p.title} — {APP_NAME}" if p else APP_NAME)

    def _on_note_requested(self, title, content, box):
        box.set(self.notes.append_note(title, content))

    def _on_figures(self, images):
        if images:
            self.experiments.show_file(images[-1])

    # ------------------------------------------------------------------
    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls() and any(
                u.toLocalFile().lower().endswith(".pdf") for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event):
        paths_ = [u.toLocalFile() for u in event.mimeData().urls() if u.toLocalFile().lower().endswith(".pdf")]
        if paths_ and not self.chat.is_busy():
            self.library_panel.import_pdfs(paths_)

    # ------------------------------------------------------------------
    def _restore_state(self):
        s = qsettings()
        geo = s.value("ui/geometry")
        if geo is not None:
            self.restoreGeometry(geo)
        split = s.value("ui/splitter")
        if split is not None:
            self.splitter.restoreState(split)

    def closeEvent(self, event):
        if self.chat.is_busy():
            if QMessageBox.question(self, "退出", "Claude 还在回答，确定退出？（这一轮会被中止）") \
                    != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
        self.chat.shutdown()
        self.notes.save()
        s = qsettings()
        s.setValue("ui/geometry", self.saveGeometry())
        s.setValue("ui/splitter", self.splitter.saveState())
        super().closeEvent(event)

