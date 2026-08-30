"""Headless Qt defaults for the UI tests.

The theme is installed on the application the whole session shares, so the
size and clipping assertions elsewhere are made against the interface that
actually ships rather than against unstyled stock widgets.
"""

from __future__ import annotations

import os

import pytest

# Must be set before pytest-qt instantiates the QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="session", autouse=True)
def themed_application(qapp):
    """Dress the shared application before the first widget is built."""
    from duo_input.ui.theme import apply_theme

    apply_theme(qapp)
    return qapp
