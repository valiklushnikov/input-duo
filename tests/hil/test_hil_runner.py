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
    ACCEPTANCE_SCENARIO_KIND,
    EXIT_INVALID_SCENARIO,
    EXIT_PASSED,
    INPUT_P95_BUDGET_MS,
    MAX_ACCEPTABLE_GAP_MS,
    MEASURABLE_CHECKS,
    U2_RELEASE_BUDGET_MS,
    UNMEASURABLE_CHECKS,
    UNMEASURED_RECORD_VALUE,
    HardwareRequired,
    PeripheralRow,
    Sample,
    ScenarioResult,
    Unmeasured,
    coverage_of,
    evaluate,
    exit_code,
    gaps,
    latency_report,
    load_scenario,
    main,
    measure,
    percentile,
    peripheral_rows,
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
    # Named for the link they were taken over. The device's own input latency
    # is reported separately and under its own heading, because a host-side
    # round trip and a firmware-internal interval are different journeys.
    assert {report["kind"] for report in document["control_link_latency"]} == {
        "keyboard",
        "mouse",
    }


def test_a_peripheral_row_states_a_reason_even_when_it_passed():
    row = PeripheralRow(
        port="keyboard slot",
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
        "pio_usb_hub_enumeration",
        "pio_usb_hub_recovery",
        "pio_usb_dual_pc_routes",
    ),
)
def test_every_committed_scenario_is_loadable(name):
    scenario = load_scenario(SCENARIOS / f"{name}.json")

    assert scenario["name"] == name
    assert scenario["requires"]
    assert scenario["steps"]
    assert scenario["checks"]


@pytest.mark.parametrize(
    "name",
    (
        "peripherals",
        "route_toggle",
        "link_fault",
        "config_power_cut",
        "profile_power_cycle",
        "soak_24h",
        "pio_usb_hub_enumeration",
        "pio_usb_hub_recovery",
        "pio_usb_dual_pc_routes",
    ),
)
def test_every_committed_scenario_says_what_it_decides_on_this_rig(name):
    # A scenario file that lists ten steps and five checks reads as runnable.
    # On a two-port bench with no logger at either computer most of them decide
    # nothing, and the reader of the file - not only the reader of a report -
    # is entitled to know which before spending an afternoon on it.
    scenario = load_scenario(SCENARIOS / f"{name}.json")

    assert scenario["on_this_rig"].strip()


def test_the_report_carries_the_scenarios_own_account_of_this_rig():
    scenario = load_scenario(SCENARIOS / "link_fault.json")

    result = measure(scenario, FakeSession(_diagnostics()))

    assert scenario["on_this_rig"] in result.notes


