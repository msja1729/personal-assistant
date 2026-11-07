import sys
from pathlib import Path

import pytest

from src import config
from src.config import Config

REPO = Path(__file__).resolve().parent.parent

SPEC_KEYS = {
    "wake_word_enabled", "wake_word", "hotkey", "speak_aloud", "show_notifications",
    "screen_access", "drawing", "stt_model", "silence_timeout_ms",
}


def test_has_every_spec_key() -> None:
    assert set(Config().to_dict()) == SPEC_KEYS


def test_missing_file_gives_defaults() -> None:
    assert config.load() == Config()


def test_save_then_load_roundtrip(app_home: Path) -> None:
    cfg = Config(speak_aloud=False, drawing=False, hotkey="<ctrl>+<alt>+j", silence_timeout_ms=1500)
    config.save(cfg)
    assert config.config_path() == app_home / "config.yaml"
    assert config.load() == cfg


def test_broken_yaml_gives_defaults() -> None:
    config.config_path().write_text("::: not yaml [", encoding="utf-8")
    assert config.load() == Config()


def test_non_mapping_gives_defaults() -> None:
    config.config_path().write_text("- just\n- a list\n", encoding="utf-8")
    assert config.load() == Config()


@pytest.mark.parametrize(
    "line, key, expected",
    [
        ("speak_aloud: maybe", "speak_aloud", True),
        ("drawing: 'off'", "drawing", False),
        ("screen_access: 'yes'", "screen_access", True),
        ("silence_timeout_ms: -5", "silence_timeout_ms", 900),
        ("silence_timeout_ms: '1200'", "silence_timeout_ms", 1200),
        ("silence_timeout_ms: true", "silence_timeout_ms", 900),
        ("wake_word: ''", "wake_word", "jarvis"),
        ("wake_word: [a, b]", "wake_word", "jarvis"),
    ],
)
def test_bad_or_loose_values(line: str, key: str, expected: object) -> None:
    config.config_path().write_text(line + "\n", encoding="utf-8")
    assert getattr(config.load(), key) == expected


def test_unknown_keys_are_ignored() -> None:
    config.config_path().write_text("something_else: 1\ndrawing: false\n", encoding="utf-8")
    assert config.load() == Config(drawing=False)


def test_ensure_default_creates_and_keeps() -> None:
    assert not config.config_path().exists()
    assert config.ensure_default() == Config()
    assert config.config_path().exists()
    config.save(Config(speak_aloud=False))
    assert config.ensure_default().speak_aloud is False  # not overwritten


def test_save_leaves_no_temp_files(app_home: Path) -> None:
    config.save(Config())
    config.save(Config(drawing=False))
    assert [p.name for p in app_home.iterdir()] == ["config.yaml"]


def test_repo_default_config_matches_code() -> None:
    assert config.load(REPO / "config.yaml") == Config()


def test_pa_home_overrides_folder(app_home: Path) -> None:
    assert config.app_home() == app_home


def test_default_folder_not_in_appdata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PA_HOME")
    home = config.app_home()
    if sys.platform == "win32":
        assert home == Path.home() / ".personal-assistant"
        assert "appdata" not in str(home).lower()
    elif sys.platform == "darwin":
        assert home == Path.home() / "Library" / "Application Support" / "PersonalAssistant"
