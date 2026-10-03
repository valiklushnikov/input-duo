"""Keep tests out of the operator's own ``%LOCALAPPDATA%\\DuoInput``.

That log is the evidence a silent crash is diagnosed from, and test lines in
it (``_boom`` tracebacks from test_runtime_wiring.py, 2026-09-27) are
indistinguishable from the program's own: ``main()`` called
``configure_logging()`` with the real LOCALAPPDATA, and the file handler stayed
on the ``duo_input`` logger for the rest of the session.

A module rather than conftest code so that test_user_files_isolation.py can
release a guard explicitly (each of its tests self-contained) and can load
the very same fixture into a separate pytest run.
"""

from __future__ import annotations

import faulthandler
import logging
import logging.handlers
import sys

import pytest
from _pytest.faulthandler import fault_handler_stderr_fd_key

from duo_input.persistence import locations


class UserFilesGuard:
    """Snapshot of what a test may change; ``release()`` puts it back."""

    def __init__(self, config: pytest.Config) -> None:
        self._config = config
        self._logger = logging.getLogger(locations.LOGGER_NAME)
        self._handlers_before = list(self._logger.handlers)
        self._level_before = self._logger.level
        self._crash_log_before = locations._crash_log_stream
        self._faulthandler_before = faulthandler.is_enabled()

    def release(self) -> None:
        """Undo what configure_logging() / enable_crash_log() attached. Idempotent."""
        if locations._crash_log_stream is not self._crash_log_before:
            if self._faulthandler_before:
                # pytest's own dup of stderr, not sys.__stderr__: fd 2 is
                # redirected into the per-test capture file, and a native
                # crash would vanish with it.
                target = self._config.stash.get(fault_handler_stderr_fd_key, None)
                faulthandler.enable(
                    sys.__stderr__ if target is None else target, all_threads=True
                )
            else:
                faulthandler.disable()
            # The last reference to the file: without it the file closes, and
            # Windows lets pytest remove its temporary directory.
            locations._crash_log_stream = self._crash_log_before
        for handler in list(self._logger.handlers):
            if handler not in self._handlers_before and isinstance(
                handler, logging.handlers.RotatingFileHandler
            ):
                self._logger.removeHandler(handler)
                handler.close()
        self._logger.setLevel(self._level_before)


@pytest.fixture(scope="session")
def _isolated_localappdata(tmp_path_factory):
    return tmp_path_factory.mktemp("localappdata")


@pytest.fixture(autouse=True)
def isolated_user_files(_isolated_localappdata, monkeypatch, request):
    """Point the per-user directory at a temp dir and undo what main() attaches."""
    monkeypatch.setenv("LOCALAPPDATA", str(_isolated_localappdata))
    guard = UserFilesGuard(request.config)
    yield guard
    guard.release()


__all__ = ["UserFilesGuard", "fault_handler_stderr_fd_key", "isolated_user_files"]
