from pathlib import Path

from src import config


def test_defaults_when_missing(app_home):
    cfg = config.load()
    assert cfg == config.Config()
    assert cfg.wake_word == "jarvis"
    assert cfg.wake_word_enabled and cfg.speak_aloud and cfg.show_notifications
    assert cfg.screen_access and cfg.drawing


def test_save_and_load_roundtrip(app_home):
    cfg = config.Config(speak_aloud=False, drawing=False, hotkey="<ctrl>+<alt>+j", silence_timeout_ms=1500)
    config.save(cfg)
    assert config.config_path().parent == app_home
    assert config.load() == cfg


def test_all_spec_keys_present():
    keys = set(config.Config().to_dict())
    assert keys == {
        "wake_word_enabled", "wake_word", "hotkey", "speak_aloud", "show_notifications",
        "screen_access", "drawing", "stt_model", "silence_timeout_ms",
    }


def test_invalid_values_fall_back_to_defaults(app_home):
    config.config_path().write_text(
        "speak_aloud: maybe\nsilence_timeout_ms: -5\nwake_word: ''\ndrawing: 'off'\nextra: 1\n", encoding="utf-8"
    )
    cfg = config.load()
    assert cfg.speak_aloud is True
    assert cfg.silence_timeout_ms == 900
    assert cfg.wake_word == "jarvis"
    assert cfg.drawing is False


def test_broken_yaml_gives_defaults(app_home):
    config.config_path().write_text("::: not yaml [", encoding="utf-8")
    assert config.load() == config.Config()


def test_ensure_default_does_not_overwrite(app_home):
    config.save(config.Config(speak_aloud=False))
    assert config.ensure_default().speak_aloud is False


def test_repo_default_config_matches_code():
    repo = Path(__file__).resolve().parent.parent / "config.yaml"
    assert config.load(repo) == config.Config()
