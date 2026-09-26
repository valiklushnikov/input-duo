"""Production path: what main assembles, not only isolated test objects."""

from __future__ import annotations

import subprocess
import sys
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import QMessageBox

from duo_input import app as app_module
from duo_input.app import build_main_window, configure_runtime, single_instance_lock
from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.coordinator import ClipboardCoordinator
from duo_input.clipboard.pairing import PairingCandidate
from duo_input.clipboard.peer import PeerLink
from duo_input.clipboard.wire import (
    CAPABILITY_CLIPBOARD,
    CAPABILITY_FILES,
    Message,
    MessageType,
    PROTOCOL_MAJOR,
)
from duo_input.i18n import TranslationManager
from duo_input.transfer.model import (
    ENTRY_FILE,
    SkippedEntry,
    TransferEntry,
    TransferManifest,
    encode_manifest,
)
from duo_input.transfer.macos_files import MacFileReceiver
from duo_input.transfer.pipe import ChunkPipe
from duo_input.transfer.platform_files import MacReceiveRouter, UnsupportedPlatformError
from duo_input.transfer.service import FileTransferService, TransferState


def _settings(tmp_path, values: bool | dict[str, object]) -> QSettings:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    if isinstance(values, bool):
        settings.setValue("clipboard/enabled", values)
    else:
        for key, value in values.items():
            settings.setValue(key, value)
    return settings


def _runtime_of(application):
    from duo_input.app import _ClipboardRuntime

    for child in reversed(application.children()):
        if isinstance(child, _ClipboardRuntime):
            return child
    raise AssertionError("_ClipboardRuntime was not created")


class _ResumeBackend(QObject):
    snapshot_taken = Signal(object)
    resume_detected = Signal()

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def publish(self, _offer, _fetcher) -> None: ...

    def payload(self, _mime):
        return None


class _ResumeCountingCoordinator(ClipboardCoordinator):
    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.resume_count = 0

    def start(self) -> None:
        """The wiring test does not need to bind the production TCP port."""

    def recover_after_resume(self) -> None:
        self.resume_count += 1


def test_macos_wake_signal_reaches_the_clipboard_coordinator(
    qapp, qtbot, tmp_path, monkeypatch
):
    """Without runtime wiring the native wake observer cannot replace the stale link."""
    made: list[_ResumeBackend] = []

    def create_backend(_clipboard, parent=None):
        backend = _ResumeBackend(parent)
        made.append(backend)
        return backend

    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(app_module, "ClipboardCoordinator", _ResumeCountingCoordinator)
    monkeypatch.setattr(app_module, "create_backend", create_backend)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    coordinator = configure_runtime(qapp, window, settings)

    try:
        made[0].resume_detected.emit()
        assert coordinator.resume_count == 1
    finally:
        _runtime_of(qapp).stop()


class _FileBackend(QObject):
    """The external COM boundary, with the real backend's lifecycle contract."""

    publish_failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.callbacks: dict[str, object] = {}
        self.publications: list[tuple[TransferManifest, bytes]] = []
        self.starts = 0
        self.stops = 0
        self.is_running = False
        self.stop_hook = None

    def set_callbacks(
        self, open_pipe, request_read, close_pipe, on_operation_finished
    ) -> None:
        self.callbacks = {
            "open_pipe": open_pipe,
            "request_read": request_read,
            "close_pipe": close_pipe,
            "on_operation_finished": on_operation_finished,
        }

    def start(self) -> None:
        if self.is_running:
            return
        self.is_running = True
        self.starts += 1

    def stop(self) -> None:
        if not self.is_running:
            return
        self.stops += 1
        if self.stop_hook is not None:
            self.stop_hook()
        self.is_running = False

    def publish(self, manifest: TransferManifest, origin_marker: bytes) -> None:
        self.publications.append((manifest, origin_marker))


def _configure_file_runtime(qapp, qtbot, tmp_path, monkeypatch, values):
    # `_start_files` (app.py) has branched hard on the REAL `sys.platform`
    # since Task 8's macOS receiver wiring (commit 11dcd65): on darwin it
    # wires the receiver-shaped signals (`transfer_started`/.../`cancel`),
    # on every other platform it imports the real `windows_files.
    # ServiceCallbackGateway` and drives `_FileBackend` through
    # `set_callbacks`/`start`/`publish` - the COM-shaped interface this fake
    # implements and that every assertion below (`.starts`, `.publications`,
    # `.callbacks[...]`) depends on byte-for-byte. That interface has no
    # darwin equivalent (MacFileReceiver drives itself; see
    # `test_macos_receiver_wiring.py`), and `duo_input.transfer.windows_com`
    # cannot even be imported outside Windows (`ctypes.WINFUNCTYPE` does not
    # exist there - confirmed: this fixture has been unable to reach a
    # passing state on macOS since 11dcd65, independent of Task 16). There is
    # no test-only way to exercise the real win32 branch here without
    # reimplementing the COM vtable machinery `windows_com` provides, so this
    # whole fixture - and every test that calls it - is win32-only; skip
    # cleanly elsewhere rather than fail on an environment it was never able
    # to run on.
    if sys.platform != "win32":
        pytest.skip("Windows file-transfer COM plumbing; requires win32 (see fixture docstring)")
    made: list[_FileBackend] = []

    def create(parent=None, **_fileprovider_kwargs):
        # _fileprovider_kwargs: ``fileprovider_flag_enabled``/``fileprovider_backend``/
        # ``fileprovider_domain``/``fileprovider_client``/``fileprovider_os_supported``
        # (Task 16's ``_build_fileprovider_kwargs``, app.py ~447). These tests exercise
        # the staging/backend wiring above ``create_file_backend``, not File Provider
        # selection itself (that's ``test_macos_receiver_wiring.py`` /
        # ``test_runtime_wiring.py``'s rollout tests below), so the kwargs are accepted
        # and ignored here rather than asserted on.
        backend = _FileBackend(parent)
        made.append(backend)
        return backend

    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(app_module, "create_file_backend", create, raising=False)
    settings = _settings(tmp_path, values)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    return settings, window, _runtime_of(qapp), made


def _activate_transfer(transfer, transfer_id: str) -> ChunkPipe:
    _offer_transfer(transfer, transfer_id)
    return transfer.open_pipe(transfer_id, 0)


def _offer_transfer(transfer, transfer_id: str, skipped=()) -> None:
    manifest = TransferManifest(
        transfer_id=transfer_id,
        entries=(
            TransferEntry(
                path="payload.bin", kind=ENTRY_FILE, size=10, mtime_ns=1
            ),
        ),
        skipped=tuple(skipped),
    )
    # Файловое сообщение принимается только от пира, объявившего files/2.
    transfer.set_peer_capabilities(frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES}))
    transfer.handle_message(Message(MessageType.FILE_OFFER, {}, encode_manifest(manifest)))


