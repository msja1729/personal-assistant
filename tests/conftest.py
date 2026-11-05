import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(autouse=True)
def app_home(tmp_path, monkeypatch):
    """Every test gets its own empty app folder."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("PA_HOME", str(home))
    return home


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
