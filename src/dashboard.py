"""Dashboard: shows status and switches every feature on or off.

Every change is written to config.yaml straight away. The app and the senses
server re-read that file, so changes apply on the next request without a
restart.
"""

from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath
from PySide6.QtWidgets import (
    QAbstractButton,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from src import APP_NAME, __version__, config
from src.platform_support import open_url, parse_hotkey

CONNECTORS_URL = "https://claude.ai/settings/connectors"

STYLE = """
QWidget#root { background: #141311; }
QLabel { color: #f2f0ec; font-size: 13px; }
QLabel[role="muted"] { color: #a9a49c; font-size: 11px; }
QLabel[role="title"] { font-size: 22px; font-weight: 600; }
QLabel[role="eyebrow"] { color: #a9a49c; font-size: 11px; letter-spacing: 2px; }
QFrame[role="card"] { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.09);
                      border-radius: 18px; }
QLineEdit, QComboBox, QSpinBox { background: rgba(255,255,255,0.07); color: #f2f0ec;
    border: 1px solid rgba(255,255,255,0.12); border-radius: 10px; padding: 6px 10px; font-size: 13px; }
QLineEdit:focus, QComboBox:focus, QSpinBox:focus { border-color: #e5d6bd; }
QComboBox QAbstractItemView { background: #23211e; color: #f2f0ec; selection-background-color: #4a4339; }
QPushButton { color: #f2f0ec; background: rgba(255,255,255,0.08); border: 1px solid rgba(255,255,255,0.12);
              border-radius: 16px; padding: 8px 18px; font-size: 13px; }
QPushButton:hover { background: rgba(255,255,255,0.14); }
QPushButton[role="primary"] { color: #1c1a18; background: #e5d6bd; border: none; font-weight: 600; }
QPushButton[role="primary"]:hover { background: #f3e8d4; }
"""


class Switch(QAbstractButton):
    """A pill-shaped on/off switch."""

    def __init__(self, checked: bool) -> None:
        super().__init__()
        self.setCheckable(True)
        self.setChecked(checked)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(46, 26)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QPainterPath()
        track.addRoundedRect(QRectF(0, 0, 46, 26), 13, 13)
        p.fillPath(track, QColor(229, 214, 189) if self.isChecked() else QColor(255, 255, 255, 40))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(255, 255, 255) if self.isChecked() else QColor(210, 206, 200))
        x = 23 if self.isChecked() else 3
        p.drawEllipse(QRectF(x, 3, 20, 20))


class StatusPill(QLabel):
    COLORS = {
        "idle": "#a9a49c",
        "listening": "#7dd3a8",
        "thinking": "#e5d6bd",
        "speaking": "#8fb8ff",
        "error": "#ff8a7a",
    }

    def set_status(self, status: str, detail: str = "") -> None:
        color = self.COLORS.get(status, "#a9a49c")
        self.setText(f"●  {status.capitalize()}" + (f" — {detail}" if detail else ""))
        self.setStyleSheet(
            f"color: {color}; background: rgba(255,255,255,0.06); border: 1px solid rgba(255,255,255,0.1);"
            " border-radius: 13px; padding: 4px 12px; font-size: 12px;"
        )


