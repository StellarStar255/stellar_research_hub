# -*- coding: utf-8 -*-
"""AI 与实验相关设置 + 设置对话框。

模型 / Base URL / 实验解释器等存在 QSettings；API Key 优先存系统钥匙串
（keyring：macOS Keychain / Windows 凭据管理器），没装 keyring 或钥匙串
不可用时退回 QSettings（明文，本机）。没配 Key 时走 anthropic SDK 的默认
凭据（ANTHROPIC_API_KEY 环境变量或 ant auth login）。
"""

import shutil
import sys

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QFormLayout, QLabel, QLineEdit, QComboBox, QPlainTextEdit,
    QCheckBox, QSpinBox, QDialogButtonBox, QHBoxLayout, QPushButton, QFileDialog, QGroupBox,
)

from core import agent

try:
    import keyring
except ImportError:          # 可选依赖：没装就用 QSettings
    keyring = None

_KEYRING_SERVICE = "ResearchHub"
_KEYRING_USER = "anthropic_api_key"

DEFAULT_TIMEOUT = 120

# 测试时指向临时 ini 文件，不碰用户真实配置
SETTINGS_FILE_OVERRIDE = None


def qsettings():
    if SETTINGS_FILE_OVERRIDE:
        return QSettings(SETTINGS_FILE_OVERRIDE, QSettings.Format.IniFormat)
    return QSettings("ResearchHub", "ResearchHubQt")


def _keyring_get():
    if keyring is None:
        return None
    try:
        return keyring.get_password(_KEYRING_SERVICE, _KEYRING_USER)
    except Exception:            # noqa: BLE001 —— NoKeyringError、权限拒绝等都视为不可用
        return None


def _keyring_set(key):
    if keyring is None:
        return False
    try:
        if key:
            keyring.set_password(_KEYRING_SERVICE, _KEYRING_USER, key)
        else:
            try:
                keyring.delete_password(_KEYRING_SERVICE, _KEYRING_USER)
            except Exception:    # noqa: BLE001 —— 本来就没有条目
                pass
        return True
    except Exception:            # noqa: BLE001
        return False


def _bool(v, default):
    if v is None:
        return default
    if isinstance(v, bool):
        return v
    return str(v).lower() in ("1", "true", "yes")


def load_settings():
    s = qsettings()
    plain = (s.value("ai/api_key") or "").strip()
    api_key = _keyring_get()
    if api_key is None:
        api_key = plain
    elif plain:
        s.remove("ai/api_key")
    try:
        timeout = int(s.value("lab/timeout") or DEFAULT_TIMEOUT)
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    return {
        "api_key": (api_key or "").strip(),
        "model": (s.value("ai/model") or agent.DEFAULT_MODEL).strip() or agent.DEFAULT_MODEL,
        "base_url": (s.value("ai/base_url") or "").strip(),
        "effort": (s.value("ai/effort") or agent.DEFAULT_EFFORT),
        "background": s.value("learn/background") or "",
        "style": s.value("learn/style") or agent.DEFAULT_STYLE,
        "web_tools": _bool(s.value("learn/web_tools"), True),
        "show_thinking": _bool(s.value("ui/show_thinking"), False),
        "python": (s.value("lab/python") or "").strip(),
        "timeout": timeout,
        "auto_run": _bool(s.value("lab/auto_run"), False),
    }


def save_settings(values):
    s = qsettings()
    if "api_key" in values:
        key = values.get("api_key") or ""
        if _keyring_set(key):
            s.remove("ai/api_key")
        else:
            s.setValue("ai/api_key", key)
    mapping = {"model": "ai/model", "base_url": "ai/base_url", "effort": "ai/effort",
               "background": "learn/background", "style": "learn/style",
               "web_tools": "learn/web_tools", "show_thinking": "ui/show_thinking",
               "python": "lab/python", "timeout": "lab/timeout", "auto_run": "lab/auto_run"}
    for k, qk in mapping.items():
        if k in values:
            s.setValue(qk, values[k])


def default_python():
    """实验用的解释器：打包后 sys.executable 是应用本身，得找系统的 python3。"""
    if getattr(sys, "frozen", False):
        return shutil.which("python3") or shutil.which("python") or ""
    return sys.executable


