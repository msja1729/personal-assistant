"""Overlay: draws the pen tools' output on a glass board or over the screen.

The window covers the primary screen, stays on top and is click-through, so
the user can keep working underneath. Every item is drawn with a short
handwriting-style animation, one after another, like someone writing.
"""

from __future__ import annotations

import io
import itertools
import logging
import math
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from PySide6.QtCore import QPointF, QRect, QRectF, Qt, QTimer
from PySide6.QtGui import (
    QColor,
    QFont,
    QFontMetricsF,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
)
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget

log = logging.getLogger(__name__)

# ============================================================ coordinates


@dataclass(frozen=True)
class ScreenGeometry:
    """A monitor's rectangle in logical (device-independent) pixels and its scale factor."""

    left: float
    top: float
    width: float
    height: float
    device_pixel_ratio: float = 1.0


def check_unit(name: str, value: Any) -> float:
    """Validate one normalized coordinate (must be a number in [0, 1])."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or math.isnan(float(value)):
        raise ValueError(f"{name} must be a number between 0 and 1")
    v = float(value)
    if not 0.0 <= v <= 1.0:
        raise ValueError(f"{name}={v} is outside 0..1")
    return v


def to_logical(nx: float, ny: float, geom: ScreenGeometry) -> tuple[float, float]:
    """Normalized (0..1) point -> logical pixels in desktop coordinates."""
    x = check_unit("x", nx)
    y = check_unit("y", ny)
    return geom.left + x * geom.width, geom.top + y * geom.height


def to_physical(nx: float, ny: float, geom: ScreenGeometry) -> tuple[int, int]:
    """Normalized point -> physical device pixels (what a screenshot uses)."""
    lx, ly = to_logical(nx, ny, geom)
    r = geom.device_pixel_ratio
    return round(lx * r), round(ly * r)


def size_to_logical(nw: float, nh: float, geom: ScreenGeometry) -> tuple[float, float]:
    return check_unit("w", nw) * geom.width, check_unit("h", nh) * geom.height


def font_px(size: float | None, geom: ScreenGeometry, default: float = 0.045) -> float:
    """Text size: a fraction of screen height (<= 1) or logical pixels (> 1)."""
    if size is None:
        size = default
    if size <= 0:
        raise ValueError("size must be positive")
    return size * geom.height if size <= 1 else float(size)


# ================================================================== items

INK_GLASS = QColor(246, 241, 230)
INK_SCREEN = QColor(255, 94, 58)
HIGHLIGHT = QColor(255, 214, 10, 90)


def handwriting_font(px: float) -> QFont:
    family = "Segoe Print" if sys.platform == "win32" else "Bradley Hand" if sys.platform == "darwin" else "Comic Sans MS"
    font = QFont(family)
    font.setPixelSize(max(8, round(px)))
    return font


@dataclass
class Item:
    id: str
    start: float
    duration: float
    kind: str
    data: dict[str, Any] = field(default_factory=dict)

    def progress(self, now: float) -> float:
        if now <= self.start:
            return 0.0
        return min(1.0, (now - self.start) / self.duration)


def _stroke_partial(painter: QPainter, path: QPainterPath, p: float) -> QPointF | None:
    """Draw the first ``p`` fraction of ``path``; return the pen tip position."""
    if p <= 0:
        return None
    steps = max(8, int(path.length() / 4))
    n = max(2, int(steps * p))
    pts = [path.pointAtPercent(min(1.0, p * i / (n - 1))) for i in range(n)]
    poly = QPainterPath(pts[0])
    for pt in pts[1:]:
        poly.lineTo(pt)
    painter.drawPath(poly)
    return pts[-1]


class BoardWindow(QWidget):
    """The full-screen, click-through drawing surface."""

    def __init__(self) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowTransparentForInput
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.mode = "screen"
        self.items: list[Item] = []
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def ink(self) -> QColor:
        return INK_GLASS if self.mode == "glass" else INK_SCREEN

    def _tick(self) -> None:
        self.update()
        now = time.monotonic()
        if all(i.progress(now) >= 1.0 for i in self.items):
            self._timer.stop()

    def animate(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self.mode == "glass":
            painter.fillRect(self.rect(), QColor(16, 18, 22, 178))
            painter.setPen(QPen(QColor(255, 255, 255, 30), 2))
            painter.drawRect(QRectF(self.rect()).adjusted(1, 1, -1, -1))
        now = time.monotonic()
        for item in self.items:
            p = item.progress(now)
            if p > 0:
                self._draw(painter, item, p)

    # -- drawing each kind
    def _pen(self, width: float = 4.0) -> QPen:
        pen = QPen(self.ink(), width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def _draw(self, painter: QPainter, item: Item, p: float) -> None:
        d = item.data
        if item.kind == "text":
            self._draw_reveal_path(painter, d["path"], d["rect"], p)
        elif item.kind == "latex":
            image: QImage = d["image"]
            rect: QRectF = d["rect"]
            painter.save()
            painter.setClipRect(QRectF(rect.left(), rect.top() - 4, rect.width() * p, rect.height() + 8))
            painter.drawImage(rect, image)
            painter.restore()
            if p < 1:
                self._draw_tip(painter, QPointF(rect.left() + rect.width() * p, rect.center().y()))
        elif item.kind in ("arrow", "shape"):
            painter.setPen(self._pen(d.get("width", 4.0)))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            tip = _stroke_partial(painter, d["path"], min(1.0, p / 0.85) if item.kind == "arrow" else p)
            if item.kind == "arrow" and p >= 0.85:
                painter.setBrush(self.ink())
                painter.drawPath(d["head"])
            if tip is not None and p < 1:
                self._draw_tip(painter, tip)
            if d.get("label") is not None and p >= 1:
                self._draw_reveal_path(painter, d["label"], d["label_rect"], 1.0)
        elif item.kind == "highlight":
            rect = d["rect"]
            painter.fillRect(QRectF(rect.left(), rect.top(), rect.width() * p, rect.height()), HIGHLIGHT)

    def _draw_reveal_path(self, painter: QPainter, path: QPainterPath, rect: QRectF, p: float) -> None:
        painter.save()
        painter.setClipRect(QRectF(rect.left() - 6, rect.top() - 10, (rect.width() + 12) * p, rect.height() + 20))
        if self.mode == "screen":
            painter.setPen(QPen(QColor(20, 20, 20, 170), 3))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawPath(path)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self.ink())
        painter.drawPath(path)
        painter.restore()
        if p < 1:
            self._draw_tip(painter, QPointF(rect.left() + rect.width() * p, rect.center().y()))

    def _draw_tip(self, painter: QPainter, at: QPointF) -> None:
        painter.save()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(255, 255, 255, 200))
        painter.drawEllipse(at, 4, 4)
        painter.restore()


class CloseChip(QWidget):
    """Small clickable 'Close board' pill, since the board itself ignores clicks."""

    def __init__(self, on_close) -> None:  # noqa: ANN001
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool
            | Qt.WindowType.WindowDoesNotAcceptFocus,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 6, 6, 6)
        label = QLabel("Jarvis is drawing")
        label.setStyleSheet("color: #e5d6bd; font-size: 12px;")
        button = QPushButton("Close  ✕")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setStyleSheet(
            "QPushButton { color: #1c1a18; background: #e5d6bd; border: none; border-radius: 12px;"
            " padding: 4px 12px; font-size: 12px; } QPushButton:hover { background: #f3e8d4; }"
        )
        button.clicked.connect(on_close)
        layout.addWidget(label)
        layout.addWidget(button)

    def paintEvent(self, event) -> None:  # noqa: ANN001, N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 18, 18)
        painter.fillPath(path, QColor(28, 26, 24, 235))
        painter.setPen(QColor(255, 255, 255, 40))
        painter.drawPath(path)


class Overlay:
    """Executes board_* commands. All methods run on the GUI thread."""

    def __init__(self) -> None:
        self.window = BoardWindow()
        self.chip = CloseChip(self.close)
        self._ids = itertools.count(1)

    # -- helpers
    def geometry(self) -> ScreenGeometry:
        screen = QGuiApplication.primaryScreen()
        g = screen.geometry()
        return ScreenGeometry(g.x(), g.y(), g.width(), g.height(), screen.devicePixelRatio())

    def _local(self, nx: float, ny: float) -> QPointF:
        g = self.geometry()
        lx, ly = to_logical(nx, ny, g)
        return QPointF(lx - g.left, ly - g.top)

    def _add(self, kind: str, data: dict[str, Any], duration: float) -> dict[str, Any]:
        if not self.window.isVisible():
            self.open("screen")
        now = time.monotonic()
        start = max([now] + [i.start + i.duration for i in self.window.items])
        item = Item(id=f"i{next(self._ids)}", start=start, duration=duration, kind=kind, data=data)
        self.window.items.append(item)
        self.window.animate()
        return {"ok": True, "id": item.id}

    def _text_path(self, text: str, center: QPointF, px: float) -> tuple[QPainterPath, QRectF]:
        font = handwriting_font(px)
        metrics = QFontMetricsF(font)
        lines = text.splitlines() or [""]
        line_h = metrics.lineSpacing()
        total_h = line_h * len(lines)
        path = QPainterPath()
        top = center.y() - total_h / 2
        widest = 0.0
        for n, line in enumerate(lines):
            w = metrics.horizontalAdvance(line)
            widest = max(widest, w)
            path.addText(QPointF(center.x() - w / 2, top + n * line_h + metrics.ascent()), font, line)
        rect = QRectF(center.x() - widest / 2, top, widest, total_h)
        return path, rect

    # -- commands
    def open(self, mode: str) -> dict[str, Any]:
        if mode not in ("glass", "screen"):
            return {"ok": False, "error": "mode must be 'glass' or 'screen'"}
        screen = QGuiApplication.primaryScreen()
        self.window.mode = mode
        self.window.setGeometry(screen.geometry())
        self.window.show()
        self.window.raise_()
        self.chip.adjustSize()
        g = screen.availableGeometry()
        self.chip.move(g.center().x() - self.chip.width() // 2, g.top() + 16)
        self.chip.show()
        self.chip.raise_()
        self.window.update()
        return {"ok": True}

    def write_text(self, text: str, x: float, y: float, size: float | None = None) -> dict[str, Any]:
        center = self._local(x, y)
        px = font_px(size, self.geometry())
        path, rect = self._text_path(str(text), center, px)
        duration = min(4.0, 0.35 + 0.045 * len(str(text)))
        return self._add("text", {"path": path, "rect": rect}, duration)

    def write_latex(self, latex: str, x: float, y: float, size: float | None = None) -> dict[str, Any]:
        g = self.geometry()
        center = self._local(x, y)
        px = font_px(size, g, default=0.06)
        image = render_latex(latex, px, self.window.ink(), g.device_pixel_ratio)
        w = image.width() / g.device_pixel_ratio
        h = image.height() / g.device_pixel_ratio
        rect = QRectF(center.x() - w / 2, center.y() - h / 2, w, h)
        return self._add("latex", {"image": image, "rect": rect}, min(3.0, 0.6 + 0.02 * len(latex)))

    def draw_arrow(self, x1: float, y1: float, x2: float, y2: float, label: str | None = None) -> dict[str, Any]:
        a = self._local(x1, y1)
        b = self._local(x2, y2)
        path = QPainterPath(a)
        path.lineTo(b)
        angle = math.atan2(b.y() - a.y(), b.x() - a.x())
        head_len = 22.0
        head = QPainterPath(b)
        for side in (-1, 1):
            head.lineTo(
                QPointF(
                    b.x() - head_len * math.cos(angle + side * math.radians(26)),
                    b.y() - head_len * math.sin(angle + side * math.radians(26)),
                )
            )
        head.closeSubpath()
        data: dict[str, Any] = {"path": path, "head": head, "width": 4.5}
        if label:
            mid = QPointF((a.x() + b.x()) / 2, (a.y() + b.y()) / 2 - 26)
            data["label"], data["label_rect"] = self._text_path(label, mid, font_px(None, self.geometry(), 0.028))
        return self._add("arrow", data, 0.7)

    def draw_shape(
        self, shape: str, x: float, y: float, w: float, h: float, label: str | None = None
    ) -> dict[str, Any]:
        if shape not in ("box", "circle", "line"):
            return {"ok": False, "error": "shape must be 'box', 'circle' or 'line'"}
        g = self.geometry()
        tl = self._local(x, y)
        lw, lh = size_to_logical(w, h, g)
        rect = QRectF(tl.x(), tl.y(), lw, lh)
        path = QPainterPath()
        if shape == "box":
            path.addRoundedRect(rect, 10, 10)
        elif shape == "circle":
            path.addEllipse(rect)
        else:
            path.moveTo(rect.topLeft())
            path.lineTo(rect.bottomRight())
        data: dict[str, Any] = {"path": path, "width": 4.0}
        if label:
            center = rect.center() if shape != "line" else QPointF(rect.center().x(), rect.center().y() - 26)
            data["label"], data["label_rect"] = self._text_path(label, center, font_px(None, g, 0.028))
        return self._add("shape", data, 0.9)

    def highlight(self, x: float, y: float, w: float, h: float) -> dict[str, Any]:
        g = self.geometry()
        tl = self._local(x, y)
        lw, lh = size_to_logical(w, h, g)
        return self._add("highlight", {"rect": QRectF(tl.x(), tl.y(), lw, lh)}, 0.5)

    def clear(self) -> dict[str, Any]:
        self.window.items.clear()
        self.window.update()
        return {"ok": True}

    def close(self) -> dict[str, Any]:
        self.window.items.clear()
        self.window.hide()
        self.chip.hide()
        return {"ok": True}

    def is_open(self) -> bool:
        return self.window.isVisible()

    def item_rect(self, item_id: str) -> QRectF | None:
        """Logical-pixel rectangle of an item (used by tests and checks)."""
        for item in self.window.items:
            if item.id == item_id:
                return item.data.get("rect") or item.data["path"].boundingRect()
        return None


def render_latex(latex: str, px: float, color: QColor, dpr: float = 1.0) -> QImage:
    """Render LaTeX math to a transparent image, ``px`` logical pixels per em."""
    from matplotlib.figure import Figure

    expr = latex.strip()
    if not (expr.startswith("$") and expr.endswith("$")):
        expr = f"${expr}$"
    fig = Figure(figsize=(1, 1))
    fig.text(0, 0, expr, fontsize=px, color=color.name())
    buf = io.BytesIO()
    try:
        fig.savefig(buf, dpi=72.0 * dpr, format="png", transparent=True, bbox_inches="tight", pad_inches=0.04)
    except ValueError as exc:  # matplotlib's message for bad LaTeX
        raise ValueError(f"could not render LaTeX: {exc}") from exc
    image = QImage.fromData(buf.getvalue(), "PNG")
    if image.isNull():
        raise ValueError(f"could not render LaTeX: {latex}")
    image.setDevicePixelRatio(dpr)
    return image
