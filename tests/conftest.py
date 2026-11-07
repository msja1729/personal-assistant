import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


@pytest.fixture(autouse=True)
def app_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test gets its own empty app folder, never the real one."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PA_HOME", str(home))
    return home
