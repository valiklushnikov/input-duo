"""Entry point for the Duo Input configurator.

The application runs without administrator rights, makes no network requests
and collects no telemetry.
"""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from duo_input import __version__
from duo_input.device.service import DeviceService
from duo_input.ui.main_window import APPLICATION_NAME, MainWindow
from duo_input.ui.models.project_session import ProjectSession

#: Console script target declared in ``pyproject.toml``.
ENTRY_POINT = "duo_input.app:main"

ORGANISATION_NAME = "Duo Input"


def build_main_window(
    service: DeviceService | None = None,
    session: ProjectSession | None = None,
) -> MainWindow:
    """Create the shell with its device service and a clean project session."""
    return MainWindow(
        service if service is not None else DeviceService(),
        session if session is not None else ProjectSession.new(),
    )


def main(argv: list[str] | None = None) -> int:
    """Run the configurator; returns the Qt exit code."""
    application = QApplication.instance() or QApplication(
        list(argv) if argv is not None else sys.argv
    )
    application.setApplicationName(APPLICATION_NAME)
    application.setApplicationVersion(__version__)
    application.setOrganizationName(ORGANISATION_NAME)

    window = build_main_window()
    window.show()
    return application.exec()


if __name__ == "__main__":  # pragma: no cover - manual launch
    raise SystemExit(main())


__all__ = ["ENTRY_POINT", "ORGANISATION_NAME", "build_main_window", "main"]
