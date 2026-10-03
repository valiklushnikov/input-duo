"""crash.log: the next silent death must leave evidence.

Two field deaths (2026-09-30, 2026-10-03) left nothing: no WER event, no
Application Error, the application log simply stopped. faulthandler writing to
a file beside that log turns such a death into a Python traceback.
"""

from __future__ import annotations

import faulthandler
import os
import subprocess
import sys
from pathlib import Path

from duo_input.persistence import locations

SOURCE = Path(__file__).resolve().parents[2] / "src"

#: Enable the crash log exactly as the program does, then die natively.
CRASHING_PROGRAM = (
    "import faulthandler\n"
    "from duo_input.persistence.locations import enable_crash_log\n"
    "enable_crash_log()\n"
    "faulthandler._sigsegv()\n"
)


def _crash_once(localappdata: Path) -> subprocess.CompletedProcess:
    environment = dict(os.environ, LOCALAPPDATA=str(localappdata), PYTHONPATH=str(SOURCE))
    return subprocess.run(
        [sys.executable, "-c", CRASHING_PROGRAM],
        env=environment,
        capture_output=True,
        timeout=60,
    )


def test_a_native_crash_is_written_to_the_crash_log_run_after_run(tmp_path):
    first = _crash_once(tmp_path)
    second = _crash_once(tmp_path)

    assert first.returncode != 0 and second.returncode != 0
    text = (tmp_path / "DuoInput" / "logs" / "crash.log").read_text(encoding="utf-8")
    # One header per run, and the first run's report survives the second.
    assert text.count("=== DuoInput ") == 2
    # A Qt access violation reads "Windows fatal exception: access violation"
    # (the repro); a fault inside the interpreter reads "Fatal Python error".
    reports = text.count("Windows fatal exception") + text.count("Fatal Python error")
    assert reports == 2
    assert text.count("most recent call first") == 2
    assert b"most recent call first" not in first.stderr  # went to the file


def test_the_header_names_the_run(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    path = locations.enable_crash_log()

    assert path == tmp_path / "DuoInput" / "logs" / "crash.log"
    assert faulthandler.is_enabled()
    header = path.read_text(encoding="utf-8")
    assert f"pid={os.getpid()}" in header
    from duo_input import __version__

    assert f"version={__version__}" in header
