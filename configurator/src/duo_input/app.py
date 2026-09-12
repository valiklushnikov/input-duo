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
        from duo_input.clipboard.backend import ClipboardBackend

        self._backend: ClipboardBackend | None = None
        self.tray = TrayIcon(application.windowIcon(), application)
        self.tray.open_requested.connect(window.showNormal)
        self.tray.quit_requested.connect(application.quit)
        self.tray.sharing_toggled.connect(self.set_enabled)
        self.tray.show()

    def set_enabled(self, enabled: bool) -> None:
        """Единственный вход для обоих переключателей (страница и трей)."""
        self._settings.setValue("clipboard/enabled", enabled)
        self._window.clipboard_page.set_sharing_checked(enabled)
        self.tray.set_sharing_checked(enabled)
        if enabled:
            self._start()
        else:
            self._stop()

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
    window.clipboard_page.autostart_toggled.connect(runtime.set_autostart)
    application.aboutToQuit.connect(runtime.stop)

    # Показать сохранённое состояние ОДИНАКОВО на странице и в трее - раньше
    # трей выставлялся принудительно checked=True независимо от настроек, а
    # страница вообще не читала своё состояние при запуске. Трей теперь
    # существует независимо от `enabled` (см. `_ClipboardRuntime.__init__`),
    # так что этот вызов - единственное место, где его галочка узнаёт о
    # реальном сохранённом состоянии на старте.
    enabled = bool(settings.value("clipboard/enabled", False, type=bool))
    autostart_enabled = bool(settings.value("clipboard/autostart", False, type=bool))
    window.clipboard_page.set_sharing_checked(enabled)
    window.clipboard_page.set_autostart_checked(autostart_enabled)
    runtime.tray.set_sharing_checked(enabled)
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
