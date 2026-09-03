"""Entry point for the Duo Input configurator.

The application runs without administrator rights and collects no telemetry.
It opens network connections only inside the local network, only to a computer
the operator explicitly paired with, and only while the shared clipboard is
switched on. With the shared clipboard off, no socket is ever opened.
"""

from __future__ import annotations

import socket
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QSettings, Qt
from PySide6.QtGui import QIcon
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton

from duo_input import __version__
from duo_input.clipboard.coordinator import ClipboardCoordinator
from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.pairing import PairingCandidate
from duo_input.clipboard.trust import TrustStore
from duo_input.clipboard.windows_backend import WindowsClipboardBackend
from duo_input.device.service import DeviceService
from duo_input.i18n import TranslationManager
from duo_input.persistence.locations import application_directory, configure_logging
from duo_input.ui.main_window import APPLICATION_NAME, MainWindow
from duo_input.ui.models.project_session import ProjectSession
from duo_input.ui.theme import apply_theme
from duo_input.ui.tray import TrayIcon

#: Console script target declared in ``pyproject.toml``.
ENTRY_POINT = "duo_input.app:main"

ORGANISATION_NAME = "Duo Input"
SINGLE_INSTANCE_NAME = "duo-input-single-instance"


def configure_application() -> Path:
    """Prepare the per-user directories and start the rotating log.

    Returns the log file, which the diagnostic report attaches later.
    """
    return configure_logging()


def icon_path() -> Path:
    """The application icon, as it sits beside the package."""
    return Path(__file__).resolve().parent / "resources" / "duo-input.ico"


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


def start_window(window: MainWindow) -> None:
    """Show the shell and give it its first chance to find a device.

    There is no file to reopen any more - the device is the document, and a
    file is only ever a copy the operator asks for by name. So this is
    startup's whole job: show the window and look for a board.

    This is a function rather than two lines inside ``main`` so that the
    order can be tested: a step that only ``main`` performs is a step nothing
    can prove is still wired.
    """
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


def configure_runtime(
    application: QApplication, window: MainWindow, settings: QSettings
) -> ClipboardCoordinator | None:
    """Assemble shared clipboard only when the operator enabled it."""
    if not bool(settings.value("clipboard/enabled", False, type=bool)):
        return None

    application.setQuitOnLastWindowClosed(False)

    directory = application_directory()
    identity = load_or_create(directory)
    coordinator = ClipboardCoordinator(
        identity=identity,
        trust=TrustStore(directory / "peers.json"),
        machine_name=socket.gethostname(),
        parent=application,
    )

    backend = WindowsClipboardBackend(application.clipboard(), coordinator)
    coordinator.service.attach_backend(backend)
    backend.snapshot_taken.connect(coordinator.service.on_local_snapshot)
    backend.start()

    tray = TrayIcon(application.windowIcon(), application)
    tray.open_requested.connect(window.showNormal)
    tray.quit_requested.connect(application.quit)
    tray.sharing_action.setChecked(True)
    coordinator.state_changed.connect(tray.set_link_state)
    coordinator.state_changed.connect(window.clipboard_page.set_link_state)
    coordinator.peer_changed.connect(window.clipboard_page.set_peer)
    coordinator.pairing_code_ready.connect(
        lambda code, candidate: _show_pairing_confirmation(
            window, coordinator, code, candidate
        )
    )
    window.clipboard_page.set_peer(coordinator.peer)
    window.clipboard_page.pair_requested.connect(coordinator.begin_pairing)
    window.clipboard_page.forget_requested.connect(coordinator.forget_peer)
    window.clipboard_page.address_changed.connect(coordinator.set_manual_address)
    tray.show()

    application.aboutToQuit.connect(backend.stop)
    application.aboutToQuit.connect(coordinator.stop)
    coordinator.start()
    return coordinator


def main(argv: list[str] | None = None) -> int:
    """Run the configurator; returns the Qt exit code."""
    application = QApplication.instance() or QApplication(
        list(argv) if argv is not None else sys.argv
    )
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
    configure_runtime(application, window, settings)
    start_window(window)
    return application.exec()


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())


__all__ = [
    "ENTRY_POINT",
    "ORGANISATION_NAME",
    "SINGLE_INSTANCE_NAME",
    "build_main_window",
    "configure_application",
    "configure_runtime",
    "icon_path",
    "main",
    "single_instance_lock",
]
