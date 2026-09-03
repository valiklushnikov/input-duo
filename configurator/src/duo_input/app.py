"""Entry point for the Duo Input configurator.

The application runs without administrator rights and collects no telemetry.
It opens network connections only inside the local network, only to a computer
the operator explicitly paired with, and only while the shared clipboard is
switched on. With the shared clipboard off, no socket is ever opened.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtGui import QIcon
from PySide6.QtWidgets import QApplication

from duo_input import __version__
from duo_input.device.service import DeviceService
from duo_input.i18n import TranslationManager
from duo_input.persistence.locations import configure_logging
from duo_input.ui.main_window import APPLICATION_NAME, MainWindow
from duo_input.ui.models.project_session import ProjectSession
from duo_input.ui.theme import apply_theme

#: Console script target declared in ``pyproject.toml``.
ENTRY_POINT = "duo_input.app:main"

ORGANISATION_NAME = "Duo Input"


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

    # Резидентность включается вместе с общим буфером и только вместе с ним.
    settings = QSettings()
    if bool(settings.value("clipboard/enabled", False, type=bool)):
        application.setQuitOnLastWindowClosed(False)

    # The language is installed before any widget exists, so every label is
    # built in the language the operator chose last time.
    translations = TranslationManager(application)
    translations.load_saved()

    window = build_main_window(translations=translations)
    start_window(window)
    return application.exec()


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())


__all__ = [
    "ENTRY_POINT",
    "ORGANISATION_NAME",
    "build_main_window",
    "configure_application",
    "icon_path",
    "main",
]
