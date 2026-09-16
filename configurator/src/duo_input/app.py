"""Entry point for the Duo Input configurator.

The application runs without administrator rights and collects no telemetry.
It opens network connections only inside the local network, only to a computer
the operator explicitly paired with, and only while the shared clipboard is
switched on. With the shared clipboard off, no socket is ever opened.
"""

from __future__ import annotations

import logging
import socket
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QObject, QSettings, Qt
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from duo_input import __version__
from duo_input.clipboard.coordinator import ClipboardCoordinator
from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.pairing import PairingCandidate
from duo_input.clipboard.trust import TrustStore
from duo_input.clipboard.platform_backend import create_backend
from duo_input.device.service import DeviceService
from duo_input.i18n import TranslationManager
from duo_input.persistence import autostart
from duo_input.persistence.locations import application_directory, configure_logging
from duo_input.transfer.platform_files import UnsupportedPlatformError, create_file_backend
from duo_input.transfer.service import FileTransferService
from duo_input.ui.main_window import APPLICATION_NAME, MainWindow
from duo_input.ui.models.project_session import ProjectSession
from duo_input.ui.theme import apply_theme
from duo_input.ui.tray import TrayIcon

logger = logging.getLogger(__name__)

#: Console script target declared in ``pyproject.toml``.
ENTRY_POINT = "duo_input.app:main"

ORGANISATION_NAME = "Duo Input"
SINGLE_INSTANCE_NAME = "duo-input-single-instance"

#: Аргумент командной строки, которым автозапуск просит не показывать окно -
#: см. persistence/autostart.py и §4 спецификации.
HIDDEN_START_ARGUMENT = autostart.HIDDEN_START_ARGUMENT


def configure_application() -> Path:
    """Prepare the per-user directories and start the rotating log.

    Returns the log file, which the diagnostic report attaches later.
    """
    return configure_logging()


