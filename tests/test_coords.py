import math

import pytest

from src.overlay import ScreenGeometry, check_unit, font_px, size_to_logical, to_logical, to_physical

FHD = ScreenGeometry(0, 0, 1920, 1080, 1.0)
WIN150 = ScreenGeometry(0, 0, 1280, 720, 1.5)  # 1920x1080 panel at 150 %
RETINA = ScreenGeometry(0, 0, 1512, 982, 2.0)  # MacBook Pro 14" default


def test_center_standard():
    assert to_logical(0.5, 0.5, FHD) == (960, 540)
    assert to_physical(0.5, 0.5, FHD) == (960, 540)


def test_corners():
    assert to_logical(0, 0, FHD) == (0, 0)
    assert to_logical(1, 1, FHD) == (1920, 1080)


def test_windows_150_percent():
    assert to_logical(0.5, 0.5, WIN150) == (640, 360)
    assert to_physical(0.5, 0.5, WIN150) == (960, 540)
    assert to_physical(1, 1, WIN150) == (1920, 1080)


def test_retina():
    assert to_physical(0.25, 0.5, RETINA) == (756, 982)


def test_secondary_monitor_offset():
    right = ScreenGeometry(1920, 0, 2560, 1440, 1.25)
    assert to_logical(0.5, 0.5, right) == (3200, 720)
    assert to_physical(0, 0, right) == (2400, 0)


def test_sizes():
    assert size_to_logical(0.5, 0.25, FHD) == (960, 270)


@pytest.mark.parametrize("value", [-0.01, 1.01, float("nan"), "0.5", None, True])
def test_rejects_bad_values(value):
    with pytest.raises(ValueError):
        check_unit("x", value)


def test_font_size():
    assert font_px(None, FHD, 0.05) == pytest.approx(54)
    assert font_px(0.1, FHD) == pytest.approx(108)
    assert font_px(32, FHD) == 32
    with pytest.raises(ValueError):
        font_px(0, FHD)


def _ink_center(image):
    """Centre of the drawn (non-transparent) pixels of a QImage, in image pixels."""
    xs, ys = [], []
    for y in range(image.height()):
        for x in range(image.width()):
            if image.pixelColor(x, y).alpha() > 40:
                xs.append(x)
                ys.append(y)
    return (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2


@pytest.mark.parametrize("dpr", [1.0, 1.5, 2.0])
def test_latex_fraction_is_centred(qapp, dpr):
    """Spec: \\frac{n(n+1)}{2} at (0.5, 0.5) is centred within 2 % of the point, at any scaling."""
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QColor

    from src.overlay import render_latex

    geom = ScreenGeometry(0, 0, 1920 / dpr, 1080 / dpr, dpr)
    image = render_latex(r"\frac{n(n+1)}{2}", font_px(0.06, geom), QColor("white"), dpr)
    w, h = image.width() / dpr, image.height() / dpr
    cx, cy = to_logical(0.5, 0.5, geom)
    rect = QRectF(cx - w / 2, cy - h / 2, w, h)  # placed exactly as Overlay.write_latex does
    corner = image.pixelColor(0, 0).alpha()
    assert corner == 0, "LaTeX must be drawn on a transparent background"
    ix, iy = _ink_center(image)
    assert math.isclose(rect.left() + ix / dpr, cx, abs_tol=0.02 * geom.width)
    assert math.isclose(rect.top() + iy / dpr, cy, abs_tol=0.02 * geom.height)


def test_overlay_places_latex_centred(qapp):
    """End-to-end through the Overlay on the (offscreen) primary screen."""
    from src.overlay import Overlay

    overlay = Overlay()
    result = overlay.write_latex(r"\frac{n(n+1)}{2}", 0.5, 0.5)
    assert result["ok"]
    rect = overlay.item_rect(result["id"])
    g = overlay.geometry()
    assert math.isclose(rect.center().x(), g.width / 2, abs_tol=0.02 * g.width)
    assert math.isclose(rect.center().y(), g.height / 2, abs_tol=0.02 * g.height)
    assert overlay.is_open()
    assert overlay.close() == {"ok": True}
    assert not overlay.is_open()
