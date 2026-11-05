import pytest

from src.platform_support import HotkeyMatcher, parse_hotkey


def test_parse():
    assert parse_hotkey("<ctrl>+<shift>+<space>") == (frozenset({"ctrl", "shift"}), "space")
    assert parse_hotkey("Control+Option+J") == (frozenset({"ctrl", "alt"}), "j")


@pytest.mark.parametrize("bad", ["", "<ctrl>+<shift>", "j+<ctrl>", "<hyper>+j"])
def test_parse_rejects(bad):
    with pytest.raises(ValueError):
        parse_hotkey(bad)


def test_fires_once_with_exact_modifiers():
    m = HotkeyMatcher("<ctrl>+<shift>+<space>")
    assert not m.press("ctrl")
    assert not m.press("shift")
    assert m.press("space")
    assert not m.press("space")  # key repeat while held
    m.release("space")
    assert m.press("space")  # pressed again


def test_extra_or_missing_modifier_does_not_fire():
    m = HotkeyMatcher("<ctrl>+<shift>+<space>")
    m.press("shift")
    assert not m.press("space")
    m.release("space")
    m.press("ctrl")
    m.press("alt")
    assert not m.press("space")
