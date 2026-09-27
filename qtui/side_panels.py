# -*- coding: utf-8 -*-
"""右侧的「笔记」和「实验」两页。"""

import os

from PyQt6.QtCore import QDir, QTimer, QUrl, Qt
from PyQt6.QtGui import QDesktopServices, QFileSystemModel, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout, QLabel, QPlainTextEdit, QPushButton, QSplitter, QStackedWidget, QTextBrowser,
    QTreeView, QVBoxLayout, QWidget, QScrollArea,
)

from core import agent


class NotesPanel(QWidget):
    """Markdown 笔记：编辑 / 预览两种状态，改动 800ms 后自动保存。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paper = None
        self._loading = False

        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        self.state_label = QLabel("")
        self.state_label.setStyleSheet("color: gray;")
        bar.addWidget(self.state_label, 1)
        self.toggle_btn = QPushButton("预览")
        self.toggle_btn.setCheckable(True)
        self.toggle_btn.toggled.connect(self._toggle_preview)
        bar.addWidget(self.toggle_btn)

        self.editor = QPlainTextEdit()
        self.editor.setPlaceholderText("在这里记笔记（Markdown）。\n也可以在对话里让 Claude「把要点记到笔记里」。")
        self.editor.textChanged.connect(self._on_changed)
        self.preview = QTextBrowser()
        self.preview.setOpenExternalLinks(True)
        self.stack = QStackedWidget()
        self.stack.addWidget(self.editor)
        self.stack.addWidget(self.preview)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(bar)
        lay.addWidget(self.stack, 1)

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.setInterval(800)
        self._save_timer.timeout.connect(self.save)

    def set_paper(self, paper):
        self.save()
        self._paper = paper
        self._loading = True
        self.editor.setPlainText(paper.read_notes() if paper else "")
        self._loading = False
        self.editor.setEnabled(paper is not None)
        self.state_label.setText("")
        if self.toggle_btn.isChecked():
            self._render_preview()

    def _on_changed(self):
        if self._loading or self._paper is None:
            return
        self.state_label.setText("未保存…")
        self._save_timer.start()

    def save(self):
        self._save_timer.stop()
        if self._paper is None:
            return
        text = self.editor.toPlainText()
        if text != self._paper.read_notes():
            self._paper.write_notes(text)
        self.state_label.setText("已保存")

    def append_note(self, title, content):
        """save_note 工具：追加到编辑器并立即保存。返回给模型看的结果。"""
        if self._paper is None:
            return "当前没有打开论文，笔记没有写入"
        new = agent.append_note(self.editor.toPlainText(), title, content)
        self._loading = True
        self.editor.setPlainText(new)
        self._loading = False
        self.editor.moveCursor(self.editor.textCursor().MoveOperation.End)
        self.save()
        if self.toggle_btn.isChecked():
            self._render_preview()
        return f"已写入笔记「{title or '笔记'}」"

    def _toggle_preview(self, on):
        self.toggle_btn.setText("编辑" if on else "预览")
        if on:
            self.save()
            self._render_preview()
        self.stack.setCurrentIndex(1 if on else 0)

    def _render_preview(self):
        self.preview.setMarkdown(self.editor.toPlainText())


_TEXT_EXTS = (".py", ".txt", ".csv", ".json", ".md", ".log", ".yaml", ".yml", ".tsv")
_IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")


class ExperimentsPanel(QWidget):
    """实验目录浏览：左边文件树，右边预览图片 / 文本。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._root = None
        self.model = QFileSystemModel(self)
        self.model.setFilter(QDir.Filter.AllEntries | QDir.Filter.NoDotAndDotDot | QDir.Filter.Hidden)
        self.tree = QTreeView()
        self.tree.setModel(self.model)
        for col in (1, 2):
            self.tree.hideColumn(col)
        self.tree.setSortingEnabled(True)
        self.tree.sortByColumn(3, Qt.SortOrder.DescendingOrder)   # 最新的在上面
        self.tree.clicked.connect(self._preview)
        self.tree.doubleClicked.connect(self._open)

        self.img_label = QLabel()
        self.img_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        img_scroll = QScrollArea()
        img_scroll.setWidgetResizable(True)
        img_scroll.setWidget(self.img_label)
        self.text_view = QPlainTextEdit()
        self.text_view.setReadOnly(True)
        self.preview = QStackedWidget()
        hint = QLabel("点左边的文件预览；双击用系统程序打开。\n"
                      ".runs/ 是每次运行的代码存档，.artifacts/ 是回传给 Claude 的图。")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint.setStyleSheet("color: gray;")
        self.preview.addWidget(hint)
        self.preview.addWidget(img_scroll)
        self.preview.addWidget(self.text_view)

        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.tree)
        split.addWidget(self.preview)
        split.setSizes([200, 300])

        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        open_btn = QPushButton("打开文件夹")
        open_btn.clicked.connect(self._open_root)
        bar.addStretch(1)
        bar.addWidget(open_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.addLayout(bar)
        lay.addWidget(split, 1)

    def set_root(self, path):
        self._root = path
        if path:
            self.tree.setRootIndex(self.model.setRootPath(path))
        self.preview.setCurrentIndex(0)

    def show_file(self, path):
        """实验跑完后直接展示产生的图。"""
        idx = self.model.index(path)
        if idx.isValid():
            self.tree.setCurrentIndex(idx)
        self._show(path)

    def _preview(self, index):
        self._show(self.model.filePath(index))

    def _show(self, path):
        low = path.lower()
        if low.endswith(_IMAGE_EXTS):
            pix = QPixmap(path)
            if not pix.isNull():
                w = max(200, self.preview.width() - 30)
                if pix.width() > w:
                    pix = pix.scaledToWidth(w, Qt.TransformationMode.SmoothTransformation)
                self.img_label.setPixmap(pix)
                self.preview.setCurrentIndex(1)
                return
        if low.endswith(_TEXT_EXTS) and os.path.isfile(path):
            try:
                with open(path, encoding="utf-8", errors="replace") as fh:
                    self.text_view.setPlainText(fh.read(200_000))
            except OSError as e:
                self.text_view.setPlainText(str(e))
            self.preview.setCurrentIndex(2)
            return
        self.preview.setCurrentIndex(0)

    def _open(self, index):
        path = self.model.filePath(index)
        if os.path.isfile(path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def _open_root(self):
        if self._root:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._root))

