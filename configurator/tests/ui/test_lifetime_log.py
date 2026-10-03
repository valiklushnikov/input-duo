"""Строки начала и конца работы: по ним видно, что процесс умер, а не вышел.

Два молчаливых падения (2026-09-30, 2026-10-03) оставили журнал, который
просто обрывался: без строки о выходе нельзя было отличить смерть от
штатного закрытия, без строки о старте - понять, какой exe и какой процесс
писал. Строка о выходе есть только у штатного выхода, поэтому её отсутствие
после строки о старте и есть признак смерти.
"""

from __future__ import annotations

import faulthandler
import logging
import os
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from duo_input import __version__
from duo_input import app as app_module
from duo_input.persistence import locations


class _FakeApplication(QObject):
    """Сигналы QGuiApplication, по которым видна причина выхода.

    Не настоящий qapp: aboutToQuit общего приложения тестов дёрнул бы всё,
    что к нему подключили другие тесты.
    """

    aboutToQuit = Signal()
    lastWindowClosed = Signal()
    commitDataRequest = Signal(object)

    def __init__(self, quit_on_last_window_closed: bool = True) -> None:
        super().__init__()
        self._quit_on_last = quit_on_last_window_closed

    def quitOnLastWindowClosed(self) -> bool:  # noqa: N802 - Qt API
        return self._quit_on_last


def _messages(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records]


def _install(monkeypatch, caplog, application) -> None:
    caplog.set_level(logging.INFO, logger="duo_input")
    monkeypatch.setattr(app_module, "running_program", lambda: Path(r"C:\Apps\DuoInput.exe"))
    app_module.install_lifetime_log(application)


def test_start_names_the_version_the_process_and_the_executable(monkeypatch, caplog):
    _install(monkeypatch, caplog, _FakeApplication())

    expected = (
        f"app_started version={__version__} pid={os.getpid()} "
        f"executable={Path(r'C:\Apps\DuoInput.exe')}"
    )
    assert expected in _messages(caplog)


def test_an_explicit_quit_is_logged_as_such(monkeypatch, caplog):
    application = _FakeApplication()
    _install(monkeypatch, caplog, application)

    application.aboutToQuit.emit()

    assert f"app_exiting pid={os.getpid()} reason=quit" in _messages(caplog)


def test_closing_the_last_window_is_the_reason_when_it_quits(monkeypatch, caplog):
    application = _FakeApplication(quit_on_last_window_closed=True)
    _install(monkeypatch, caplog, application)

    application.lastWindowClosed.emit()
    application.aboutToQuit.emit()

    assert f"app_exiting pid={os.getpid()} reason=last_window_closed" in _messages(caplog)


def test_closing_the_window_to_the_tray_is_not_the_reason(monkeypatch, caplog):
    application = _FakeApplication(quit_on_last_window_closed=False)
    _install(monkeypatch, caplog, application)

    application.lastWindowClosed.emit()  # окно ушло в трей, программа живёт
    application.aboutToQuit.emit()  # потом - «Выход» в меню трея

    assert f"app_exiting pid={os.getpid()} reason=quit" in _messages(caplog)


def test_the_end_of_the_windows_session_is_the_reason(monkeypatch, caplog):
    application = _FakeApplication()
    _install(monkeypatch, caplog, application)

    application.commitDataRequest.emit(None)
    application.aboutToQuit.emit()

    assert f"app_exiting pid={os.getpid()} reason=session_end" in _messages(caplog)


class _FakeLock:
    class _Signal:
        def connect(self, *_args) -> None:
            pass

    newConnection = _Signal()


def test_main_installs_the_lifetime_log_and_the_crash_log(qapp, monkeypatch):
    """Tested but never called: main() обязан дойти до обоих."""
    installed: list[object] = []
    monkeypatch.setattr(app_module, "install_lifetime_log", installed.append)
    monkeypatch.setattr(app_module, "configure_runtime", lambda *args: None)
    monkeypatch.setattr(app_module, "single_instance_lock", lambda *a, **k: _FakeLock())
    monkeypatch.setattr(app_module, "start_window", lambda window, **kwargs: None)
    monkeypatch.setattr(app_module, "build_main_window", lambda **kwargs: object())
    monkeypatch.setattr(qapp, "exec", lambda: 0)

    app_module.main([])

    assert installed == [qapp]
    assert faulthandler.is_enabled()
    assert locations.crash_log_path().read_text(encoding="utf-8").count(
        f"pid={os.getpid()}"
    ) >= 1
