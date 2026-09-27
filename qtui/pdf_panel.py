# -*- coding: utf-8 -*-
"""论文阅读器：QtPdf 多页连续显示，页码跳转、缩放、用系统阅读器打开。

对话里的引用页码链接（page:N）会调 jump_to_page 跳过来。
"""

from PyQt6.QtCore import QPointF, QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtPdfWidgets import QPdfView
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSpinBox, QToolButton, QVBoxLayout, QWidget


class PdfPanel(QWidget):

    def __init__(self, parent=None):
        super().__init__(parent)
        self._path = None
        self.doc = QPdfDocument(self)
        self.view = QPdfView(self)
        self.view.setDocument(self.doc)
        self.view.setPageMode(QPdfView.PageMode.MultiPage)
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

        bar = QHBoxLayout()
        bar.setContentsMargins(4, 2, 4, 2)
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setKeyboardTracking(False)
        self.page_spin.valueChanged.connect(lambda v: self.jump_to_page(v))
        self.total_label = QLabel("/ 0")
        bar.addWidget(QLabel("页"))
        bar.addWidget(self.page_spin)
        bar.addWidget(self.total_label)
        bar.addStretch(1)
        for text, tip, slot in (("－", "缩小", lambda: self._zoom(1 / 1.2)),
                                ("＋", "放大", lambda: self._zoom(1.2)),
                                ("↔", "适应宽度", self._fit_width),
                                ("↗", "用系统阅读器打开（可以划词、批注）", self._open_external)):
            b = QToolButton()
            b.setText(text)
            b.setToolTip(tip)
            b.clicked.connect(slot)
            bar.addWidget(b)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addLayout(bar)
        lay.addWidget(self.view, 1)

        self.view.pageNavigator().currentPageChanged.connect(self._on_page_changed)

    def load(self, path):
        self._path = path
        self.doc.close()
        if path:
            self.doc.load(path)
        n = self.doc.pageCount()
        self.page_spin.blockSignals(True)
        self.page_spin.setMaximum(max(1, n))
        self.page_spin.setValue(1)
        self.page_spin.blockSignals(False)
        self.total_label.setText(f"/ {n}")
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

    def pdf_title(self):
        """PDF 元数据里的标题（很多论文 PDF 有），没有返回空串。"""
        title = self.doc.metaData(QPdfDocument.MetaDataField.Title)
        return str(title or "").strip()

    def jump_to_page(self, page_number):
        """page_number 从 1 开始。"""
        n = self.doc.pageCount()
        if n <= 0:
            return
        idx = max(0, min(n - 1, int(page_number) - 1))
        self.view.pageNavigator().jump(idx, QPointF(), self.view.zoomFactor())

    def current_page(self):
        return self.view.pageNavigator().currentPage() + 1

    def _on_page_changed(self, idx):
        self.page_spin.blockSignals(True)
        self.page_spin.setValue(idx + 1)
        self.page_spin.blockSignals(False)

    def _zoom(self, factor):
        self.view.setZoomMode(QPdfView.ZoomMode.Custom)
        self.view.setZoomFactor(max(0.25, min(5.0, self.view.zoomFactor() * factor)))

    def _fit_width(self):
        self.view.setZoomMode(QPdfView.ZoomMode.FitToWidth)

    def _open_external(self):
        if self._path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._path))

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_PageDown,):
            self.jump_to_page(self.current_page() + 1)
        elif event.key() in (Qt.Key.Key_PageUp,):
            self.jump_to_page(self.current_page() - 1)
        else:
            super().keyPressEvent(event)
