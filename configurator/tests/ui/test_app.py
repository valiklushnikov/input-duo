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


def test_file_self_check_invokes_the_com_vtable_before_starting_qt(capsys, monkeypatch):
    from duo_input import app

    def qt_must_not_start(*_args, **_kwargs):
        raise AssertionError("the file self-check must remain windowless")

    with monkeypatch.context() as context:
        context.setattr(app.QApplication, "instance", qt_must_not_start)
        result = main(["DuoInput.exe", "--self-check-files"])

    output = capsys.readouterr().out.lower()
    assert result == 0
    assert "files: ok" in output
    assert "callback: addref 2, release 1" in output
    assert "descriptor: 592" in output


def test_build_main_window_produces_a_wired_shell(qtbot):
    service = DeviceService(timeout_ms=5000)

    window = build_main_window(service, transport_factory=lambda: None)
    qtbot.addWidget(window)

    assert isinstance(window, MainWindow)
    assert window.service is service
    assert window.session.dirty is False


def test_starting_a_window_still_adopts_a_device_that_answers(qtbot, tmp_path):
    """The startup sequence is a function so it can be tested at all.

    There is no file to reopen any more, so the only thing ``start_window``
    still does beyond showing the window is give the autoconnect its first
    synchronous chance to find a board - covered end to end here through the
    real entry point rather than through ``MainWindow`` directly.
    """
    from PySide6.QtCore import QSettings

    from duo_input.app import build_main_window, start_window
    from duo_input.device.emulator import U1Emulator
    from duo_input.device.service import DeviceService
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import ProjectSession, RenameProfile

    store = QSettings(str(tmp_path / "duo-input.ini"), QSettings.Format.IniFormat)
    store.clear()

    emulator = U1Emulator()
    on_board = ProjectSession.new().apply(RenameProfile(1, "On the board")).project
    emulator.install_active(compile_project_to_binary(on_board))

    window = build_main_window(
        DeviceService(timeout_ms=5000),
        transport_factory=lambda: emulator,
        settings=store,
    )
    qtbot.addWidget(window)
    start_window(window)

    qtbot.waitUntil(
        lambda: window.session.active_profile.name == "On the board", timeout=5000
    )


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
