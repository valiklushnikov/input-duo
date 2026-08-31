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

    Reopening the last project used to be reachable only from main(), where
    nothing could check that it was still wired.
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
    start_window(later)

    assert later.session.path == saved
    assert later.session.active_profile.name == "Remembered"


def test_startup_prefers_the_device_over_the_last_file(qtbot, tmp_path):
    """The device wins: the question on opening is what the hardware is doing.

    This asserts the end state, not ``start_window``'s ordering: it passes
    whether or not ``start_window`` branches on the device at all, because a
    successful connect already replaces the session on its own (the
    construction-time autoconnect timer plus Task 3's connect-triggered
    read). It is kept because the end state is still worth pinning, not as
    proof that ``start_window`` is what delivers it - see
    ``test_startup_falls_back_to_the_file_when_the_device_never_answers``
    for the case that actually distinguishes ``start_window``'s behaviour.
    """
    from PySide6.QtCore import QSettings

    from duo_input.app import build_main_window, start_window
    from duo_input.device.emulator import U1Emulator
    from duo_input.device.service import DeviceService, DeviceState
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import ProjectSession, RenameProfile

    store = QSettings(str(tmp_path / "duo-input.ini"), QSettings.Format.IniFormat)
    store.clear()
    saved = tmp_path / "on-disk.duoinput.json"

    on_disk = build_main_window(DeviceService(), transport_factory=lambda: None, settings=store)
    qtbot.addWidget(on_disk)
    on_disk.set_session(on_disk.session.apply(RenameProfile(1, "On disk")))
    assert on_disk.save_project(saved) is True

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


def test_startup_falls_back_to_the_file_when_the_device_never_answers(qtbot, tmp_path):
    """A device that opens but never completes its handshake must not strand
    the operator with a blank project.

    ``DeviceService.is_connected`` is true the instant the port opens - well
    before HELLO is answered, since replies always arrive on the next event
    loop tick (``SynchronousTransportLink.send`` defers through
    ``QTimer.singleShot``). A ``start_window`` that branches on
    ``is_connected`` right after ``try_autoconnect()`` therefore branches
    before the device has answered anything, and if the handshake then
    fails, nothing ever reopens the last file. The last file must still be
    what the operator sees.
    """
    from PySide6.QtCore import QSettings

    from duo_input.app import build_main_window, start_window
    from duo_input.device.service import DeviceService
    from duo_input.device.transport import AbstractByteTransport
    from duo_input.ui.models.project_session import RenameProfile

    class _SilentTransport(AbstractByteTransport):
        """Opens immediately; answers every write with an undecodable frame."""

        def write(self, data: bytes) -> bytes:
            return b"not-a-real-reply\x00"

    store = QSettings(str(tmp_path / "duo-input.ini"), QSettings.Format.IniFormat)
    store.clear()
    saved = tmp_path / "on-disk.duoinput.json"

    on_disk = build_main_window(DeviceService(), transport_factory=lambda: None, settings=store)
    qtbot.addWidget(on_disk)
    on_disk.set_session(on_disk.session.apply(RenameProfile(1, "On disk")))
    assert on_disk.save_project(saved) is True

    window = build_main_window(
        DeviceService(timeout_ms=5000),
        transport_factory=lambda: _SilentTransport(),
        settings=store,
    )
    qtbot.addWidget(window)
    start_window(window)

    assert window.session.path == saved
    assert window.session.active_profile.name == "On disk"


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
