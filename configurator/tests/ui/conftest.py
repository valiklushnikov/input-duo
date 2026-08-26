"""Headless Qt defaults for the UI tests."""

from __future__ import annotations

import os

# Must be set before pytest-qt instantiates the QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
