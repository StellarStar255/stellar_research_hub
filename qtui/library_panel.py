# -*- coding: utf-8 -*-
"""左侧论文库：搜索、导入（本地 PDF / arXiv）、重命名、删除。"""

import os

from PyQt6.QtCore import QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtPdf import QPdfDocument
from PyQt6.QtWidgets import (
    QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QInputDialog, QLabel, QLineEdit,
    QListWidget, QListWidgetItem, QMenu, QMessageBox, QProgressBar, QPushButton, QVBoxLayout,
    QWidget,
)

from qtui import workers


def _pdf_meta_title(path):
    """PDF 元数据里像样的标题；Word 导出之类的垃圾标题不要。"""
    doc = QPdfDocument(None)
    try:
        doc.load(path)
        title = str(doc.metaData(QPdfDocument.MetaDataField.Title) or "").strip()
    finally:
        doc.close()
    if len(title) < 6 or title.lower().startswith(("microsoft word", "untitled", "arxiv")) \
            or title.lower().endswith((".dvi", ".pdf", ".tex", ".doc", ".docx")):
        return ""
    return title


class ArxivImportDialog(QDialog):
    """输入 arXiv 编号 / 链接 → 后台下载。成功后 self.fetched = (临时 PDF 路径, meta)。"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("从 arXiv 导入")
        self.setMinimumWidth(480)
        self.fetched = None
        self._worker = None
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("arXiv 编号、arXiv 链接，或任意 PDF 链接："))
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("例如 1706.03762 或 https://arxiv.org/abs/2106.09685")
        lay.addWidget(self.edit)
        self.progress = QProgressBar()
        self.progress.setVisible(False)
        lay.addWidget(self.progress)
        self.status = QLabel("")
        self.status.setStyleSheet("color: gray;")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                        | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("下载")
        self.buttons.accepted.connect(self._start)
        self.buttons.rejected.connect(self.reject)
        lay.addWidget(self.buttons)

    def _start(self):
        text = self.edit.text().strip()
        if not text:
            return
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(False)
        self.edit.setEnabled(False)
        self.progress.setVisible(True)
        self.progress.setRange(0, 0)
        self.status.setText("正在获取论文信息…")
        w = workers.ImportWorker(text)
        w.progress.connect(self._on_progress)
        w.done.connect(self._on_done)
        w.failed.connect(self._on_failed)
        w.finished.connect(w.deleteLater)
        self._worker = w
        w.start()

    def _on_progress(self, done, total):
        self.status.setText(f"正在下载 PDF… {done / 1024 / 1024:.1f} MB")
        if total:
            self.progress.setRange(0, total)
            self.progress.setValue(done)

    def _on_done(self, path, meta):
        self._worker = None
        self.fetched = (path, meta)
        self.accept()

    def _on_failed(self, msg):
        self._worker = None
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(True)
        self.edit.setEnabled(True)
        self.progress.setVisible(False)
        self.status.setText(msg or "已取消")

    def reject(self):
        w, self._worker = self._worker, None
        if w is not None:
            for sig in (w.progress, w.done, w.failed):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            w.cancel()
            workers.detach(w)
        super().reject()


class LibraryPanel(QWidget):
    paper_selected = pyqtSignal(object)      # Paper 或 None
    paper_renamed = pyqtSignal(object)

    def __init__(self, library, parent=None):
        super().__init__(parent)
        self.library = library
        self._busy = False

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索标题 / 作者 / arXiv 号")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filter)
        self.list = QListWidget()
        self.list.setWordWrap(True)
        self.list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._context_menu)
        self.list.currentItemChanged.connect(self._on_current_changed)

        row = QHBoxLayout()
        pdf_btn = QPushButton("＋ PDF")
        pdf_btn.setToolTip("导入本地 PDF（也可以直接把 PDF 拖进窗口）")
        pdf_btn.clicked.connect(self.import_pdf_dialog)
        arxiv_btn = QPushButton("＋ arXiv")
        arxiv_btn.clicked.connect(self.import_arxiv_dialog)
        row.addWidget(pdf_btn)
        row.addWidget(arxiv_btn)

        lay = QVBoxLayout(self)
        lay.setContentsMargins(4, 4, 4, 4)
        lay.addWidget(self.search)
        lay.addWidget(self.list, 1)
        lay.addLayout(row)

    # ---- 列表 ----
    def refresh(self, select_id=None):
        cur = select_id or (self.current_paper().id if self.current_paper() else None)
        self.list.blockSignals(True)
        self.list.clear()
        target = None
        for p in self.library.list_papers():
            sub = p.display_subtitle()
            item = QListWidgetItem(p.title + (("\n" + sub) if sub else ""))
            item.setData(Qt.ItemDataRole.UserRole, p)
            item.setToolTip(p.title)
            self.list.addItem(item)
            if p.id == cur:
                target = item
        self.list.blockSignals(False)
        self._apply_filter(self.search.text())
        if target is not None:
            self.list.setCurrentItem(target)
        elif self.list.count() == 0:
            self.paper_selected.emit(None)

    def current_paper(self):
        item = self.list.currentItem()
        return item.data(Qt.ItemDataRole.UserRole) if item else None

    def set_busy(self, busy):
        """对话进行中不许切论文（worker 正在写这篇论文的会话）。"""
        self._busy = busy
        self.list.setEnabled(not busy)

    def _apply_filter(self, text):
        t = (text or "").lower().strip()
        for i in range(self.list.count()):
            item = self.list.item(i)
            p = item.data(Qt.ItemDataRole.UserRole)
            hay = " ".join([p.title, p.meta.get("arxiv_id") or "", " ".join(p.meta.get("authors") or [])]).lower()
            item.setHidden(bool(t) and t not in hay)

    def _on_current_changed(self, cur, _prev):
        self.paper_selected.emit(cur.data(Qt.ItemDataRole.UserRole) if cur else None)

    # ---- 导入 ----
    def import_pdf_dialog(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "导入论文 PDF", os.path.expanduser("~"), "PDF (*.pdf)")
        self.import_pdfs(paths)

    def import_pdfs(self, paths):
        last = None
        for path in paths:
            dup = self.library.find_duplicate(pdf_path=path)
            if dup is not None:
                last = dup
                continue
            try:
                title = _pdf_meta_title(path)
                last = self.library.add_pdf(path, {"title": title} if title else None)
            except (OSError, ValueError) as e:
                QMessageBox.warning(self, "导入失败", f"{os.path.basename(path)}：{e}")
        if last is not None:
            self.refresh(select_id=last.id)

    def import_arxiv_dialog(self):
        dlg = ArxivImportDialog(self)
        if dlg.exec() != QDialog.DialogCode.Accepted or not dlg.fetched:
            return
        path, meta = dlg.fetched
        try:
            dup = self.library.find_duplicate(pdf_path=path, arxiv_id=meta.get("arxiv_id"))
            if dup is not None:
                QMessageBox.information(self, "已在库里", f"「{dup.title}」已经导入过了，直接打开它。")
                paper = dup
            else:
                paper = self.library.add_pdf(path, meta)
        except (OSError, ValueError) as e:
            QMessageBox.warning(self, "导入失败", str(e))
            return
        finally:
            try:
                os.remove(path)
            except OSError:
                pass
        self.refresh(select_id=paper.id)

    # ---- 右键菜单 ----
    def _context_menu(self, pos):
        item = self.list.itemAt(pos)
        if item is None:
            return
        paper = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)
        menu.addAction("重命名…", lambda: self._rename(paper))
        menu.addAction("在文件夹中显示", lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(paper.root)))
        if paper.meta.get("url"):
            menu.addAction("打开论文网页", lambda: QDesktopServices.openUrl(QUrl(paper.meta["url"])))
        menu.addSeparator()
        act = menu.addAction("删除…", lambda: self._delete(paper))
        act.setEnabled(not self._busy)
        menu.exec(self.list.mapToGlobal(pos))

    def _rename(self, paper):
        text, ok = QInputDialog.getText(self, "重命名", "标题:", text=paper.title)
        if ok and text.strip():
            paper.rename(text)
            self.refresh(select_id=paper.id)
            self.paper_renamed.emit(paper)

    def _delete(self, paper):
        n = len(paper.list_sessions())
        msg = f"删除「{paper.title}」？\n它的 {n} 个对话、笔记和实验文件都会一起删除，不能撤销。"
        if QMessageBox.question(self, "删除论文", msg) != QMessageBox.StandardButton.Yes:
            return
        self.library.delete(paper)
        self.refresh()
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
