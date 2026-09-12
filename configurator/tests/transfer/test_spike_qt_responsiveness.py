"""The responsiveness instrument must expose a stopped timer and a long pause."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spike_qt_responsiveness import Instrument


def test_an_instrument_that_never_ticked_says_so_instead_of_reporting_health(qapp):
    instrument = Instrument()

    assert "нет тиков вовсе" in instrument.report()


def test_the_report_names_the_worst_interval_not_only_the_typical_one(qapp):
    instrument = Instrument()
    instrument.intervals = [0.016] * 99 + [1.400]

    report = instrument.report()

    assert "максимум 1400.0 мс" in report, (
        "an instrument that reports only the median would hide the exact pause "
        "that it exists to measure"
    )