def test_a_scenario_that_does_not_say_what_it_decides_here_is_refused(tmp_path):
    path = tmp_path / "silent.json"
    path.write_text(
        json.dumps(
            {
                "name": "x",
                "description": "",
                "requires": [],
                "steps": [{"action": "connect"}],
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError):
        load_scenario(path)


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


# ------------------------------------------------- what the device reports
#
# Everything below drives the runner with a stand-in for the board. That is not
# a way of pretending to have hardware: no number in these tests reaches a
# report, and what is under test is the arithmetic and the honesty of the
# verdict, both of which have to be right before a real reading is worth taking.


def _histogram(counts, edges=(250, 500, 1000, 2000, 5000, 10000, 20000, 50000), max_us=0):
    from duo_input.device.transactions import LatencyHistogram

    return LatencyHistogram(
        edges_us=tuple(edges), buckets=tuple(counts), count=sum(counts), max_us=max_us
    )


def _port(**overrides):
    from duo_input.device.transactions import PeripheralPort

    fields = {
        "attached": True,
        "ready": True,
        "kind": "keyboard",
        "vendor_id": 0x046D,
        "product_id": 0xC31C,
        "buttons": None,
        "report_descriptor_bytes": 0,
        "descriptor_hash": None,
    }
    fields.update(overrides)
    return PeripheralPort(**fields)


def _diagnostics(**overrides):
    from duo_input.device.transactions import DeviceDiagnostics

    fields = {
        "bad_crc": 0,
        "disconnect": 0,
        "timeout": 0,
        "bad_sequence": 0,
        "aborted_staging": 0,
    }
    fields.update(overrides)
    return DeviceDiagnostics(**fields)


class FakeSession:
    """A U1 that answers exactly one question, with whatever it was given."""

    def __init__(self, diagnostics):
        self._diagnostics = diagnostics
        self.reads = 0

    def diagnostics(self):
        self.reads += 1
        return self._diagnostics


def _scenario(name="x", checks=None, steps=None, **extra):
    document = {
        "name": name,
        "description": "",
        "requires": ["a U1"],
        "steps": steps or [{"action": "connect"}],
        "checks": checks or {},
    }
    document.update(extra)
    return document


def test_a_p95_inside_the_budget_passes_on_counts_the_device_supplied():
    fast = _histogram((0, 0, 100, 0, 0, 0, 0, 0, 0), max_us=900)

    checks, unmeasured = evaluate(
        _scenario(checks={"keyboard_p95_within_budget": ""}),
        _diagnostics(keyboard_latency=fast),
    )

    assert checks == {"keyboard_p95_within_budget": True}
    assert unmeasured == []


def test_a_p95_over_the_budget_fails():
    slow = _histogram((0, 0, 0, 0, 0, 0, 90, 10, 0), max_us=45000)

    checks, _ = evaluate(
        _scenario(checks={"keyboard_p95_within_budget": ""}),
        _diagnostics(keyboard_latency=slow),
    )

    assert checks == {"keyboard_p95_within_budget": False}


def test_a_device_that_measured_nothing_is_not_a_device_that_was_fast():
    empty = _histogram((0,) * 9)

    checks, unmeasured = evaluate(
        _scenario(checks={"keyboard_p95_within_budget": ""}),
        _diagnostics(keyboard_latency=empty),
    )

    assert checks == {}
    assert [entry.check for entry in unmeasured] == ["keyboard_p95_within_budget"]
    assert "no keyboard events" in unmeasured[0].reason


def test_a_baseline_makes_the_counters_describe_this_run_and_not_the_lifetime():
    # A thousand fast samples were already on the device when the run began, and
    # every one of the ten that arrived during it was over budget. Judged over
    # the lifetime the old samples drown the new ones and this passes; judged
    # over the run it fails, and the run is the question being asked.
    before = _histogram((0, 0, 1000, 0, 0, 0, 0, 0, 0))
    after = _histogram((0, 0, 1000, 0, 0, 0, 0, 10, 0), max_us=45000)

    # The lifetime reading really does pass, so this test fails for the reason
    # it says rather than because the numbers were slow either way.
    assert evaluate(
        _scenario(checks={"keyboard_p95_within_budget": ""}),
        _diagnostics(keyboard_latency=after),
    )[0] == {"keyboard_p95_within_budget": True}

    checks, _ = evaluate(
        _scenario(checks={"keyboard_p95_within_budget": ""}),
        _diagnostics(keyboard_latency=after),
        baseline=_diagnostics(keyboard_latency=before),
    )

    assert checks == {"keyboard_p95_within_budget": False}


def test_a_stall_is_a_slow_sample_and_not_a_pause_between_two_keystrokes():
    # 50 ms is a bucket edge, so "nothing took longer than 50 ms" is a count and
    # not an estimate. One sample past the edge decides it.
    stalled = _histogram((0, 0, 99, 0, 0, 0, 0, 0, 1), max_us=90000)

    checks, _ = evaluate(
        _scenario(checks={"no_gap_over_50ms": ""}),
        _diagnostics(keyboard_latency=stalled),
    )

    assert checks == {"no_gap_over_50ms": False}


def test_no_stall_passes_when_every_sample_is_inside_the_threshold():
    fine = _histogram((0, 0, 100, 0, 0, 0, 0, 0, 0))

    checks, _ = evaluate(
        _scenario(checks={"no_gap_over_50ms": ""}),
        _diagnostics(keyboard_latency=fine, mouse_latency=_histogram((0,) * 9)),
    )

    assert checks == {"no_gap_over_50ms": True}


def _release(checks, now, baseline):
    return evaluate(_scenario(checks={checks: ""}), now, baseline=baseline)


def test_the_u2_release_is_measured_against_the_hundred_millisecond_budget():
    inside = _release(
        "u2_released_within_budget",
        _diagnostics(endpoint_drops=8, endpoint_release_ms=88),
        _diagnostics(endpoint_drops=7, endpoint_release_ms=100),
    )[0]
    assert inside == {"u2_released_within_budget": True}

    outside = _release(
        "u2_released_within_budget",
        _diagnostics(endpoint_drops=8, endpoint_release_ms=104),
        _diagnostics(endpoint_drops=7, endpoint_release_ms=100),
    )[0]
    assert outside == {"u2_released_within_budget": False}
    assert U2_RELEASE_BUDGET_MS == 100.0


def test_a_release_that_never_happened_is_not_a_release_within_budget():
    checks, unmeasured = _release(
        "u2_released_within_budget",
        _diagnostics(endpoint_drops=0, endpoint_release_ms=0),
        _diagnostics(endpoint_drops=0, endpoint_release_ms=0),
    )

    assert checks == {}
    assert "no release to time" in unmeasured[0].reason


def test_a_release_from_before_the_run_is_not_a_release_this_run_caused():
    # This bench read `endpoint_drops: 7, endpoint_release_ms: 100` during a
    # scenario that never went near SPI1. Both are lifetime values: the count
    # saturates upward and the time is whichever release happened last, whenever
    # that was. Deciding the check from them would report a pass for a fault
    # nobody caused, which is exactly a scenario that looks runnable and proves
    # nothing.
    checks, unmeasured = _release(
        "u2_released_within_budget",
        _diagnostics(endpoint_drops=7, endpoint_release_ms=100),
        _diagnostics(endpoint_drops=7, endpoint_release_ms=100),
    )

    assert checks == {}
    assert "did not release anything between the two readings" in unmeasured[0].reason


def test_a_release_cannot_be_attributed_to_a_run_that_took_no_baseline():
    checks, unmeasured = evaluate(
        _scenario(checks={"u2_released_within_budget": ""}),
        _diagnostics(endpoint_drops=7, endpoint_release_ms=100),
    )

    assert checks == {}
    assert "baseline" in unmeasured[0].reason


def test_several_drops_in_one_run_leave_only_the_last_release_timed():
    # U2 keeps `last_release_ms`, not every release. "Every held key released
    # within 100 ms of the cut" is not decidable from one of three cuts.
    checks, unmeasured = _release(
        "u2_released_within_budget",
        _diagnostics(endpoint_drops=10, endpoint_release_ms=88),
        _diagnostics(endpoint_drops=7, endpoint_release_ms=100),
    )

    assert checks == {}
    assert "3 times" in unmeasured[0].reason
    assert "last" in unmeasured[0].reason


def test_a_link_that_was_never_cut_answering_now_is_not_a_recovery():
    checks, unmeasured = _release(
        "link_recovered",
        _diagnostics(endpoint_answering=True, endpoint_drops=7),
        _diagnostics(endpoint_answering=True, endpoint_drops=7),
    )

    assert checks == {}
    assert "nothing interrupted the link" in unmeasured[0].reason


def test_a_link_that_answers_after_a_drop_in_this_run_has_recovered():
    up = _release(
        "link_recovered",
        _diagnostics(endpoint_answering=True, endpoint_drops=8),
        _diagnostics(endpoint_answering=True, endpoint_drops=7),
    )[0]
    assert up == {"link_recovered": True}

    down = _release(
        "link_recovered",
        _diagnostics(endpoint_answering=False, endpoint_drops=8),
        _diagnostics(endpoint_answering=True, endpoint_drops=7),
    )[0]
    assert down == {"link_recovered": False}


def test_a_counter_that_only_climbs_says_nothing_about_a_run_with_no_baseline():
    checks, unmeasured = evaluate(
        _scenario(checks={"crc_counter_incremented": ""}),
        _diagnostics(link_crc_errors=7),
    )

    assert checks == {}
    assert "baseline" in unmeasured[0].reason


def test_with_a_baseline_the_crc_counter_answers_for_the_run():
    checks, _ = evaluate(
        _scenario(checks={"crc_counter_incremented": ""}),
        _diagnostics(link_crc_errors=57),
        baseline=_diagnostics(link_crc_errors=7),
    )

    assert checks == {"crc_counter_incremented": True}


def test_no_crc_error_counted_is_not_a_failure_to_count_one():
    # The runner cannot see whether anybody grounded MOSI. A run where no frame
    # was corrupted and a firmware that counts no corruption look identical from
    # here, and calling the pair "failed" fails every honest run that skipped
    # the step.
    checks, unmeasured = evaluate(
        _scenario(checks={"crc_counter_incremented": ""}),
        _diagnostics(link_crc_errors=7),
        baseline=_diagnostics(link_crc_errors=7),
    )

    assert checks == {}
    assert "never corrupted" in unmeasured[0].reason


# ----------------------------------------------------- what it cannot measure


def test_a_check_that_needs_the_far_computer_is_recorded_not_skipped():
    checks, unmeasured = evaluate(
        _scenario(checks={"no_stuck_keys": "", "no_misrouted_event": ""}),
        _diagnostics(),
    )

    assert checks == {}
    assert {entry.check for entry in unmeasured} == {"no_stuck_keys", "no_misrouted_event"}
    for entry in unmeasured:
        assert entry.reason
        assert entry.needs


def test_an_unknown_check_is_never_quietly_a_pass():
    # The failure this prevents: a check added to a scenario later, matched by
    # nothing, and silently absent from a report that says everything passed.
    checks, unmeasured = evaluate(
        _scenario(checks={"something_nobody_taught_this_runner": ""}), _diagnostics()
    )

    assert checks == {}
    assert unmeasured[0].check == "something_nobody_taught_this_runner"


def test_every_check_in_every_committed_scenario_is_accounted_for():
    # Either it is measured or the runner says why not. There is no third state,
    # and a scenario check falling through both tables is a silence nobody chose.
    for path in sorted(SCENARIOS.glob("*.json")):
        for name in load_scenario(path)["checks"]:
            assert name in MEASURABLE_CHECKS or name in UNMEASURABLE_CHECKS, (
                f"{path.name} asks for {name} and hil_runner.py has no ruling on it"
            )


def test_a_run_with_an_unmeasured_check_is_partial_and_not_a_pass():
    result = ScenarioResult(scenario="x", started_at="now")
    result.checks = {"keyboard_p95_within_budget": True}

    assert result.verdict == "passed"
    assert exit_code(result) == 0

    result.unmeasured = [Unmeasured("no_stuck_keys", "reason", "needs")]

    assert result.verdict == "partial"
    assert result.complete is False
    assert exit_code(result) == 3


def test_a_failure_outranks_an_unmeasured_check():
    result = ScenarioResult(scenario="x", started_at="now")
    result.checks = {"a": False}
    result.unmeasured = [Unmeasured("b", "reason", "needs")]

    assert result.verdict == "failed"
    assert exit_code(result) == 1


def test_a_run_that_decided_nothing_is_not_a_pass():
    result = ScenarioResult(scenario="x", started_at="now")

    assert result.verdict == "nothing measured"
    assert exit_code(result) == 3


# --------------------------------------------------------- the device rows


def test_each_port_becomes_a_row_naming_the_device_the_firmware_found():
    rows = peripheral_rows(
        _diagnostics(
            peripherals=(
                _port(),
                _port(
                    kind="mouse",
                    vendor_id=0x1234,
                    product_id=0x5678,
                    buttons=5,
                    report_descriptor_bytes=67,
                    descriptor_hash="ab" * 32,
                ),
            )
        )
    )

    assert [row.role for row in rows] == ["keyboard", "mouse"]
    assert [row.port for row in rows] == ["keyboard slot", "mouse slot"]
    assert rows[0].vendor_id == "0x046D"
    assert rows[1].buttons == 5
    assert rows[1].descriptor_hash == "ab" * 32
    for row in rows:
        assert row.reason


def test_a_row_takes_its_role_from_the_descriptor_and_not_from_the_channel():
    # This bench has the mouse on the channel the firmware calls "keyboard" and
    # the keyboard on the one it calls "mouse". A role read off the channel name
    # would print `role: keyboard` beside a reason reading "enumerated as
    # mouse" - the row contradicting itself, in the one artifact whose whole
    # job is to say which device is which. The descriptor is the authority.
    rows = peripheral_rows(
        _diagnostics(
            peripherals=(
                _port(kind="mouse", vendor_id=0x1BCF, product_id=0x0005, buttons=5),
                _port(kind="keyboard", vendor_id=0x258A, product_id=0x010C),
            )
        )
    )

    assert [row.role for row in rows] == ["mouse", "keyboard"]
    assert rows[0].vendor_id == "0x1BCF"
    assert rows[1].vendor_id == "0x258A"
    # And each row still says which channel it came off, because that is how a
    # crossed cable is diagnosed - it is just never the role.
    assert [row.port for row in rows] == ["keyboard slot", "mouse slot"]
    for row in rows:
        assert row.role in row.reason


def test_a_role_and_a_reason_can_never_disagree_in_a_row():
    # The two are now read from the same field, so there is no pair of readings
    # that could drift apart.
    for kind in ("keyboard", "mouse", "unknown"):
        row = peripheral_rows(_diagnostics(peripherals=(_port(kind=kind),)))[0]
        assert row.role == kind
        assert f"enumerated as {kind}" in row.reason


def test_an_unenumerated_port_still_reports_the_role_enumeration_saw():
    row = peripheral_rows(_diagnostics(peripherals=(_port(kind="mouse", ready=False),)))[0]

    assert row.role == "mouse"
    assert row.port == "keyboard slot"
    assert row.passed is False


def test_an_empty_port_claims_no_role_rather_than_the_channels_name():
    # "keyboard" on an empty port would be a claim about a device that is not
    # there. The channel is still named, because the empty channel is the fact.
    row = peripheral_rows(
        _diagnostics(peripherals=(_port(attached=False, ready=False, kind="none"),))
    )[0]

    assert row.role == "none"
    assert row.port == "keyboard slot"


def test_an_empty_port_is_a_row_that_says_so_rather_than_a_missing_row():
    rows = peripheral_rows(_diagnostics(peripherals=(_port(attached=False, ready=False),)))

    assert len(rows) == 1
    assert rows[0].passed is False
    assert "nothing attached" in rows[0].reason


def test_a_device_that_never_enumerated_fails_its_row_with_the_reason():
    rows = peripheral_rows(_diagnostics(peripherals=(_port(ready=False),)))

    assert rows[0].passed is False
    assert "never reached ready" in rows[0].reason


def test_two_ports_working_is_not_ten_devices_verified():
    # The whole point of the coverage block. A report that ran the ports and the
    # requirement together would imply the eight devices nobody saw.
    scenario = _scenario(device_requirements={"keyboard": 5, "mouse": 5})
    rows = peripheral_rows(_diagnostics(peripherals=(_port(), _port(kind="mouse"))))

    coverage = coverage_of(scenario, rows)

    assert coverage["devices_required"] == 10
    assert coverage["devices_exercised"] == 2
    assert "2 device(s) verified of 10" in coverage["note"]


def test_a_scenario_that_names_no_device_requirement_gets_no_coverage_claim():
    assert coverage_of(_scenario(), []) == {}


# ------------------------------------------------------- long runs, resumed


def test_a_long_scenario_measured_too_soon_records_every_check_as_unmeasured():
    scenario = _scenario(
        name="soak",
        checks={"error_counters_stable": ""},
        steps=[{"action": "run_for", "hours": 24, "steps": []}],
    )

    checks, unmeasured = evaluate(
        scenario, _diagnostics(), baseline=_diagnostics(), elapsed_seconds=600
    )

    assert checks == {}
    assert "24 hours" in unmeasured[0].reason
    assert "0.17" in unmeasured[0].reason


def test_a_long_scenario_measured_after_its_full_span_is_decided_normally():
    scenario = _scenario(
        name="soak",
        checks={"error_counters_stable": ""},
        steps=[{"action": "run_for", "hours": 24, "steps": []}],
    )

    checks, unmeasured = evaluate(
        scenario,
        _diagnostics(bad_crc=0),
        baseline=_diagnostics(bad_crc=0),
        elapsed_seconds=25 * 3600,
    )

    assert checks == {"error_counters_stable": True}
    assert unmeasured == []


def test_the_baseline_phase_writes_a_reading_and_measures_nothing(tmp_path):
    state = tmp_path / "soak.state.json"
    session = FakeSession(
        _diagnostics(keyboard_latency=_histogram((0, 0, 5, 0, 0, 0, 0, 0, 0)))
    )

    result = run(
        SCENARIOS / "soak_24h.json",
        port=None,
        phase="baseline",
        state=state,
        session=session,
    )

    assert state.exists()
    assert result.checks == {}
    assert "baseline written" in result.notes[0]


def test_a_measure_phase_reads_the_baseline_the_earlier_phase_left(tmp_path):
    state = tmp_path / "link.state.json"
    run(
        SCENARIOS / "link_fault.json",
        port=None,
        phase="baseline",
        state=state,
        session=FakeSession(_diagnostics(link_crc_errors=3)),
    )

    result = run(
        SCENARIOS / "link_fault.json",
        port=None,
        phase="measure",
        state=state,
        session=FakeSession(_diagnostics(link_crc_errors=9, endpoint_answering=True)),
    )

    assert result.checks["crc_counter_incremented"] is True


def test_a_baseline_phase_with_nowhere_to_write_refuses():
    with pytest.raises(ValueError):
        run(
            SCENARIOS / "link_fault.json",
            port=None,
            phase="baseline",
            state=None,
            session=FakeSession(_diagnostics()),
        )


# ------------------------------------------------------------- the report


def test_the_report_says_what_it_measured_and_what_it_did_not():
    session = FakeSession(
        _diagnostics(
            keyboard_latency=_histogram((0, 0, 100, 0, 0, 0, 0, 0, 0), max_us=900),
            mouse_latency=_histogram((0,) * 9),
            peripherals=(_port(), _port(kind="mouse", attached=False, ready=False)),
        )
    )

    result = measure(load_scenario(SCENARIOS / "peripherals.json"), session)
    document = json.loads(result.to_json())

    # The sentence that stops the report being read as something it is not.
    assert "firmware-internal latency" in document["measures"]
    assert "end-to-end" in document["measures"]
    assert document["verdict"] == "partial"
    assert document["unmeasured"]
    assert document["measurements"]["keyboard_latency_since_boot"]["samples"] == 100
    assert document["coverage"]["devices_required"] == 10


def test_the_report_times_the_round_trip_it_actually_made():
    session = FakeSession(_diagnostics())

    result = measure(_scenario(), session)
    document = json.loads(result.to_json())

    assert session.reads == 1
    assert len(document["samples"]) == 1
    # Named for what it is. It is the configuration link, not the input path,
    # and the two must never be reported under one heading.
    assert document["samples"][0]["kind"] == "cdc_round_trip"
    assert document["control_link_latency"][0]["kind"] == "cdc_round_trip"


def test_a_report_with_no_baseline_says_its_numbers_are_since_boot():
    session = FakeSession(
        _diagnostics(keyboard_latency=_histogram((5, 0, 0, 0, 0, 0, 0, 0, 0)))
    )

    result = measure(_scenario(), session)

    assert any("since the device booted" in note for note in result.notes)



# ------------------------------------------------ which backend read the ports


def _backend(**overrides):
    from duo_input.device.transactions import InputBackendReport

    fields = {"name": "PIO_USB"}
    fields.update(overrides)
    return InputBackendReport(**fields)


def test_the_report_names_the_backend_that_read_the_peripherals():
    """A HIL report is read months later against a firmware image. Which host
    stack produced it is the first thing that has to be recoverable, because
    the two fail in entirely different ways."""
    from hil_runner import measure

    result = measure(
        _scenario(),
        FakeSession(_diagnostics(backend=_backend(ignored_interfaces=2,
                                                  ignored_role_already_claimed=1))),
    )
    document = json.loads(result.to_json())

    assert document["input_backend"] == "PIO_USB"
    assert document["input_backend_counters"]["ignored_interfaces"] == 2
    assert document["input_backend_counters"]["ignored_role_already_claimed"] == 1


def test_a_run_against_firmware_without_the_suffix_says_unknown():
    """Older firmware names no backend, and the runner must not name one for
    it - a report claiming CH375 about a board nobody asked is worse than a
    report that says it does not know."""
    from hil_runner import measure

    result = measure(_scenario(), FakeSession(_diagnostics()))
    document = json.loads(result.to_json())

    assert document["input_backend"] == "unknown"
    assert document["input_backend_counters"] == {}


def test_a_baseline_reading_survives_the_round_trip_through_its_state_file(tmp_path):
    """The baseline phase writes the reading to JSON and the measure phase reads
    it back. A field the round trip drops is a field the comparison silently
    stops making."""
    from hil_runner import _diagnostics_from_state, _state_document

    document = _state_document(_diagnostics(backend=_backend(arm_escalations=4)), 1.0)
    restored, taken_at = _diagnostics_from_state({"diagnostics": document["diagnostics"],
                                                  "taken_at": 1.0})

    assert taken_at == 1.0
    assert restored.backend is not None
    assert restored.backend.name == "PIO_USB"
    assert restored.backend.arm_escalations == 4


# ============================================================================
# Task 13: hardware-acceptance scenario schema
#
# A scenario that opts into `"kind": "hardware_acceptance"` is claiming to be
# executable by someone standing at a bench with no author present to answer
# questions. That claim is checked at load time, not trusted: a check with no
# stated failure criterion, or a scenario with no manual observation for what
# this two-port rig cannot see for itself, must be *refused*, not accepted
# with the gap silently left for the operator to discover mid-session.
#
# Every scenario written before this task carries none of these fields and
# must keep loading exactly as it always has - see
# test_a_non_acceptance_scenario_is_not_held_to_the_stricter_shape.
# ============================================================================


def _acceptance_scenario(**overrides):
    """A minimal, fully valid ``hardware_acceptance`` scenario document."""
    document = {
        "name": "acc",
        "description": "a minimal acceptance scenario used only by tests",
        "kind": ACCEPTANCE_SCENARIO_KIND,
        "requires": ["a U1 running the PIO USB backend"],
        "on_this_rig": "everything this scenario names is measured directly",
        "steps": [{"action": "connect"}],
        "checks": {"both_devices_ready": ""},
        "failure_criteria": {
            "both_devices_ready": "one or both slots never reached ready"
        },
        "manual_observations": [
            {
                "item": "hub downstream ports",
                "expected": "every downstream port enumerates a device when one is plugged in",
                "fail_if": "a downstream port stays silent with a device on it that works elsewhere",
            }
        ],
        "record": ["input_backend", "hub_model"],
    }
    document.update(overrides)
    return document


def _write_scenario(tmp_path, document, name="scenario.json"):
    path = tmp_path / name
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_a_valid_hardware_acceptance_scenario_loads(tmp_path):
    path = _write_scenario(tmp_path, _acceptance_scenario())

    scenario = load_scenario(path)

    assert scenario["kind"] == ACCEPTANCE_SCENARIO_KIND


def test_a_non_acceptance_scenario_is_not_held_to_the_stricter_shape(tmp_path):
    # The six scenarios that predate this task carry none of the acceptance
    # fields and must go on loading exactly as they always have.
    document = {
        "name": "x",
        "description": "",
        "requires": ["a U1"],
        "on_this_rig": "...",
        "steps": [{"action": "connect"}],
        "checks": {"keyboard_p95_within_budget": ""},
    }
    path = _write_scenario(tmp_path, document)

    scenario = load_scenario(path)

    assert scenario["name"] == "x"


def test_a_hardware_acceptance_scenario_with_no_checks_is_refused(tmp_path):
    path = _write_scenario(tmp_path, _acceptance_scenario(checks={}))

    with pytest.raises(ValueError, match="no checks"):
        load_scenario(path)


def test_a_hardware_acceptance_scenario_with_no_failure_criteria_is_refused(tmp_path):
    path = _write_scenario(tmp_path, _acceptance_scenario(failure_criteria={}))

    with pytest.raises(ValueError, match="failure_criteria"):
        load_scenario(path)


def test_a_scenario_with_the_failure_criteria_key_entirely_absent_is_refused_cleanly(tmp_path):
    # Not the same case as the test above: that one supplies an empty dict,
    # this one omits the key altogether. document.get("failure_criteria")
    # then returns None, and set(None) raises TypeError rather than the
    # ValueError every other malformed scenario here produces - a crash
    # instead of a refusal. This is why the check reads `... or {}` rather
    # than trusting document.get to already be a dict.
    document = _acceptance_scenario()
    del document["failure_criteria"]
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError, match="failure_criteria"):
        load_scenario(path)


def test_a_check_with_no_stated_failure_criterion_is_refused(tmp_path):
    document = _acceptance_scenario(
        checks={"both_devices_ready": "", "backend_error_counters_stable": ""},
        failure_criteria={"both_devices_ready": "one or both slots never reached ready"},
    )
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError, match="backend_error_counters_stable"):
        load_scenario(path)