def icon_path() -> Path:
    """The application icon, as it sits beside the package.

    In a frozen standalone build the entry module's ``__file__`` does not point
    beside the bundled ``duo_input`` package, so also look next to the running
    executable, where Nuitka places the data files (``duo_input/resources/``).
    """
    candidates = (
        Path(__file__).resolve().parent / "resources" / "duo-input.ico",
        Path(sys.executable).resolve().parent / "duo_input" / "resources" / "duo-input.ico",
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return candidates[0]


def build_main_window(
    service: DeviceService | None = None,
    session: ProjectSession | None = None,
    translations: TranslationManager | None = None,
    transport_factory: object | None = None,
    settings: object | None = None,
) -> MainWindow:
    """Create the shell with its device service and a clean project session.

    ``transport_factory`` exists for tests: the window attaches itself to
    whatever the factory offers, so a suite that left it at its default would
    open the operator's real device.
    """
    return MainWindow(
        service if service is not None else DeviceService(),
        session if session is not None else ProjectSession.new(),
        translations=translations,
        transport_factory=transport_factory,
        settings=settings,
    )


def start_window(window: MainWindow, *, show: bool = True) -> None:
    """Show the shell and give it its first chance to find a device.

    There is no file to reopen any more - the device is the document, and a
    file is only ever a copy the operator asks for by name. So this is
    startup's whole job: show the window and look for a board.

    This is a function rather than two lines inside ``main`` so that the
    order can be tested: a step that only ``main`` performs is a step nothing
    can prove is still wired.

    ``show`` is ``False`` only for a hidden autostart launch (§4: "при
    автозапуске окно не показывается"). The device search still runs either
    way - a hidden start is not a reason to also hide whether a board is
    plugged in once the operator does open the window.
    """
    if show:
        window.show()
    # Called here, synchronously, rather than left entirely to the window's
    # own deferred attach: a board already plugged in should be found before
    # the first paint, not two ticks after the operator is already looking
    # at a "disconnected" chip. Blocking on a port open here is a local
    # enumeration, not a handshake, which still happens on the loop.
    window.try_autoconnect()


def single_instance_lock(name: str = SINGLE_INSTANCE_NAME) -> QLocalServer | None:
    """Claim ``name``; ``None`` means another configurator already owns it."""
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(100):
        probe.disconnectFromServer()
        return None

    QLocalServer.removeServer(name)
    server = QLocalServer()
    if not server.listen(name):
        return None
    return server


def _show_pairing_confirmation(
    window: MainWindow,
    coordinator: ClipboardCoordinator,
    code: str,
    candidate: PairingCandidate,
) -> None:
    """Ask about the candidate emitted with this exact pairing attempt."""
    dialog, accept_button = _pairing_confirmation_dialog(window, code, candidate)
    dialog.exec()
    if dialog.clickedButton() is accept_button:
        coordinator.confirm_pairing(candidate)
    else:
        coordinator.reject_pairing(candidate)


def _pairing_confirmation_dialog(
    window: MainWindow, code: str, candidate: PairingCandidate
) -> tuple[QMessageBox, QPushButton]:
    """Build the plain-text, application-translated security prompt."""
    title = QCoreApplication.translate("PairingDialog", "Подтвердите связывание")
    text = QCoreApplication.translate(
        "PairingDialog",
        "Компьютер «{0}» показывает тот же код?\n\nКод: {1}",
    ).format(candidate.machine_name, code)
    dialog = QMessageBox(window)
    dialog.setIcon(QMessageBox.Icon.Question)
    dialog.setWindowTitle(title)
    dialog.setTextFormat(Qt.TextFormat.PlainText)
    dialog.setText(text)
    accept_button = dialog.addButton(
        QCoreApplication.translate("PairingDialog", "Связать"),
        QMessageBox.ButtonRole.AcceptRole,
    )
    reject_button = dialog.addButton(
        QCoreApplication.translate("PairingDialog", "Отказать"),
        QMessageBox.ButtonRole.RejectRole,
    )
    dialog.setDefaultButton(reject_button)
    dialog.setEscapeButton(reject_button)
    return dialog, accept_button


class _ClipboardRuntime(QObject):
    """Поднимает и останавливает подсистему общего буфера по переключателю.

    Наследуется от ``QObject`` и создаётся с ``parent=application`` не для
    сигналов - у него их нет, - а потому что PySide6 хранит слабую ссылку на
    связанный метод обычного Python-объекта: без владельца этот объект
    собирался бы GC сразу после возврата из ``configure_runtime()``, и оба
    переключателя (страница и трей) молча переставали бы что-либо делать -
    сигнал эмитился бы, но обработчик уже был бы мёртв. QObject с реальным
    родителем живёт, пока жив ``application``.

    До этой правки `configure_runtime` читал `clipboard/enabled` РОВНО один
    раз при старте: включённая настройка собирала всё намертво, выключенная -
    не делала ничего, а сигналы `sharing_toggled` страницы и трея никуда не
    были подключены - переключатели существовали, но не могли ни включить, ни
    выключить фичу после запуска. Здесь оба пути (чтение сохранённого
    состояния при старте и живой переключатель) идут через один и тот же
    метод, `set_enabled`, поэтому "прочитать состояние" и "включить фичу"
    гарантированно ведут себя одинаково.

    Трей (`self.tray`) создаётся здесь, в конструкторе, а не в `_start()` -
    решение владельца продукта от 2026-09-03 (§4 спецификации) разделило
    жизненный цикл окна и трея от подсистемы общего буфера: `MainWindow`
    теперь ВСЕГДА уходит в трей по закрытию, независимо от того, включён ли
    общий буфер, а значит трей обязан существовать всегда - иначе скрытое
    окно с выключенной фичей стало бы недоступным (показать нечем, выйти
    нечем). Пока `_start()` ни разу не вызван, ни `TrustStore`, ни
    `ClipboardCoordinator` не существуют и ни один сокет не открывается - это
    по-прежнему требование спецификации §4, и трей само по себе сокетов не
    открывает.
    """

    def __init__(self, application: QApplication, window: MainWindow, settings: QSettings) -> None:
        super().__init__(application)
        self._application = application
        self._window = window
        self._settings = settings
        self.coordinator: ClipboardCoordinator | None = None
        self.transfer: FileTransferService | None = None
        self.file_backend: QObject | None = None
        self._file_callback_gateway = None
        self._file_cancel_slot = None
        self._file_capabilities_slot = None
        self._file_capabilities_source: ClipboardCoordinator | None = None
        self._file_snapshot_source = None
        self._file_link = None
        from duo_input.clipboard.backend import ClipboardBackend

        self._backend: ClipboardBackend | None = None
        self.tray = TrayIcon(application.windowIcon(), application)
        self.tray.open_requested.connect(window.showNormal)
        self.tray.quit_requested.connect(application.quit)
        self.tray.sharing_toggled.connect(self.set_enabled)
        self.tray.files_toggled.connect(self.set_files_enabled)
        self.tray.show()

    def set_enabled(self, enabled: bool) -> None:
        """Единственный вход для обоих переключателей (страница и трей)."""
        self._settings.setValue("clipboard/enabled", enabled)
        self._window.clipboard_page.set_sharing_checked(enabled)
        self.tray.set_sharing_checked(enabled)
        if enabled:
            self._start()
            files_enabled = bool(
                self._settings.value("clipboard/files_enabled", False, type=bool)
            )
            if self.coordinator is not None and files_enabled:
                self._start_files()
        else:
            self._stop()

    def set_files_enabled(self, enabled: bool) -> None:
        """Apply either file-transfer toggle and persist their shared state."""
        self._settings.setValue("clipboard/files_enabled", enabled)
        self._window.clipboard_page.set_files_checked(enabled)
        self.tray.set_files_checked(enabled)
        if enabled and self.coordinator is not None:
            self._start_files()
        else:
            self._stop_files()

    def set_autostart(self, enabled: bool) -> None:
        self._settings.setValue("clipboard/autostart", enabled)
        self._window.clipboard_page.set_autostart_checked(enabled)
        try:
            if enabled:
                autostart.enable(Path(sys.executable))
            else:
                autostart.disable()
        except OSError:
            # I6: отказ файловой операции здесь не должен утянуть за собой
            # ничего, кроме самого автозапуска.
            logger.exception("не удалось изменить автозапуск")

    def stop(self) -> None:
        """Остановить перед выходом (aboutToQuit) - безопасно, если и так выключено."""
        self._stop()

    def _start(self) -> None:
        if self.coordinator is not None:
            return

        application = self._application
        window = self._window
        try:
            directory = application_directory()
            identity = load_or_create(directory)
            trust = TrustStore(directory / "peers.json")
        except OSError:
            # I6: отказ работы с файлами идентичности не должен ронять всё
            # приложение (включая страницы, к общему буферу отношения не
            # имеющие) - деградация до "общий буфер не запустился", а не до
            # "программа не запускается".
            logger.exception("общий буфер не запустился: файлы идентичности недоступны")
            window.clipboard_page.add_event("общий буфер не запустился: нет доступа к файлам идентичности")
            self._settings.setValue("clipboard/enabled", False)
            window.clipboard_page.set_sharing_checked(False)
            self.tray.set_sharing_checked(False)
            return

        application.setQuitOnLastWindowClosed(False)
        coordinator = ClipboardCoordinator(
            identity=identity,
            trust=trust,
            machine_name=socket.gethostname(),
            parent=application,
        )

        backend = create_backend(application.clipboard(), coordinator)
        coordinator.service.attach_backend(backend)
        backend.snapshot_taken.connect(coordinator.service.on_local_snapshot)
        backend.start()

        self.tray.set_link_state(coordinator.state.value)
        coordinator.state_changed.connect(self.tray.set_link_state)
        coordinator.state_changed.connect(window.clipboard_page.set_link_state)
        coordinator.peer_changed.connect(window.clipboard_page.set_peer)
        coordinator.event_logged.connect(window.clipboard_page.add_event)
        coordinator.pairing_code_ready.connect(
            lambda code, candidate: _show_pairing_confirmation(
                window, coordinator, code, candidate
            )
        )
        window.clipboard_page.set_peer(coordinator.peer)
        window.clipboard_page.pair_requested.connect(coordinator.begin_pairing)
        window.clipboard_page.forget_requested.connect(coordinator.forget_peer)
        window.clipboard_page.address_changed.connect(coordinator.set_manual_address)

        coordinator.start()

        self.coordinator = coordinator
        self._backend = backend

    def _stop(self) -> None:
        self._stop_files()
        coordinator = self.coordinator
        if coordinator is None:
            return
        backend = self._backend
        self.coordinator = None
        self._backend = None

        # stop() эмитит финальный state_changed (UNPAIRED/DISCONNECTED) ДО
        # отключения - трей сам обновляется на осмысленное значение вместо
        # того, чтобы застыть на последнем состоянии живой связи. Отключаем
        # только после этого: трей переживает остановку (в отличие от
        # координатора и backend), а без отключения следующий _start() добавил
        # бы вторую подписку поверх этой при создании нового координатора.
        coordinator.stop()
        coordinator.state_changed.disconnect(self.tray.set_link_state)
        page = self._window.clipboard_page
        page.pair_requested.disconnect(coordinator.begin_pairing)
        page.forget_requested.disconnect(coordinator.forget_peer)
        page.address_changed.disconnect(coordinator.set_manual_address)
        if backend is not None:
            backend.stop()
        coordinator.deleteLater()

        self._application.setQuitOnLastWindowClosed(True)

    def _start_files(self) -> None:
        if self.transfer is not None:
            return
        coordinator = self.coordinator
        clipboard_backend = self._backend
        if coordinator is None or clipboard_backend is None:
            return
        try:
            backend = create_file_backend(coordinator)
        except UnsupportedPlatformError:
            logger.info("file transfer is not supported on this platform")
            self._settings.setValue("clipboard/files_enabled", False)
            self._window.clipboard_page.set_files_checked(False)
            self.tray.set_files_checked(False)
            return

        # The only implementation currently selected by create_file_backend is
        # Windows-specific. Keep its COM module out of every other runtime graph.
        from duo_input.transfer.windows_files import ServiceCallbackGateway

        transfer = FileTransferService(coordinator)
        callback_gateway = ServiceCallbackGateway(transfer)
        page = self._window.clipboard_page
        # Отмена доступна с первого мгновения сессии, а не с первого чанка:
        # вставка, застрявшая до первого байта, - именно та, которую отменяют.
        transfer.transfer_started.connect(
            lambda manifest: page.set_transfer_progress(0, manifest.total_bytes)
        )
        transfer.transfer_progress.connect(page.set_transfer_progress)
        transfer.transfer_completed.connect(page.clear_transfer)
        transfer.transfer_cancelled.connect(page.clear_transfer)
        transfer.transfer_failed.connect(lambda _reason: page.clear_transfer())
        transfer.transfer_failed.connect(
            lambda reason: page.add_event(f"передача файлов не удалась: {reason}")
        )
        transfer.send_failed.connect(
            lambda reason: page.add_event(f"файлы не объявлены: {reason}")
        )
        transfer.entries_skipped.connect(
            lambda count, names: page.add_event(
                f"пропущено при копировании файлов: {count} ({', '.join(names)})"
            )
        )

        cancel_slot = lambda transfer=transfer: transfer.finish_session("cancelled")
        page.cancel_requested.connect(cancel_slot)

        backend.set_callbacks(
            open_pipe=callback_gateway.open_pipe,
            request_read=callback_gateway.request_read,
            close_pipe=callback_gateway.close_pipe,
            on_operation_finished=callback_gateway.on_operation_finished,
        )
        transfer.offer_received.connect(
            lambda manifest: backend.publish(
                manifest, origin_marker=manifest.transfer_id.encode("ascii")
            )
        )
        backend.publish_failed.connect(
            lambda reason: page.add_event(
                f"буфер обмена не принял файлы: {reason}"
            )
        )

        link = coordinator.link
        if link is not None:
            self._attach_file_link(transfer, link)
        transfer.set_peer_capabilities(coordinator.peer_capabilities)

        def apply_capabilities(capabilities) -> None:
            current_link = coordinator.link
            if current_link is not None and current_link is not self._file_link:
                self._attach_file_link(transfer, current_link)
            transfer.set_peer_capabilities(capabilities)

        coordinator.capabilities_known.connect(apply_capabilities)
        clipboard_backend.snapshot_taken.connect(self._offer_files_from)

        self.transfer = transfer
        self.file_backend = backend
        self._file_callback_gateway = callback_gateway
        self._file_cancel_slot = cancel_slot
        self._file_capabilities_slot = apply_capabilities
        self._file_capabilities_source = coordinator
        self._file_snapshot_source = clipboard_backend
        backend.start()

    def _attach_file_link(self, transfer: FileTransferService, link) -> None:
        old_link = self._file_link
        if old_link is link:
            return
        if old_link is not None:
            try:
                old_link.message_received.disconnect(transfer.handle_message)
            except (RuntimeError, TypeError):
                pass
        transfer.attach_link(link)
        link.message_received.connect(transfer.handle_message)
        self._file_link = link

    def _offer_files_from(self, snapshot) -> None:
        transfer = self.transfer
        if transfer is None or not snapshot.file_paths:
            return
        transfer.offer_local_files([Path(path) for path in snapshot.file_paths])

    def _stop_files(self) -> None:
        transfer, backend = self.transfer, self.file_backend
        callback_gateway = self._file_callback_gateway
        cancel_slot = self._file_cancel_slot
        capabilities_slot = self._file_capabilities_slot
        capabilities_source = self._file_capabilities_source
        snapshot_source = self._file_snapshot_source
        link = self._file_link

        self.transfer = None
        self.file_backend = None
        self._file_callback_gateway = None
        self._file_cancel_slot = None
        self._file_capabilities_slot = None
        self._file_capabilities_source = None
        self._file_snapshot_source = None
        self._file_link = None

        if cancel_slot is not None:
            try:
                self._window.clipboard_page.cancel_requested.disconnect(cancel_slot)
            except (RuntimeError, TypeError):
                pass
        if capabilities_source is not None and capabilities_slot is not None:
            try:
                capabilities_source.capabilities_known.disconnect(capabilities_slot)
            except (RuntimeError, TypeError):
                pass
        if snapshot_source is not None:
            try:
                snapshot_source.snapshot_taken.disconnect(self._offer_files_from)
            except (RuntimeError, TypeError):
                pass
        if link is not None and transfer is not None:
            try:
                link.message_received.disconnect(transfer.handle_message)
            except (RuntimeError, TypeError):
                pass

        self._window.clipboard_page.clear_transfer()
        # Порядок существенен. Сперва колбэки становятся безвредными: вызов,
        # который COM-поток сделает во время остановки, уже не дойдёт до
        # сервиса. Затем сервис закрывает трубы - Read, заблокированный на
        # одной из них в потоке STA, просыпается сейчас же. И только потом
        # бэкенд ждёт свой поток: в обратном порядке это ожидание стояло бы
        # на заблокированном Read до таймаута, вместе с интерфейсом.
        if callback_gateway is not None:
            callback_gateway.invalidate()
        if transfer is not None:
            transfer.detach_link()
        if backend is not None:
            backend.stop()
            backend.deleteLater()
        if transfer is not None:
            transfer.deleteLater()


def configure_runtime(
    application: QApplication, window: MainWindow, settings: QSettings
) -> ClipboardCoordinator | None:
    """Подключить оба переключателя к настоящему запуску/остановке подсистемы.

    Возвращает координатор, если фича уже включена сохранённой настройкой -
    тот же контракт, что и раньше, для кода, которому нужен координатор сразу
    после старта. Но, в отличие от прежней версии, переключатель остаётся
    рабочим и после этого вызова: и страница, и трей могут включить или
    выключить общий буфер в любой момент, а не только при следующем запуске
    программы.
    """
    runtime = _ClipboardRuntime(application, window, settings)
    window.clipboard_page.sharing_toggled.connect(runtime.set_enabled)
    window.clipboard_page.files_toggled.connect(runtime.set_files_enabled)
    window.clipboard_page.autostart_toggled.connect(runtime.set_autostart)
    application.aboutToQuit.connect(runtime.stop)

    # Показать сохранённое состояние ОДИНАКОВО на странице и в трее - раньше
    # трей выставлялся принудительно checked=True независимо от настроек, а
    # страница вообще не читала своё состояние при запуске. Трей теперь
    # существует независимо от `enabled` (см. `_ClipboardRuntime.__init__`),
    # так что этот вызов - единственное место, где его галочка узнаёт о
    # реальном сохранённом состоянии на старте.
    enabled = bool(settings.value("clipboard/enabled", False, type=bool))
    files_enabled = bool(
        settings.value("clipboard/files_enabled", False, type=bool)
    )
    autostart_enabled = bool(settings.value("clipboard/autostart", False, type=bool))
    window.clipboard_page.set_sharing_checked(enabled)
    window.clipboard_page.set_files_checked(files_enabled)
    window.clipboard_page.set_autostart_checked(autostart_enabled)
    runtime.tray.set_sharing_checked(enabled)
    runtime.tray.set_files_checked(files_enabled)
    if enabled:
        runtime.set_enabled(True)
    return runtime.coordinator


def _raise_existing_window(lock: QLocalServer, window: MainWindow) -> None:
    """Второй экземпляр достучался до нас - поднять окно первого (§4).

    Содержимое соединения не имеет значения - сам факт того, что кто-то
    подключился к замку единственного экземпляра, и есть сообщение "меня
    запустили ещё раз, покажи окно". Входящий сокет всё равно нужно вычитать,
    иначе он останется висеть в очереди сервера.
    """
    socket_ = lock.nextPendingConnection()
    if socket_ is not None:
        socket_.disconnectFromServer()
    window.showNormal()
    window.raise_()
    window.activateWindow()


def main(argv: list[str] | None = None) -> int:
    """Run the configurator; returns the Qt exit code."""
    arguments = list(argv) if argv is not None else sys.argv
    if "--self-check-tls" in arguments:
        # Собранная программа должна уметь доказать, что TLS в ней работает:
        # недостающая криптографическая библиотека выглядит у пользователя
        # как "нет связи" и никак иначе, поэтому проверка нужна именно здесь,
        # до создания QApplication, чтобы её можно было вызвать из готового
        # exe без графического окна.
        from PySide6.QtNetwork import QSslSocket

        print(f"tls: {'ok' if QSslSocket.supportsSsl() else 'missing'}")
        print(f"backend: {QSslSocket.activeBackend()}")
        return 0

    if "--self-check-files" in arguments:
        # This runs before QApplication so the packaged executable can prove
        # its ctypes COM boundary without opening a window. Keep the Windows
        # imports inside this branch: other platforms and ordinary startup do
        # not need to load the native file-transfer implementation.
        from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest
        from duo_input.transfer.pipe import ChunkPipe
        from duo_input.transfer.windows_com import call_add_ref, call_release
        from duo_input.transfer.windows_files import (
            VirtualFilesDataObject,
            descriptor_size,
            group_descriptor_bytes,
        )

        manifest = TransferManifest(
            transfer_id="self-check",
            entries=(
                TransferEntry(
                    path="a.bin",
                    kind=ENTRY_FILE,
                    size=4,
                    mtime_ns=1,
                ),
            ),
        )
        try:
            descriptor_bytes = descriptor_size()
            blob = group_descriptor_bytes(manifest)
            data_object = VirtualFilesDataObject(
                manifest,
                open_pipe=lambda *_: ChunkPipe(),
                request_read=lambda *_: None,
                close_pipe=lambda *_: None,
                origin_marker=b"self-check",
            )
            added = call_add_ref(data_object.pointer)
            released = call_release(data_object.pointer)
            usable = (
                data_object.pointer.value is not None
                and len(blob) == 4 + descriptor_bytes
                and descriptor_bytes == 592
                and added == 2
                and released == 1
                and data_object.refcount == 1
            )
        except Exception as error:  # noqa: BLE001 - this is a diagnostic boundary
            print(f"files: missing ({error})")
            return 1

        print(f"files: {'ok' if usable else 'missing'}")
        print(f"callback: addref {added}, release {released}")
        print(f"descriptor: {descriptor_bytes}")
        return 0 if usable else 1

    application = QApplication.instance() or QApplication(arguments)
    application.setApplicationName(APPLICATION_NAME)
    application.setApplicationVersion(__version__)
    application.setOrganizationName(ORGANISATION_NAME)
    configure_application()

    # Set before the first window exists, so nothing is ever shown wearing the
    # platform's default icon and then corrected.
    icon = icon_path()
    if icon.is_file():
        application.setWindowIcon(QIcon(str(icon)))

    # The look is installed before the first widget exists, so nothing is ever
    # built, shown and then restyled in front of the operator.
    apply_theme(application)

    # The language is installed before any widget exists, so every label is
    # built in the language the operator chose last time.
    translations = TranslationManager(application)
    translations.load_saved()

    lock = single_instance_lock()
    if lock is None:
        return 0

    settings = QSettings()
    window = build_main_window(translations=translations, settings=settings)
    lock.newConnection.connect(lambda: _raise_existing_window(lock, window))
    try:
        configure_runtime(application, window, settings)
    except Exception:
        # I6: отказ подсистемы общего буфера не имеет права утащить за собой
        # весь конфигуратор, включая страницы устройства, к буферу отношения
        # не имеющие. load_or_create() сам ловит то, что можно предвидеть
        # (OSError/PermissionError на файлах идентичности) - это последний
        # рубеж на случай того, что предвидеть было нельзя.
        logger.exception("общий буфер не запустился")

    # Скрытый старт - только для автозапуска (§4): аргумент дописывает сама
    # программа в собственный ярлык, оператор его руками не вводит.
    hidden = HIDDEN_START_ARGUMENT in arguments
    background = bool(settings.value("clipboard/enabled", False, type=bool))
    start_window(window, show=not (hidden and background))
    return application.exec()


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())


__all__ = [
    "ENTRY_POINT",
    "HIDDEN_START_ARGUMENT",
    "ORGANISATION_NAME",
    "SINGLE_INSTANCE_NAME",
    "build_main_window",
    "configure_application",
    "configure_runtime",
    "icon_path",
    "main",
    "single_instance_lock",
    "start_window",
]
