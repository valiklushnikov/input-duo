from __future__ import annotations

import logging
from types import SimpleNamespace

import pytest

from duo_input.transfer.fileprovider_perf import PerfEmitter
from duo_input.transfer import fileprovider_perf as perf_module


def test_emitter_records_injected_monotonic_time_and_sorted_fields(caplog):
    log = logging.getLogger("duo_input.test.perf")
    perf = PerfEmitter(log, "mac_python_monotonic", clock=lambda: 123_456_789)

    with caplog.at_level(logging.INFO, logger=log.name):
        returned = perf.emit_at(
            perf.now(), "file_read_send", read_id=7, length=4096
        )

    assert returned == 123_456_789
    assert caplog.records[-1].getMessage() == (
        "fp_perf event=file_read_send mono_ns=123456789 "
        "clock=mac_python_monotonic length=4096 read_id=7"
    )


def test_emitter_renders_boolean_and_none_as_stable_atoms(caplog):
    log = logging.getLogger("duo_input.test.perf.types")
    perf = PerfEmitter(log, "mac_python_monotonic", clock=lambda: 17)

    with caplog.at_level(logging.INFO, logger=log.name):
        perf.emit("slot_acquired", queued=True, error=None)

    assert caplog.records[-1].getMessage().endswith("error=none queued=true")


@pytest.mark.parametrize("value", ["has space", "line\nbreak", b"secret"])
def test_emitter_rejects_fields_that_cannot_be_safe_key_values(value):
    perf = PerfEmitter(logging.getLogger("test"), "mac_python_monotonic")

    with pytest.raises(ValueError):
        perf.emit("unsafe", payload=value)


def test_emit_uses_exactly_one_clock_sample(caplog):
    ticks = iter([41, 42])
    log = logging.getLogger("duo_input.test.perf.once")
    perf = PerfEmitter(log, "mac_python_monotonic", clock=lambda: next(ticks))

    with caplog.at_level(logging.INFO, logger=log.name):
        assert perf.emit("open_fetch_enter") == 41

    assert perf.now() == 42


def test_windows_default_uses_high_resolution_performance_counter(monkeypatch):
    monkeypatch.setattr(perf_module.sys, "platform", "win32")
    monkeypatch.setattr(perf_module.time, "perf_counter_ns", lambda: 987_654_321)
    monkeypatch.setattr(
        perf_module.time,
        "get_clock_info",
        lambda name: SimpleNamespace(resolution=1e-7, implementation="QueryPerformanceCounter")
        if name == "perf_counter"
        else SimpleNamespace(resolution=0.015625, implementation="GetTickCount64"),
    )

    perf = PerfEmitter(logging.getLogger("test"), "windows_python_monotonic")

    assert perf.now() == 987_654_321
    assert perf.clock_name == "perf_counter_ns"
    assert perf.clock_resolution_ns == 100
    assert perf.clock_implementation == "QueryPerformanceCounter"
