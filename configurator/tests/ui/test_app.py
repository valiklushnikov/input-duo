"""The application entry point and the window factory behind it."""

from __future__ import annotations

import tomllib
from pathlib import Path

from duo_input.app import ENTRY_POINT, build_main_window, main
from duo_input.device.service import DeviceService
from duo_input.ui.main_window import MainWindow

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"


def test_pyproject_declares_the_console_entry_point():
    document = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    scripts = document["project"]["scripts"]

    assert scripts["duo-input-configurator"] == ENTRY_POINT


def test_entry_point_target_is_the_main_callable():
    module_name, _, attribute = ENTRY_POINT.partition(":")

    assert module_name == "duo_input.app"
    assert attribute == "main"
    assert callable(main)


def test_build_main_window_produces_a_wired_shell(qtbot):
    service = DeviceService(timeout_ms=5000)

    window = build_main_window(service, transport_factory=lambda: None)
    qtbot.addWidget(window)

    assert isinstance(window, MainWindow)
    assert window.service is service
    assert window.session.dirty is False


def test_starting_a_window_reopens_what_was_open_last(qtbot, tmp_path):
    """The startup sequence is a function so it can be tested at all.

    Both steps it performs - reopening the last project and offering an
    autosave recovery - used to be reachable only from main(), where nothing
    could check that they were still wired.
    """
    from PySide6.QtCore import QSettings

    from duo_input.app import build_main_window, start_window
    from duo_input.device.service import DeviceService
    from duo_input.ui.models.project_session import RenameProfile

    store = QSettings(str(tmp_path / "duo-input.ini"), QSettings.Format.IniFormat)
    store.clear()
    saved = tmp_path / "startup.duoinput.json"

    first = build_main_window(
        DeviceService(), transport_factory=lambda: None, settings=store
    )
    qtbot.addWidget(first)
    first.set_session(first.session.apply(RenameProfile(1, "Remembered")))
    assert first.save_project(saved) is True

    later = build_main_window(
        DeviceService(), transport_factory=lambda: None, settings=store
    )
    qtbot.addWidget(later)
    later._confirm_close = lambda: None
    start_window(later)

    assert later.session.path == saved
    assert later.session.active_profile.name == "Remembered"


def test_starting_the_application_configures_the_log(tmp_path, monkeypatch):
    from duo_input.app import configure_application
    from duo_input.persistence.locations import log_directory

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    path = configure_application()

    assert path.parent == log_directory()
    assert path.parent.is_dir()


def test_the_program_carries_an_icon(qapp):
    from duo_input.app import icon_path

    assert icon_path().is_file()
    assert icon_path().suffix == ".ico"