class _ThreadRecordingTransfer(FileTransferService):
    """The real service with diagnostic observations at its thread boundary."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.operation_threads: list[tuple[str, int]] = []
        self.lifecycle_events: list[str] = []

    def open_pipe(self, transfer_id: str, entry_index: int) -> ChunkPipe:
        self.operation_threads.append(("open_pipe", threading.get_ident()))
        return super().open_pipe(transfer_id, entry_index)

    def request_read(self, pipe, offset: int, length: int) -> None:
        self.operation_threads.append(("request_read", threading.get_ident()))
        super().request_read(pipe, offset, length)

    def close_pipe(self, pipe, reason: str | None = None) -> None:
        self.operation_threads.append(("close_pipe", threading.get_ident()))
        super().close_pipe(pipe, reason)

    def finish_session(self, status: str, pipes=None) -> None:
        self.operation_threads.append(("finish_session", threading.get_ident()))
        super().finish_session(status, pipes)

    def detach_link(self) -> None:
        self.lifecycle_events.append("service-detach")
        super().detach_link()


def _run_in_worker(callback):
    done = threading.Event()
    result: list[object] = []
    errors: list[BaseException] = []
    worker_ids: list[int] = []

    def invoke() -> None:
        worker_ids.append(threading.get_ident())
        try:
            result.append(callback())
        except BaseException as error:  # the assertion reports the callback error
            errors.append(error)
        finally:
            done.set()

    worker = threading.Thread(target=invoke, name="runtime-file-callback-test")
    worker.start()
    return worker, done, result, errors, worker_ids


class _FileSnapshot:
    """Complete clipboard snapshot carrying one local file path."""

    payloads: dict[str, bytes] = {}

    def __init__(self, path: Path) -> None:
        self.file_paths = (str(path),)

    @property
    def is_empty(self) -> bool:
        return False

    def payload(self, _mime: str) -> bytes | None:
        return None


class _CountingEmptySnapshot:
    """A complete empty clipboard snapshot that counts file consumers."""

    payloads: dict[str, bytes] = {}

    def __init__(self) -> None:
        self.file_path_reads = 0

    @property
    def file_paths(self) -> tuple[str, ...]:
        self.file_path_reads += 1
        return ()

    @property
    def is_empty(self) -> bool:
        return True

    def payload(self, _mime: str) -> bytes | None:
        return None


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


def _click_role(dialog: QMessageBox, role: QMessageBox.ButtonRole) -> int:
    button = next(
        button for button in dialog.buttons() if dialog.buttonRole(button) is role
    )
    button.click()
    return 0


def test_nothing_is_built_while_sharing_is_off(qtbot, qapp, tmp_path, monkeypatch):
    """Подсистема общего буфера не собирается, пока фича выключена - но, в
    отличие от прежнего правила, ``TrayIcon`` в этот список запретов больше не
    входит: решение продукта от 2026-09-03 (§4) требует трей всегда, поэтому
    его конструктор здесь не запрещён, а проверен отдельным тестом ниже."""
    window = build_main_window(settings=_settings(tmp_path, False))
    qtbot.addWidget(window)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("the disabled path constructed a clipboard runtime object")

    for name in (
        "load_or_create",
        "TrustStore",
        "ClipboardCoordinator",
        "WindowsClipboardBackend",
    ):
        monkeypatch.setattr(app_module, name, forbidden, raising=False)

    assert configure_runtime(qapp, window, _settings(tmp_path, False)) is None


def test_the_tray_icon_exists_even_while_sharing_is_off(qtbot, qapp, tmp_path, monkeypatch):
    """КРИТИЧНО (§4, решение продукта 2026-09-03): без всегда-живого трея
    окно, которое теперь всегда прячется по закрытию, стало бы недоступным
    при выключенном общем буфере - показать нечем, выйти нечем."""
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    trays: list = []
    real_tray_icon = app_module.TrayIcon

    def _capturing_tray_icon(*args, **kwargs):
        tray = real_tray_icon(*args, **kwargs)
        trays.append(tray)
        return tray

    monkeypatch.setattr(app_module, "TrayIcon", _capturing_tray_icon)
    configure_runtime(qapp, window, settings)

    assert len(trays) == 1
    # Галочка отражает настоящую сохранённую настройку, а не принудительное
    # "включено" (старый дефект: трей раньше ставился только вместе с
    # подсистемой, поэтому его галочка была жёстко True).
    assert trays[0].sharing_action.isChecked() is False


def test_no_socket_listens_while_sharing_starts_disabled(qtbot, qapp, tmp_path):
    """§4: пока общий буфер выключен, ни один сокет не открывается - включая
    тот случай, когда фича никогда не включалась в этом запуске вовсе, а не
    только когда её выключили после включения (это уже покрыто
    ``test_switching_off_actually_releases_the_listening_socket``)."""
    from PySide6.QtNetwork import QTcpServer

    from duo_input.clipboard.coordinator import TCP_PORT

    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    configure_runtime(qapp, window, settings)

    probe = QTcpServer()
    try:
        assert probe.listen(port=TCP_PORT) is True, "порт должен быть свободен"
    finally:
        probe.close()


def test_the_tray_checkbox_starts_and_stops_the_subsystem_from_a_cold_start(
    qtbot, qapp, tmp_path, monkeypatch
):
    """Требование продукта: галочка в трее обязана работать в обе стороны
    даже тогда, когда подсистема ни разу не запускалась в этом сеансе -
    старт через настоящий пункт меню трея, а не через запись настройки."""
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    trays: list = []
    real_tray_icon = app_module.TrayIcon

    def _capturing_tray_icon(*args, **kwargs):
        tray = real_tray_icon(*args, **kwargs)
        trays.append(tray)
        return tray

    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(app_module, "TrayIcon", _capturing_tray_icon)
    configure_runtime(qapp, window, settings)

    tray = trays[0]
    assert tray.sharing_action.isChecked() is False

    tray.sharing_action.trigger()  # то же самое, что клик по пункту меню - вкл.

    assert bool(settings.value("clipboard/enabled", False, type=bool)) is True
    assert window.clipboard_page.sharing_checkbox.isChecked() is True
    assert tray.sharing_action.isChecked() is True

    tray.sharing_action.trigger()  # повторный клик - выкл.

    assert bool(settings.value("clipboard/enabled", False, type=bool)) is False
    assert window.clipboard_page.sharing_checkbox.isChecked() is False
    assert tray.sharing_action.isChecked() is False


def test_the_coordinator_is_built_when_sharing_is_on(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    coordinator = configure_runtime(qapp, window, settings)

    assert coordinator is not None
    coordinator.service._backend.stop()
    coordinator.stop()


def test_switching_sharing_on_with_the_real_checkbox_stops_quitting_with_the_window(
    qtbot, qapp, tmp_path, monkeypatch
):
    """C1: включение через настоящий переключатель обязано поднять подсистему.

    До исправления (M5 ревью) этот тест сам клал ``clipboard/enabled`` в
    настройки и звал ``configure_runtime`` напрямую - он проходил даже когда
    ``sharing_toggled`` был не подключён ни к чему, потому что ни разу не
    трогал сам чекбокс. Здесь подсистема стартует выключенной, и единственный
    способ её включить - реальный виджет на странице.
    """
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)

    window.clipboard_page.sharing_checkbox.setChecked(True)

    assert qapp.quitOnLastWindowClosed() is False
    assert bool(settings.value("clipboard/enabled", False, type=bool)) is True
    # Трей и страница обязаны показывать одно и то же состояние (C1).
    assert window.clipboard_page.sharing_checkbox.isChecked() is True

    # Выключение тем же путём обязано по-настоящему остановить подсистему -
    # а не только сменить надпись (§4).
    window.clipboard_page.sharing_checkbox.setChecked(False)

    assert qapp.quitOnLastWindowClosed() is True
    assert bool(settings.value("clipboard/enabled", False, type=bool)) is False


def test_switching_off_actually_releases_the_listening_socket(qtbot, qapp, tmp_path, monkeypatch):
    """C1: выключение обязано ДЕЙСТВИТЕЛЬНО остановить координатора, а не
    только сменить надпись - иначе порт остался бы занятым навсегда."""
    from PySide6.QtNetwork import QTcpServer

    from duo_input.clipboard.coordinator import TCP_PORT

    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)

    window.clipboard_page.sharing_checkbox.setChecked(True)
    probe = QTcpServer()
    assert probe.listen(port=TCP_PORT) is False, "порт должен быть занят, пока фича включена"

    window.clipboard_page.sharing_checkbox.setChecked(False)
    probe2 = QTcpServer()
    try:
        assert probe2.listen(port=TCP_PORT) is True, "выключение обязано освободить порт"
    finally:
        probe2.close()


def test_switching_off_disconnects_page_controls_from_the_stopped_coordinator(
    qtbot, qapp, tmp_path, monkeypatch
):
    """With sharing off, Pair/address edits must not revive the old runtime."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    coordinators = []
    real_coordinator = app_module.ClipboardCoordinator

    def capturing_coordinator(*args, **kwargs):
        coordinator = real_coordinator(*args, **kwargs)
        coordinators.append(coordinator)
        return coordinator

    monkeypatch.setattr(app_module, "ClipboardCoordinator", capturing_coordinator)
    configure_runtime(qapp, window, settings)
    window.clipboard_page.sharing_checkbox.setChecked(True)
    coordinator = coordinators[0]

    window.clipboard_page.sharing_checkbox.setChecked(False)
    window.clipboard_page.pair_button.click()
    window.clipboard_page.address_combo.lineEdit().setText("192.168.1.42")
    window.clipboard_page.address_combo.lineEdit().editingFinished.emit()

    try:
        assert coordinator._discovery._timer.isActive() is False
        assert coordinator.state.value == "unpaired"
        assert coordinator._manual_address == ""
    finally:
        coordinator.stop()


def test_repeated_enable_cycles_destroy_each_stopped_coordinator(
    qtbot, qapp, tmp_path, monkeypatch
):
    """Enable/disable cycles must not accumulate QApplication-owned runtimes."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    coordinators = []
    destroyed: list[int] = []
    real_coordinator = app_module.ClipboardCoordinator

    def capturing_coordinator(*args, **kwargs):
        coordinator = real_coordinator(*args, **kwargs)
        coordinators.append(coordinator)
        coordinator.destroyed.connect(
            lambda _object=None, number=len(coordinators): destroyed.append(number)
        )
        return coordinator

    monkeypatch.setattr(app_module, "ClipboardCoordinator", capturing_coordinator)
    configure_runtime(qapp, window, settings)

    for _ in range(3):
        window.clipboard_page.sharing_checkbox.setChecked(True)
        coordinator = coordinators[-1]
        window.clipboard_page.sharing_checkbox.setChecked(False)
        QCoreApplication.sendPostedEvents(coordinator, QEvent.Type.DeferredDelete)

    assert len(coordinators) == 3
    assert destroyed == [1, 2, 3]


def test_the_tray_checkbox_toggle_stops_the_subsystem_and_the_page_agrees(
    qtbot, qapp, tmp_path, monkeypatch
):
    """C1: трей и страница подключены к одному переключателю в обе стороны."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    trays: list = []
    real_tray_icon = app_module.TrayIcon

    def _capturing_tray_icon(*args, **kwargs):
        tray = real_tray_icon(*args, **kwargs)
        trays.append(tray)
        return tray

    monkeypatch.setattr(app_module, "TrayIcon", _capturing_tray_icon)

    configure_runtime(qapp, window, settings)
    window.clipboard_page.sharing_checkbox.setChecked(True)

    assert len(trays) == 1
    tray = trays[0]
    assert tray.sharing_action.isChecked() is True

    tray.sharing_action.trigger()  # то же самое, что клик по пункту меню

    assert bool(settings.value("clipboard/enabled", False, type=bool)) is False
    assert window.clipboard_page.sharing_checkbox.isChecked() is False
    assert qapp.quitOnLastWindowClosed() is True


