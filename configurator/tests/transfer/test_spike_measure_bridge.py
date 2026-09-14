# configurator/tests/transfer/test_spike_measure_bridge.py
"""Прибор проверяется отдельно от того, что он измеряет."""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="мост - это Windows")

from spike_measure_bridge import Measurement


def _measurement(**overrides) -> Measurement:
    fields = dict(
        bytes_transferred=1024 * 1024 * 100,
        elapsed_seconds=2.0,
        peak_rss_bytes=180 * 1024 * 1024,
        peak_python_bytes=3 * 1024 * 1024,
        pipe_high_water=1,
        cancel_latency_seconds=None,
        gui_tick_p99_ms=18.0,
        gui_tick_max_ms=41.0,
        socket_bytes_to_write_max=65536,
        rtt_median_ms=0.8,
        rtt_p99_ms=2.4,
        disk_read_median_ms=0.3,
        disk_read_p99_ms=1.1,
        bypassed_transport_mib_s=112.0,
        heartbeat_gaps_seconds=[10.0, 10.1],
    )
    fields.update(overrides)
    return Measurement(**fields)


def test_throughput_is_derived_from_the_bytes_and_the_clock():
    assert _measurement().throughput_mib_s == pytest.approx(50.0, rel=0.01)


def test_a_zero_length_run_reports_no_throughput_instead_of_dividing_by_zero():
    assert _measurement(bytes_transferred=0, elapsed_seconds=0.0).throughput_mib_s == 0.0


def test_the_table_names_the_worst_gui_tick_not_only_the_typical_one():
    table = _measurement(gui_tick_max_ms=1400.0).as_table()

    assert "1400" in table, (
        "отчёт без максимума скрыл бы ровно то замирание, ради которого "
        "измерение и делается"
    )


def test_the_table_reports_the_peak_queue_depth():
    assert "high_water" in _measurement().as_table()


def test_the_rtt_bound_says_what_one_sequential_round_trip_alone_would_allow():
    # 256 КиБ за 0.8 мс = 312 МиБ/с. Если это намного выше измеренной
    # пропускной способности, узкое место НЕ в круге, и окно его не сдвинет.
    assert _measurement().rtt_bound_mib_s(256 * 1024) == pytest.approx(312.5, rel=0.01)


def test_a_zero_rtt_reports_no_bound_instead_of_dividing_by_zero():
    assert _measurement(rtt_median_ms=0.0).rtt_bound_mib_s(256 * 1024) == 0.0


def test_the_table_names_the_bottleneck_evidence_the_gate_needs():
    table = _measurement().as_table()

    for needed in ("RTT", "чтение с диска", "транспорт без моста"):
        assert needed in table, (
            f"в отчёте нет строки {needed!r} - гейт задачи 3.2 не сможет "
            "отличить медленный круг от медленного транспорта"
        )


def test_the_table_reports_cancel_latency_as_absent_rather_than_as_zero():
    # Ноль означал бы "отмена мгновенна", а не "отмену не проверяли".
    table = _measurement(cancel_latency_seconds=None).as_table()

    assert "не измерялась" in table
