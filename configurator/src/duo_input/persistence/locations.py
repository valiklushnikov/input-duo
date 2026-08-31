r"""The per-user directories the configurator writes to, and the log it keeps.

Everything lives under ``%LOCALAPPDATA%\DuoInput`` so the program never needs
administrator rights and never writes beside the executable. On a machine that
has no ``LOCALAPPDATA`` the home directory stands in, which keeps the tests and
any non-Windows development host honest.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
from pathlib import Path

APPLICATION_DIRECTORY_NAME = "DuoInput"
LOG_FILE_NAME = "duo-input.log"

#: Five files of two mebibytes: enough history to explain a failure, small
#: enough to attach to a report.
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 5

LOGGER_NAME = "duo_input"


def application_directory() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    base = Path(root) if root else Path.home() / ".local" / "share"
    return base / APPLICATION_DIRECTORY_NAME


def log_directory() -> Path:
    return application_directory() / "logs"


def log_path() -> Path:
    return log_directory() / LOG_FILE_NAME


def configure_logging(level: int = logging.INFO) -> Path:
    """Attach a rotating file handler to the application logger.

    Calling this twice replaces the handler rather than stacking a second one,
    so a language or log-level change does not double every line.
    """
    destination = log_path()
    destination.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    for handler in list(logger.handlers):
        if isinstance(handler, logging.handlers.RotatingFileHandler):
            handler.close()
            logger.removeHandler(handler)

    handler = logging.handlers.RotatingFileHandler(
        destination,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    )
    logger.addHandler(handler)
    return destination


__all__ = [
    "APPLICATION_DIRECTORY_NAME",
    "LOGGER_NAME",
    "LOG_BACKUP_COUNT",
    "LOG_FILE_NAME",
    "LOG_MAX_BYTES",
    "application_directory",
    "configure_logging",
    "log_directory",
    "log_path",
]