def test_a_manual_address_typed_on_the_page_reaches_the_coordinator(
    qtbot, qapp, tmp_path, monkeypatch
):
    """C1: поле ручного адреса должно быть подключено к координатору."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    coordinator = configure_runtime(qapp, window, settings)

    window.clipboard_page.address_combo.lineEdit().setText("192.168.1.42")
    window.clipboard_page.address_combo.lineEdit().editingFinished.emit()

    assert coordinator._manual_address == "192.168.1.42"
    coordinator.service._backend.stop()
    coordinator.stop()


def test_toggling_autostart_on_the_page_writes_the_shortcut(qtbot, qapp, tmp_path, monkeypatch):
    """C2: автозапуск - мёртвый код, пока переключатель к нему не подключён."""
    from duo_input.persistence import autostart

    startup_dir = tmp_path / "startup"
    monkeypatch.setattr(autostart, "startup_directory", lambda: startup_dir)
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path / "app")
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)

    window.clipboard_page.autostart_checkbox.setChecked(True)

    assert autostart.is_enabled() is True
    assert bool(settings.value("clipboard/autostart", False, type=bool)) is True
    content = autostart.shortcut_path().read_text("utf-8")
    assert autostart.HIDDEN_START_ARGUMENT in content

    window.clipboard_page.autostart_checkbox.setChecked(False)

    assert autostart.is_enabled() is False
    assert bool(settings.value("clipboard/autostart", False, type=bool)) is False


def test_settings_read_at_startup_show_the_same_state_on_page_and_tray(
    qtbot, qapp, tmp_path, monkeypatch
):
    """C1: страница никогда не читала своё состояние - трей выставлялся
    принудительно checked=True, вне зависимости от настроек."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    settings.setValue("clipboard/autostart", True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    trays: list = []
    real_tray_icon = app_module.TrayIcon

    def _capturing_tray_icon(*args, **kwargs):
        tray = real_tray_icon(*args, **kwargs)
        trays.append(tray)
        return tray

    monkeypatch.setattr(app_module, "TrayIcon", _capturing_tray_icon)

    coordinator = configure_runtime(qapp, window, settings)

    assert window.clipboard_page.sharing_checkbox.isChecked() is True
    assert window.clipboard_page.autostart_checkbox.isChecked() is True
    assert trays[0].sharing_action.isChecked() is True
    coordinator.service._backend.stop()
    coordinator.stop()


def test_identity_failure_degrades_to_sharing_disabled_without_crashing(
    qtbot, qapp, tmp_path, monkeypatch
):
    """I6: отказ файлов идентичности не должен ронять всё приложение."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)

    def _broken_load_or_create(directory):
        raise OSError("нет доступа")

    monkeypatch.setattr(app_module, "load_or_create", _broken_load_or_create)
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)

    # Не должно бросить исключение наружу.
    window.clipboard_page.sharing_checkbox.setChecked(True)

    assert bool(settings.value("clipboard/enabled", False, type=bool)) is False
    assert window.clipboard_page.sharing_checkbox.isChecked() is False
    assert window.clipboard_page.events_list.count() >= 1


def test_a_state_change_is_recorded_in_the_recent_events_list(qtbot, qapp, tmp_path, monkeypatch):
    """I7: страница должна показывать последние события - иначе увидеть, что
    произошло, негде вообще (то же ревью отметило это для I5)."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    coordinator = configure_runtime(qapp, window, settings)

    coordinator.event_logged.emit("тестовое событие")

    assert window.clipboard_page.events_list.item(0).text() == "тестовое событие"
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
    shown: list[QMessageBox] = []
    confirmed: list[PairingCandidate] = []

    def accept(dialog):
        shown.append(dialog)
        return _click_role(dialog, QMessageBox.ButtonRole.AcceptRole)

    monkeypatch.setattr(QMessageBox, "exec", accept)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(coordinator, "confirm_pairing", confirmed.append)

    coordinator.pairing_code_ready.emit("004271", candidate)

    assert shown[0].parent() is window
    assert "DESKTOP-ALPHA" in shown[0].text()
    assert "004271" in shown[0].text()
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
        "exec",
        lambda dialog: _click_role(dialog, QMessageBox.ButtonRole.RejectRole),
    )
    monkeypatch.setattr(
        QMessageBox, "question", lambda *_args: QMessageBox.StandardButton.No
    )
    monkeypatch.setattr(coordinator, "reject_pairing", rejected.append, raising=False)

    coordinator.pairing_code_ready.emit("918205", candidate)

    assert rejected == [candidate]
    coordinator.service._backend.stop()
    coordinator.stop()


def test_pairing_dialog_renders_an_untrusted_machine_name_as_plain_text(qtbot, qapp):
    window = build_main_window(transport_factory=lambda: None)
    qtbot.addWidget(window)
    candidate = _candidate(machine_name="<b>NOT THE REAL NAME</b>")

    dialog, _accept_button = app_module._pairing_confirmation_dialog(
        window, "004271", candidate
    )

    assert dialog.textFormat() is Qt.TextFormat.PlainText
    assert "<b>NOT THE REAL NAME</b>" in dialog.text()
    assert "004271" in dialog.text()
    dialog.deleteLater()


@pytest.mark.parametrize(
    ("language", "accept_text", "reject_text"),
    (
        ("ru", "Связать", "Отказать"),
        ("en", "Pair", "Reject"),
    ),
)
def test_pairing_dialog_owns_localized_button_labels(
    qtbot, qapp, tmp_path, language, accept_text, reject_text
):
    manager = TranslationManager(
        qapp,
        settings=QSettings(
            str(tmp_path / f"{language}.ini"), QSettings.Format.IniFormat
        ),
    )
    assert manager.set_language(language, remember=False) is True
    window = build_main_window(translations=manager, transport_factory=lambda: None)
    qtbot.addWidget(window)

    dialog, accept_button = app_module._pairing_confirmation_dialog(
        window, "918205", _candidate()
    )
    labels = {
        dialog.buttonRole(button): button.text()
        for button in dialog.buttons()
    }

    assert dialog.standardButtons() == QMessageBox.StandardButton.NoButton
    assert labels[QMessageBox.ButtonRole.AcceptRole] == accept_text
    assert labels[QMessageBox.ButtonRole.RejectRole] == reject_text
    assert accept_button.text() == accept_text
    dialog.deleteLater()


def test_the_second_instance_cannot_take_the_lock(qapp):
    first = single_instance_lock("duo-input-test-lock")
    second = single_instance_lock("duo-input-test-lock")

    assert first is not None
    assert second is None

    first.close()


def test_a_second_instance_connecting_raises_the_first_window(qtbot, qapp):
    """I7: второй запуск обязан поднять окно первого, а не молча завершиться."""
    from PySide6.QtNetwork import QLocalSocket

    lock = single_instance_lock("duo-input-test-raise")
    assert lock is not None
    window = build_main_window(transport_factory=lambda: None)
    qtbot.addWidget(window)
    window.hide()
    lock.newConnection.connect(lambda: app_module._raise_existing_window(lock, window))

    second_instance_probe = QLocalSocket()
    second_instance_probe.connectToServer("duo-input-test-raise")
    assert second_instance_probe.waitForConnected(1000) is True

    qtbot.waitUntil(lambda: window.isVisible(), timeout=2000)

    second_instance_probe.disconnectFromServer()
    lock.close()


class _FakeSignal:
    def connect(self, callback) -> None:
        pass


class _FakeLock:
    """Достаточно замка, чтобы main() мог подписаться на newConnection."""

    newConnection = _FakeSignal()


def test_main_calls_configure_runtime(qapp, monkeypatch):
    """A helper main never reaches is missing production behavior."""
    called: list[bool] = []

    monkeypatch.setattr(app_module, "configure_runtime", lambda *args: called.append(True))
    monkeypatch.setattr(app_module, "single_instance_lock", lambda *args, **kwargs: _FakeLock())
    monkeypatch.setattr(app_module, "start_window", lambda window, **kwargs: None)
    monkeypatch.setattr(app_module, "build_main_window", lambda **kwargs: object())
    monkeypatch.setattr(qapp, "exec", lambda: 0)

    app_module.main([])

    assert called == [True]


def test_main_survives_an_unexpected_configure_runtime_failure(qapp, monkeypatch):
    """I6: последний рубеж - main() не должен упасть, даже если подсистема
    сломалась непредвиденно (не только предвиденным OSError на файлах
    идентичности, который ловит сам _start())."""

    def _boom(*_args):
        raise RuntimeError("непредвиденный сбой")

    monkeypatch.setattr(app_module, "configure_runtime", _boom)
    monkeypatch.setattr(app_module, "single_instance_lock", lambda *args, **kwargs: _FakeLock())
    monkeypatch.setattr(app_module, "start_window", lambda window, **kwargs: None)
    monkeypatch.setattr(app_module, "build_main_window", lambda **kwargs: object())
    monkeypatch.setattr(qapp, "exec", lambda: 0)

    result = app_module.main([])

    assert result == 0


