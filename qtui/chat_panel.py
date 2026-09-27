# -*- coding: utf-8 -*-
"""对话页：会话切换、流式显示、快捷提问、运行代码前确认。"""

from PyQt6.QtCore import QEvent, QPoint, QRect, QSize, QTimer, QUrl, Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices, QFontDatabase, QImageReader, QTextCursor
from PyQt6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QLabel, QLayout, QLineEdit, QMenu,
    QMessageBox, QPlainTextEdit, QPushButton, QSizePolicy, QTextBrowser, QToolButton,
    QVBoxLayout, QWidget,
)

from core import agent, render
from qtui import workers
from qtui.settings import load_settings, save_settings


# ---------------------------------------------------------------------------
# 自动换行的按钮布局（Qt 官方 FlowLayout 示例的精简版）
# ---------------------------------------------------------------------------

class FlowLayout(QLayout):
    def __init__(self, parent=None, spacing=4):
        super().__init__(parent)
        self._items = []
        self.setSpacing(spacing)
        self.setContentsMargins(0, 0, 0, 0)

    def addItem(self, item):
        self._items.append(item)

    def count(self):
        return len(self._items)

    def itemAt(self, i):
        return self._items[i] if 0 <= i < len(self._items) else None

    def takeAt(self, i):
        return self._items.pop(i) if 0 <= i < len(self._items) else None

    def expandingDirections(self):
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        return True

    def heightForWidth(self, width):
        return self._do_layout(QRect(0, 0, width, 0), True)

    def setGeometry(self, rect):
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        return self.minimumSize()

    def minimumSize(self):
        size = QSize()
        for it in self._items:
            size = size.expandedTo(it.minimumSize())
        return size

    def _do_layout(self, rect, test_only):
        x, y, line_h = rect.x(), rect.y(), 0
        sp = self.spacing()
        for it in self._items:
            hint = it.sizeHint()
            nx = x + hint.width() + sp
            if nx - sp > rect.right() and line_h > 0:
                x, y = rect.x(), y + line_h + sp
                nx = x + hint.width() + sp
                line_h = 0
            if not test_only:
                it.setGeometry(QRect(QPoint(x, y), hint))
            x = nx
            line_h = max(line_h, hint.height())
        return y + line_h - rect.y()


# ---------------------------------------------------------------------------
# 运行代码前的确认框
# ---------------------------------------------------------------------------

