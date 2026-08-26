"""The parts of the HIL runner that can be checked without the rig.

The measurement arithmetic and the refusal to run without hardware are both
testable here. The measurements themselves are not: that is the whole point of
the file under test.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hil_runner import (  # noqa: E402
    INPUT_P95_BUDGET_MS,
    MAX_ACCEPTABLE_GAP_MS,
    U2_RELEASE_BUDGET_MS,
    HardwareRequired,
    PeripheralRow,
    Sample,
    ScenarioResult,
    gaps,
    latency_report,
    load_scenario,
    percentile,
    run,
)

SCENARIOS = Path(__file__).resolve().parent / "scenarios"

MS = 1_000_000


def _sample(kind: str, latency_ms: float, at_ms: float = 0.0) -> Sample:
    injected = int(at_ms * MS)
    return Sample(kind, injected, injected + int(latency_ms * MS))


# ------------------------------------------------------------------ arithmetic


def test_a_latency_is_the_gap_between_injection_and_observation():
    assert _sample("keyboard", 12.5).latency_ms == pytest.approx(12.5)


def test_the_percentile_is_a_value_that_was_actually_measured():
    values = [1.0, 2.0, 3.0, 100.0]

    # Nearest rank, so p95 is a real measurement rather than a number that
    # falls between two of them.
    assert percentile(values, 0.95) in values
    assert percentile(values, 0.95) == 100.0
    assert percentile(values, 0.5) == 2.0


def test_a_percentile_of_nothing_is_refused():
    with pytest.raises(ValueError):
        percentile([], 0.95)


@pytest.mark.parametrize("fraction", (0.0, 1.5, -0.1))
def test_a_percentile_outside_the_range_is_refused(fraction):
    with pytest.raises(ValueError):
        percentile([1.0], fraction)


def test_a_report_within_budget_passes():
    samples = [_sample("keyboard", 5.0) for _ in range(100)]

    report = latency_report("keyboard", samples)

    assert report.count == 100
    assert report.p95_ms == 5.0
    assert report.passed is True


def test_more_than_one_slow_event_in_twenty_fails_the_p95():
    samples = [_sample("keyboard", 5.0) for _ in range(94)]
    samples += [_sample("keyboard", 40.0) for _ in range(6)]

    report = latency_report("keyboard", samples)

    assert report.p95_ms > INPUT_P95_BUDGET_MS
    assert report.passed is False


def test_exactly_one_slow_event_in_twenty_still_passes():
    # "p95 <= 20 ms" means the slowest 5% may exceed it. Five slow events in a
    # hundred sit exactly on that line, and the line is a pass; the maximum is
    # reported alongside so the tail stays visible.
    samples = [_sample("keyboard", 5.0) for _ in range(95)]
    samples += [_sample("keyboard", 40.0) for _ in range(5)]

    report = latency_report("keyboard", samples)

    assert report.p95_ms == 5.0
    assert report.max_ms == 40.0
    assert report.passed is True


def test_a_report_with_no_samples_never_passes():
    # An empty measurement is not a pass. Nothing was measured.
    assert latency_report("mouse", []).passed is False


def test_a_report_only_counts_its_own_kind():
    samples = [_sample("keyboard", 5.0), _sample("mouse", 90.0)]

    assert latency_report("keyboard", samples).count == 1
    assert latency_report("keyboard", samples).max_ms == 5.0


def test_only_pauses_over_the_threshold_are_reported():
    observed = [0, 10 * MS, 20 * MS, 100 * MS]

    assert gaps(observed) == [80.0]
    assert gaps(observed, threshold_ms=200.0) == []


def test_gaps_are_measured_in_time_order_not_arrival_order():
    assert gaps([100 * MS, 0, 10 * MS]) == [90.0]


def test_the_budgets_are_the_ones_the_specification_names():
    assert INPUT_P95_BUDGET_MS == 20.0
    assert MAX_ACCEPTABLE_GAP_MS == 50.0
    assert U2_RELEASE_BUDGET_MS == 100.0


# ------------------------------------------------------------------- results


def test_a_result_with_no_checks_has_not_passed():
    result = ScenarioResult(scenario="x", started_at="now")

    assert result.passed is False


def test_a_result_passes_only_when_every_check_did():
    result = ScenarioResult(scenario="x", started_at="now")
    result.checks = {"a": True, "b": True}
    assert result.passed is True

    result.checks["c"] = False
    assert result.passed is False


def test_a_report_records_every_sample_it_derived_from():
    result = ScenarioResult(scenario="x", started_at="now")
    result.samples = [_sample("keyboard", 4.0), _sample("mouse", 6.0)]
    result.checks = {"a": True}

    document = json.loads(result.to_json())

    assert len(document["samples"]) == 2
    assert {report["kind"] for report in document["latency"]} == {"keyboard", "mouse"}


def test_a_peripheral_row_states_a_reason_even_when_it_passed():
    row = PeripheralRow(
        role="keyboard",
        vendor_id="0x046D",
        product_id="0xC31C",
        descriptor_hash="ab" * 32,
        buttons=0,
        passed=True,
        reason="enumerated as a boot keyboard, 6KRO, no hub",
    )

    assert row.reason


# ----------------------------------------------------------------- scenarios


@pytest.mark.parametrize(
    "name",
    (
        "peripherals",
        "route_toggle",
        "link_fault",
        "config_power_cut",
        "profile_power_cycle",
        "soak_24h",
    ),
)
def test_every_committed_scenario_is_loadable(name):
    scenario = load_scenario(SCENARIOS / f"{name}.json")

    assert scenario["name"] == name
    assert scenario["requires"]
    assert scenario["steps"]
    assert scenario["checks"]


def test_a_scenario_missing_a_key_is_refused(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"name": "x", "steps": []}), encoding="utf-8")

    with pytest.raises(ValueError):
        load_scenario(path)


def test_a_scenario_with_no_steps_is_refused(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(
        json.dumps({"name": "x", "description": "", "requires": [], "steps": []}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_scenario(path)


def test_a_step_without_an_action_is_refused(tmp_path):
    path = tmp_path / "vague.json"
    path.write_text(
        json.dumps(
            {"name": "x", "description": "", "requires": [], "steps": [{"note": "do a thing"}]}
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_scenario(path)


# --------------------------------------------------------- no rig, no result


def test_running_without_hardware_refuses_rather_than_reporting():
    # The one behaviour that matters most here: a HIL report produced with no
    # hardware would look exactly like evidence, so there must be no way to
    # get one.
    with pytest.raises(HardwareRequired):
        run(SCENARIOS / "route_toggle.json", port=None)


def test_the_refusal_names_what_the_scenario_needs():
    with pytest.raises(HardwareRequired) as error:
        run(SCENARIOS / "link_fault.json", port=None)

    assert "port" in str(error.value).lower()
