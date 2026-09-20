"""Production path (task 8): the macOS receiver actually gets built and wired.

Mirrors the fixture setup ``tests/ui/test_runtime_wiring.py`` uses to prove the
*Windows* file subsystem is created by ``configure_runtime`` - here for the
darwin branch, which is otherwise untested by that file (its ``_FileBackend``
fake only speaks the Windows-shaped interface: ``set_callbacks``/``publish``/
``start``). ``create_file_backend`` is deliberately left unmocked: the point
of this file is that production actually constructs a ``MacFileReceiver``,
not that the module exists.
"""

from __future__ import annotations

import sys

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from duo_input import app as app_module
from duo_input.app import build_main_window, configure_runtime
from duo_input.transfer.macos_files import MacFileReceiver
from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.platform_files import MacReceiveRouter

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="darwin receiver only")


def _settings(tmp_path, values: dict[str, object]) -> QSettings:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    for key, value in values.items():
        settings.setValue(key, value)
    return settings


def _runtime_of(application):
    from duo_input.app import _ClipboardRuntime

    for child in reversed(application.children()):
        if isinstance(child, _ClipboardRuntime):
            return child
    raise AssertionError("_ClipboardRuntime was not created")


def _configure(qapp, qtbot, tmp_path, monkeypatch, values):
    # Same isolation the Windows fixture uses - identity/trust files land in
    # tmp_path rather than the operator's real profile.
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, values)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    return settings, window, _runtime_of(qapp)


def _manifest(transfer_id: str = "t1") -> TransferManifest:
    return TransferManifest(
        transfer_id=transfer_id,
        entries=(TransferEntry(path="a.bin", kind=ENTRY_FILE, size=10, mtime_ns=1),),
    )


def test_files_enabled_creates_mac_receiver(qapp, qtbot, tmp_path, monkeypatch):
    """set_files_enabled(True) on darwin builds a real MacFileReceiver behind
    the router, not a module-existence stand-in - create_file_backend is NOT
    monkeypatched. Task 16's ``create_file_backend`` always wraps darwin's
    receiver in a ``MacReceiveRouter`` (byte-for-byte staging-only behavior
    with the File Provider flag off, per its docstring), so the object that
    now proves staging is the active/default backend is
    ``router._staging``, not ``file_backend`` itself - matching the pattern
    already used by the flag-off rollout test at
    ``test_runtime_wiring.py``'s ``router._staging`` assertion."""
    _settings_, _window, runtime = _configure(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        assert isinstance(runtime.file_backend, MacReceiveRouter)
        assert isinstance(runtime.file_backend._staging, MacFileReceiver)
        assert runtime._file_receiver is runtime.file_backend
        # win32-only plumbing must stay untouched on darwin.
        assert runtime._file_callback_gateway is None
    finally:
        runtime.stop()


def test_files_disabled_leaves_no_receiver(qapp, qtbot, tmp_path, monkeypatch):
    _settings_, _window, runtime = _configure(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": False},
    )
    try:
        assert runtime.file_backend is None
        assert runtime._file_receiver is None
    finally:
        runtime.stop()


def test_disabling_files_stops_the_receiver(qapp, qtbot, tmp_path, monkeypatch):
    _settings_, window, runtime = _configure(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        receiver = runtime.file_backend
        window.clipboard_page.files_checkbox.setChecked(False)

        assert runtime.file_backend is None
        assert runtime._file_receiver is None
        # stop() reached the receiver: it is back to a state with no session.
        assert receiver is not None
    finally:
        runtime.stop()


def test_auto_mode_authorizes_without_prompt(qapp, qtbot, tmp_path, monkeypatch):
    _settings_, _window, runtime = _configure(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {
            "clipboard/enabled": True,
            "clipboard/files_enabled": True,
            "clipboard/incoming_files": "auto",
        },
    )
    try:
        prompted: list[object] = []
        monkeypatch.setattr(
            runtime, "_prompt_file_authorization", lambda manifest: prompted.append(manifest)
        )
        authorized: list[bool] = []
        monkeypatch.setattr(
            runtime.file_backend, "authorize", lambda accepted: authorized.append(accepted)
        )

        runtime.file_backend.handle_offer(_manifest())

        assert prompted == []
        assert authorized == [True]
    finally:
        runtime.stop()


def test_ask_mode_is_the_default_and_prompts(qapp, qtbot, tmp_path, monkeypatch):
    _settings_, _window, runtime = _configure(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        assert (
            runtime._settings.value("clipboard/incoming_files", "ask", type=str) == "ask"
        )
        prompted: list[object] = []
        monkeypatch.setattr(
            runtime, "_prompt_file_authorization", lambda manifest: prompted.append(manifest)
        )
        authorized: list[bool] = []
        monkeypatch.setattr(
            runtime.file_backend, "authorize", lambda accepted: authorized.append(accepted)
        )

        manifest = _manifest()
        runtime.file_backend.handle_offer(manifest)

        assert prompted == [manifest]
        assert authorized == []
    finally:
        runtime.stop()


def test_prompt_survives_receiver_stopped_while_modal_is_open(qapp, qtbot, tmp_path, monkeypatch):
    """If the file subsystem is stopped (files toggle off) while the
    authorization modal is up, exec() returning must not touch a now-None
    ``self._file_receiver`` - it must instead use the receiver instance
    captured before the nested event loop started."""
    _settings_, _window, runtime = _configure(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        receiver = runtime.file_backend
        authorized: list[bool] = []
        monkeypatch.setattr(receiver, "authorize", lambda accepted: authorized.append(accepted))

        def fake_exec(self):
            # Simulate the file subsystem being stopped while the modal's
            # nested event loop is running - this nulls runtime._file_receiver.
            runtime._stop_files()
            return QMessageBox.StandardButton.Ok

        monkeypatch.setattr(QMessageBox, "exec", fake_exec)

        # Must not raise AttributeError on a None self._file_receiver.
        runtime._prompt_file_authorization(_manifest())

        assert runtime._file_receiver is None
        # The captured local still got its authorize() call.
        assert authorized == [True]
    finally:
        runtime.stop()


def test_auto_incoming_checkbox_persists_the_setting(qapp, qtbot, tmp_path, monkeypatch):
    _settings_, window, runtime = _configure(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        window.clipboard_page.auto_incoming_checkbox.setChecked(True)

        assert runtime._settings.value("clipboard/incoming_files", type=str) == "auto"

        window.clipboard_page.auto_incoming_checkbox.setChecked(False)

        assert runtime._settings.value("clipboard/incoming_files", type=str) == "ask"
    finally:
        runtime.stop()