class ApproveCodeDialog(QDialog):
    RUN, RUN_ALWAYS, DENY = 1, 2, 0

    def __init__(self, purpose, code, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Claude 想运行一段实验代码")
        self.resize(680, 520)
        self.choice = self.DENY
        lay = QVBoxLayout(self)
        if purpose:
            p = QLabel("目的：" + purpose)
            p.setWordWrap(True)
            lay.addWidget(p)
        view = QPlainTextEdit(code)
        view.setReadOnly(True)
        view.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        lay.addWidget(view, 1)
        warn = QLabel("代码会以你的用户权限在本机运行（工作目录：这篇论文的 experiments 文件夹）。")
        warn.setStyleSheet("color: gray;")
        warn.setWordWrap(True)
        lay.addWidget(warn)
        self.reason = QLineEdit()
        self.reason.setPlaceholderText("拒绝理由（可选，会告诉 Claude，例如：先别画图，只打印数值）")
        lay.addWidget(self.reason)
        box = QDialogButtonBox()
        run = box.addButton("运行", QDialogButtonBox.ButtonRole.AcceptRole)
        run.setDefault(True)
        always = box.addButton("本次对话都直接运行", QDialogButtonBox.ButtonRole.AcceptRole)
        deny = box.addButton("不运行", QDialogButtonBox.ButtonRole.RejectRole)
        run.clicked.connect(lambda: self._done(self.RUN))
        always.clicked.connect(lambda: self._done(self.RUN_ALWAYS))
        deny.clicked.connect(lambda: self._done(self.DENY))
        lay.addWidget(box)

    def _done(self, choice):
        self.choice = choice
        self.accept() if choice != self.DENY else self.reject()


# ---------------------------------------------------------------------------
# 对话页
# ---------------------------------------------------------------------------

class ChatPanel(QWidget):
    page_requested = pyqtSignal(int)
    note_requested = pyqtSignal(str, str, object)   # 转给笔记页：title, content, box
    figures_created = pyqtSignal(list)
    busy_changed = pyqtSignal(bool)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._paper = None
        self._session = None
        self._sessions = []
        self._worker = None
        self._live_text = ""
        self._live_thinking = ""
        self._status = ""
        self._session_auto_run = set()     # 用户选了「本次对话都直接运行」的会话 id
        self.current_page_provider = None  # 主窗口注入：返回阅读器当前页码

        # ---- 顶栏：会话 + 风格 ----
        top = QHBoxLayout()
        top.setContentsMargins(4, 2, 4, 2)
        self.session_combo = QComboBox()
        self.session_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToMinimumContentsLengthWithIcon)
        self.session_combo.setMinimumContentsLength(12)
        self.session_combo.currentIndexChanged.connect(self._on_session_selected)
        top.addWidget(self.session_combo, 1)
        self.new_btn = QPushButton("新对话")
        self.new_btn.clicked.connect(self.new_session)
        top.addWidget(self.new_btn)
        more = QToolButton()
        more.setText("⋯")
        more.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(more)
        menu.addAction("重命名当前对话…", self._rename_session)
        menu.addAction("删除当前对话", self._delete_session)
        menu.addSeparator()
        menu.addAction("复制整段对话（Markdown）", self._copy_markdown)
        more.setMenu(menu)
        top.addWidget(more)

        style_row = QHBoxLayout()
        style_row.setContentsMargins(4, 0, 4, 0)
        style_row.addWidget(QLabel("风格:"))
        self.style_combo = QComboBox()
        for key, (label, _) in agent.STYLES.items():
            self.style_combo.addItem(label, key)
        self.style_combo.setToolTip("每个对话开始时定下风格；想换风格就开一个新对话")
        self.style_combo.currentIndexChanged.connect(self._on_style_changed)
        style_row.addWidget(self.style_combo)
        style_row.addStretch(1)
        self.usage_label = QLabel("")
        self.usage_label.setStyleSheet("color: gray;")
        style_row.addWidget(self.usage_label)

        # ---- 对话显示 ----
        self.view = QTextBrowser()
        self.view.setOpenLinks(False)
        self.view.anchorClicked.connect(self._on_anchor)
        f = self.view.font()
        f.setPointSize(f.pointSize() + 1)
        self.view.setFont(f)

        # ---- 快捷提问 ----
        quick = QWidget()
        flow = FlowLayout(quick)
        for label, prompt in agent.QUICK_PROMPTS:
            b = QPushButton(label)
            b.setToolTip(prompt)
            b.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            b.clicked.connect(lambda _=False, p=prompt: self.send(p))
            flow.addWidget(b)
        self.quick_widget = quick

        # ---- 输入 ----
        self.input = QPlainTextEdit()
        self.input.setPlaceholderText("问点什么……（⌘/Ctrl+Enter 发送）\n例如：式 (3) 里为什么要除以 √d？ / 这个方法和 LoRA 有什么区别？")
        self.input.setFixedHeight(84)
        self.input.installEventFilter(self)
        in_row = QHBoxLayout()
        in_row.setContentsMargins(0, 0, 0, 0)
        in_row.addWidget(self.input, 1)
        btn_col = QVBoxLayout()
        self.page_btn = QPushButton("引用本页")
        self.page_btn.setToolTip("在问题前加上「结合第 N 页」，N 是阅读器当前页")
        self.page_btn.clicked.connect(self._insert_page_ref)
        self.send_btn = QPushButton("发送")
        self.send_btn.setDefault(True)
        self.send_btn.clicked.connect(self._on_send_clicked)
        btn_col.addWidget(self.page_btn)
        btn_col.addWidget(self.send_btn)
        in_row.addLayout(btn_col)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: gray;")

        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(4)
        lay.addLayout(top)
        lay.addLayout(style_row)
        lay.addWidget(self.view, 1)
        lay.addWidget(quick)
        lay.addLayout(in_row)
        lay.addWidget(self.status_label)

        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(120)
        self._render_timer.timeout.connect(self.render)

        self._set_enabled(False)

    # ------------------------------------------------------------------
    # 论文 / 会话
    # ------------------------------------------------------------------
    def is_busy(self):
        return self._worker is not None

    def set_paper(self, paper):
        self._paper = paper
        self._set_enabled(paper is not None)
        self._reload_sessions(select_id=None)

    def _reload_sessions(self, select_id=None):
        self._sessions = self._paper.list_sessions() if self._paper else []
        if self._paper and (select_id is None and not self._sessions):
            self._sessions.insert(0, self._make_new_session())
        self._fill_combo(select_id)

    def _fill_combo(self, select_id=None):
        self.session_combo.blockSignals(True)
        self.session_combo.clear()
        idx = 0
        for i, s in enumerate(self._sessions):
            self.session_combo.addItem(s.display_title())
            if select_id and s.id == select_id:
                idx = i
        self.session_combo.blockSignals(False)
        if self._sessions:
            self.session_combo.setCurrentIndex(idx)
            self._on_session_selected(idx)
        else:
            self._session = None
            self.render()

    def _make_new_session(self):
        st = load_settings()
        return self._paper.new_session(st["style"], st["background"], st["web_tools"])

    def new_session(self):
        if self._paper is None or self.is_busy():
            return
        # 已经有一个空的新对话就直接切过去
        for i, s in enumerate(self._sessions):
            if not s.messages:
                self.session_combo.setCurrentIndex(i)
                self.input.setFocus()
                return
        self._sessions.insert(0, self._make_new_session())
        self._fill_combo(self._sessions[0].id)
        self.input.setFocus()

    def _on_session_selected(self, idx):
        if not (0 <= idx < len(self._sessions)):
            return
        self._session = self._sessions[idx]
        style = self._session.data.get("style") or agent.DEFAULT_STYLE
        self.style_combo.blockSignals(True)
        i = self.style_combo.findData(style)
        self.style_combo.setCurrentIndex(max(0, i))
        self.style_combo.blockSignals(False)
        self.style_combo.setEnabled(not self._session.messages and not self.is_busy())
        self._update_usage()
        self.render(scroll_to_end=True)

    def _on_style_changed(self, _idx):
        key = self.style_combo.currentData()
        if self._session is not None and not self._session.messages:
            self._session.data["style"] = key
            save_settings({"style": key})   # 下次新对话默认也用它

    def _rename_session(self):
        if self._session is None or not self._session.messages:
            return
        from PyQt6.QtWidgets import QInputDialog
        text, ok = QInputDialog.getText(self, "重命名对话", "名称:", text=self._session.display_title())
        if ok:
            self._session.data["title"] = text.strip()
            self._session.save()
            self._fill_combo(self._session.id)

    def _delete_session(self):
        if self._session is None or self.is_busy():
            return
        if self._session.messages:
            if QMessageBox.question(self, "删除对话", f"删除「{self._session.display_title()}」？不能撤销。") \
                    != QMessageBox.StandardButton.Yes:
                return
        self._session.delete()
        self._reload_sessions()

    def _copy_markdown(self):
        if self._session is None:
            return
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(render.render_history(
            self._session.messages, self._paper.experiments_dir, show_thinking=True))

    # ------------------------------------------------------------------
    # 发送 / 停止
    # ------------------------------------------------------------------
    def eventFilter(self, obj, event):
        if obj is self.input and event.type() == QEvent.Type.KeyPress:
            if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and \
                    event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.MetaModifier):
                self._on_send_clicked()
                return True
        return super().eventFilter(obj, event)

    def _on_send_clicked(self):
        if self.is_busy():
            self.stop()
            return
        text = self.input.toPlainText().strip()
        if text:
            self.send(text)

    def _insert_page_ref(self):
        page = self.current_page_provider() if self.current_page_provider else None
        if page:
            cur = self.input.textCursor()
            cur.movePosition(cur.MoveOperation.Start)
            cur.insertText(f"结合论文第 {page} 页：")
            self.input.setFocus()

    def send(self, text):
        if self._paper is None or self._session is None or self.is_busy():
            return
        settings = load_settings()
        s = self._session
        if not s.messages:
            # 空会话在开口前才定下背景和联网设置（期间用户可能改过设置）
            s.data["background"] = settings["background"]
            s.data["web_tools"] = settings["web_tools"]
        if s.id in self._session_auto_run:
            settings["auto_run"] = True
        if not settings["python"]:
            from qtui.settings import default_python
            settings["python"] = default_python()

        self.input.clear()
        self._live_text = ""
        self._live_thinking = ""
        self._status = "正在连接 Claude…"
        worker = workers.AgentWorker(s, text, settings)
        worker.text_delta.connect(self._on_text)
        worker.thinking_delta.connect(self._on_thinking)
        worker.status.connect(self._on_status)
        worker.history_changed.connect(self._on_history_changed)
        worker.approval_needed.connect(self._on_approval)
        worker.note_requested.connect(self.note_requested)
        worker.tool_done.connect(self._on_tool_done)
        worker.finished_turn.connect(self._on_finished)
        worker.failed.connect(lambda msg, rolled, t=text: self._on_failed(msg, rolled, t))
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        self._set_busy(True)
        worker.start()
        self.render(scroll_to_end=True)

    def stop(self):
        if self._worker is not None:
            self._status = "正在停止…"
            self._worker.cancel()
            self._schedule_render()

    def shutdown(self):
        """窗口关闭：让正在跑的回合停下，线程跑完再销毁。"""
        w, self._worker = self._worker, None
        if w is not None:
            for sig in (w.text_delta, w.thinking_delta, w.status, w.history_changed,
                        w.approval_needed, w.note_requested, w.tool_done, w.finished_turn, w.failed):
                try:
                    sig.disconnect()
                except TypeError:
                    pass
            w.cancel()
            if not w.wait(3000):
                workers.detach(w)

    # ------------------------------------------------------------------
    # worker 回调
    # ------------------------------------------------------------------
    def _on_text(self, delta):
        self._live_text += delta
        self._schedule_render()

    def _on_thinking(self, delta):
        self._live_thinking += delta
        self._schedule_render()

    def _on_status(self, text):
        self._status = text
        self.status_label.setText(text)
        self._schedule_render()

    def _on_history_changed(self):
        # 完整的一步已经进了历史，流式缓冲清空（否则会显示两遍）
        self._live_text = ""
        self._live_thinking = ""
        self._update_usage()
        if self.session_combo.currentIndex() >= 0 and self._session is not None:
            self.session_combo.setItemText(self.session_combo.currentIndex(), self._session.display_title())
        self._schedule_render()

    def _on_tool_done(self, name, _text, images):
        if images:
            self.figures_created.emit(images)

    def _on_approval(self, purpose, code, box):
        dlg = ApproveCodeDialog(purpose, code, self)
        dlg.exec()
        if dlg.choice == ApproveCodeDialog.RUN_ALWAYS and self._session is not None:
            self._session_auto_run.add(self._session.id)
        if dlg.choice == ApproveCodeDialog.DENY:
            box.set((False, dlg.reason.text().strip()))
        else:
            box.set((True, ""))

    def _on_finished(self, outcome):
        self._end_turn()
        self.status_label.setText("已停止" if outcome == "cancelled" else "")

    def _on_failed(self, msg, rolled_back, text):
        self._end_turn()
        if rolled_back and not self.input.toPlainText().strip():
            self.input.setPlainText(text)     # 这句话没发出去，放回输入框方便重试
        self.status_label.setText("出错了")
        QMessageBox.warning(self, "提问失败", msg)

    def _end_turn(self):
        self._worker = None
        self._live_text = ""
        self._live_thinking = ""
        self._status = ""
        self._set_busy(False)
        self.render()

    # ------------------------------------------------------------------
    # 显示
    # ------------------------------------------------------------------
    def _set_enabled(self, on):
        for w in (self.input, self.send_btn, self.new_btn, self.session_combo, self.quick_widget,
                  self.page_btn):
            w.setEnabled(on)

    def _set_busy(self, busy):
        self.send_btn.setText("停止" if busy else "发送")
        self.quick_widget.setEnabled(not busy)
        self.new_btn.setEnabled(not busy)
        self.session_combo.setEnabled(not busy)
        self.style_combo.setEnabled(not busy and self._session is not None and not self._session.messages)
        self.busy_changed.emit(busy)

    def _update_usage(self):
        if self._session is None:
            self.usage_label.setText("")
            return
        u = self._session.data.get("usage") or {}
        total_in = u.get("input", 0) + u.get("cache_read", 0) + u.get("cache_write", 0)
        if not total_in:
            self.usage_label.setText("")
            return
        model = load_settings()["model"]
        cost = agent.estimate_cost(u, model)
        hit = (u.get("cache_read", 0) / total_in * 100) if total_in else 0
        self.usage_label.setText(f"本对话 ≈ ${cost:.2f} · 缓存命中 {hit:.0f}%")
        self.usage_label.setToolTip(
            f"输入 {u.get('input', 0):,}，缓存读 {u.get('cache_read', 0):,}，缓存写 {u.get('cache_write', 0):,}，"
            f"输出 {u.get('output', 0):,} tokens（按 {model} 的价格估算）")

    def _schedule_render(self):
        if not self._render_timer.isActive():
            self._render_timer.start()

    def _welcome_markdown(self):
        p = self._paper
        if p is None:
            return ("## 欢迎使用 Research Hub\n\n"
                    "左边导入一篇论文（本地 PDF 或 arXiv 编号），然后在这里和 Claude 一问一答地读它、"
                    "做实验验证它。\n\n先在菜单 **设置 → 设置…** 里填好 Anthropic API Key。")
        md = [f"## {p.title}"]
        if p.display_subtitle():
            md.append("*" + p.display_subtitle() + "*")
        if p.meta.get("abstract"):
            md.append("> " + p.meta["abstract"])
        md.append("**怎么用：** 直接提问，或点下面的快捷按钮。回答里的 [p.N] 是出处，点一下阅读器就跳到那一页。"
                  "想验证论文里的说法，就让 Claude「做个小实验」，它会写代码在你电脑上跑，并解读结果。"
                  "上方可以选这次对话的风格（导师 / 讲解 / 速读 / 实验）。")
        return "\n\n".join(md)

    def render(self, scroll_to_end=False):
        bar = self.view.verticalScrollBar()
        at_bottom = scroll_to_end or bar.value() >= bar.maximum() - 30
        keep = bar.value()
        s = self._session
        show_thinking = load_settings()["show_thinking"]
        if s is None or not s.messages:
            md = self._welcome_markdown()
        else:
            md = render.render_history(s.messages, self._paper.experiments_dir, show_thinking)
        if self._worker is not None and s is not None and s.messages:
            md += "\n\n" + self._live_markdown(s.messages, show_thinking)
        self.view.setMarkdown(md)
        self._fit_images()
        if at_bottom:
            bar.setValue(bar.maximum())
        else:
            bar.setValue(keep)

    def _fit_images(self):
        """Markdown 里没法指定图片宽度：渲染后把宽于视图的图按比例缩到视图宽。"""
        doc = self.view.document()
        max_w = max(200, self.view.viewport().width() - 40)
        block = doc.begin()
        while block.isValid():
            it = block.begin()
            while not it.atEnd():
                frag = it.fragment()
                fmt = frag.charFormat()
                if frag.isValid() and fmt.isImageFormat():
                    img_fmt = fmt.toImageFormat()
                    size = QImageReader(QUrl(img_fmt.name()).toLocalFile()).size()
                    if size.width() > max_w:
                        img_fmt.setWidth(max_w)
                        img_fmt.setHeight(size.height() * max_w / size.width())
                        cur = QTextCursor(doc)
                        cur.setPosition(frag.position())
                        cur.setPosition(frag.position() + frag.length(), QTextCursor.MoveMode.KeepAnchor)
                        cur.setCharFormat(img_fmt)
                it += 1
            block = block.next()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_render()

    def _live_markdown(self, messages, show_thinking):
        last = messages[-1]
        last_is_claude = last.get("role") == "assistant" or not any(
            b.get("type") == "text" for b in (last.get("content") or []) if isinstance(b, dict))
        parts = [] if last_is_claude else [render.CLAUDE_HEADER]
        if show_thinking and self._live_thinking.strip():
            parts.append("💭 *思考中*\n\n" + "\n".join("> " + ln for ln in self._live_thinking.strip().splitlines()[-8:]))
        if self._live_text:
            parts.append(self._live_text)
        if self._status:
            parts.append(f"*⏳ {self._status}*")
        return "\n\n".join(parts)

    def _on_anchor(self, url):
        if url.scheme() == "page":
            try:
                self.page_requested.emit(int(url.path() or url.toString().split(":", 1)[1]))
            except ValueError:
                pass
            return
        if url.scheme() in ("http", "https", "file", "mailto"):
            QDesktopServices.openUrl(url)
        elif not url.scheme():
            QDesktopServices.openUrl(QUrl(url.toString()))

