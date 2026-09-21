#!/usr/bin/env python3
"""Bounded, rotation- and reopen-safe capture of the Duo Input File Provider
extension's unified log (OSLog) for E2E debugging.

Why this exists
---------------
The extension itself emits everything through unified logging / OSLog
(``Logger(subsystem: "com.duoinput.configurator.fileprovider")``) - that is the
production observability channel and needs no file. But OSLog ``info``/``debug``
records are NOT retained in the persisted store, so a retrospective
``log show`` finds nothing; the only way to capture the fetch/eviction/enumerate
chain is a LIVE ``log stream``.

The previous ad-hoc capture (``log stream > /private/tmp/duo-appex.log``) had two
defects this tool fixes:

* it was **unbounded** - it grew to 113 MB with no rotation;
* the output file was later **deleted**, yet the stream kept writing to the now
  unlinked inode (``UNLINKED_LOG_GROWTH``) - invisible, unrecoverable growth.

This wrapper reads ``log stream`` line-by-line and writes to a real path with:

* **bounding + rotation** - at ``--max-bytes`` the active file is rotated to
  ``<path>.1`` (keeping ``--backups`` generations) and a fresh file is opened;
* **reopen-on-unlink** - before each write batch it compares the inode currently
  at ``--path`` with the inode of its open descriptor. If the file was removed or
  replaced, it closes the stale descriptor (so the unlinked inode stops growing)
  and reopens ``--path``.

It touches nothing in the extension and changes no lifecycle semantics; it is a
pure external observer.

Usage
-----
    tools/fp_log_capture.py                 # default path, follows the FP appex
    tools/fp_log_capture.py --path /tmp/x   # custom output
    tools/fp_log_capture.py --max-bytes 33554432 --backups 2

Stop with SIGINT/SIGTERM; the child ``log stream`` is torn down cleanly.
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
import time

DEFAULT_PROCESS = "DuoInputFileProvider"
DEFAULT_PATH = os.path.expanduser("~/Library/Logs/DuoInput/fileprovider-capture.log")
DEFAULT_MAX_BYTES = 32 * 1024 * 1024  # 32 MiB active file
DEFAULT_BACKUPS = 2                   # -> ~96 MiB hard ceiling total
CHECK_EVERY = 50                      # lines between inode/size checks (bounds
                                      # the worst-case unlinked-inode leak window)


class BoundedReopeningWriter:
    """A line sink that stays bounded and never writes to an unlinked inode."""

    def __init__(self, path: str, max_bytes: int, backups: int, check_every: int):
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self.check_every = check_every
        self._fh = None
        self._since_check = 0
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        self._open()

    def _open(self) -> None:
        # Append so a rotation/reopen never truncates a file another reader may
        # be tailing; line-buffered so NEW_EXTENSION_LOGS are visible promptly.
        self._fh = open(self.path, "a", buffering=1)

    def _path_inode(self):
        try:
            return os.stat(self.path).st_ino
        except FileNotFoundError:
            return None

    def _ensure_live_target(self) -> None:
        """Reopen if our descriptor points at an unlinked/replaced inode."""
        open_ino = os.fstat(self._fh.fileno()).st_ino
        if self._path_inode() != open_ino:
            # The file at self.path was removed or swapped. Drop the stale (now
            # unlinked) descriptor so it stops growing, then reopen the path.
            try:
                self._fh.close()
            finally:
                self._open()

    def _rotate_if_needed(self) -> None:
        try:
            size = os.fstat(self._fh.fileno()).st_size
        except OSError:
            return
        if size < self.max_bytes:
            return
        self._fh.close()
        # Shift <path>.1 .. <path>.N, drop the oldest.
        for i in range(self.backups, 0, -1):
            src = self.path if i == 1 else f"{self.path}.{i - 1}"
            dst = f"{self.path}.{i}"
            if os.path.exists(src):
                os.replace(src, dst)
        self._open()

    def write(self, line: str) -> None:
        self._since_check += 1
        if self._since_check >= self.check_every:
            self._since_check = 0
            self._ensure_live_target()
            self._rotate_if_needed()
        self._fh.write(line)

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()
            finally:
                self._fh = None


def build_stream_cmd(process: str, predicate_extra: str | None) -> list[str]:
    predicate = f'process == "{process}"'
    if predicate_extra:
        predicate = f"({predicate}) AND ({predicate_extra})"
    return [
        "/usr/bin/log", "stream",
        "--predicate", predicate,
        "--info", "--debug",
        "--style", "compact",
    ]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--process", default=DEFAULT_PROCESS,
                    help=f"process name to follow (default: {DEFAULT_PROCESS})")
    ap.add_argument("--predicate", default=None,
                    help="extra NSPredicate ANDed with the process filter")
    ap.add_argument("--path", default=DEFAULT_PATH,
                    help=f"capture file path (default: {DEFAULT_PATH})")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES,
                    help=f"rotate active file at this size (default: {DEFAULT_MAX_BYTES})")
    ap.add_argument("--backups", type=int, default=DEFAULT_BACKUPS,
                    help=f"rotated generations to keep (default: {DEFAULT_BACKUPS})")
    args = ap.parse_args()

    cmd = build_stream_cmd(args.process, args.predicate)
    writer = BoundedReopeningWriter(args.path, args.max_bytes, args.backups, CHECK_EVERY)

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)

    stopping = {"flag": False}

    def _stop(_signum, _frame):
        stopping["flag"] = True
        if proc.poll() is None:
            proc.terminate()

    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    header = (f"# fp_log_capture started {time.strftime('%Y-%m-%dT%H:%M:%S')} "
              f"pid={os.getpid()} cmd={' '.join(cmd)}\n")
    writer.write(header)
    sys.stderr.write(header)

    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            writer.write(line)
            if stopping["flag"]:
                break
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        writer.close()
    return proc.returncode or 0


if __name__ == "__main__":
    raise SystemExit(main())