def test_a_failure_criterion_for_a_check_the_scenario_does_not_name_is_refused(tmp_path):
    document = _acceptance_scenario(
        failure_criteria={
            "both_devices_ready": "one or both slots never reached ready",
            "nonexistent_check": "this check is not in checks at all",
        }
    )
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError, match="nonexistent_check"):
        load_scenario(path)


def test_a_blank_failure_criterion_is_refused(tmp_path):
    document = _acceptance_scenario(failure_criteria={"both_devices_ready": "   "})
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError, match="no text"):
        load_scenario(path)


def test_a_hardware_acceptance_scenario_with_no_manual_observations_is_refused(tmp_path):
    path = _write_scenario(tmp_path, _acceptance_scenario(manual_observations=[]))

    with pytest.raises(ValueError, match="manual_observations"):
        load_scenario(path)


@pytest.mark.parametrize("missing_key", ("item", "expected", "fail_if"))
def test_a_manual_observation_missing_a_required_field_is_refused(tmp_path, missing_key):
    entry = {
        "item": "mouse wheel",
        "expected": "scrolls both directions on the routed computer",
        "fail_if": "no scroll events reach the routed computer",
    }
    entry.pop(missing_key)
    path = _write_scenario(tmp_path, _acceptance_scenario(manual_observations=[entry]))

    with pytest.raises(ValueError, match=missing_key):
        load_scenario(path)