class Dashboard(QWidget):
    listen_requested = Signal()
    new_conversation_requested = Signal()
    close_board_requested = Signal()

    TOGGLES = [
        ("wake_word_enabled", "Wake word", "Say the wake word to start talking"),
        ("speak_aloud", "Speak replies", "Read Claude's answers out loud"),
        ("show_notifications", "Notifications", "Show each reply in a card"),
        ("screen_access", "Screen access", "Let Claude take a screenshot when it helps"),
        ("drawing", "Drawing", "Let Claude write and draw on a board or the screen"),
    ]

    def __init__(self, on_change: Callable[[config.Config], None] | None = None) -> None:
        super().__init__()
        self.setObjectName("root")
        self.setWindowTitle(APP_NAME)
        self.setStyleSheet(STYLE)
        self.setMinimumWidth(520)
        self._on_change = on_change
        self._cfg = config.load()
        self._switches: dict[str, Switch] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 24)
        outer.setSpacing(16)

        eyebrow = QLabel(f"{APP_NAME.upper()} · v{__version__}")
        eyebrow.setProperty("role", "eyebrow")
        title = QLabel("Your voice assistant for Claude")
        title.setProperty("role", "title")
        self.status = StatusPill()
        self.status.set_status("idle")
        head = QHBoxLayout()
        head_text = QVBoxLayout()
        head_text.addWidget(eyebrow)
        head_text.addWidget(title)
        head.addLayout(head_text, 1)
        head.addWidget(self.status, 0, Qt.AlignmentFlag.AlignTop)
        outer.addLayout(head)

        # toggles
        card = self._card()
        grid = QGridLayout(card)
        grid.setContentsMargins(20, 16, 20, 16)
        grid.setVerticalSpacing(12)
        for row, (key, label, hint) in enumerate(self.TOGGLES):
            text = QVBoxLayout()
            text.setSpacing(1)
            name = QLabel(label)
            sub = QLabel(hint)
            sub.setProperty("role", "muted")
            text.addWidget(name)
            text.addWidget(sub)
            switch = Switch(bool(getattr(self._cfg, key)))
            switch.toggled.connect(lambda checked, k=key: self._set(k, checked))
            self._switches[key] = switch
            grid.addLayout(text, row, 0)
            grid.addWidget(switch, row, 1, Qt.AlignmentFlag.AlignRight)
        outer.addWidget(card)

        # voice settings
        card2 = self._card()
        form = QGridLayout(card2)
        form.setContentsMargins(20, 16, 20, 16)
        form.setVerticalSpacing(10)
        self.wake_word = QLineEdit(self._cfg.wake_word)
        self.wake_word.editingFinished.connect(lambda: self._set("wake_word", self.wake_word.text().strip()))
        self.hotkey = QLineEdit(self._cfg.hotkey)
        self.hotkey.setPlaceholderText("<ctrl>+<shift>+<space>")
        self.hotkey.editingFinished.connect(self._set_hotkey)
        self.hotkey_error = QLabel("")
        self.hotkey_error.setProperty("role", "muted")
        self.stt_model = QComboBox()
        self.stt_model.addItems(["tiny.en", "base.en", "small.en", "medium.en", "small", "medium"])
        if self.stt_model.findText(self._cfg.stt_model) < 0:
            self.stt_model.addItem(self._cfg.stt_model)
        self.stt_model.setCurrentText(self._cfg.stt_model)
        self.stt_model.currentTextChanged.connect(lambda v: self._set("stt_model", v))
        self.silence = QSpinBox()
        self.silence.setRange(300, 5000)
        self.silence.setSingleStep(100)
        self.silence.setSuffix(" ms")
        self.silence.setValue(self._cfg.silence_timeout_ms)
        self.silence.editingFinished.connect(lambda: self._set("silence_timeout_ms", self.silence.value()))
        rows = [
            ("Wake word", self.wake_word),
            ("Hotkey", self.hotkey),
            ("Speech model", self.stt_model),
            ("Stop after silence", self.silence),
        ]
        for r, (label, widget) in enumerate(rows):
            form.addWidget(QLabel(label), r, 0)
            form.addWidget(widget, r, 1)
        form.addWidget(self.hotkey_error, len(rows), 1)
        outer.addWidget(card2)

        # actions
        actions = QHBoxLayout()
        listen = QPushButton("Listen now")
        listen.setProperty("role", "primary")
        listen.clicked.connect(self.listen_requested.emit)
        new_chat = QPushButton("New conversation")
        new_chat.clicked.connect(self.new_conversation_requested.emit)
        close_board = QPushButton("Close board")
        close_board.clicked.connect(self.close_board_requested.emit)
        connectors = QPushButton("Connectors…")
        connectors.clicked.connect(lambda: open_url(CONNECTORS_URL))
        for b in (listen, new_chat, close_board, connectors):
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            actions.addWidget(b)
        outer.addLayout(actions)

        foot = QLabel("Settings are saved to config.yaml and apply on the next request.")
        foot.setProperty("role", "muted")
        outer.addWidget(foot)

    def _card(self) -> QFrame:
        frame = QFrame()
        frame.setProperty("role", "card")
        return frame

    def _set(self, key: str, value: object) -> None:
        self._cfg = config.load()  # pick up edits made elsewhere
        if getattr(self._cfg, key) == value:
            return
        setattr(self._cfg, key, value)
        config.save(self._cfg)
        if self._on_change is not None:
            self._on_change(self._cfg)

    def _set_hotkey(self) -> None:
        value = self.hotkey.text().strip()
        try:
            parse_hotkey(value)
        except ValueError as exc:
            self.hotkey_error.setText(str(exc))
            return
        self.hotkey_error.setText("")
        self._set("hotkey", value)

    def reload(self) -> None:
        """Refresh the controls from config.yaml (after an outside edit)."""
        self._cfg = config.load()
        for key, switch in self._switches.items():
            switch.blockSignals(True)
            switch.setChecked(bool(getattr(self._cfg, key)))
            switch.blockSignals(False)
            switch.update()
        for widget, value in (
            (self.wake_word, self._cfg.wake_word),
            (self.hotkey, self._cfg.hotkey),
        ):
            if not widget.hasFocus():
                widget.setText(value)
        self.stt_model.blockSignals(True)
        self.stt_model.setCurrentText(self._cfg.stt_model)
        self.stt_model.blockSignals(False)
        self.silence.blockSignals(True)
        self.silence.setValue(self._cfg.silence_timeout_ms)
        self.silence.blockSignals(False)
