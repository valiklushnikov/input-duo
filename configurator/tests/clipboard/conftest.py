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


@pytest.fixture
def real_clipboard(qapp):
    """Настоящий буфер обмена QApplication, пустой к концу теста.

    ``QClipboard.setMimeData()`` передаёт объект во владение Qt, и на
    платформе offscreen он переживает выход интерпретатора: процесс падает
    с access violation (0xC0000005) уже ПОСЛЕ того, как pytest напечатал
    зелёную сводку. Собственно тесты при этом проходят, поэтому увидеть это
    можно только по коду возврата - а сборка на него смотрит и отказывается
    компилировать. ``clear()`` в конце снимает ровно это; проверено
    отдельным репро: ``setMimeData`` без ``clear`` падает, с ``clear`` - нет.
    """
    clipboard = qapp.clipboard()
    yield clipboard
    clipboard.clear()
