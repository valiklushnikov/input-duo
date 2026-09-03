"""Фикстуры для тестов clipboard."""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="session")
def qapp():
    """Создать QApplication для тестов.

    QTimer.isActive() требует живого QApplication для работы.
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app
    # Не удаляем приложение в scope="session", оно нужно всей сессии