def test_a_blank_manual_observation_field_is_refused(tmp_path):
    entry = {
        "item": "mouse wheel",
        "expected": "   ",
        "fail_if": "no scroll events reach the routed computer",
    }
    path = _write_scenario(tmp_path, _acceptance_scenario(manual_observations=[entry]))

    with pytest.raises(ValueError, match="expected"):
        load_scenario(path)


def test_a_hardware_acceptance_scenario_with_no_record_fields_is_refused(tmp_path):
    path = _write_scenario(tmp_path, _acceptance_scenario(record=[]))

    with pytest.raises(ValueError, match="record"):
        load_scenario(path)


def test_a_blank_record_field_is_refused(tmp_path):
    path = _write_scenario(tmp_path, _acceptance_scenario(record=["input_backend", "   "]))

    with pytest.raises(ValueError, match="record"):
        load_scenario(path)


#: Fix round 1: this used to hard-code the three current file names, so a
#: fourth ``pio_usb_*.json`` scenario added later would not be covered by
#: this test at all. Globbing means a new file is in scope the moment it is
#: committed, with no second commit needed to add it to a list here.
@pytest.mark.parametrize(
    "path", sorted((SCENARIOS).glob("pio_usb_*.json")), ids=lambda path: path.stem
)
def test_every_pio_acceptance_scenario_is_hardware_acceptance_shaped(path):
    scenario = load_scenario(path)

    assert scenario["kind"] == ACCEPTANCE_SCENARIO_KIND
    assert scenario["failure_criteria"]
    assert scenario["manual_observations"]
    assert scenario["record"]
    assert set(scenario["failure_criteria"]) == set(scenario["checks"])
    for entry in scenario["manual_observations"]:
        assert entry["item"].strip()
        assert entry["expected"].strip()
        assert entry["fail_if"].strip()


