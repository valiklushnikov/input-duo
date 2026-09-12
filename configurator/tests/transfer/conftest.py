from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(scope="session")
def qapp():
    """Create the QApplication required by the responsiveness instrument."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