class SettingsDialog(QDialog):

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("设置")
        self.setMinimumWidth(560)
        cur = load_settings()
        lay = QVBoxLayout(self)

        ai = QGroupBox("Claude")
        form = QFormLayout(ai)
        self.key_edit = QLineEdit(cur["api_key"])
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_edit.setPlaceholderText("留空则使用环境变量 ANTHROPIC_API_KEY")
        form.addRow("Anthropic API Key:", self.key_edit)
        self.model_combo = QComboBox()
        self.model_combo.setEditable(True)
        self.model_combo.addItems(agent.MODEL_CHOICES)
        self.model_combo.setCurrentText(cur["model"])
        form.addRow("模型:", self.model_combo)
        self.effort_combo = QComboBox()
        self.effort_combo.addItems(agent.EFFORT_CHOICES)
        self.effort_combo.setCurrentText(cur["effort"])
        self.effort_combo.setToolTip("思考深度：越高越仔细、越慢、越贵。读论文 high 足够，难推导可以用 xhigh/max")
        form.addRow("思考深度:", self.effort_combo)
        self.url_edit = QLineEdit(cur["base_url"])
        self.url_edit.setPlaceholderText("可选，默认官方地址")
        form.addRow("Base URL:", self.url_edit)
        self.thinking_cb = QCheckBox("在对话里显示 Claude 的思考摘要")
        self.thinking_cb.setChecked(cur["show_thinking"])
        form.addRow("", self.thinking_cb)
        lay.addWidget(ai)

        learn = QGroupBox("学习（对新建的对话生效）")
        lf = QFormLayout(learn)
        self.bg_edit = QPlainTextEdit(cur["background"])
        self.bg_edit.setPlaceholderText("例如：研一，做 CV；熟悉 PyTorch 和基础深度学习，概率论一般，没学过强化学习")
        self.bg_edit.setFixedHeight(70)
        lf.addRow("我的背景:", self.bg_edit)
        self.web_cb = QCheckBox("允许 Claude 联网搜索相关工作（web search / fetch，会产生额外费用）")
        self.web_cb.setChecked(cur["web_tools"])
        lf.addRow("", self.web_cb)
        lay.addWidget(learn)

        lab = QGroupBox("实验")
        xf = QFormLayout(lab)
        row = QHBoxLayout()
        self.py_edit = QLineEdit(cur["python"])
        self.py_edit.setPlaceholderText(f"默认：{default_python()}")
        row.addWidget(self.py_edit, 1)
        browse = QPushButton("选择…")
        browse.clicked.connect(self._browse_python)
        row.addWidget(browse)
        xf.addRow("Python 解释器:", row)
        self.timeout_spin = QSpinBox()
        self.timeout_spin.setRange(10, 3600)
        self.timeout_spin.setSuffix(" 秒")
        self.timeout_spin.setValue(cur["timeout"])
        xf.addRow("单次运行超时:", self.timeout_spin)
        self.auto_cb = QCheckBox("Claude 写的实验代码直接运行，不再逐次确认")
        self.auto_cb.setChecked(cur["auto_run"])
        xf.addRow("", self.auto_cb)
        warn = QLabel("实验代码以你的用户权限在本机运行（能读写文件、联网）。"
                      "不确定时保持逐次确认，先看一眼代码再运行。")
        warn.setWordWrap(True)
        warn.setStyleSheet("color: gray;")
        xf.addRow("", warn)
        lay.addWidget(lab)

        note = QLabel(("Key 保存在系统钥匙串里。" if keyring is not None else "Key 明文保存在本机应用设置里。")
                      + "提问时会把当前论文 PDF 和对话内容发送给 Anthropic API。")
        note.setWordWrap(True)
        note.setStyleSheet("color: gray;")
        lay.addWidget(note)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok
                                   | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def _browse_python(self):
        path, _ = QFileDialog.getOpenFileName(self, "选择 Python 解释器")
        if path:
            self.py_edit.setText(path)

    def values(self):
        return {
            "api_key": self.key_edit.text().strip(),
            "model": self.model_combo.currentText().strip() or agent.DEFAULT_MODEL,
            "effort": self.effort_combo.currentText(),
            "base_url": self.url_edit.text().strip(),
            "show_thinking": self.thinking_cb.isChecked(),
            "background": self.bg_edit.toPlainText().strip(),
            "web_tools": self.web_cb.isChecked(),
            "python": self.py_edit.text().strip(),
            "timeout": self.timeout_spin.value(),
            "auto_run": self.auto_cb.isChecked(),
        }

    def accept(self):
        save_settings(self.values())
        super().accept()