def test_the_report_carries_the_scenarios_manual_observations_as_still_open():
    # A manual observation is documentation the scenario states, never a
    # verdict the runner can hand out on a human's behalf. It must appear in
    # the report so a reader sees exactly what a rig-only run left open, and
    # it must never contribute to `checks` or `passed`.
    scenario = _acceptance_scenario()

    result = measure(
        scenario,
        FakeSession(
            _diagnostics(peripherals=(_port(kind="keyboard"), _port(kind="mouse")))
        ),
    )
    document = json.loads(result.to_json())

    assert document["manual_observations"][0]["item"] == "hub downstream ports"
    assert "human" in document["manual_observations"][0]["recorded_by"]
    assert "hub downstream ports" not in document["checks"]
    assert "hub downstream ports" not in document.get("input_backend_counters", {})


# ----------------------------------------------------- PIO backend-aware checks


def test_both_devices_ready_passes_when_both_slots_are_ready():
    checks, unmeasured = evaluate(
        _scenario(checks={"both_devices_ready": ""}),
        _diagnostics(peripherals=(_port(kind="keyboard"), _port(kind="mouse"))),
    )

    assert checks == {"both_devices_ready": True}
    assert unmeasured == []


def test_both_devices_ready_fails_when_one_slot_never_reached_ready():
    checks, _ = evaluate(
        _scenario(checks={"both_devices_ready": ""}),
        _diagnostics(
            peripherals=(_port(kind="keyboard"), _port(kind="mouse", ready=False))
        ),
    )

    assert checks == {"both_devices_ready": False}


