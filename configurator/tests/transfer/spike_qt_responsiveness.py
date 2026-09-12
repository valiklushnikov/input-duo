"""Visible Qt animation plus timer-interval measurements for Explorer spikes."""

from __future__ import annotations

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QProgressBar

TICK_MS = 16


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

    def _tick(self) -> None:
        now = time.perf_counter()
        self.intervals.append(now - self._last)
        self._last = now
        self._value = (self._value + 1) % 101
        self.bar.setValue(self._value)

    def report(self) -> str:
        if not self.intervals:
            return "нет тиков вовсе - таймер не запускался"
        ordered = sorted(self.intervals)
        p99 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]
        return (
            f"тиков {len(ordered)}, "
            f"медиана {ordered[len(ordered) // 2] * 1000:.1f} мс, "
            f"p99 {p99 * 1000:.1f} мс, "
            f"максимум {ordered[-1] * 1000:.1f} мс"
        )


def run(seconds: int, pump) -> str:
    """Run Qt for ``seconds``, drain Windows messages, and return measurements."""
    application = QApplication.instance() or QApplication([])
    instrument = Instrument()
    instrument.start()
    driver = QTimer()
    driver.setInterval(5)
    driver.timeout.connect(pump)
    driver.start()
    stop = QTimer()
    stop.setSingleShot(True)
    stop.setInterval(seconds * 1000)
    stop.timeout.connect(application.quit)
    stop.start()
    application.exec()
    return instrument.report()
