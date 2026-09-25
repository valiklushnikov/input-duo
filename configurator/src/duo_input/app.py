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
from duo_input.clipboard.address_exchange import AddressExchange
from duo_input.clipboard.coordinator import ClipboardCoordinator
from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.local_addresses import local_ipv4_addresses
from duo_input.clipboard.pairing import PairingCandidate
from duo_input.clipboard.trust import TrustStore
from duo_input.clipboard.platform_backend import create_backend
from duo_input.device.endpoint_service import EndpointService
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
        self.address_exchange: AddressExchange | None = None
        self._endpoint: EndpointService | None = None
        self.transfer: FileTransferService | None = None
        self.file_backend: QObject | None = None
        self._file_callback_gateway = None
        self._file_cancel_slot = None
        self._file_capabilities_slot = None
        self._file_capabilities_source: ClipboardCoordinator | None = None
        self._file_snapshot_source = None
        self._file_link = None
        #: The macOS receiver, when the platform branch of ``_start_files``
        #: built one - ``None`` on win32, where the receiver role is played by
        #: ``self.transfer`` itself (driven by Explorer through the callback
        #: gateway) rather than by a standalone object.
        self._file_receiver: QObject | None = None
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

    def set_incoming_files_mode(self, auto: bool) -> None:
        """Persist and reflect the ask/auto choice for incoming file offers.

        Only the setting is touched here - an offer already waiting on
        ``authorization_needed`` keeps waiting for its own prompt/auto
        decision; this changes how the *next* offer is handled, not the one
        in flight.
        """
        mode = "auto" if auto else "ask"
        self._settings.setValue("clipboard/incoming_files", mode)
        self._window.clipboard_page.set_auto_incoming_checked(auto)

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

    def set_manual_address(self, address: str) -> None:
        """Ручной адрес со страницы: сохранить (переживает перезапуск и
        пересборку) и отдать координатору.

        Подключено ОДИН раз, в ``configure_runtime`` - поле адреса
        редактируемо независимо от того, включён ли общий буфер (ревью
        Task 14, раунд 1: адрес, набранный при выключенном общем буфере,
        раньше терялся - страница ни к чему не была подключена до первого
        ``_start()``), так что сохранение обязано работать всегда, а
        отправка координатору - только когда он существует.
        """
        self._settings.setValue("clipboard/manual_address", address)
        if self.coordinator is not None:
            self.coordinator.set_manual_address(address)

    def _on_device_connected(self, result: object) -> None:
        """Полный успешный ``connect_device`` - самый первый обмен адресами
        не обязан ждать до пяти секунд общего таймера (ревью Task 14, раунд
        1). В этот самый момент ``DeviceService._finish_success`` уже снял
        ``_operation`` (он выставляется в None ДО эмита), а собственный
        отложенный запрос MainWindow (``QTimer.singleShot(0,
        read_device_project)``) ещё не выполнился - значит сервис сейчас
        простаивает, и синхронный ``tick()`` здесь успевает начать обмен
        раньше, чем встанет в очередь чтение конфигурации, которое
        деферится за ним (Task 8)."""
        if result.operation == "connect_device" and self.address_exchange is not None:
            self.address_exchange.tick()

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

        # Поле адреса на странице живёт (и подключено к set_manual_address)
        # независимо от того, включён ли общий буфер - подключено один раз в
        # configure_runtime. Здесь координатор лишь узнаёт то, что уже
        # сохранено.
        manual = str(self._settings.value("clipboard/manual_address", "", type=str) or "")
        coordinator.restore_manual_address(manual)
        coordinator.address_in_use.connect(window.clipboard_page.show_address_in_use)

        # ПК1 спрашивает через U1 (порт уже держит DeviceService), ПК2 - через
        # свою U2. На каждом компьютере отвечает ровно один из двух: второй
        # просто не находит своей платы и пропускает такт.
        self._endpoint = EndpointService(parent=self)
        exchange = AddressExchange(
            [window.service, self._endpoint], lambda: local_ipv4_addresses(), self
        )
        exchange.peer_addresses_changed.connect(coordinator.set_board_addresses)
        exchange.peer_addresses_changed.connect(window.clipboard_page.set_board_addresses)
        self.address_exchange = exchange
        window.service.operation_succeeded.connect(self._on_device_connected)

        coordinator.start()
        exchange.start()

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
        coordinator.address_in_use.disconnect(page.show_address_in_use)
        self._window.service.operation_succeeded.disconnect(self._on_device_connected)
        if backend is not None:
            backend.stop()
        coordinator.deleteLater()

        exchange, self.address_exchange = self.address_exchange, None
        if exchange is not None:
            exchange.stop()
            exchange.deleteLater()
        endpoint, self._endpoint = self._endpoint, None
        if endpoint is not None:
            endpoint.stop()
            endpoint.deleteLater()

        self._application.setQuitOnLastWindowClosed(True)

    def _fileprovider_flag_enabled(self) -> bool:
        """Read live - checked by ``MacReceiveRouter`` on every offer (Task
        16 ruling #3), so toggling this setting takes effect for the very
        next offer without restarting the file-transfer subsystem."""
        return bool(
            self._settings.value("clipboard/fileprovider_enabled", False, type=bool)
        )

    def _build_fileprovider_kwargs(self) -> dict:
        """Assemble the ``MacReceiveRouter`` File Provider dependencies
        (Task 16), or an empty-FP set of kwargs when the feature should stay
        off. Default is OFF (Task 20 owns flipping the rollout default): with
        the flag off, this builds NOTHING File-Provider-specific - no domain,
        no XPC client, no connection attempt - so behavior with the flag off
        is byte-for-byte the plain-staging behavior that existed before Task
        16. Construction is best-effort: any failure (missing PyObjC/
        FileProvider framework, XPC discovery failure, ...) is caught and
        logged, and the router falls back to staging-only, exactly as if the
        flag were off.
        """
        flag_enabled = self._fileprovider_flag_enabled
        if not flag_enabled():
            return {"fileprovider_flag_enabled": flag_enabled}
        try:
            from duo_input.transfer.fileprovider_backend import (
                _FP_AVAILABLE,
                FileProviderBackend,
            )
            from duo_input.transfer.fileprovider_client import FileProviderServiceClient
            from duo_input.transfer.fileprovider_domain import FileProviderDomainManager
            from duo_input.transfer.fileprovider_generation_store import (
                GenerationRegistryStore,
            )
            from duo_input.transfer.macos_pasteboard import arm_urls
            from duo_input.persistence.locations import application_directory

            domain = FileProviderDomainManager(parent=self._application)
            client = FileProviderServiceClient(parent=self._application)
            client.set_domain(domain.domain_identifier)
            # Host-owned durable generation registry: rehydrated at construction
            # so an old RETIRED generation stays fetchable after a Mac app restart
            # with no new publish (see fileprovider_generation_store).
            generation_store = GenerationRegistryStore(
                application_directory() / "fileprovider" / "generations"
            )
            fp_backend = FileProviderBackend(
                client,
                domain,
                arm_urls,
                parent=self._application,
                generation_store=generation_store,
            )
            # "Domain is ensured at app startup" (Task 6) - this is the first
            # point the file-transfer subsystem (and thus the FP feature) is
            # started, so that is here, not at process launch, and only when
            # the flag is actually on. Both calls are idempotent/non-blocking
            # (QTimer-driven backoff, no sleep) - see their own docstrings.
            import os as _os

            if _os.environ.get("DUO_FP_RESET") == "1":
                # One-shot manual cleanup: remove the domain (reclaims the whole
                # mount + its materialized blobs via public API) then re-add it
                # after a short pause so the removal finishes first. Diagnostic /
                # emergency reset only - NOT production GC.
                from PySide6.QtCore import QTimer as _QTimer

                logger.warning("DUO_FP_RESET=1: recreating File Provider domain to reclaim the mount")
                domain.remove_domain()
                _QTimer.singleShot(8000, domain.ensure_domain)
            else:
                domain.ensure_domain()
            client.connect_service()
        except Exception:  # noqa: BLE001 - best-effort optional subsystem
            logger.exception(
                "File Provider недоступен на этом хосте - остаёмся на staging"
            )
            return {"fileprovider_flag_enabled": flag_enabled}
        return {
            "fileprovider_backend": fp_backend,
            "fileprovider_domain": domain,
            "fileprovider_client": client,
            "fileprovider_flag_enabled": flag_enabled,
            "fileprovider_os_supported": lambda: _FP_AVAILABLE,
        }

    def _start_files(self) -> None:
        if self.transfer is not None:
            return
        coordinator = self.coordinator
        clipboard_backend = self._backend
        if coordinator is None or clipboard_backend is None:
            return
        fileprovider_kwargs = (
            self._build_fileprovider_kwargs() if sys.platform == "darwin" else {}
        )
        try:
            backend = create_file_backend(coordinator, **fileprovider_kwargs)
        except UnsupportedPlatformError:
            logger.info("file transfer is not supported on this platform")
            self._settings.setValue("clipboard/files_enabled", False)
            self._window.clipboard_page.set_files_checked(False)
            self.tray.set_files_checked(False)
            return

        # `backend` is per-platform (create_file_backend's only sys.platform
        # check), but its INTERFACE differs by platform, not just its
        # implementation: the Windows backend is a COM publisher driven by
        # Explorer (set_callbacks/publish/start), while the macOS backend
        # (MacFileReceiver) is a self-driving receiver with no equivalent of
        # any of those. `transfer` (FileTransferService) is common to both -
        # it plays the Mac->Windows SENDER role everywhere, and on win32 it
        # ALSO plays the receiver role (Explorer pulls through it). On darwin
        # its receiver role stays dormant: `_on_chunk` drops chunks it never
        # asked for, because nothing ever calls its `open_pipe`/`request_read`
        # there - the macOS receiver answers offers and pulls chunks itself.
        transfer = FileTransferService(coordinator)
        page = self._window.clipboard_page

        transfer.send_failed.connect(
            lambda reason: page.add_event(f"файлы не объявлены: {reason}")
        )
        transfer.entries_skipped.connect(
            lambda count, names: page.add_event(
                f"пропущено при копировании файлов: {count} ({', '.join(names)})"
            )
        )

        callback_gateway = None
        cancel_slot = None

        if sys.platform == "darwin":
            receiver = backend
            # Прогресс и отмена приходят из собственных сигналов приёмника, а
            # не из `transfer` - на macOS сессия чтения никогда не проходит
            # через FileTransferService (см. комментарий выше).
            receiver.transfer_started.connect(
                lambda manifest: page.set_transfer_progress(0, manifest.total_bytes)
            )
            receiver.transfer_progress.connect(page.set_transfer_progress)
            receiver.transfer_completed.connect(page.clear_transfer)
            receiver.transfer_cancelled.connect(page.clear_transfer)
            receiver.transfer_failed.connect(lambda _reason: page.clear_transfer())
            receiver.transfer_failed.connect(
                lambda reason: page.add_event(f"передача файлов не удалась: {reason}")
            )

            cancel_slot = receiver.cancel
            page.cancel_requested.connect(cancel_slot)

            # `transfer` still receives the offer over the wire (it owns the
            # link) - sanitizes it, then hands the manifest to the receiver,
            # which drives its own FILE_READ/FILE_CHUNK loop from here on.
            transfer.offer_received.connect(receiver.handle_offer)
            receiver.authorization_needed.connect(self._on_file_authorization_needed)
            self._file_receiver = receiver
        else:
            # существующая Windows-проводка без изменений. The only implementation
            # currently selected by create_file_backend for win32 needs its COM
            # module - keep that import (and ctypes.WINFUNCTYPE) out of every
            # other runtime graph by not importing it at module scope.
            from duo_input.transfer.windows_files import ServiceCallbackGateway

            callback_gateway = ServiceCallbackGateway(transfer)
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

            def cancel_slot(transfer=transfer) -> None:
                transfer.finish_session("cancelled")

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
        if self._file_receiver is not None:
            self._file_receiver.set_peer_capabilities(coordinator.peer_capabilities)

        def apply_capabilities(capabilities) -> None:
            current_link = coordinator.link
            if current_link is not None and current_link is not self._file_link:
                self._attach_file_link(transfer, current_link)
            transfer.set_peer_capabilities(capabilities)
            if self._file_receiver is not None:
                self._file_receiver.set_peer_capabilities(capabilities)

        coordinator.capabilities_known.connect(apply_capabilities)
        clipboard_backend.snapshot_taken.connect(self._offer_files_from)

        self.transfer = transfer
        self.file_backend = backend
        self._file_callback_gateway = callback_gateway
        self._file_cancel_slot = cancel_slot
        self._file_capabilities_slot = apply_capabilities
        self._file_capabilities_source = coordinator
        self._file_snapshot_source = clipboard_backend
        # Kept at the very end, in its original position, so the Windows path's
        # statement order is byte-for-byte what it was before the platform
        # branch existed: the COM backend starts only after the link, the
        # capabilities, the capability subscription and the self.* assignments
        # are all in place. MacFileReceiver has no start() and must never be
        # called here - it drives itself from incoming offers.
        if sys.platform != "darwin":
            backend.start()

    def _on_file_authorization_needed(self, manifest) -> None:
        """The macOS receiver is holding an offer open, waiting on us."""
        mode = self._settings.value("clipboard/incoming_files", "ask", type=str)
        if mode == "auto":
            self._file_receiver.authorize(True)
            return
        self._prompt_file_authorization(manifest)

    def _prompt_file_authorization(self, manifest) -> None:
        """Ask the operator; a distinct method so tests can monkeypatch it
        instead of driving a real modal dialog through qtbot."""
        total_mb = manifest.total_bytes / (1024 * 1024)
        box = QMessageBox(self._window)
        box.setWindowTitle(self.tr("Входящие файлы"))
        box.setText(
            self.tr(
                "Другой компьютер хочет передать {0} объект(ов) ({1:.1f} МБ)."
            ).format(len(manifest.entries), total_mb)
        )
        box.setStandardButtons(
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel
        )
        # Capture before the nested event loop: exec() can pump events that
        # stop the file subsystem (self._file_receiver -> None) while the
        # modal is still open, so re-reading the attribute afterwards would
        # risk calling authorize() on None.
        receiver = self._file_receiver
        accepted = box.exec() == QMessageBox.StandardButton.Ok
        if receiver is not None:
            receiver.authorize(accepted)

    def _attach_file_link(self, transfer: FileTransferService, link) -> None:
        old_link = self._file_link
        if old_link is link:
            return
        receiver = self._file_receiver
        if old_link is not None:
            try:
                old_link.message_received.disconnect(transfer.handle_message)
            except (RuntimeError, TypeError):
                pass
            if receiver is not None:
                try:
                    old_link.message_received.disconnect(receiver.handle_message)
                except (RuntimeError, TypeError):
                    pass
        transfer.attach_link(link)
        link.message_received.connect(transfer.handle_message)
        if receiver is not None:
            link.message_received.connect(receiver.handle_message)
            receiver.attach_link(link)
            coordinator = self.coordinator
            receiver.set_peer_capabilities(
                coordinator.peer_capabilities if coordinator is not None else frozenset()
            )
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
        receiver = self._file_receiver

        self.transfer = None
        self.file_backend = None
        self._file_callback_gateway = None
        self._file_cancel_slot = None
        self._file_capabilities_slot = None
        self._file_capabilities_source = None
        self._file_snapshot_source = None
        self._file_link = None
        self._file_receiver = None

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
        if link is not None and receiver is not None:
            try:
                link.message_received.disconnect(receiver.handle_message)
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
    window.clipboard_page.auto_incoming_toggled.connect(runtime.set_incoming_files_mode)
    window.clipboard_page.autostart_toggled.connect(runtime.set_autostart)
    # Подключено здесь, один раз, а не в _start()/_stop() (ревью Task 14,
    # раунд 1): поле адреса на странице редактируемо и при выключенном общем
    # буфере, и адрес, набранный в это время, обязан сохраниться - раньше
    # страница подключалась к координатору только между _start и _stop, и
    # набранный при выключенной фиче адрес терялся молча.
    window.clipboard_page.address_changed.connect(runtime.set_manual_address)
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
    incoming_auto = settings.value("clipboard/incoming_files", "ask", type=str) == "auto"
    # То же самое - читается и показывается независимо от `enabled` (исходная
    # жалоба: выключенный общий буфер при запуске показывал пустое поле, даже
    # если адрес был сохранён с прошлого сеанса).
    manual_address = str(settings.value("clipboard/manual_address", "", type=str) or "")
    window.clipboard_page.set_sharing_checked(enabled)
    window.clipboard_page.set_files_checked(files_enabled)
    window.clipboard_page.set_auto_incoming_checked(incoming_auto)
    window.clipboard_page.set_autostart_checked(autostart_enabled)
    window.clipboard_page.set_manual_address(manual_address)
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