def test_endpoint_reconnect_clean_needs_a_baseline():
    checks, unmeasured = evaluate(
        _scenario(checks={"endpoint_reconnect_clean": ""}),
        _diagnostics(peripherals=(_port(),), backend=_backend(duplicate_mounts=0)),
    )

    assert checks == {}
    assert "baseline" in unmeasured[0].reason


def test_endpoint_reconnect_clean_needs_something_attached():
    checks, unmeasured = evaluate(
        _scenario(checks={"endpoint_reconnect_clean": ""}),
        _diagnostics(
            peripherals=(_port(attached=False, ready=False),),
            backend=_backend(duplicate_mounts=2),
        ),
        baseline=_diagnostics(backend=_backend(duplicate_mounts=2)),
    )

    assert checks == {}
    assert "nothing is attached" in unmeasured[0].reason


def test_endpoint_reconnect_clean_passes_when_ready_and_no_duplicate_mount_grew():
    checks, _ = evaluate(
        _scenario(checks={"endpoint_reconnect_clean": ""}),
        _diagnostics(peripherals=(_port(),), backend=_backend(duplicate_mounts=2)),
        baseline=_diagnostics(backend=_backend(duplicate_mounts=2)),
    )

    assert checks == {"endpoint_reconnect_clean": True}


def test_endpoint_reconnect_clean_fails_when_a_duplicate_mount_was_recorded():
    checks, _ = evaluate(
        _scenario(checks={"endpoint_reconnect_clean": ""}),
        _diagnostics(peripherals=(_port(),), backend=_backend(duplicate_mounts=3)),
        baseline=_diagnostics(backend=_backend(duplicate_mounts=2)),
    )

    assert checks == {"endpoint_reconnect_clean": False}


def test_endpoint_reconnect_clean_is_unmeasured_without_a_backend_block():
    checks, unmeasured = evaluate(
        _scenario(checks={"endpoint_reconnect_clean": ""}),
        _diagnostics(peripherals=(_port(),)),
        baseline=_diagnostics(),
    )

    assert checks == {}
    assert "backend" in unmeasured[0].reason


def test_backend_error_counters_stable_passes_when_nothing_grew():
    checks, _ = evaluate(
        _scenario(checks={"backend_error_counters_stable": ""}),
        _diagnostics(backend=_backend(event_overflows=4, arm_failures=1)),
        baseline=_diagnostics(backend=_backend(event_overflows=4, arm_failures=1)),
    )

    assert checks == {"backend_error_counters_stable": True}


def test_backend_error_counters_stable_fails_when_one_grew():
    checks, _ = evaluate(
        _scenario(checks={"backend_error_counters_stable": ""}),
        _diagnostics(backend=_backend(event_overflows=5, arm_failures=1)),
        baseline=_diagnostics(backend=_backend(event_overflows=4, arm_failures=1)),
    )

    assert checks == {"backend_error_counters_stable": False}


def test_backend_error_counters_stable_ignores_the_deterministic_ignore_counters():
    # V1 accepting one keyboard and one mouse and refusing every other
    # interface deterministically - a Keychron receiver's un-owned second
    # interface, say - is correct behaviour, not a fault, and this check must
    # never fail a run because ignored_interfaces climbed.
    checks, _ = evaluate(
        _scenario(checks={"backend_error_counters_stable": ""}),
        _diagnostics(
            backend=_backend(
                ignored_interfaces=3, ignored_role_already_claimed=1, event_overflows=0
            )
        ),
        baseline=_diagnostics(
            backend=_backend(
                ignored_interfaces=0, ignored_role_already_claimed=0, event_overflows=0
            )
        ),
    )

    assert checks == {"backend_error_counters_stable": True}


def test_backend_error_counters_stable_needs_a_baseline():
    checks, unmeasured = evaluate(
        _scenario(checks={"backend_error_counters_stable": ""}),
        _diagnostics(backend=_backend(event_overflows=4)),
    )

    assert checks == {}
    assert "baseline" in unmeasured[0].reason


def test_detach_releases_observed_is_unmeasurable_on_this_rig():
    checks, unmeasured = evaluate(
        _scenario(checks={"detach_releases_observed": ""}), _diagnostics()
    )

    assert checks == {}
    assert unmeasured[0].check == "detach_releases_observed"
    assert unmeasured[0].reason
    assert unmeasured[0].needs


def test_the_new_pio_checks_are_all_accounted_for():
    # Same guarantee as test_every_check_in_every_committed_scenario_is_
    # accounted_for above, stated directly for the four names this task adds.
    for name in (
        "both_devices_ready",
        "endpoint_reconnect_clean",
        "backend_error_counters_stable",
        "detach_releases_observed",
    ):
        assert name in MEASURABLE_CHECKS or name in UNMEASURABLE_CHECKS


# ============================================================================
# Task 13 fix round 1
# ============================================================================


# --------------------------------------------------- Critical 1: both_devices_ready


def test_both_devices_ready_is_unmeasured_when_the_mouse_was_never_attached():
    # The reviewer's exact repro: MOUSE ABSENT used to read
    # {'both_devices_ready': True} because the old alias only ever asked
    # "is every *attached* port ready", and an absent mouse is not an
    # attached-but-unready one - it is simply not in the `attached` list at
    # all, so `all(...)` over zero mouse rows was vacuously true.
    checks, unmeasured = evaluate(
        _scenario(checks={"both_devices_ready": ""}),
        _diagnostics(
            peripherals=(
                _port(kind="keyboard"),
                _port(kind="mouse", attached=False, ready=False),
            )
        ),
    )

    assert checks == {}
    assert unmeasured[0].check == "both_devices_ready"
    assert "mouse" in unmeasured[0].reason


