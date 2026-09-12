"""Visible Qt animation plus timer-interval measurements for Explorer spikes.

Every label here is plain ASCII on purpose.  This report is the one line the
six-axis comparison quotes verbatim, and the spike is always run with its
output redirected - often through PowerShell 5.1's ``Tee-Object``, which
decodes our bytes with the console code page before writing UTF-16LE.  The
round-4 run recorded the decisive line as "Єшъют 3513, ьхфшрэр 54.8 ьё":
correct numbers, unquotable labels.  ASCII survives every code page, so the
numbers stay quotable no matter how the operator captures the log.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication, QProgressBar

TICK_MS = 16

#: How often the end-condition watchdog looks at the consumer-call counter.
WATCHDOG_MS = 250

#: Silence this long after a consumer call means the transfer is over.
DEFAULT_IDLE_SECONDS = 20.0

#: How long to wait for the first consumer call before giving up on the paste.
DEFAULT_PASTE_WINDOW_SECONDS = 180.0

#: Absolute ceiling, so a wedged consumer cannot hold the run for ever.
DEFAULT_MAX_SECONDS = 1800.0


class Instrument:
    """A progress bar that must move, and a record of whether it did."""

    def __init__(self) -> None:
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setWindowTitle("Duo Input spike: GUI responsiveness")
        self.bar.resize(420, 40)
        self.intervals: list[float] = []
        self._value = 0
        self._last = time.perf_counter()
        self._timer = QTimer()
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self.bar.show()
        self._last = time.perf_counter()
        self._timer.start()

    def stop(self) -> None:
        """Stop ticking and take the window down; the measurement is over."""
        self._timer.stop()
        self.bar.close()

    def _tick(self) -> None:
        now = time.perf_counter()
        self.intervals.append(now - self._last)
        self._last = now
        self._value = (self._value + 1) % 101
        self.bar.setValue(self._value)

    def report(self) -> str:
        if not self.intervals:
            return "no ticks at all - the timer never started"
        ordered = sorted(self.intervals)
        p99 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]
        return (
            f"ticks {len(ordered)}, "
            f"median {ordered[len(ordered) // 2] * 1000:.1f} ms, "
            f"p99 {p99 * 1000:.1f} ms, "
            f"max {ordered[-1] * 1000:.1f} ms"
        )


@dataclass(frozen=True)
class Outcome:
    """What the run measured, and the end condition that stopped it."""

    report: str
    reason: str


def ceiling_reason(elapsed: float, max_seconds: float) -> str:
    """Word the ceiling once, so both things that can reach it agree."""
    return f"ceiling reached after {elapsed:.1f}s (--max-seconds {max_seconds:.0f})"


def stop_reason(
    elapsed: float,
    quiet_for: float,
    saw_consumer_call: bool,
    *,
    idle_seconds: float,
    paste_window_seconds: float,
    max_seconds: float,
) -> str | None:
    """Name the end condition that has been met, or ``None`` to keep running.

    A wall-clock budget is the wrong end condition for a transfer whose
    throughput nobody knows in advance: round 4 gave the 4 GiB entry 180s of a
    run it needed about 915s of, and cut it at 18.9%.  So the run ends on
    silence instead - but only silence that follows a consumer call, because
    the silence before one is just the operator walking to Explorer.
    """
    if elapsed >= max_seconds:
        return ceiling_reason(elapsed, max_seconds)
    if not saw_consumer_call:
        if quiet_for >= paste_window_seconds:
            return (
                f"no consumer called in {quiet_for:.1f}s "
                f"(--paste-window-seconds {paste_window_seconds:.0f}) "
                "- was Ctrl+V ever pressed?"
            )
        return None
    if quiet_for >= idle_seconds:
        return (
            f"quiescent: no consumer call for {quiet_for:.1f}s "
            f"(--idle-seconds {idle_seconds:.0f})"
        )
    return None


def run(
    pump,
    *,
    seconds: int | None = None,
    idle_seconds: float = DEFAULT_IDLE_SECONDS,
    paste_window_seconds: float = DEFAULT_PASTE_WINDOW_SECONDS,
    max_seconds: float = DEFAULT_MAX_SECONDS,
    activity=None,
) -> Outcome:
    """Run Qt while draining Windows messages, and say why the run ended.

    With ``seconds`` set the run keeps the brief's fixed budget.  Without it
    the run ends on quiescence: ``activity`` returns a count of consumer calls
    so far, and the watchdog stops the loop once that count has stood still.
    ``max_seconds`` is enforced by a timer of its own, so the ceiling still
    holds when the watchdog is the thing that is broken.
    """
    if QApplication.instance() is None:
        QApplication([])
    # The run exits its own loop rather than the application's: quitting the
    # QApplication latches Qt's quit state, and then every later QEventLoop in
    # the process returns at once.
    loop = QEventLoop()
    instrument = Instrument()
    instrument.start()
    driver = QTimer()
    driver.setInterval(5)
    driver.timeout.connect(pump)
    driver.start()

    started = time.perf_counter()
    count = activity() if activity else 0
    changed_at = started
    saw_consumer_call = False
    reason = ""

    if seconds is not None:
        def budget_expired() -> None:
            nonlocal reason
            reason = f"fixed --seconds {seconds} budget expired"
            loop.quit()

        stop = QTimer()
        stop.setSingleShot(True)
        stop.setInterval(int(seconds * 1000))
        stop.timeout.connect(budget_expired)
        stop.start()
    else:
        # The ceiling gets its own timer, evaluated by nothing but itself.  It
        # used to be checked only inside watch() below, which left it bounding
        # nothing in the one case it exists for: a watchdog that never fires.
        def ceiling_expired() -> None:
            nonlocal reason
            reason = ceiling_reason(time.perf_counter() - started, max_seconds)
            loop.quit()

        ceiling = QTimer()
        ceiling.setSingleShot(True)
        ceiling.setInterval(int(max_seconds * 1000))
        ceiling.timeout.connect(ceiling_expired)
        ceiling.start()

        def watch() -> None:
            nonlocal count, changed_at, saw_consumer_call, reason
            now = time.perf_counter()
            current = activity() if activity else 0
            if current != count:
                count = current
                changed_at = now
                saw_consumer_call = True
            met = stop_reason(
                now - started,
                now - changed_at,
                saw_consumer_call,
                idle_seconds=idle_seconds,
                paste_window_seconds=paste_window_seconds,
                max_seconds=max_seconds,
            )
            if met is not None:
                reason = met
                loop.quit()

        watchdog = QTimer()
        watchdog.setInterval(WATCHDOG_MS)
        watchdog.timeout.connect(watch)
        watchdog.start()

    loop.exec()
    driver.stop()
    instrument.stop()
    return Outcome(report=instrument.report(), reason=reason)
