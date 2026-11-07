"""Settings (config.yaml) and the app's folder on disk.

Every feature switch lives in ``config.yaml`` inside the app folder. Readers
always get a complete, valid ``Config``: a missing file, broken YAML or a
wrong value falls back to the default instead of crashing.
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

import yaml

from src import APP_ID


@dataclass
class Config:
    """All settings from SPEC.md with their defaults."""

    wake_word_enabled: bool = True
    wake_word: str = "jarvis"
    hotkey: str = "<ctrl>+<shift>+<space>"
    speak_aloud: bool = True
    show_notifications: bool = True
    screen_access: bool = True
    drawing: bool = True
    stt_model: str = "base.en"
    silence_timeout_ms: int = 900

    @classmethod
    def from_dict(cls, data: object) -> Config:
        """Build a Config from raw YAML data, keeping the default for anything missing or invalid."""
        cfg = cls()
        if not isinstance(data, dict):
            return cfg
        for f in fields(cls):
            if f.name in data:
                try:
                    setattr(cfg, f.name, _coerce(data[f.name], getattr(cfg, f.name)))
                except ValueError:
                    pass  # keep the default
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce(value: object, default: object) -> object:
    """Convert ``value`` to the type of ``default`` or raise ValueError."""
    if isinstance(default, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str) and value.strip().lower() in ("true", "yes", "on"):
            return True
        if isinstance(value, str) and value.strip().lower() in ("false", "no", "off"):
            return False
        raise ValueError(f"not a yes/no value: {value!r}")
    if isinstance(default, int):
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            raise ValueError(f"not a whole number: {value!r}")
        number = int(value)
        if number <= 0:
            raise ValueError(f"must be positive: {value!r}")
        return number
    if isinstance(value, (str, int, float)) and str(value).strip():
        return str(value).strip()
    raise ValueError(f"not usable text: {value!r}")


# ---------------------------------------------------------------- locations


def app_home() -> Path:
    """Folder for config, logs, models and everything else the app stores.

    ``PA_HOME`` overrides it (used by tests).
    """
    override = os.environ.get("PA_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        # Not under AppData: Microsoft Store Python silently redirects writes
        # there into a private folder other programs (Claude Code) cannot see.
        return Path.home() / ".personal-assistant"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_ID
    return Path.home() / ".local" / "share" / APP_ID


def config_path() -> Path:
    return app_home() / "config.yaml"


# ---------------------------------------------------------------- load / save


def load(path: Path | None = None) -> Config:
    """Read config.yaml. A missing or broken file gives the defaults."""
    path = path or config_path()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return Config()
    return Config.from_dict(data)


def save(cfg: Config, path: Path | None = None) -> None:
    """Write config.yaml atomically, so a reader never sees a half-written file."""
    path = path or config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "# Personal Assistant settings. The dashboard edits this file.\n" + yaml.safe_dump(
        cfg.to_dict(), sort_keys=False, allow_unicode=True
    )
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".config-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def ensure_default(path: Path | None = None) -> Config:
    """Create config.yaml with the defaults if it does not exist; return the current settings."""
    path = path or config_path()
    if not path.exists():
        save(Config(), path)
    return load(path)
