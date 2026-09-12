"""The responsiveness instrument must expose a stopped timer and a long pause.

Its report is also the one line Task 0.5 quotes verbatim, so it is checked for
being plain ASCII: the round-4 log recorded it as "Єшъют 3513, ьхфшрэр 54.8 ьё"
after PowerShell re-encoded Cyrillic labels through the console code page.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import spike_qt_responsiveness
from spike_qt_responsiveness import Instrument, run, stop_reason


def test_an_instrument_that_never_ticked_says_so_instead_of_reporting_health(qapp):
    instrument = Instrument()

    assert "no ticks at all" in instrument.report()


def test_the_report_names_the_worst_interval_not_only_the_typical_one(qapp):
    instrument = Instrument()
    instrument.intervals = [0.016] * 99 + [1.400]

    report = instrument.report()

    assert "max 1400.0 ms" in report, (
        "an instrument that reports only the median would hide the exact pause "
        "that it exists to measure"
    )


def test_started_instrument_ticks_and_advances_the_visible_progress_bar(qapp, qtbot):
    instrument = Instrument()
    qtbot.addWidget(instrument.bar)
    starting_value = instrument.bar.value()

    instrument.start()
    qtbot.waitUntil(
        lambda: bool(instrument.intervals) and instrument.bar.value() != starting_value,
        timeout=1000,
    )

    assert instrument.intervals
    assert instrument.bar.value() != starting_value
    instrument.stop()


def test_every_report_is_plain_ascii_so_a_redirected_log_stays_readable(qapp):
    silent = Instrument()
    measured = Instrument()
    measured.intervals = [0.016, 0.054, 0.120]

    for report in (silent.report(), measured.report()):
        assert report.isascii(), (
            "the console code page mangles non-ASCII on its way to a file or "
            "through Tee-Object, and these six numbers are quoted verbatim"
        )

    assert "ticks 3, median 54.0 ms, p99 120.0 ms, max 120.0 ms" == measured.report()


def test_a_busy_consumer_keeps_the_run_alive_past_any_guessed_budget():
    assert (
        stop_reason(
            elapsed=900.0,
            quiet_for=0.1,
            saw_consumer_call=True,
            idle_seconds=20.0,
            paste_window_seconds=180.0,
            max_seconds=1800.0,
        )
        is None
    ), "a 4 GiB entry needs ~915s; a run that ends on a guess truncates it"


def test_silence_after_the_last_consumer_call_ends_the_run():
    reason = stop_reason(
        elapsed=930.0,
        quiet_for=20.0,
        saw_consumer_call=True,
        idle_seconds=20.0,
        paste_window_seconds=180.0,
        max_seconds=1800.0,
    )

    assert reason is not None
    assert reason.startswith("quiescent:") and "20" in reason


def test_waiting_for_the_operator_to_press_ctrl_v_is_not_silence_after_a_transfer():
    assert (
        stop_reason(
            elapsed=30.0,
            quiet_for=30.0,
            saw_consumer_call=False,
            idle_seconds=20.0,
            paste_window_seconds=180.0,
            max_seconds=1800.0,
        )
        is None
    ), "quitting after 20s of an unpasted run would end it before the paste"


def test_a_run_nobody_ever_pasted_into_says_so_rather_than_finishing_quietly():
    reason = stop_reason(
        elapsed=180.0,
        quiet_for=180.0,
        saw_consumer_call=False,
        idle_seconds=20.0,
        paste_window_seconds=180.0,
        max_seconds=1800.0,
    )

    assert reason is not None
    assert "Ctrl+V" in reason


def test_the_ceiling_stops_even_a_consumer_that_is_still_reading():
    reason = stop_reason(
        elapsed=1800.0,
        quiet_for=0.0,
        saw_consumer_call=True,
        idle_seconds=20.0,
        paste_window_seconds=180.0,
        max_seconds=1800.0,
    )

    assert reason is not None
    assert "ceiling" in reason


def test_run_ends_on_quiescence_and_still_returns_the_tick_statistics(qapp):
    counted = iter([1, 2, 3, 4])
    latest = 0

    def activity():
        nonlocal latest
        latest = next(counted, latest)
        return latest

    # Nothing here bounds the loop except the watchdog and its own ceiling.
    # A rescue that called qapp.exit() would latch Qt's quit state exactly as
    # quit() does - measured: every later QEventLoop.exec() returns -1 after
    # 0.0 ms - which is the defect this test exists to protect. If the watchdog
    # ever stops working this test hangs, and the mutation sweep scores that
    # timeout as a kill.
    outcome = run(
        lambda: None,
        idle_seconds=0.3,
        paste_window_seconds=0.3,
        max_seconds=5.0,
        activity=activity,
    )

    assert outcome.reason.startswith("quiescent:"), (
        "without a quiescence watchdog the run can only end on a guessed budget"
    )
    assert "ticks" in outcome.report


def test_run_still_honours_the_fixed_seconds_budget_the_brief_uses(qapp):
    outcome = run(lambda: None, seconds=0, activity=lambda: 0)

    assert "--seconds" in outcome.reason


def test_a_stopped_instrument_stops_ticking_and_takes_its_window_down(qapp, qtbot):
    instrument = Instrument()
    qtbot.addWidget(instrument.bar)
    instrument.start()
    qtbot.waitUntil(lambda: bool(instrument.intervals), timeout=1000)

    instrument.stop()
    frozen = len(instrument.intervals)
    qtbot.wait(120)

    assert len(instrument.intervals) == frozen
    assert not instrument.bar.isVisible()


def test_the_ceiling_ends_the_run_even_though_the_watchdog_never_fired(qapp, monkeypatch):
    """The ceiling must not be evaluated by the mechanism it backstops.

    The watchdog is pushed out past the ceiling, so during this run it never
    fires at all - exactly the case in which the ceiling has to hold.  Its
    thresholds are still short enough to end the run late, so deleting the
    ceiling's timer fails this test instead of hanging the suite.
    """
    monkeypatch.setattr(spike_qt_responsiveness, "WATCHDOG_MS", 2500)
    polls = []

    outcome = run(
        lambda: None,
        idle_seconds=2.0,
        paste_window_seconds=2.0,
        max_seconds=0.4,
        activity=lambda: polls.append(1) or 0,
    )

    assert len(polls) == 1, (
        "the watchdog must never have run: the only activity() call belongs to "
        f"run() itself, but it was polled {len(polls)} times"
    )
    assert outcome.reason.startswith("ceiling reached"), (
        "a ceiling checked only inside the watchdog's callback bounds nothing "
        "precisely when the watchdog is the thing that is broken"
    )
