"""Entry point for the Duo Input configurator.

The application runs without administrator rights, makes no network requests
and collects no telemetry.
"""

from __future__ import annotations

import sys
from pathlib import Path

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
    """Show the shell and restore what the operator was working on.

    The file opens first, and the device replaces it. That order looks
    backwards and is not: the read is asynchronous, so branching on whether
    a device is present means branching before it has answered - the port is
    open long before the handshake finishes, and a device that never answers
    would leave the operator with neither its configuration nor their file.
    Opening the file costs nothing when a device does answer, because
    adopting its configuration replaces an unmodified session anyway.

    This is a function rather than three lines inside ``main`` so that the
    order can be tested: a step that only ``main`` performs is a step nothing
    can prove is still wired.
    """
    window.show()
    # Called here, synchronously, rather than left to the window's own
    # deferred attach: the port has to be open before the next line asks
    # about the file, or the fallback below is never actually exercised.
    # test_startup_falls_back_to_the_file_when_the_device_never_answers
    # asserts straight after start_window() without pumping the event loop,
    # so with only the deferred attach no device would be in play at all by
    # then and the test would pass without proving anything. Blocking on a
    # port open before the first paint is the price of that proof; it is a
    # local enumeration, not a handshake, which still happens on the loop.
    window.try_autoconnect()
    # The file first: a recovery is only offered when the autosave is newer
    # than the project, and that comparison needs the project to be loaded.
    window.reopen_last_project()
    # Asked after the window exists, so the prompt has something to sit on.
    window.offer_recovery()


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
