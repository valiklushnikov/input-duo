"""Lightweight monotonic performance events for File Provider diagnostics.

The emitter deliberately owns no files, buffers, threads, or flush policy.  It
adds one parseable line to the application's existing bounded logging path for
each stage or chunk selected by the caller.
"""

from __future__ import annotations

import logging
import math
import time
from collections.abc import Callable


def _atom(value: object) -> str:
    if value is None:
        rendered = "none"
    elif isinstance(value, bool):
        rendered = "true" if value else "false"
    elif isinstance(value, int):
        rendered = str(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("perf event floats must be finite")
        rendered = str(value)
    elif isinstance(value, str):
        rendered = value
    else:
        raise ValueError(f"unsupported perf event atom type: {type(value).__name__}")

    if not rendered or any(character.isspace() or ord(character) < 0x20 for character in rendered):
        raise ValueError("perf event atoms must be non-empty and contain no whitespace")
    return rendered


class PerfEmitter:
    """Emit stable key/value events using an injectable monotonic clock."""

    def __init__(
        self,
        logger: logging.Logger,
        clock_domain: str,
        clock: Callable[[], int] = time.monotonic_ns,
    ) -> None:
        self._logger = logger
        self._clock_domain = _atom(clock_domain)
        self._clock = clock

    def now(self) -> int:
        return int(self._clock())

    def emit(self, event: str, **fields: object) -> int:
        return self.emit_at(self.now(), event, **fields)

    def emit_at(self, stamp: int, event: str, **fields: object) -> int:
        atoms = [f"{_atom(key)}={_atom(value)}" for key, value in sorted(fields.items())]
        suffix = f" {' '.join(atoms)}" if atoms else ""
        self._logger.info(
            "fp_perf event=%s mono_ns=%d clock=%s%s",
            _atom(event),
            int(stamp),
            self._clock_domain,
            suffix,
        )
        return int(stamp)


__all__ = ["PerfEmitter"]
