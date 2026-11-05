"""Settings (config.yaml) and the app's on-disk locations.

Every feature toggle lives in ``config.yaml`` inside the app home folder. The
dashboard edits this file; the app and the senses MCP server re-read it, so a
change applies on the next request without a restart.
"""

from __future__ import annotations

import os
import sys
import tempfile
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from src import APP_ID


@dataclass
class Config:
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
    def from_dict(cls, data: dict[str, Any] | None) -> "Config":
        """Build a Config from raw YAML data, keeping defaults for missing or invalid values."""
        cfg = cls()
        if not isinstance(data, dict):
            return cfg
        for f in fields(cls):
            if f.name not in data:
                continue
            value = data[f.name]
            default = getattr(cfg, f.name)
            try:
                if isinstance(default, bool):
                    value = _to_bool(value)
                elif isinstance(default, int):
                    value = int(value)
                    if value <= 0:
                        raise ValueError
                else:
                    value = str(value).strip()
                    if not value:
                        raise ValueError
            except (TypeError, ValueError):
                continue  # keep the default for an unusable value
            setattr(cfg, f.name, value)
        return cfg

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "yes", "on", "1"}:
        return True
    if isinstance(value, str) and value.strip().lower() in {"false", "no", "off", "0"}:
        return False
    if isinstance(value, int):
        return bool(value)
    raise ValueError(f"not a boolean: {value!r}")


# ---------------------------------------------------------------- locations


def app_home() -> Path:
    """Folder holding config, logs, models, the venv and the install manifest.

    ``PA_HOME`` overrides it (used by tests and portable runs).
    """
    override = os.environ.get("PA_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        # Not under AppData: Microsoft Store Python silently redirects writes there
        # into a private folder that Claude Code (and other programs) cannot see.
        return Path.home() / ".personal-assistant"
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return base / APP_ID


def keep_caches_in_app_home() -> None:
    """Point library caches into the app folder so uninstall leaves nothing behind."""
    os.environ.setdefault("MPLCONFIGDIR", str(app_home() / "cache" / "matplotlib"))
    os.environ.setdefault("HF_HOME", str(app_home() / "cache" / "huggingface"))
    os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def config_path() -> Path:
    return app_home() / "config.yaml"


def logs_dir() -> Path:
    return app_home() / "logs"


def models_dir() -> Path:
    return app_home() / "models"


def workspace_dir() -> Path:
    """Working folder for Claude Code sessions, so they never touch the user's projects."""
    return app_home() / "workspace"


def state_path() -> Path:
    return app_home() / "state.json"


def manifest_path() -> Path:
    return app_home() / "install_manifest.json"


def ipc_key_path() -> Path:
    return app_home() / "ipc.key"


# ---------------------------------------------------------------- load / save


def load(path: Path | None = None) -> Config:
    """Read config.yaml. A missing or broken file gives the defaults."""
    import yaml

    path = path or config_path()
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return Config()
    return Config.from_dict(data)


def save(cfg: Config, path: Path | None = None) -> None:
    """Write config.yaml atomically, so a reader never sees a half-written file."""
    import yaml

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
    """Create config.yaml with defaults if it does not exist; return the current config."""
    path = path or config_path()
    if not path.exists():
        save(Config(), path)
    return load(path)