def test_both_devices_ready_is_unmeasured_when_the_keyboard_was_never_attached():
    checks, unmeasured = evaluate(
        _scenario(checks={"both_devices_ready": ""}),
        _diagnostics(
            peripherals=(
                _port(kind="keyboard", attached=False, ready=False),
                _port(kind="mouse"),
            )
        ),
    )

    assert checks == {}
    assert "keyboard" in unmeasured[0].reason


def test_both_devices_ready_is_unmeasured_when_nothing_is_attached_at_all():
    checks, unmeasured = evaluate(
        _scenario(checks={"both_devices_ready": ""}),
        _diagnostics(
            peripherals=(
                _port(kind="none", attached=False, ready=False),
                _port(kind="none", attached=False, ready=False),
            )
        ),
    )

    assert checks == {}
    assert "neither" in unmeasured[0].reason


def test_both_devices_ready_passes_only_when_both_roles_are_attached_and_ready():
    checks, _ = evaluate(
        _scenario(checks={"both_devices_ready": ""}),
        _diagnostics(peripherals=(_port(kind="keyboard"), _port(kind="mouse"))),
    )

    assert checks == {"both_devices_ready": True}


def test_both_devices_ready_fails_when_both_are_attached_but_one_is_not_ready():
    checks, _ = evaluate(
        _scenario(checks={"both_devices_ready": ""}),
        _diagnostics(
            peripherals=(_port(kind="keyboard"), _port(kind="mouse", ready=False))
        ),
    )

    assert checks == {"both_devices_ready": False}


def test_both_devices_ready_reads_role_from_the_descriptor_not_the_slot():
    # A mouse plugged into the physical "keyboard" channel still counts as
    # the mouse - the same guarantee peripheral_rows already gives readers of
    # the report, now also given to this check's own decision.
    checks, _ = evaluate(
        _scenario(checks={"both_devices_ready": ""}),
        _diagnostics(peripherals=(_port(kind="mouse"), _port(kind="keyboard"))),
    )

    assert checks == {"both_devices_ready": True}


# ------------------------------------------- Important 3: resets read as stable


def test_backend_error_counters_stable_is_unmeasured_across_a_device_reset():
    # The reviewer's exact repro: a baseline of event_overflows=9,
    # arm_failures=4, duplicate_mounts=7 against an all-zero post-reset
    # reading used to read {'backend_error_counters_stable': True} - the
    # exact brownout-reset event pio_usb_hub_recovery's RGB gate exists to
    # catch, reported as a clean pass.
    checks, unmeasured = evaluate(
        _scenario(checks={"backend_error_counters_stable": ""}),
        _diagnostics(
            backend=_backend(event_overflows=0, arm_failures=0, duplicate_mounts=0)
        ),
        baseline=_diagnostics(
            backend=_backend(event_overflows=9, arm_failures=4, duplicate_mounts=7)
        ),
    )

    assert checks == {}
    assert unmeasured[0].check == "backend_error_counters_stable"
    assert "restart" in unmeasured[0].reason


def test_endpoint_reconnect_clean_is_unmeasured_across_a_device_reset():
    checks, unmeasured = evaluate(
        _scenario(checks={"endpoint_reconnect_clean": ""}),
        _diagnostics(peripherals=(_port(),), backend=_backend(duplicate_mounts=0)),
        baseline=_diagnostics(backend=_backend(duplicate_mounts=7)),
    )

    assert checks == {}
    assert unmeasured[0].check == "endpoint_reconnect_clean"
    assert "restart" in unmeasured[0].reason


def test_backend_error_counters_stable_still_passes_on_an_honest_zero_growth_run():
    # The fix must not turn "nothing grew, nothing reset" into unmeasured too.
    checks, _ = evaluate(
        _scenario(checks={"backend_error_counters_stable": ""}),
        _diagnostics(backend=_backend(event_overflows=9, arm_failures=4)),
        baseline=_diagnostics(backend=_backend(event_overflows=9, arm_failures=4)),
    )

    assert checks == {"backend_error_counters_stable": True}


def test_endpoint_reconnect_clean_still_passes_on_an_honest_zero_growth_run():
    checks, _ = evaluate(
        _scenario(checks={"endpoint_reconnect_clean": ""}),
        _diagnostics(peripherals=(_port(),), backend=_backend(duplicate_mounts=2)),
        baseline=_diagnostics(backend=_backend(duplicate_mounts=2)),
    )

    assert checks == {"endpoint_reconnect_clean": True}


# ------------------------------------------------- Important 7: record surfaced


def test_the_report_surfaces_every_declared_record_field():
    scenario = _acceptance_scenario(
        record=["input_backend", "hub_model", "test_duration"]
    )

    result = measure(
        scenario,
        FakeSession(
            _diagnostics(peripherals=(_port(kind="keyboard"), _port(kind="mouse")))
        ),
    )
    document = json.loads(result.to_json())

    # Present and filled in where the rig can know the answer...
    assert document["record"]["input_backend"] == "unknown"
    # ...present and explicitly unmeasured, never simply absent, everywhere
    # else - the same omission-reads-as-forgotten failure mode `checks` is
    # already protected against.
    assert document["record"]["hub_model"] == UNMEASURED_RECORD_VALUE
    assert "unmeasured" in document["record"]["test_duration"]
    assert set(document["record"]) == {"input_backend", "hub_model", "test_duration"}


def test_record_test_duration_is_filled_in_when_a_baseline_was_taken():
    scenario = _acceptance_scenario(record=["test_duration"])

    result = measure(
        scenario,
        FakeSession(_diagnostics(peripherals=(_port(),))),
        elapsed_seconds=125.4,
    )
    document = json.loads(result.to_json())

    assert "125.4" in document["record"]["test_duration"]
    assert "unmeasured" not in document["record"]["test_duration"]


def test_record_identity_fields_are_filled_in_when_that_role_enumerated():
    scenario = _acceptance_scenario(
        record=["keyboard_vendor_id_product_id_descriptor_hash"]
    )

    result = measure(
        scenario,
        FakeSession(
            _diagnostics(
                peripherals=(
                    _port(
                        kind="keyboard",
                        vendor_id=0x1234,
                        product_id=0x5678,
                        descriptor_hash="a1b2c3d4",
                    ),
                )
            )
        ),
    )
    document = json.loads(result.to_json())

    value = document["record"]["keyboard_vendor_id_product_id_descriptor_hash"]
    assert value == "0x1234 0x5678 a1b2c3d4"


