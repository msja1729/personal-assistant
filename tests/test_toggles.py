"""Every MCP tool must refuse, and do nothing, when its feature is off."""

import pytest

from src import config, ipc
from src import senses_server as ss

BOARD_CALLS = {
    "board_open": lambda: ss.board_open("glass"),
    "board_write_text": lambda: ss.board_write_text("hi", 0.5, 0.5),
    "board_write_latex": lambda: ss.board_write_latex(r"\frac{1}{2}", 0.5, 0.5),
    "board_draw_arrow": lambda: ss.board_draw_arrow(0.1, 0.1, 0.9, 0.9, "x"),
    "board_draw_shape": lambda: ss.board_draw_shape("box", 0.1, 0.1, 0.2, 0.2),
    "board_highlight": lambda: ss.board_highlight(0.1, 0.1, 0.2, 0.2),
    "board_clear": lambda: ss.board_clear(),
    "board_close": lambda: ss.board_close(),
}


@pytest.fixture
def calls(monkeypatch):
    sent = []

    def fake_send(cmd, args=None, timeout=15.0):
        sent.append((cmd, args))
        return {"ok": True, "id": "i1"}

    def fake_capture(monitor):
        sent.append(("capture", monitor))
        return b"png", 100, 50

    monkeypatch.setattr(ipc, "send", fake_send)
    monkeypatch.setattr(ss, "grab_screen", fake_capture)
    return sent


def test_every_tool_is_covered():
    assert set(ss.TOOLS) == set(BOARD_CALLS) | {"capture_screen"}


def test_screen_disabled(calls):
    config.save(config.Config(screen_access=False))
    assert ss.capture_screen() == {"ok": False, "error": "screen_access disabled by user"}
    assert calls == []


@pytest.mark.parametrize("name", sorted(BOARD_CALLS))
def test_drawing_disabled(calls, name):
    config.save(config.Config(drawing=False))
    assert BOARD_CALLS[name]() == {"ok": False, "error": "drawing disabled by user"}
    assert calls == []


@pytest.mark.parametrize("name", sorted(BOARD_CALLS))
def test_drawing_enabled_forwards_to_app(calls, name):
    config.save(config.Config(drawing=True))
    result = BOARD_CALLS[name]()
    assert result["ok"] is True
    assert calls and calls[0][0] == name


def test_screen_enabled_returns_image_and_size(calls):
    config.save(config.Config(screen_access=True))
    _image, info = ss.capture_screen()
    assert info == {"ok": True, "width": 100, "height": 50}
    assert calls == [("capture", 0)]


def test_toggle_change_applies_without_restart(calls):
    config.save(config.Config(drawing=True))
    assert ss.board_clear()["ok"] is True
    config.save(config.Config(drawing=False))
    assert ss.board_clear()["ok"] is False


@pytest.mark.parametrize(
    "call",
    [
        lambda: ss.board_write_text("x", 1.5, 0.5),
        lambda: ss.board_draw_arrow(0, 0, -0.1, 0.5),
        lambda: ss.board_highlight(0.1, 0.1, 2, 0.1),
        lambda: ss.board_draw_shape("star", 0.1, 0.1, 0.1, 0.1),
        lambda: ss.board_open("wall"),
    ],
)
def test_bad_arguments_rejected_before_app(calls, call):
    result = call()
    assert result["ok"] is False
    assert calls == []


def test_app_not_running_is_reported():
    config.save(config.Config())
    result = ss.board_clear()  # no app running, no IPC key
    assert result == {"ok": False, "error": "Personal Assistant app is not running"}


def test_app_side_also_enforces_drawing():
    """The app refuses board commands itself when drawing is off."""
    from src import main

    config.save(config.Config(drawing=False))
    result = main.Assistant.handle_ipc(object(), "board_clear", {})
    assert result == {"ok": False, "error": "drawing disabled by user"}
