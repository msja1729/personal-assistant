"""Output: show the reply in a notification card and speak it seamlessly.

Text streams in from Claude in small pieces. Each complete sentence is
cleaned for speech and queued on the OS speech engine straight away, while
the previous sentence is still playing, so speech flows without gaps.
"""

from __future__ import annotations

import logging
import re

from PySide6.QtCore import QEasingCurve, QPropertyAnimation, QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPainterPath
from PySide6.QtWidgets import QLabel, QVBoxLayout, QWidget

from src.platform_support import Speaker

log = logging.getLogger(__name__)

_SENTENCE_END = re.compile(r"(?<=[.!?…])[\"')\]]*\s+|\n{2,}|\n(?=\s*[-*•\d])")
_ABBREVIATIONS = {"e.g.", "i.e.", "mr.", "mrs.", "ms.", "dr.", "vs.", "etc.", "st.", "no."}


def split_sentences(buffer: str) -> tuple[list[str], str]:
    """Split off complete sentences; return (sentences, unfinished remainder)."""
    sentences: list[str] = []
    start = 0
    for match in _SENTENCE_END.finditer(buffer):
        candidate = buffer[start : match.end()].strip()
        last_word = candidate.rsplit(None, 1)[-1].lower() if candidate else ""
        if last_word in _ABBREVIATIONS or re.fullmatch(r"\d+\.", candidate):
            continue  # "e.g." or a list marker like "3." is not the end of a sentence
        if candidate:
            sentences.append(candidate)
        start = match.end()
    return sentences, buffer[start:]


def clean_for_speech(text: str) -> str:
    """Remove markdown and symbols that sound bad when read aloud."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)  # code blocks
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", text)  # images
    text = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)  # links -> label
    text = re.sub(r"https?://\S+", "a link", text)
    text = re.sub(r"\$\$?([^$]+)\$\$?", r"\1", text)  # inline math
    text = re.sub(r"^\s{0,3}#{1,6}\s*", "", text, flags=re.M)  # headings
    text = re.sub(r"^\s*(?:[-*•+]|\d+[.)])\s+", "", text, flags=re.M)  # list markers
    text = re.sub(r"(\*\*|__|\*|_|~~)(?=\S)(.+?)(?<=\S)\1", r"\2", text)  # emphasis
    text = text.replace("|", " ").replace("#", " ")
    return re.sub(r"\s+", " ", text).strip()


class SpeechStream:
    """Feeds streamed reply text to the speaker one sentence at a time."""

    def __init__(self, speaker: Speaker, enabled: bool) -> None:
        self._speaker = speaker
        self._enabled = enabled
        self._buffer = ""

    def feed(self, text: str) -> None:
        if not self._enabled:
            return
        self._buffer += text
        sentences, self._buffer = split_sentences(self._buffer)
        for s in sentences:
            self._say(s)

    def flush(self) -> None:
        if self._enabled and self._buffer.strip():
            self._say(self._buffer)
        self._buffer = ""

    def _say(self, sentence: str) -> None:
        spoken = clean_for_speech(sentence)
        if spoken:
            self._speaker.say(spoken)


# ======================================================== notification card

ACCENT = QColor(229, 214, 189)  # warm beige from the design reference
TEXT = QColor(242, 240, 236)
MUTED = QColor(170, 166, 160)


class NotificationCard(QWidget):
    """A glass-style card in the bottom-right corner showing the live reply."""

    WIDTH = 400
    LINGER_MS = 6000

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedWidth(self.WIDTH)

        self._title = QLabel("Jarvis")
        self._title.setStyleSheet(f"color: {MUTED.name()}; font-size: 11px; letter-spacing: 2px;")
        self._body = QLabel("")
        self._body.setWordWrap(True)
        self._body.setTextInteractionFlags(Qt.TextInteractionFlag.NoTextInteraction)
        self._body.setStyleSheet(f"color: {TEXT.name()}; font-size: 14px;")
        font = QFont(self._body.font())
        font.setPointSizeF(11.5)
        self._body.setFont(font)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 18)
        layout.setSpacing(6)
        layout.addWidget(self._title)
        layout.addWidget(self._body)

        self._fade = QPropertyAnimation(self, b"windowOpacity", self)
        self._fade.setDuration(450)
        self._fade.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._fade.finished.connect(self._after_fade)
        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.fade_out)
        self._text = ""

    # -- content
    def start(self, status: str) -> None:
        self._hide_timer.stop()
        self._fade.stop()
        self._text = ""
        self._title.setText(status.upper())
        self._body.setText("…")
        self._place()
        self.setWindowOpacity(1.0)
        self.show()
        self.raise_()

    def set_status(self, status: str) -> None:
        self._title.setText(status.upper())

    def append(self, text: str) -> None:
        self._text += text
        shown = self._text.strip()
        if len(shown) > 700:
            shown = "…" + shown[-700:]
        self._body.setText(shown)
        self._place()

    def show_message(self, status: str, text: str) -> None:
        self.start(status)
        self.append(text)

    def text(self) -> str:
        return self._text

    def linger(self) -> None:
        """Start the countdown to fade out (called when speech has finished)."""
        self._hide_timer.start(self.LINGER_MS)

    def fade_out(self) -> None:
        self._fade.stop()
        self._fade.setStartValue(self.windowOpacity())
        self._fade.setEndValue(0.0)
        self._fade.start()

    def _after_fade(self) -> None:
        if self.windowOpacity() < 0.05:
            self.hide()

    def _place(self) -> None:
        self.adjustSize()
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        area = screen.availableGeometry()
        self.move(area.right() - self.width() - 24, area.bottom() - self.height() - 24)

    # -- glass look
    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = QRectF(self.rect()).adjusted(1, 1, -1, -1)
        path = QPainterPath()
        path.addRoundedRect(rect, 22, 22)
        painter.fillPath(path, QColor(28, 26, 24, 228))
        painter.setPen(QColor(255, 255, 255, 38))
        painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(ACCENT)
        painter.drawRoundedRect(QRectF(20, 10, 28, 3), 1.5, 1.5)
