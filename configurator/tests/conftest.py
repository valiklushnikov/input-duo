"""Fixtures shared by every configurator test.

Tests must never write into the operator's own ``%LOCALAPPDATA%\\DuoInput``:
that log is the evidence a silent crash is diagnosed from, and test lines in
it (``_boom`` tracebacks from test_runtime_wiring.py, 2026-09-27) are
indistinguishable from the program's own. See test_user_files_isolation.py.
"""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import sys

import pytest

from duo_input.persistence import locations


@pytest.fixture(scope="session")
def _isolated_localappdata(tmp_path_factory):
    return tmp_path_factory.mktemp("localappdata")


@pytest.fixture(autouse=True)
def isolated_user_files(_isolated_localappdata, monkeypatch):
    """Point the per-user directory at a temp dir and undo what main() attaches.

    ``configure_logging()`` leaves a file handler on the ``duo_input`` logger
    for the rest of the process, and ``enable_crash_log()`` points
    faulthandler at an open file; left alone, every later test would append
    to the one and a crash anywhere later would be reported into the other.
    """
    monkeypatch.setenv("LOCALAPPDATA", str(_isolated_localappdata))
    application_logger = logging.getLogger(locations.LOGGER_NAME)
    handlers_before = list(application_logger.handlers)
    level_before = application_logger.level
    crash_log_before = locations._crash_log_stream
    faulthandler_before = faulthandler.is_enabled()
    yield
    if locations._crash_log_stream is not crash_log_before:
        if faulthandler_before:
            faulthandler.enable(sys.__stderr__, all_threads=True)
        else:
            faulthandler.disable()
        # Последняя ссылка на файл: без неё он закрывается, и Windows даёт
        # pytest убрать временный каталог.
        locations._crash_log_stream = crash_log_before
    for handler in list(application_logger.handlers):
        if handler not in handlers_before and isinstance(
            handler, logging.handlers.RotatingFileHandler
        ):
            application_logger.removeHandler(handler)
            handler.close()
    application_logger.setLevel(level_before)
