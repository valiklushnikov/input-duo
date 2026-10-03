r"""The per-user directories the configurator writes to, and the log it keeps.

Everything lives under ``%LOCALAPPDATA%\DuoInput`` so the program never needs
administrator rights and never writes beside the executable. On a machine that
has no ``LOCALAPPDATA`` the home directory stands in, which keeps the tests and
any non-Windows development host honest.
"""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import os
import time
from pathlib import Path
from typing import TextIO

from duo_input import __version__

APPLICATION_DIRECTORY_NAME = "DuoInput"
LOG_FILE_NAME = "duo-input.log"
CRASH_LOG_FILE_NAME = "crash.log"

#: Five files of two mebibytes: enough history to explain a failure, small
#: enough to attach to a report.
LOG_MAX_BYTES = 2 * 1024 * 1024
LOG_BACKUP_COUNT = 5

LOGGER_NAME = "duo_input"

#: The stream faulthandler writes into; open for the life of the process,
#: because a crash is exactly the moment there is no chance to open it.
_crash_log_stream: TextIO | None = None


def application_directory() -> Path:
    root = os.environ.get("LOCALAPPDATA")
    base = Path(root) if root else Path.home() / ".local" / "share"
    return base / APPLICATION_DIRECTORY_NAME


def log_directory() -> Path:
    return application_directory() / "logs"


def log_path() -> Path:
    return log_directory() / LOG_FILE_NAME


def crash_log_path() -> Path:
    return log_directory() / CRASH_LOG_FILE_NAME


def enable_crash_log() -> Path:
    """Send faulthandler's report of a native crash to ``crash.log``.

    A crash inside Qt (an access violation in a QMimeData that Windows was
    still reading, 2026-09-30 and 2026-10-03) kills the process without a
    WER event and without a last line in the application log. The file is
    appended to, with a header per run, so the report of one death is not
    erased by the next start.
    """
    global _crash_log_stream
    destination = crash_log_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    stream = open(destination, "a", encoding="utf-8")  # noqa: SIM115 - kept open
    stream.write(
        f"=== DuoInput {time.strftime('%Y-%m-%d %H:%M:%S')} "
        f"pid={os.getpid()} version={__version__} ===\n"
    )
    stream.flush()
    faulthandler.enable(stream, all_threads=True)
    _crash_log_stream = stream  # a previous stream closes with its last reference
    return destination


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
    "CRASH_LOG_FILE_NAME",
    "LOGGER_NAME",
    "LOG_BACKUP_COUNT",
    "LOG_FILE_NAME",
    "LOG_MAX_BYTES",
    "application_directory",
    "configure_logging",
    "crash_log_path",
    "enable_crash_log",
    "log_directory",
    "log_path",
]
