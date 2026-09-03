"""Production path: what main assembles, not only isolated test objects."""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMessageBox

from duo_input import app as app_module
from duo_input.app import build_main_window, configure_runtime, single_instance_lock
from duo_input.clipboard.pairing import PairingCandidate


def _settings(tmp_path, enabled: bool) -> QSettings:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("clipboard/enabled", enabled)
    return settings


def _candidate(
    *, fingerprint: str = "f" * 64, machine_name: str = "LAPTOP-TWO"
) -> PairingCandidate:
    return PairingCandidate(
        origin_id="2" * 32,
        machine_name=machine_name,
        fingerprint=fingerprint,
        address="192.168.1.5",
        port=47654,
    )


def test_nothing_is_built_while_sharing_is_off(qtbot, qapp, tmp_path, monkeypatch):
    window = build_main_window(settings=_settings(tmp_path, False))
    qtbot.addWidget(window)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the disabled path constructed a clipboard runtime object")

    for name in (
        "load_or_create",
        "TrustStore",
        "ClipboardCoordinator",
        "WindowsClipboardBackend",
        "TrayIcon",
    ):
        monkeypatch.setattr(app_module, name, forbidden, raising=False)

    assert configure_runtime(qapp, window, _settings(tmp_path, False)) is None


def test_the_coordinator_is_built_when_sharing_is_on(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    coordinator = configure_runtime(qapp, window, settings)

    assert coordinator is not None
    coordinator.service._backend.stop()
    coordinator.stop()


def test_switching_sharing_on_stops_the_program_quitting_with_the_window(
    qtbot, qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    coordinator = configure_runtime(qapp, window, settings)

    assert qapp.quitOnLastWindowClosed() is False
    coordinator.service._backend.stop()
    coordinator.stop()


def test_pairing_dialog_accepts_the_exact_candidate_and_six_digit_code(
    qtbot, qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    coordinator = configure_runtime(qapp, window, settings)
    candidate = _candidate(machine_name="DESKTOP-ALPHA")
    shown: list[tuple[object, str, str]] = []
    confirmed: list[PairingCandidate] = []

    def accept(parent, title, text, *_args):
        shown.append((parent, title, text))
        return QMessageBox.StandardButton.Yes

    monkeypatch.setattr(QMessageBox, "question", accept)
    monkeypatch.setattr(coordinator, "confirm_pairing", confirmed.append)

    coordinator.pairing_code_ready.emit("004271", candidate)

    assert shown[0][0] is window
    assert "DESKTOP-ALPHA" in shown[0][2]
    assert "004271" in shown[0][2]
    assert confirmed == [candidate]
    coordinator.service._backend.stop()
    coordinator.stop()


def test_pairing_dialog_rejects_the_exact_candidate(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    coordinator = configure_runtime(qapp, window, settings)
    candidate = _candidate()
    rejected: list[PairingCandidate] = []

    monkeypatch.setattr(
        QMessageBox,
        "question",
        lambda *_args: QMessageBox.StandardButton.No,
    )
    monkeypatch.setattr(coordinator, "reject_pairing", rejected.append, raising=False)

    coordinator.pairing_code_ready.emit("918205", candidate)

    assert rejected == [candidate]
    coordinator.service._backend.stop()
    coordinator.stop()


def test_the_second_instance_cannot_take_the_lock(qapp):
    first = single_instance_lock("duo-input-test-lock")
    second = single_instance_lock("duo-input-test-lock")

    assert first is not None
    assert second is None

    first.close()


def test_main_calls_configure_runtime(qapp, monkeypatch):
    """A helper main never reaches is missing production behavior."""
    called: list[bool] = []

    monkeypatch.setattr(app_module, "configure_runtime", lambda *args: called.append(True))
    monkeypatch.setattr(app_module, "single_instance_lock", lambda *args, **kwargs: object())
    monkeypatch.setattr(app_module, "start_window", lambda window: None)
    monkeypatch.setattr(app_module, "build_main_window", lambda **kwargs: object())
    monkeypatch.setattr(qapp, "exec", lambda: 0)

    app_module.main([])

    assert called == [True]