def test_with_files_enabled_the_transfer_service_is_actually_created(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, _window, runtime, made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        assert runtime.transfer is not None
        assert made == [runtime.file_backend]
        assert made[0].starts == 1
    finally:
        runtime.stop()


def test_with_files_disabled_no_transfer_service_exists(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, _window, runtime, made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": False},
    )
    try:
        assert runtime.transfer is None
        assert made == []
    finally:
        runtime.stop()


def test_the_tray_toggle_starts_the_subsystem_through_the_real_menu_item(
    qapp, qtbot, tmp_path, monkeypatch
):
    settings, _window, runtime, made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": False},
    )
    try:
        runtime.tray.files_action.trigger()

        assert runtime.transfer is not None
        assert made == [runtime.file_backend]
        assert settings.value("clipboard/files_enabled", type=bool) is True
    finally:
        runtime.stop()


def test_the_page_toggle_stops_the_subsystem_through_the_real_checkbox(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, window, runtime, made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        window.clipboard_page.files_checkbox.setChecked(False)

        assert runtime.transfer is None
        assert runtime.file_backend is None
        assert made[0].stops == 1
    finally:
        runtime.stop()


def test_both_toggles_show_the_same_saved_state_at_startup(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        assert window.clipboard_page.files_checkbox.isChecked()
        assert runtime.tray.files_action.isChecked()
    finally:
        runtime.stop()


def test_files_cannot_be_enabled_while_the_clipboard_itself_is_off(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, _window, runtime, made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": False, "clipboard/files_enabled": True},
    )
    try:
        assert runtime.transfer is None
        assert made == []
    finally:
        runtime.stop()


def test_saved_files_start_when_the_clipboard_is_enabled_later(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, window, runtime, made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": False, "clipboard/files_enabled": True},
    )
    try:
        window.clipboard_page.sharing_checkbox.setChecked(True)

        assert runtime.transfer is not None
        assert made == [runtime.file_backend]
    finally:
        runtime.stop()


def test_progress_from_the_service_reaches_the_page(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        runtime.transfer.transfer_progress.emit(1_500_000_000, 8_800_000_000)

        assert "1.4 \u0413\u0411" in window.clipboard_page.transfer_label.text()
    finally:
        runtime.stop()


def test_cancelling_from_the_page_reaches_the_service(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        _activate_transfer(runtime.transfer, "cancel-me")
        runtime.transfer.transfer_progress.emit(1, 100)

        window.clipboard_page.cancel_button.click()

        assert runtime.transfer.state is TransferState.CANCELLED
    finally:
        runtime.stop()


def test_the_peers_capabilities_reach_the_transfer_service(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, _window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        runtime.coordinator.capabilities_known.emit(
            frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES})
        )

        assert runtime.transfer.peer_supports_files
    finally:
        runtime.stop()


def test_a_new_transfer_is_seeded_from_capabilities_already_known(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": False},
    )
    try:
        runtime.coordinator._on_message(
            Message(
                MessageType.HELLO,
                {
                    "protocol_major": PROTOCOL_MAJOR,
                    "capabilities": [CAPABILITY_CLIPBOARD, CAPABILITY_FILES],
                },
                b"",
            )
        )

        window.clipboard_page.files_checkbox.setChecked(True)

        assert runtime.coordinator.peer_capabilities == frozenset(
            {CAPABILITY_CLIPBOARD, CAPABILITY_FILES}
        )
        assert runtime.transfer.peer_supports_files
    finally:
        runtime.stop()


def test_with_files_disabled_no_com_object_is_registered_on_the_clipboard(
    qapp, qtbot, tmp_path, monkeypatch
):
    constructions: list[object] = []

    def forbidden_backend(parent=None):
        constructions.append(parent)
        raise AssertionError("disabled file transfer constructed its COM backend")

    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(
        app_module, "create_file_backend", forbidden_backend, raising=False
    )
    settings = _settings(
        tmp_path,
        {"clipboard/enabled": True, "clipboard/files_enabled": False},
    )
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    try:
        assert runtime.transfer is None
        assert runtime.file_backend is None
        assert constructions == []
    finally:
        runtime.stop()


def test_disabling_and_reenabling_files_disconnects_the_old_cancel_slot(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        old_transfer = runtime.transfer
        _activate_transfer(old_transfer, "old")

        window.clipboard_page.files_checkbox.setChecked(False)
        window.clipboard_page.files_checkbox.setChecked(True)
        new_transfer = runtime.transfer
        _activate_transfer(new_transfer, "new")
        new_transfer.transfer_progress.emit(1, 10)
        # Остановка уже вывела старый сервис из сессии, поэтому его состояние
        # не отличает "отмена дошла" от "не дошла": смотрим на сам вызов.
        old_cancels: list[str] = []
        old_transfer.finish_session = lambda status, pipes=None: old_cancels.append(status)

        window.clipboard_page.cancel_button.click()

        assert old_cancels == []
        assert new_transfer.state is TransferState.CANCELLED
    finally:
        runtime.stop()


def test_disabling_and_reenabling_files_disconnects_old_capability_updates(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        old_transfer = runtime.transfer
        window.clipboard_page.files_checkbox.setChecked(False)
        window.clipboard_page.files_checkbox.setChecked(True)
        new_transfer = runtime.transfer

        runtime.coordinator.capabilities_known.emit(
            frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES})
        )

        assert not old_transfer.peer_supports_files
        assert new_transfer.peer_supports_files
    finally:
        runtime.stop()


def test_disabling_and_reenabling_files_keeps_one_snapshot_subscription(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        for _ in range(2):
            window.clipboard_page.files_checkbox.setChecked(False)
            window.clipboard_page.files_checkbox.setChecked(True)

        snapshot = _CountingEmptySnapshot()
        runtime._backend.snapshot_taken.emit(snapshot)

        assert snapshot.file_path_reads == 1
    finally:
        runtime.stop()


def test_disabling_and_reenabling_clipboard_restarts_saved_file_transfer(
    qapp, qtbot, tmp_path, monkeypatch
):
    settings, window, runtime, made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        old_transfer = runtime.transfer
        old_backend = runtime.file_backend

        window.clipboard_page.sharing_checkbox.setChecked(False)
        assert runtime.transfer is None
        assert old_backend.stops == 1

        window.clipboard_page.sharing_checkbox.setChecked(True)

        assert runtime.transfer is not old_transfer
        assert runtime.file_backend is made[1]
        assert settings.value("clipboard/files_enabled", type=bool) is True
    finally:
        runtime.stop()


def test_stopped_clipboard_snapshot_does_not_reach_the_restarted_transfer(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        assert runtime.transfer is not None
        old_clipboard_backend = runtime._backend
        window.clipboard_page.sharing_checkbox.setChecked(False)
        window.clipboard_page.sharing_checkbox.setChecked(True)

        snapshot = _CountingEmptySnapshot()
        old_clipboard_backend.snapshot_taken.emit(snapshot)

        assert snapshot.file_path_reads == 0
    finally:
        runtime.stop()


def test_importing_app_does_not_eagerly_import_the_windows_file_backend():
    source_root = Path(__file__).resolve().parents[2] / "src"
    code = (
        "import sys; "
        f"sys.path.insert(0, {str(source_root)!r}); "
        "import duo_input.app; "
        "assert 'duo_input.transfer.windows_files' not in sys.modules"
    )

    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, timeout=30
    )

    assert result.returncode == 0, result.stderr


def test_open_pipe_callback_returns_the_real_pipe_after_running_on_qt_thread(
    qapp, qtbot, tmp_path, monkeypatch
):
    """Mutation: binding open_pipe directly executes service state on COM's STA."""
    monkeypatch.setattr(app_module, "FileTransferService", _ThreadRecordingTransfer)
    _settings_, _window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        transfer = runtime.transfer
        _offer_transfer(transfer, "thread-open")
        transfer.operation_threads.clear()
        worker, done, result, errors, worker_ids = _run_in_worker(
            lambda: runtime.file_backend.callbacks["open_pipe"]("thread-open", 0)
        )

        qtbot.waitUntil(done.is_set, timeout=5000)
        worker.join(timeout=1.0)

        assert errors == []
        assert len(result) == 1 and isinstance(result[0], ChunkPipe)
        assert transfer.state is TransferState.TRANSFERRING
        assert result[0] in transfer._streams
        assert worker_ids[0] != threading.get_ident()
        assert transfer.operation_threads
        assert all(
            operation_thread == threading.get_ident()
            for _operation, operation_thread in transfer.operation_threads
        )
    finally:
        runtime.stop()


def test_one_way_backend_callbacks_reach_real_service_and_page_on_qt_thread(
    qapp, qtbot, tmp_path, monkeypatch
):
    """Mutation: direct request/close/finish delivery mutates Qt state on COM's STA."""
    monkeypatch.setattr(app_module, "FileTransferService", _ThreadRecordingTransfer)
    _settings_, window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        transfer = runtime.transfer
        pipe = _activate_transfer(transfer, "thread-one-way")
        transfer.operation_threads.clear()

        def in_flight():
            stream = transfer._streams.get(pipe)
            return stream is not None and stream.read is not None and (
                stream.read.offset, stream.read.length
            ) == (0, 10)

        worker, _done, _result, errors, _worker_ids = _run_in_worker(
            lambda: runtime.file_backend.callbacks["request_read"](pipe, 0, 10)
        )
        worker.join(timeout=1.0)
        assert not worker.is_alive()
        assert errors == []
        qtbot.waitUntil(in_flight, timeout=5000)

        worker, _done, _result, errors, _worker_ids = _run_in_worker(
            lambda: runtime.file_backend.callbacks["close_pipe"](pipe)
        )
        worker.join(timeout=1.0)
        assert not worker.is_alive()
        assert errors == []
        qtbot.waitUntil(lambda: pipe.finished, timeout=5000)
        assert pipe not in transfer._streams

        transfer.transfer_progress.emit(1, 10)
        assert window.clipboard_page.transfer_label.text()
        worker, _done, _result, errors, _worker_ids = _run_in_worker(
            lambda: runtime.file_backend.callbacks["on_operation_finished"](0, (pipe,))
        )
        worker.join(timeout=1.0)
        assert not worker.is_alive()
        assert errors == []
        qtbot.waitUntil(
            lambda: transfer.state is TransferState.COMPLETED, timeout=5000
        )
        qtbot.waitUntil(
            lambda: window.clipboard_page.transfer_label.text() == "", timeout=5000
        )

        assert window.clipboard_page.transfer_label.text() == ""
        assert [name for name, _thread in transfer.operation_threads] == [
            "request_read",
            "close_pipe",
            "finish_session",
        ]
        assert all(
            operation_thread == threading.get_ident()
            for _operation, operation_thread in transfer.operation_threads
        )
    finally:
        runtime.stop()


def test_open_pipe_callback_called_on_qt_thread_does_not_deadlock(
    qapp, qtbot, tmp_path, monkeypatch
):
    """Mutation: an unconditional blocking queued call deadlocks its owning thread."""
    monkeypatch.setattr(app_module, "FileTransferService", _ThreadRecordingTransfer)
    _settings_, _window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        _offer_transfer(runtime.transfer, "same-thread")

        pipe = runtime.file_backend.callbacks["open_pipe"]("same-thread", 0)

        assert isinstance(pipe, ChunkPipe)
        assert runtime.transfer.operation_threads[0] == (
            "open_pipe",
            threading.get_ident(),
        )
    finally:
        runtime.stop()


def test_file_shutdown_invalidates_callbacks_and_detaches_before_stopping_backend(
    qapp, qtbot, tmp_path, monkeypatch
):
    """Callbacks die first, then the pipes are closed, then the STA is joined.

    Joining the STA while a Read is blocked on a pipe would freeze the GUI for
    the whole join timeout; invalidating the gateway first keeps a callback
    that races the shutdown away from the detached service.
    """
    monkeypatch.setattr(app_module, "FileTransferService", _ThreadRecordingTransfer)
    _settings_, window, runtime, made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    old_transfer = runtime.transfer
    old_backend = made[0]
    callbacks = dict(old_backend.callbacks)
    pipe = _activate_transfer(old_transfer, "shutdown")
    old_transfer.operation_threads.clear()
    old_transfer.lifecycle_events.clear()
    pipe_closed_at_stop = []

    def callback_while_stopping() -> None:
        old_transfer.lifecycle_events.append("backend-stop")
        pipe_closed_at_stop.append(pipe.closed_reason is not None)
        worker, _done, _result, _errors, _worker_ids = _run_in_worker(
            lambda: callbacks["on_operation_finished"](0, (pipe,))
        )
        worker.join(timeout=1.0)
        assert not worker.is_alive()

    old_backend.stop_hook = callback_while_stopping
    try:
        window.clipboard_page.files_checkbox.setChecked(False)

        assert old_transfer.lifecycle_events == ["service-detach", "backend-stop"]
        assert pipe_closed_at_stop == [True]
        assert old_transfer.operation_threads == []

        worker, _done, _result, errors, _worker_ids = _run_in_worker(
            lambda: callbacks["on_operation_finished"](0, (pipe,))
        )
        worker.join(timeout=1.0)
        assert not worker.is_alive()
        assert errors == []
        QCoreApplication.processEvents()
        assert old_transfer.operation_threads == []
    finally:
        runtime.stop()


def test_nonempty_local_snapshot_creates_a_real_service_offer(
    qapp, qtbot, tmp_path, monkeypatch
):
    """Mutation: dropping the snapshot subscription leaves local files unoffered."""
    _settings_, _window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    source = tmp_path / "offered.txt"
    source.write_bytes(b"real offer")
    link = PeerLink(load_or_create(tmp_path))
    offers: list[str] = []
    try:
        runtime._attach_file_link(runtime.transfer, link)
        runtime.transfer.set_peer_capabilities(frozenset({CAPABILITY_FILES}))
        runtime.transfer.offer_sent.connect(offers.append)

        runtime._backend.snapshot_taken.emit(_FileSnapshot(source))

        assert len(offers) == 1
        assert runtime.transfer.snapshots.transfer_ids == (offers[0],)
    finally:
        link.close()
        runtime.stop()


def test_remote_offer_is_published_and_publish_failure_reaches_page(
    qapp, qtbot, tmp_path, monkeypatch
):
    """Mutation: omitting either backend publication or page failure routing is silent."""
    _settings_, window, runtime, made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        _offer_transfer(runtime.transfer, "remote-publish")

        assert runtime.transfer.state is TransferState.OFFERED
        assert len(made[0].publications) == 1
        manifest, marker = made[0].publications[0]
        assert manifest.transfer_id == "remote-publish"
        assert marker == b"remote-publish"

        before = window.clipboard_page.events_list.count()
        made[0].publish_failed.emit("clipboard busy")

        assert window.clipboard_page.events_list.count() == before + 1
        assert "clipboard busy" in window.clipboard_page.events_list.item(0).text()
    finally:
        runtime.stop()


def test_unsupported_backend_after_user_toggle_rolls_back_setting_and_controls(
    qapp, qtbot, tmp_path, monkeypatch
):
    """Mutation: returning early without rollback leaves a saved, checked false promise."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)

    def unsupported(_parent=None, **_fileprovider_kwargs):
        raise UnsupportedPlatformError("test platform")

    monkeypatch.setattr(app_module, "create_file_backend", unsupported)
    settings = _settings(
        tmp_path,
        {"clipboard/enabled": True, "clipboard/files_enabled": False},
    )
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    try:
        window.clipboard_page.files_checkbox.click()

        assert settings.value("clipboard/files_enabled", type=bool) is False
        assert not window.clipboard_page.files_checkbox.isChecked()
        assert not runtime.tray.files_action.isChecked()
        assert runtime.transfer is None
        assert runtime.file_backend is None
    finally:
        runtime.stop()


def test_repeated_enable_disable_keeps_only_current_callback_delivery(
    qapp, qtbot, tmp_path, monkeypatch
):
    """Mutation: retaining old gateways or duplicating callbacks delivers twice."""
    monkeypatch.setattr(app_module, "FileTransferService", _ThreadRecordingTransfer)
    _settings_, window, runtime, made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    old_transfer = runtime.transfer
    old_callback = made[0].callbacks["on_operation_finished"]
    try:
        window.clipboard_page.files_checkbox.setChecked(False)
        window.clipboard_page.files_checkbox.setChecked(True)
        current_transfer = runtime.transfer
        pipe = _activate_transfer(current_transfer, "current")
        old_transfer.operation_threads.clear()
        current_transfer.operation_threads.clear()

        workers = [
            _run_in_worker(lambda: old_callback(0, (pipe,)))[0],
            _run_in_worker(
                lambda: runtime.file_backend.callbacks["on_operation_finished"](0, (pipe,))
            )[0],
        ]
        for worker in workers:
            worker.join(timeout=1.0)
            assert not worker.is_alive()
        qtbot.waitUntil(
            lambda: current_transfer.state is TransferState.COMPLETED, timeout=5000
        )

        assert old_transfer.operation_threads == []
        assert [
            operation
            for operation, _thread in current_transfer.operation_threads
            if operation == "finish_session"
        ] == ["finish_session"]
    finally:
        runtime.stop()


def test_cancel_is_available_as_soon_as_a_transfer_starts(
    qapp, qtbot, tmp_path, monkeypatch
):
    """A paste stalled before its first chunk must still be cancellable."""
    _settings_, window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        assert not window.clipboard_page.cancel_button.isEnabled()

        _activate_transfer(runtime.transfer, "stalled")

        assert window.clipboard_page.cancel_button.isEnabled()
        window.clipboard_page.cancel_button.click()
        assert runtime.transfer.state is TransferState.CANCELLED
        assert not window.clipboard_page.cancel_button.isEnabled()
    finally:
        runtime.stop()


def test_skipped_entries_of_an_offer_are_reported_on_the_page(
    qapp, qtbot, tmp_path, monkeypatch
):
    _settings_, window, runtime, _made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        before = window.clipboard_page.events_list.count()

        _offer_transfer(
            runtime.transfer,
            "with-skips",
            skipped=(
                SkippedEntry(path="Private/junction", reason="reparse_point"),
                SkippedEntry(path="Private/locked", reason="unreadable"),
            ),
        )

        assert window.clipboard_page.events_list.count() == before + 1
        text = window.clipboard_page.events_list.item(0).text()
        assert "2" in text
        assert "junction" in text and "locked" in text
        assert "Private" not in text
    finally:
        runtime.stop()


def test_file_shutdown_does_not_wait_on_a_read_blocked_in_the_sta(
    qapp, qtbot, tmp_path, monkeypatch
):
    """A Read blocked on its pipe is woken before the STA join begins."""
    _settings_, window, runtime, made = _configure_file_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    transfer = runtime.transfer
    pipe = _activate_transfer(transfer, "blocked")
    woken = threading.Event()

    def blocked_read() -> None:
        try:
            pipe.wait(30.0)
        except Exception:  # PipeClosed is the expected wake-up
            pass
        woken.set()

    reader = threading.Thread(target=blocked_read, daemon=True)
    reader.start()
    reader_released_before_join = []
    made[0].stop_hook = lambda: reader_released_before_join.append(woken.wait(1.0))
    try:
        window.clipboard_page.files_checkbox.setChecked(False)

        assert reader_released_before_join == [True]
    finally:
        runtime.stop()


# --------------------------------------------------------------------------
# Task 20: rollout flag (default OFF), per-offer fallback, and the
# fp_backend_selected_{file_provider,staging} telemetry counter (Task 17's
# registry, wired at the real selection site in ``MacReceiveRouter``).
#
# ``create_file_backend`` is deliberately left unmocked here (mirrors
# ``test_macos_receiver_wiring.py``): the point is that the REAL
# ``MacReceiveRouter`` picks a REAL ``FileProviderBackend``/``MacFileReceiver``
# and that the REAL Task 17 counters move - not that some fake object was
# asked the right question. Only the XPC/domain I/O boundary (an actual macOS
# File Provider domain + extension connection) is faked, exactly like
# ``FakeDomain``/``FakeFPClient`` in test_fileprovider_backend_selection.py.
# --------------------------------------------------------------------------


class _FakeFPDomainManager:
    """Stands in for the real ``FileProviderDomainManager`` (an actual
    fileproviderd domain, Task 6) - these tests exercise the router's
    SELECTION decision (Task 16) and its telemetry (Task 20), not the domain
    machinery itself (covered by its own suite, test_fileprovider_domain.py)."""

    def __init__(self, parent=None) -> None:
        self.domain_identifier = "test.duo-input.fileprovider"
        self.is_ready = True
        self.ensure_domain_calls = 0

    def ensure_domain(self) -> None:
        self.ensure_domain_calls += 1


class _FakeFPServiceClient:
    """Stands in for the real ``FileProviderServiceClient`` (real XPC to the
    extension) - ``remote()`` is the only thing ``MacReceiveRouter._select_backend``
    reads from it."""

    def __init__(self, parent=None) -> None:
        self._remote = object()
        self.domain_id = None
        self.connect_calls = 0

    def set_domain(self, domain_id) -> None:
        self.domain_id = domain_id

    def connect_service(self) -> None:
        self.connect_calls += 1

    def remote(self):
        return self._remote


def _wire_fake_fileprovider_boundary(monkeypatch, qapp, *, domain_ready: bool):
    """Patch the two I/O-boundary classes ``app.py``'s
    ``_build_fileprovider_kwargs`` local-imports at call time, so
    ``configure_runtime`` builds a REAL ``FileProviderBackend`` wired to fake
    domain/client objects instead of touching real fileproviderd/XPC."""
    domains: list[_FakeFPDomainManager] = []
    clients: list[_FakeFPServiceClient] = []

    def make_domain(parent=None):
        domain = _FakeFPDomainManager(parent)
        domain.is_ready = domain_ready
        domains.append(domain)
        return domain

    def make_client(parent=None):
        client = _FakeFPServiceClient(parent)
        clients.append(client)
        return client

    monkeypatch.setattr(
        "duo_input.transfer.fileprovider_domain.FileProviderDomainManager", make_domain
    )
    monkeypatch.setattr(
        "duo_input.transfer.fileprovider_client.FileProviderServiceClient", make_client
    )
    return domains, clients


def _fp_manifest(transfer_id: str) -> TransferManifest:
    return TransferManifest(
        transfer_id=transfer_id,
        entries=(TransferEntry(path="a.bin", kind=ENTRY_FILE, size=10, mtime_ns=1),),
    )


def _configure_rollout_runtime(qapp, qtbot, tmp_path, monkeypatch, values):
    """Same production path as ``test_macos_receiver_wiring.py``'s
    ``_configure`` - ``create_file_backend`` is real. The stubbed prompt (in
    place of the default "ask" mode's modal) keeps every test focused on
    backend SELECTION, never calling ``authorize()`` at all - the deeper
    publish/authorize flow is covered by Task 7/8/16's own suites, and would
    otherwise need a full fake XPC remote object here."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, dict(values))
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    monkeypatch.setattr(runtime, "_prompt_file_authorization", lambda manifest: None)
    return settings, window, runtime


@pytest.mark.skipif(sys.platform != "darwin", reason="File Provider rollout is darwin-only")
def test_the_fileprovider_flag_defaults_to_false_on_first_run(
    qapp, qtbot, tmp_path, monkeypatch
):
    """A settings file with no ``clipboard/fileprovider_enabled`` key at all
    (a genuinely first run) must read as OFF - Stage 1 default (ruling #1:
    hard block on shipping default True)."""
    _settings_, _window, runtime = _configure_rollout_runtime(
        qapp, qtbot, tmp_path, monkeypatch, {"clipboard/enabled": True}
    )
    try:
        assert runtime._fileprovider_flag_enabled() is False
    finally:
        runtime.stop()


@pytest.mark.skipif(sys.platform != "darwin", reason="File Provider rollout is darwin-only")
def test_flag_on_with_fileprovider_ready_routes_the_offer_to_file_provider_and_counts_it(
    qapp, qtbot, tmp_path, monkeypatch
):
    domains, clients = _wire_fake_fileprovider_boundary(monkeypatch, qapp, domain_ready=True)
    _settings_, _window, runtime = _configure_rollout_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {
            "clipboard/enabled": True,
            "clipboard/files_enabled": True,
            "clipboard/fileprovider_enabled": True,
        },
    )
    try:
        router = runtime.file_backend
        assert isinstance(router, MacReceiveRouter)
        assert isinstance(router._staging, MacFileReceiver)
        fp_backend = router._fp
        assert fp_backend is not None
        assert domains and clients  # the real boundary classes were constructed

        router.handle_offer(_fp_manifest("gen-fp"))

        assert router._active_backend is fp_backend
        assert fp_backend.counters["fp_backend_selected_file_provider"] == 1
        assert "fp_backend_selected_staging" not in fp_backend.counters
    finally:
        runtime.stop()


@pytest.mark.skipif(sys.platform != "darwin", reason="File Provider rollout is darwin-only")
def test_flag_off_routes_the_offer_to_staging_without_building_file_provider_and_counts_it(
    qapp, qtbot, tmp_path, monkeypatch
):
    """Stage 1 default: the flag is off, so no domain/client/backend is even
    constructed (app.py's ``_build_fileprovider_kwargs`` docstring) - and the
    staging selection still increments the counter through the router's own
    fallback registry (ruling #2: no FP backend instance exists to hold it)."""
    _settings_, _window, runtime = _configure_rollout_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {"clipboard/enabled": True, "clipboard/files_enabled": True},
    )
    try:
        router = runtime.file_backend
        assert isinstance(router, MacReceiveRouter)
        assert router._fp is None

        router.handle_offer(_fp_manifest("gen-staging"))

        assert router._active_backend is router._staging
        assert router.selection_counters["fp_backend_selected_staging"] == 1
    finally:
        runtime.stop()


@pytest.mark.skipif(sys.platform != "darwin", reason="File Provider rollout is darwin-only")
def test_toggling_the_flag_mid_generation_does_not_migrate_the_active_transfer(
    qapp, qtbot, tmp_path, monkeypatch
):
    """Rollback is 'flip the flag' precisely because the per-offer choice is
    fixed once (ruling #4): flipping mid-flight must not touch the transfer
    already in progress, only the NEXT offer."""
    _wire_fake_fileprovider_boundary(monkeypatch, qapp, domain_ready=True)
    settings, _window, runtime = _configure_rollout_runtime(
        qapp,
        qtbot,
        tmp_path,
        monkeypatch,
        {
            "clipboard/enabled": True,
            "clipboard/files_enabled": True,
            "clipboard/fileprovider_enabled": True,
        },
    )
    try:
        router = runtime.file_backend
        fp_backend = router._fp
        router.handle_offer(_fp_manifest("gen-1"))
        assert router._active_backend is fp_backend

        # Flip the flag off while "gen-1" is still in flight.
        settings.setValue("clipboard/fileprovider_enabled", False)

        assert router._active_backend is fp_backend  # unchanged - no migration

        forwarded: list[object] = []
        monkeypatch.setattr(fp_backend, "handle_message", forwarded.append)
        message = Message(MessageType.FILE_CHUNK, {}, b"")
        router.handle_message(message)
        assert forwarded == [message]  # still addressed to the same backend

        # The NEXT offer, after the toggle, is a fresh per-offer decision -
        # it goes to staging without restarting anything.
        router.handle_offer(_fp_manifest("gen-2"))
        assert router._active_backend is router._staging
    finally:
        runtime.stop()


# --------------------------------------------------------------------------
# Task 14: wire the address exchange into the running app, persist the
# manual address, and prove the chain from the board through to the page -
# every piece above was unit-tested in isolation and none of it was reachable
# from ``main()`` (see project memory: "Tested but never called").
#
# Round 1 review fixes:
#   1. The first exchange must not wait for the 5 s periodic timer - it is
#      kicked by ``window.service.operation_succeeded`` on ``connect_device``
#      (``_ClipboardRuntime._on_device_connected``). The e2e test below
#      mirrors production ordering (runtime first, device connected after)
#      and waits well under the 5 s interval, so a pass can only come from
#      the connect-triggered tick.
#   2. The manual address field is connected to ``runtime.set_manual_address``
#      once, in ``configure_runtime`` - not per ``_start``/``_stop`` cycle -
#      so it is saved and restored regardless of whether sharing is on.
#   3. Every test below that leaves sharing switched on stops the whole
#      runtime (``_runtime_of(qapp).stop()``), not just the coordinator, so
#      no test leaks a live 5 s ``AddressExchange`` timer or an open
#      emulator link into the rest of the session.
# --------------------------------------------------------------------------


def test_default_link_factory_is_patched_in_tests(monkeypatch):
    """Guards the controller ruling itself: a real U1 and a real U2 are
    plugged into this machine, so ``EndpointService``'s default factory -
    which opens the first real U2 serial port - must never run un-patched
    in a test process.

    Made deterministic regardless of whether real hardware happens to be
    plugged in (round 1 review): ``find_u2_ports`` is forced to report a
    port. If ``configurator/tests/ui/conftest.py``'s ``no_real_u2_link``
    autouse fixture is doing its job, the WHOLE ``default_link_factory`` is
    replaced before this test body runs, so ``find_u2_ports`` (patched or
    not) is never even consulted and the factory still returns ``None``.
    Remove that fixture and this assertion fails on any machine, because
    the real ``default_link_factory`` would call this (patched) discovery
    function, get the fake port, and return a transport."""
    from duo_input.device import discovery
    from duo_input.device.endpoint_service import EndpointService

    monkeypatch.setattr(
        discovery,
        "find_u2_ports",
        lambda: (discovery.PortCandidate(port_name="COM99", serial_number="DIU2-TEST"),),
    )

    service = EndpointService()

    assert service._factory() is None


def test_a_manual_address_survives_a_restart(qtbot, qapp, tmp_path, monkeypatch):
    """Исходная жалоба: после пересборки строка адреса пустела."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    line = window.clipboard_page.address_combo.lineEdit()
    qtbot.keyClicks(line, "192.168.1.42")
    qtbot.keyClick(line, Qt.Key.Key_Return)
    runtime.stop()

    again = build_main_window(settings=settings)
    qtbot.addWidget(again)
    restored = configure_runtime(qapp, again, settings)
    restored_runtime = _runtime_of(qapp)
    try:
        assert again.clipboard_page.address_combo.currentText() == "192.168.1.42"
        assert again.clipboard_page.is_manual is True
        assert restored._manual_address == "192.168.1.42"
    finally:
        restored_runtime.stop()


def test_addresses_from_the_board_reach_the_page_and_the_coordinator(
    qtbot, qapp, tmp_path, monkeypatch
):
    """Сквозной путь: эмулятор U1 -> DeviceService -> AddressExchange ->
    координатор и строка. Без него каждая часть протестирована, а цепочка -
    нет (см. память «Tested but never called»).

    Round 1 review: mirrors production ordering - the runtime (and its
    ``AddressExchange``) is built FIRST, with sharing already enabled, and
    the device is connected AFTER. ``DeviceService.exchange_addresses``
    refuses while any operation is in flight, and immediately after
    ``connect_device`` succeeds ``MainWindow`` queues its own
    ``read_config`` via ``QTimer.singleShot(0, ...)`` - so a plain
    ``AddressExchange.tick()`` reaching the service between those two
    points would need to win a race against that deferred read. The
    ``waitUntil`` timeout (3 s) is well under the 5 s periodic interval, so
    a pass here can only be explained by
    ``_ClipboardRuntime._on_device_connected`` firing a tick on
    ``operation_succeeded`` - not by the periodic timer coincidentally
    landing first. Mutation: removing that connection makes this test
    time out.
    """
    from duo_input.device.emulator import U1Emulator
    from duo_input.device.qt_transport import SynchronousTransportLink

    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(app_module, "local_ipv4_addresses", lambda: ["192.168.1.10"])
    emulator = U1Emulator()
    emulator.set_peer_addresses(["10.0.0.2"])
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    coordinator = configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    try:
        window.service.connect_device(SynchronousTransportLink(emulator))
        qtbot.waitUntil(
            lambda: window.service.is_connected and window.service.device_info is not None
        )

        qtbot.waitUntil(
            lambda: [
                window.clipboard_page.address_combo.itemText(i)
                for i in range(window.clipboard_page.address_combo.count())
            ]
            == ["10.0.0.2"],
            timeout=3000,
        )
        assert coordinator._board_addresses == ["10.0.0.2"]
        assert emulator.local_addresses == ["192.168.1.10"]
    finally:
        runtime.stop()


def test_pc2_addresses_from_u2_reach_the_page_and_the_coordinator(
    qtbot, qapp, tmp_path, monkeypatch
):
    """Сквозной путь для ПК2: эмулированная U2 -> EndpointService ->
    AddressExchange -> координатор и строка. Симметрично тесту выше для
    ПК1 (``test_addresses_from_the_board_reach_the_page_and_the_coordinator``),
    но здесь ``window.service`` (U1) остаётся неподключённым - на ПК2 своей
    U1 нет, поэтому отвечает только ``EndpointService`` через свою U2, а
    второй бэкенд ``AddressExchange`` просто пропускает такт (см. комментарий
    в ``app.py`` у сборки ``exchange``).

    ``duo_input.device.endpoint_service.default_link_factory`` подменяется
    здесь напрямую, поверх автоиспользуемой заглушки в
    ``configurator/tests/ui/conftest.py`` (та всегда возвращает ``None``) -
    именно так, как предписывает её собственный докстринг: имя разрешается
    из глобалов модуля в момент конструирования ``EndpointService``, уже
    ПОСЛЕ того, как эта подмена встанет. Гранты U2 урезаны до
    ``Capability.ADDRESS_EXCHANGE`` - ровно то, что реальная U2 отдаёт (см.
    firmware/u2_endpoint/address_service.cpp): она отвечает только на HELLO
    и EXCHANGE_ADDRESSES.

    Mutation: сузив список бэкендов при сборке ``AddressExchange`` в app.py
    до одного только ``[window.service]`` (без ``self._endpoint``), этот
    тест перестаёт проходить - именно ту проводку он и проверяет.
    """
    import duo_input.device.emulator as emulator_module
    from duo_input.device import endpoint_service as endpoint_service_module
    from duo_input.device.emulator import U1Emulator
    from duo_input.device.qt_transport import SynchronousTransportLink
    from duo_input.generated.protocol import Capability

    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(app_module, "local_ipv4_addresses", lambda: ["192.168.0.77"])
    monkeypatch.setattr(
        emulator_module, "DEVICE_CAPABILITIES", int(Capability.ADDRESS_EXCHANGE)
    )
    emulator = U1Emulator()
    emulator.set_peer_addresses(["192.168.0.128"])
    monkeypatch.setattr(
        endpoint_service_module,
        "default_link_factory",
        lambda: SynchronousTransportLink(emulator),
    )

    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    coordinator = configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    try:
        assert window.service.is_connected is False  # ПК2: своей U1 нет

        qtbot.waitUntil(
            lambda: [
                window.clipboard_page.address_combo.itemText(i)
                for i in range(window.clipboard_page.address_combo.count())
            ]
            == ["192.168.0.128"],
            timeout=3000,
        )
        assert coordinator._board_addresses == ["192.168.0.128"]
        assert emulator.local_addresses == ["192.168.0.77"]
    finally:
        runtime.stop()


def test_pc1_does_not_scan_for_u2_while_u1_is_connected(
    qtbot, qapp, tmp_path, monkeypatch
):
    """(Recommendation) PC1 already has an answer from its own U1 - it must
    not also go looking for a U2 port while that link is up. The runtime
    wraps ``EndpointService`` so a tick skips it (returns ``False`` without
    ever calling ``default_link_factory``, i.e. without enumerating serial
    ports) whenever ``window.service.is_connected``; once U1 disconnects,
    the same tick is free to look for U2 again.

    ``default_link_factory`` is patched (overriding the autouse
    ``no_real_u2_link`` stub) with one that only counts its calls, so this
    test can tell "skipped" from "tried and found nothing" - both leave U2
    unreached, but only the second one calls the factory at all.
    """
    from duo_input.device import endpoint_service as endpoint_service_module
    from duo_input.device.emulator import U1Emulator
    from duo_input.device.qt_transport import SynchronousTransportLink

    factory_calls: list[int] = []

    def counting_factory():
        factory_calls.append(1)
        return None

    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(
        endpoint_service_module, "default_link_factory", counting_factory
    )

    u1_emulator = U1Emulator()
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    try:
        window.service.connect_device(SynchronousTransportLink(u1_emulator))
        qtbot.waitUntil(lambda: window.service.is_connected)
        # configure_runtime() already ticked once, before the device was
        # connected - only the tick below, taken while U1 IS connected, is
        # under test here.
        factory_calls.clear()

        runtime.address_exchange.tick()

        assert factory_calls == []

        window.service.disconnect_device()
        assert window.service.is_connected is False

        runtime.address_exchange.tick()

        assert factory_calls == [1]
    finally:
        runtime.stop()


def test_connect_device_success_ticks_the_address_exchange(
    qtbot, qapp, tmp_path, monkeypatch
):
    """Direct, fast unit check of ``_on_device_connected`` (round 1 review
    ruling 1a), independent of the slower end-to-end test above: a
    ``connect_device`` success on ``window.service`` must call
    ``address_exchange.tick()``, and no other operation may."""
    from duo_input.device.transactions import OperationResult

    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    try:
        ticks: list[int] = []
        monkeypatch.setattr(runtime.address_exchange, "tick", lambda: ticks.append(1))

        window.service.operation_succeeded.emit(OperationResult("read_config", None))
        assert ticks == []

        window.service.operation_succeeded.emit(OperationResult("connect_device", None))
        assert ticks == [1]
    finally:
        runtime.stop()


def test_stop_then_start_does_not_double_tick_on_reconnect(
    qtbot, qapp, tmp_path, monkeypatch
):
    """``window.service`` (like the page) survives an enable/disable cycle -
    only the coordinator, exchange and endpoint are rebuilt. If ``_stop``
    left ``window.service.operation_succeeded`` connected to
    ``self._on_device_connected``, the next ``_start`` would add a second
    connection to the same bound method, and one ``connect_device`` success
    would tick the (new) exchange twice."""
    from duo_input.device.transactions import OperationResult

    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)

    window.clipboard_page.sharing_checkbox.setChecked(False)
    window.clipboard_page.sharing_checkbox.setChecked(True)
    try:
        ticks: list[int] = []
        monkeypatch.setattr(runtime.address_exchange, "tick", lambda: ticks.append(1))

        window.service.operation_succeeded.emit(OperationResult("connect_device", None))

        assert ticks == [1]
    finally:
        runtime.stop()


def test_a_manual_address_typed_on_the_page_is_saved_immediately(
    qtbot, qapp, tmp_path, monkeypatch
):
    """Saving must not wait for a restart: it happens on the same edit that
    reaches the coordinator, through ``_ClipboardRuntime.set_manual_address``
    - not through a direct ``page.address_changed -> coordinator.
    set_manual_address`` connection, which would reach the coordinator but
    never touch ``QSettings`` at all."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    try:
        line = window.clipboard_page.address_combo.lineEdit()
        qtbot.keyClicks(line, "192.168.1.99")
        qtbot.keyClick(line, Qt.Key.Key_Return)

        assert settings.value("clipboard/manual_address", "", type=str) == "192.168.1.99"
    finally:
        runtime.stop()


def test_a_saved_manual_address_shows_on_the_page_even_with_sharing_off(
    qtbot, qapp, tmp_path, monkeypatch
):
    """Исходная жалоба (round 1 review, ruling 2): launching with sharing
    OFF used to show an empty address field even when an address had been
    saved in a previous session, because the page was only connected to
    (and restored from) the coordinator between ``_start`` and ``_stop`` -
    which never ran at all while sharing stayed off."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(
        tmp_path,
        {"clipboard/enabled": False, "clipboard/manual_address": "192.168.1.55"},
    )
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    coordinator = configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    try:
        assert coordinator is None  # sharing is off: no coordinator was built
        assert window.clipboard_page.address_combo.currentText() == "192.168.1.55"
        assert window.clipboard_page.is_manual is True
    finally:
        runtime.stop()


def test_typing_a_manual_address_while_sharing_is_off_is_saved_and_used_once_enabled(
    qtbot, qapp, tmp_path, monkeypatch
):
    """Round 1 review, ruling 2: the address field is editable even while
    sharing is off (there is no coordinator to reach yet), and what is
    typed must still be saved - and handed to the coordinator the moment
    sharing turns on, not lost because the page was not connected to
    anything at the time it was typed."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, False)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    try:
        line = window.clipboard_page.address_combo.lineEdit()
        qtbot.keyClicks(line, "10.20.30.40")
        qtbot.keyClick(line, Qt.Key.Key_Return)

        assert settings.value("clipboard/manual_address", "", type=str) == "10.20.30.40"

        window.clipboard_page.sharing_checkbox.setChecked(True)

        assert runtime.coordinator is not None
        assert runtime.coordinator._manual_address == "10.20.30.40"
    finally:
        runtime.stop()


def test_address_in_use_reaches_the_page(qtbot, qapp, tmp_path, monkeypatch):
    """The coordinator's ``address_in_use`` signal must be wired to the
    page's ``show_address_in_use`` - otherwise a candidate the coordinator
    dials from the board's list never shows up in the combo box at all."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    coordinator = configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    try:
        assert window.clipboard_page.is_manual is False

        coordinator.address_in_use.emit("192.168.1.77")

        assert window.clipboard_page.address_combo.currentText() == "192.168.1.77"
    finally:
        runtime.stop()


def test_stop_disconnects_address_in_use_from_the_page(qtbot, qapp, tmp_path, monkeypatch):
    """Round 1 review, ruling 5: the ``address_in_use`` disconnect in
    ``_stop`` was untested. A signal on the now-stopped coordinator must no
    longer reach the page."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    coordinator = configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    before = window.clipboard_page.address_combo.currentText()

    window.clipboard_page.sharing_checkbox.setChecked(False)
    coordinator.address_in_use.emit("10.10.10.10")

    assert window.clipboard_page.address_combo.currentText() == before
    assert window.clipboard_page.address_combo.currentText() != "10.10.10.10"
    runtime.stop()


def test_address_exchange_starts_with_sharing(qtbot, qapp, tmp_path, monkeypatch):
    """``AddressExchange.start()`` must actually be called - an exchange
    object that exists but never started would leave the periodic tick, and
    therefore every address the board ever offers, dead on arrival."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    try:
        assert runtime.address_exchange is not None
        assert runtime._endpoint is not None
        assert runtime.address_exchange._timer.isActive() is True
    finally:
        runtime.stop()


def test_stop_calls_stop_on_both_exchange_and_endpoint(
    qtbot, qapp, tmp_path, monkeypatch
):
    """``_stop`` must stop (not merely drop the reference to) both the
    exchange and the endpoint service - otherwise the endpoint's timer (and
    a still-open U2 link, on real hardware) survives sharing being switched
    off."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)

    exchange = runtime.address_exchange
    endpoint = runtime._endpoint
    exchange_stops: list[int] = []
    endpoint_stops: list[int] = []
    monkeypatch.setattr(exchange, "stop", lambda: exchange_stops.append(1))
    monkeypatch.setattr(endpoint, "stop", lambda: endpoint_stops.append(1))

    window.clipboard_page.sharing_checkbox.setChecked(False)

    assert exchange_stops == [1]
    assert endpoint_stops == [1]
    assert runtime.address_exchange is None
    assert runtime._endpoint is None


def test_stop_then_start_does_not_double_connect_the_manual_address_signal(
    qtbot, qapp, tmp_path, monkeypatch
):
    """The page's ``address_changed`` is connected to
    ``runtime.set_manual_address`` exactly once, in ``configure_runtime`` -
    untouched by ``_start``/``_stop`` (round 1 review, ruling 2). This
    guards that an enable/disable/enable cycle still delivers exactly one
    call to the (rebuilt) coordinator's ``set_manual_address`` per edit,
    not two."""
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)

    window.clipboard_page.sharing_checkbox.setChecked(False)
    window.clipboard_page.sharing_checkbox.setChecked(True)
    new_coordinator = runtime.coordinator

    calls: list[str] = []
    monkeypatch.setattr(new_coordinator, "set_manual_address", calls.append)

    line = window.clipboard_page.address_combo.lineEdit()
    qtbot.keyClicks(line, "10.0.0.9")
    qtbot.keyClick(line, Qt.Key.Key_Return)

    assert calls == ["10.0.0.9"]

    runtime.stop()