def test_record_identity_field_is_unmeasured_without_a_descriptor_hash():
    # Removing the descriptor hash must never leave an apparent exact identity
    # made from only VID/PID; the report must state that this rig did not measure it.
    scenario = _acceptance_scenario(
        record=["keyboard_vendor_id_product_id_descriptor_hash"]
    )

    result = measure(
        scenario,
        FakeSession(
            _diagnostics(
                peripherals=(
                    _port(kind="keyboard", vendor_id=0x1234, product_id=0x5678),
                )
            )
        ),
    )
    document = json.loads(result.to_json())

    assert (
        document["record"]["keyboard_vendor_id_product_id_descriptor_hash"]
        == UNMEASURED_RECORD_VALUE
    )


# ---------------------------------------- Important 8: kind is not the only gate


def test_a_scenario_naming_a_hardware_acceptance_check_without_kind_is_refused(
    tmp_path,
):
    document = {
        "name": "x",
        "description": "",
        "requires": ["a U1"],
        "on_this_rig": "...",
        "steps": [{"action": "connect"}],
        "checks": {"both_devices_ready": ""},
        # kind is entirely absent, unlike every other rejection test above.
    }
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError, match="both_devices_ready"):
        load_scenario(path)


def test_a_scenario_naming_a_hardware_acceptance_check_with_the_wrong_kind_string_is_refused(
    tmp_path,
):
    document = _acceptance_scenario(kind="hardware-acceptance")  # hyphen, not underscore
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError, match="hardware-acceptance"):
        load_scenario(path)


def test_a_scenario_with_only_ordinary_checks_and_no_kind_is_unaffected():
    # The six pre-Task-13 scenarios name none of the four hardware-acceptance
    # check names, so this new gate must leave them exactly alone.
    for name in ("peripherals", "route_toggle", "link_fault", "config_power_cut",
                 "profile_power_cycle", "soak_24h"):
        load_scenario(SCENARIOS / f"{name}.json")  # must not raise


# --------------------------------------------- Smaller fixes: type confusion

def test_a_null_failure_criterion_is_refused(tmp_path):
    document = _acceptance_scenario(failure_criteria={"both_devices_ready": None})
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError, match="no text"):
        load_scenario(path)


def test_a_list_valued_manual_observation_field_is_refused(tmp_path):
    entry = {
        "item": "mouse wheel",
        "expected": None,
        "fail_if": ["not", "text"],
    }
    document = _acceptance_scenario(manual_observations=[entry])
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError, match="expected|fail_if"):
        load_scenario(path)


def test_a_bare_string_record_is_refused_rather_than_iterated_as_characters(tmp_path):
    document = _acceptance_scenario(record="input_backend")
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError, match="record"):
        load_scenario(path)


def test_a_zero_valued_record_field_is_refused(tmp_path):
    document = _acceptance_scenario(record=["input_backend", 0])
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError, match="record"):
        load_scenario(path)


def test_manual_observations_as_a_dict_is_refused_cleanly_not_with_an_attributeerror(
    tmp_path,
):
    # A dict is truthy and iterable, so this exercises the list-type guard
    # directly - reviewer's exact repro (enumerate() over a dict yields its
    # *keys*, each a plain string, and calling .get() on a string is where
    # the AttributeError this fix removes used to come from).
    document = _acceptance_scenario(
        manual_observations={"item": "x", "expected": "y", "fail_if": "z"}
    )
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError, match="manual_observations"):
        load_scenario(path)


def test_manual_observations_as_a_non_iterable_value_is_refused_cleanly(tmp_path):
    # Not the same case as the dict test above: a dict is iterable, so with
    # the list-type guard removed, enumerate() over it still reaches the
    # entry-type guard a few lines down (each key is a string, not a dict)
    # and gets caught there instead - the two tests turned out not to
    # discriminate the same guard. A plain int is not iterable at all:
    # enumerate(42) raises TypeError before the loop body ever runs, so
    # nothing downstream can catch it - only the list-type guard itself can.
    document = _acceptance_scenario(manual_observations=42)
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError, match="manual_observations"):
        load_scenario(path)


def test_a_manual_observation_entry_that_is_a_string_is_refused_cleanly(tmp_path):
    document = _acceptance_scenario(manual_observations=["just some text"])
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError, match=r"manual_observations\[0\]"):
        load_scenario(path)


# ------------------------------------------ Smaller fix: nested steps unchecked


def test_a_step_nested_inside_a_for_each_block_without_an_action_is_refused(tmp_path):
    document = {
        "name": "x",
        "description": "",
        "requires": ["a U1"],
        "on_this_rig": "...",
        "steps": [
            {
                "action": "for_each_downstream_port",
                "steps": [
                    {"note": "forgot the action key"},
                ],
            }
        ],
    }
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError):
        load_scenario(path)


def test_a_step_nested_two_levels_deep_without_an_action_is_refused(tmp_path):
    document = {
        "name": "x",
        "description": "",
        "requires": ["a U1"],
        "on_this_rig": "...",
        "steps": [
            {
                "action": "outer",
                "steps": [
                    {
                        "action": "inner",
                        "steps": [{"note": "still no action, two levels down"}],
                    }
                ],
            }
        ],
    }
    path = _write_scenario(tmp_path, document)

    with pytest.raises(ValueError):
        load_scenario(path)


def test_every_new_scenarios_nested_steps_all_name_an_action():
    # Positive companion to the two tests above: the three files this task
    # actually ships must themselves pass the recursive check, not just fail
    # it on purpose-built bad input.
    for path in sorted(SCENARIOS.glob("pio_usb_*.json")):
        load_scenario(path)  # must not raise


# --------------------------------------------- Smaller fix: --validate-only exit


def test_validate_only_on_an_invalid_scenario_exits_cleanly_not_with_a_traceback(
    tmp_path, capsys
):
    path = tmp_path / "broken.json"
    path.write_text(json.dumps({"name": "x", "steps": []}), encoding="utf-8")

    code = main([str(path), "--validate-only"])

    assert code == EXIT_INVALID_SCENARIO
    captured = capsys.readouterr()
    assert "invalid scenario" in captured.err
    # No Python traceback reached stderr - if load_scenario's ValueError had
    # propagated uncaught, pytest would have reported this test as an error
    # rather than a pass, so the assertion above is the real proof; this one
    # additionally pins the exact wording an operator running this by hand
    # would see.


def test_validate_only_on_a_valid_scenario_still_exits_zero():
    code = main([str(SCENARIOS / "pio_usb_hub_enumeration.json"), "--validate-only"])

    assert code == EXIT_PASSED
