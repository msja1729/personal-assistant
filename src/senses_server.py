"""Senses MCP server (stdio): screen capture and the pen tools for Claude Code.

Claude Code starts this process itself. Every tool re-reads ``config.yaml``
first and refuses with ``{"ok": false, "error": "<feature> disabled by user"}``
when its feature is switched off — this check is in code, never left to the
model. Pen commands are forwarded to the running app, which owns the overlay.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path
from typing import Any, Callable

if __package__ in (None, ""):  # started as a script by Claude Code
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from mcp.server.fastmcp import FastMCP, Image  # noqa: E402

from src import config, ipc  # noqa: E402
from src.overlay import check_unit  # noqa: E402

mcp = FastMCP("senses")

MAX_IMAGE_EDGE = 1568  # larger screenshots are scaled down to save tokens


def disabled(feature: str) -> dict[str, Any]:
    return {"ok": False, "error": f"{feature} disabled by user"}


def feature_enabled(feature: str) -> bool:
    """Fresh read of the toggle on every call, so dashboard changes apply at once."""
    return bool(getattr(config.load(), feature))


def _send(cmd: str, args: dict[str, Any]) -> dict[str, Any]:
    try:
        return ipc.send(cmd, args)
    except ipc.AppNotRunning as exc:
        return {"ok": False, "error": str(exc)}


def _board(cmd: str, args: dict[str, Any], units: tuple[str, ...] = ()) -> dict[str, Any]:
    if not feature_enabled("drawing"):
        return disabled("drawing")
    try:
        for name in units:
            check_unit(name, args[name])
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return _send(cmd, args)


# ------------------------------------------------------------------ screen


def grab_screen(monitor: int) -> tuple[bytes, int, int]:
    """Return (png_bytes, width, height) of one monitor; 0 is the primary monitor."""
    import mss
    from PIL import Image as PILImage

    factory = getattr(mss, "MSS", None) or mss.mss
    with factory() as sct:
        monitors = sct.monitors[1:]  # [0] is the union of all monitors
        if not 0 <= monitor < len(monitors):
            raise ValueError(f"monitor {monitor} does not exist (have {len(monitors)})")
        shot = sct.grab(monitors[monitor])
    img = PILImage.frombytes("RGB", shot.size, shot.rgb)
    width, height = img.size
    if max(width, height) > MAX_IMAGE_EDGE:
        scale = MAX_IMAGE_EDGE / max(width, height)
        img = img.resize((round(width * scale), round(height * scale)), PILImage.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue(), width, height


@mcp.tool()
def capture_screen(monitor: int = 0) -> Any:
    """Take one screenshot of a monitor (0 = primary). Returns the image and its size in pixels."""
    if not feature_enabled("screen_access"):
        return disabled("screen_access")
    try:
        png, width, height = grab_screen(monitor)
    except Exception as exc:
        return {"ok": False, "error": f"screen capture failed: {exc}"}
    return [Image(data=png, format="png"), {"ok": True, "width": width, "height": height}]


# --------------------------------------------------------------------- pen


@mcp.tool()
def board_open(mode: str = "glass") -> dict[str, Any]:
    """Open the drawing surface: "glass" (translucent blackboard) or "screen" (draw over the screen)."""
    if mode not in ("glass", "screen"):
        return {"ok": False, "error": "mode must be 'glass' or 'screen'"}
    return _board("board_open", {"mode": mode})


@mcp.tool()
def board_write_text(text: str, x: float, y: float, size: float | None = None) -> dict[str, Any]:
    """Handwrite text centred at (x, y), both 0..1 of the screen. size: fraction of screen height (default 0.045)."""
    return _board("board_write_text", {"text": text, "x": x, "y": y, "size": size}, ("x", "y"))


@mcp.tool()
def board_write_latex(latex: str, x: float, y: float, size: float | None = None) -> dict[str, Any]:
    """Write LaTeX math (e.g. \\frac{n(n+1)}{2}) centred at (x, y), 0..1 of the screen."""
    return _board("board_write_latex", {"latex": latex, "x": x, "y": y, "size": size}, ("x", "y"))


@mcp.tool()
def board_draw_arrow(x1: float, y1: float, x2: float, y2: float, label: str | None = None) -> dict[str, Any]:
    """Draw an arrow from (x1, y1) to (x2, y2), all 0..1 of the screen, with an optional label."""
    args = {"x1": x1, "y1": y1, "x2": x2, "y2": y2, "label": label}
    return _board("board_draw_arrow", args, ("x1", "y1", "x2", "y2"))


@mcp.tool()
def board_draw_shape(
    shape: str, x: float, y: float, w: float, h: float, label: str | None = None
) -> dict[str, Any]:
    """Draw a "box", "circle" or "line". (x, y) is the top-left corner, w/h the size, all 0..1 of the screen."""
    if shape not in ("box", "circle", "line"):
        return {"ok": False, "error": "shape must be 'box', 'circle' or 'line'"}
    args = {"shape": shape, "x": x, "y": y, "w": w, "h": h, "label": label}
    return _board("board_draw_shape", args, ("x", "y", "w", "h"))


@mcp.tool()
def board_highlight(x: float, y: float, w: float, h: float) -> dict[str, Any]:
    """Highlight a rectangle (top-left x, y and size w, h, all 0..1 of the screen) with a marker."""
    return _board("board_highlight", {"x": x, "y": y, "w": w, "h": h}, ("x", "y", "w", "h"))


@mcp.tool()
def board_clear() -> dict[str, Any]:
    """Erase everything drawn so far, keeping the board open."""
    return _board("board_clear", {})


@mcp.tool()
def board_close() -> dict[str, Any]:
    """Close the drawing surface."""
    return _board("board_close", {})


TOOLS: dict[str, Callable[..., Any]] = {
    "capture_screen": capture_screen,
    "board_open": board_open,
    "board_write_text": board_write_text,
    "board_write_latex": board_write_latex,
    "board_draw_arrow": board_draw_arrow,
    "board_draw_shape": board_draw_shape,
    "board_highlight": board_highlight,
    "board_clear": board_clear,
    "board_close": board_close,
}


def main() -> None:
    config.keep_caches_in_app_home()
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
