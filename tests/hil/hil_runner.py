"""Hardware-in-the-loop acceptance: measure what the device actually does.

Nothing in this file estimates. Every number it reports comes from a clock at
both ends of the interval it describes, and a check whose interval this rig
cannot see is recorded as unmeasured, with the reason, rather than filled in
with something plausible. That rule is the entire point of the file: a latency
figure produced by a simulator is not evidence about a device, and a check
quietly skipped is worse than one that failed, because a skipped check leaves a
report that looks complete.

**What this measures is the interval inside U1**: from a peripheral report
reaching its input core to the command that report produced being applied on
its output core. Both ends are on the same board and on the same clock, so the
difference is a measurement. It is *not* the journey from a finger to a far
screen. This rig has no way to timestamp a human keypress and nothing that
injects HID into U1's peripheral ports, so the end-to-end figure the
specification names cannot be produced here at all, and no report from this
file may be read as if it had been.

Usage, in two phases:

    python tests/hil/hil_runner.py tests/hil/scenarios/route_toggle.json \\
        --port COM7 --phase baseline --state artifacts/route_toggle.state.json

    ... exercise the device as the scenario describes ...

    python tests/hil/hil_runner.py tests/hil/scenarios/route_toggle.json \\
        --port COM7 --phase measure --state artifacts/route_toggle.state.json \\
        --output artifacts/route_toggle.json

The baseline is what turns a counter that only ever climbs into a statement
about one run, and what lets a scenario measured over hours be started now and
finished later.

Exit codes: 0 every check measured and passed, 1 something measured failed,
2 no hardware, 3 everything measured passed but some checks could not be
measured on this rig, 4 the scenario file itself is invalid (including under
--validate-only, which prints the reason to stderr rather than a traceback).
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --- the numbers the specification asks for ---------------------------------

#: Keyboard and mouse events must reach the target computer within this at p95.
INPUT_P95_BUDGET_MS = 20.0

#: A gap longer than this is a stall the operator would feel.
MAX_ACCEPTABLE_GAP_MS = 50.0

#: U2 must release every held key within this of losing the link.
U2_RELEASE_BUDGET_MS = 100.0

#: What this runner can see, said once so every report can point at it.
MEASUREMENT_SCOPE = (
    "firmware-internal latency: from a peripheral report reaching U1's input "
    "core to the command it produced being applied on U1's output core. Not "
    "end-to-end keystroke latency, which this rig cannot measure - there is no "
    "clock on a human finger and nothing injects HID into U1's peripheral ports."
)

#: Why the rig cannot see a computer at the far end of the link.
NEEDS_SECOND_COMPUTER = (
    "a logger on PC1 and PC2 recording what each actually received, which this "
    "rig does not have"
)


class HardwareRequired(RuntimeError):
    """The scenario needs hardware that is not attached.

    Raised rather than returning a result. A HIL report that was produced
    without hardware is worse than no report: it looks like evidence.
    """


@dataclass(frozen=True)
class Sample:
    """One request sent and the moment its reply came back.

    Both timestamps are taken on the host, around a real exchange with a real
    device, which is what makes this a measurement rather than a guess. It is
    the *configuration link's* round trip and nothing else: it says the board
    is answering and how quickly. It is not the input path, and reporting it as
    keystroke latency would be reporting the wrong journey entirely.
    """

    kind: str
    injected_ns: int
    observed_ns: int

    @property
    def latency_ms(self) -> float:
        return (self.observed_ns - self.injected_ns) / 1_000_000


@dataclass(frozen=True)
class LatencyReport:
    """What a set of samples says, and whether it met its budget."""

    kind: str
    count: int
    p50_ms: float
    p95_ms: float
    max_ms: float
    budget_ms: float

    @property
    def passed(self) -> bool:
        return self.count > 0 and self.p95_ms <= self.budget_ms


def percentile(values: list[float], fraction: float) -> float:
    """The value at ``fraction`` through ``values``, nearest rank.

    Nearest rank rather than interpolation: with a few hundred samples an
    interpolated p95 can land between two real measurements and report a
    latency the device never actually produced. The device's own histogram
    uses the same convention, so the two agree about what p95 means.
    """
    if not values:
        raise ValueError("percentile of no samples")
    if not 0.0 < fraction <= 1.0:
        raise ValueError("fraction must be in (0, 1]")
    ordered = sorted(values)
    rank = max(1, min(len(ordered), int(-(-len(ordered) * fraction // 1))))
    return ordered[rank - 1]


def latency_report(
    kind: str, samples: list[Sample], budget_ms: float = INPUT_P95_BUDGET_MS
) -> LatencyReport:
    latencies = [sample.latency_ms for sample in samples if sample.kind == kind]
    if not latencies:
        return LatencyReport(kind, 0, 0.0, 0.0, 0.0, budget_ms)
    return LatencyReport(
        kind=kind,
        count=len(latencies),
        p50_ms=round(statistics.median(latencies), 3),
        p95_ms=round(percentile(latencies, 0.95), 3),
        max_ms=round(max(latencies), 3),
        budget_ms=budget_ms,
    )


def gaps(observed_ns: list[int], threshold_ms: float = MAX_ACCEPTABLE_GAP_MS) -> list[float]:
    """Pauses between consecutive observations longer than ``threshold_ms``."""
    ordered = sorted(observed_ns)
    found: list[float] = []
    for earlier, later in zip(ordered, ordered[1:]):
        gap = (later - earlier) / 1_000_000
        if gap > threshold_ms:
            found.append(round(gap, 3))
    return found


@dataclass(frozen=True)
class PeripheralRow:
    """One device that was tried, and exactly why it passed or failed.

    ``reason`` is mandatory even on a pass. "It worked" is not a result anyone
    can act on six months later; "enumerated as boot keyboard, 6KRO" is.

    ``role`` is what the **device** said it is - the kind enumeration read out
    of its own report descriptor - and never where it was plugged in. ``port``
    is the logical role slot it arrived in: V1 accepts exactly one keyboard and
    one mouse, and U1 has always reported these two records in that order. The
    two fields are independent: a mouse in the keyboard slot is an ordinary
    bench, and a matrix that took the slot for the role would print `keyboard`
    beside a reason reading "enumerated as mouse". Keep the slot, because a
    crossed or dead cable is diagnosed from it; never let it name the device.

    The slot names are backend-neutral on purpose. They used to be "keyboard
    channel" and "mouse channel", after the pins each CH375 used - a name the
    PIO USB host makes wrong, since it has one bus and no channels at all.
    """

    port: str
    role: str
    vendor_id: str
    product_id: str
    descriptor_hash: str
    buttons: int
    passed: bool
    reason: str


@dataclass(frozen=True)
class Unmeasured:
    """A check this rig could not decide, and precisely what it would take.

    Recorded rather than skipped. A skipped check leaves a report that looks
    complete, and the whole value of an acceptance report is that its silences
    are visible.
    """

    check: str
    reason: str
    needs: str


@dataclass
class ScenarioResult:
    """Everything one scenario run produced."""

    scenario: str
    started_at: str
    finished_at: str = ""
    samples: list[Sample] = field(default_factory=list)
    peripherals: list[PeripheralRow] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    unmeasured: list[Unmeasured] = field(default_factory=list)
    measurements: dict[str, object] = field(default_factory=dict)
    coverage: dict[str, object] = field(default_factory=dict)
    #: Which host stack read U1's own USB ports during this run: "CH375",
    #: "PIO_USB", or "unknown" for firmware that named none. A report read
    #: months later against a firmware image has to be able to say which one
    #: produced it, and nothing else in the document can.
    input_backend: str = "unknown"
    #: That backend's own counters, by name, and only the ones it sent.
    input_backend_counters: dict[str, int] = field(default_factory=dict)
    #: The scenario's own ``manual_observations``, copied in verbatim and
    #: never decided by this file. They exist so a reader of the report sees
    #: exactly what a rig-only run left open - each one is documentation, not
    #: a verdict, and none of them is ever allowed to reach ``checks``: that
    #: is what keeps "the rig measured this" and "a human would have to
    #: assert this" from blurring into each other in the one place a reader
    #: might mistake one for the other.
    manual_observations: list[dict] = field(default_factory=list)
    #: Every field the scenario's own ``record`` list names, populated from
    #: whatever this rig actually knows and marked ``"unmeasured - needs a
    #: human at the bench"`` for the rest - never simply absent. Fix round 1
    #: found `record` validated as a non-empty list and then never surfaced:
    #: a scenario could name "hub_model" and a report would carry no trace of
    #: whether anyone ever wrote it down, which is the exact omission-reads-
    #: as-forgotten failure mode the brief names for `checks`.
    record: dict[str, object] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    @property
    def complete(self) -> bool:
        """Was every check the scenario names actually decided?"""
        return not self.unmeasured

    @property
    def verdict(self) -> str:
        """Three outcomes, never two.

        Collapsing "partial" into "passed" is how a report comes to imply that
        ten devices were tried when two were, and collapsing it into "failed"
        would make an honest partial run indistinguishable from a defect.
        """
        if self.checks and not self.passed:
            return "failed"
        if not self.checks:
            return "nothing measured"
        return "passed" if self.complete else "partial"

    def to_json(self) -> str:
        kinds = sorted({sample.kind for sample in self.samples})
        latencies = [latency_report(kind, self.samples) for kind in kinds]
        document = {
            "scenario": self.scenario,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "measures": MEASUREMENT_SCOPE,
            "verdict": self.verdict,
            "passed": self.passed,
            "complete": self.complete,
            "checks": self.checks,
            "unmeasured": [asdict(entry) for entry in self.unmeasured],
            "measurements": self.measurements,
            "coverage": self.coverage,
            "control_link_latency": [
                asdict(report) | {"passed": report.passed} for report in latencies
            ],
            "control_link_gaps_over_50ms": gaps(
                [sample.observed_ns for sample in self.samples]
            ),
            "input_backend": self.input_backend,
            "input_backend_counters": self.input_backend_counters,
            "manual_observations": self.manual_observations,
            "record": self.record,
            "peripherals": [asdict(row) for row in self.peripherals],
            "samples": [asdict(sample) | {"latency_ms": round(sample.latency_ms, 3)}
                        for sample in self.samples],
            "notes": self.notes,
        }
        return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)


# --- scenarios ---------------------------------------------------------------


#: ``on_this_rig`` is mandatory. A scenario file listing ten steps and five
#: checks reads as runnable; on a bench with two peripheral ports and no logger
#: at either computer, most of them decide nothing. The report says so in its
#: ``unmeasured`` list, but by then an afternoon has been spent, so the scenario
#: file says it too, in its own terms, before anybody starts.
REQUIRED_SCENARIO_KEYS = ("name", "description", "requires", "on_this_rig", "steps")

#: A scenario document declaring ``"kind": "hardware_acceptance"`` is claiming
#: to be executable by someone standing at a bench with no author present to
#: ask. ``_validate_acceptance_scenario`` checks that claim; every scenario
#: written before Task 13 carries no ``kind`` at all and is unaffected.
ACCEPTANCE_SCENARIO_KIND = "hardware_acceptance"

#: Checks introduced for PIO USB hardware acceptance specifically. A scenario
#: naming any of these is claiming the hardware_acceptance shape whether or
#: not it spelled ``kind`` right - fix round 1 found that a scenario could
#: name these checks with ``kind`` omitted, mis-cased or misspelled and get
#: none of ``_validate_acceptance_scenario``'s guarantees while still reading,
#: on a skim, like a scenario that had them. Dispatching on the check names as
#: well as on ``kind`` closes that: a typo or omission next to one of these is
#: now itself a load-time refusal, not a silent downgrade to the loose shape.
ACCEPTANCE_FAMILY_CHECK_NAMES = frozenset(
    {
        "both_devices_ready",
        "endpoint_reconnect_clean",
        "backend_error_counters_stable",
        "detach_releases_observed",
    }
)

#: What one entry of ``manual_observations`` must state. A bare description
#: ("check the side button") is not enough to gate on: this rig cannot decide
#: it, so what a human would call a pass and what a human would call a
#: failure both have to be written down before the bench session starts, not
#: improvised there.
REQUIRED_MANUAL_OBSERVATION_KEYS = ("item", "expected", "fail_if")


def _non_blank_str(value: object) -> bool:
    """True only for an actual, non-blank string.

    Fix round 1 found that ``str(value).strip()`` accepts anything -
    ``None`` reads as the four characters ``"None"``, ``[]`` reads as
    ``"[]"``, ``0`` reads as ``"0"`` - so a scenario stating
    ``"expected": null`` or ``"fail_if": []`` validated clean. A criterion or
    an observation field has to actually be text, not merely stringify to
    something non-empty.
    """
    return isinstance(value, str) and bool(value.strip())


def _validate_acceptance_scenario(document: dict, path: str | Path) -> None:
    """The stricter shape a ``hardware_acceptance`` scenario must have.

    ``load_scenario`` already requires ``requires`` (physical setup) and
    ``steps`` (action) of every scenario. This adds the three pieces that are
    specific to a scenario meant to gate real hardware: ``checks`` must be
    non-empty (measurable checks), every one of them must carry a stated
    ``failure_criteria`` entry naming what a failure looks like, every
    ``manual_observations`` entry must state what this rig cannot infer for
    itself, and ``record`` must name what a completed report has to state
    alongside its checks (backend, toolchain revisions, hub model, exact
    identity, route observations, power/RGB symptoms, test duration - see the
    Interfaces line in the Task 13 brief).

    Each failure names exactly what is missing, because the alternative - one
    generic "scenario is invalid" - would send the next person back through
    this whole function to find out which of five things they forgot.
    """
    checks = document.get("checks")
    if not checks:
        raise ValueError(f"{path}: hardware_acceptance scenario names no checks")

    # No separate "failure_criteria is present at all" guard here on purpose:
    # a mutation sweep found one and it was dead code. With `checks` already
    # guaranteed non-empty above, an absent or empty `failure_criteria` always
    # makes every name in `checks` show up in `missing_criteria` below - the
    # two guards can never disagree, so the first one was tested by nothing
    # its own removal could expose. See the Task 13 report for the sweep.
    failure_criteria = document.get("failure_criteria") or {}
    if not isinstance(failure_criteria, dict):
        raise ValueError(f"{path}: failure_criteria must be an object, not {failure_criteria!r}")
    missing_criteria = sorted(set(checks) - set(failure_criteria))
    if missing_criteria:
        raise ValueError(
            f"{path}: checks with no stated failure_criteria: "
            f"{', '.join(missing_criteria)}"
        )
    extra_criteria = sorted(set(failure_criteria) - set(checks))
    if extra_criteria:
        raise ValueError(
            f"{path}: failure_criteria for checks the scenario does not name: "
            f"{', '.join(extra_criteria)}"
        )
    blank_criteria = sorted(
        name for name, text in failure_criteria.items() if not _non_blank_str(text)
    )
    if blank_criteria:
        raise ValueError(
            f"{path}: failure_criteria with no text: {', '.join(blank_criteria)}"
        )

    observations = document.get("manual_observations")
    if not observations:
        raise ValueError(
            f"{path}: hardware_acceptance scenario states no manual_observations"
        )
    if not isinstance(observations, list):
        raise ValueError(
            f"{path}: manual_observations must be a list of entries, not "
            f"{type(observations).__name__}"
        )
    for index, entry in enumerate(observations):
        if not isinstance(entry, dict):
            raise ValueError(
                f"{path}: manual_observations[{index}] must be an object with "
                f"{', '.join(REQUIRED_MANUAL_OBSERVATION_KEYS)}, not "
                f"{type(entry).__name__}"
            )
        missing_fields = [
            key
            for key in REQUIRED_MANUAL_OBSERVATION_KEYS
            if not _non_blank_str(entry.get(key))
        ]
        if missing_fields:
            raise ValueError(
                f"{path}: manual_observations[{index}] is missing "
                f"{', '.join(missing_fields)}"
            )

    record = document.get("record")
    if not record:
        raise ValueError(
            f"{path}: hardware_acceptance scenario states no record fields"
        )
    if not isinstance(record, list):
        raise ValueError(
            f"{path}: record must be a list of field names, not {type(record).__name__}"
        )
    if any(not _non_blank_str(name) for name in record):
        raise ValueError(f"{path}: record contains a blank field name")


def _validate_steps(steps: list, path: str | Path, where: str = "top level") -> None:
    """Every step names an action - including one nested inside another
    step's own ``steps`` list (``for_each_keyboard_route``,
    ``for_each_downstream_port``, ``repeat`` and the rest all carry one).

    Fix round 1 found that the original guard walked only the top level, so
    an action-less step one level down loaded clean. This recurses into every
    ``steps`` list it finds, however deep, rather than trusting depth to stay
    at one.
    """
    for index, step in enumerate(steps):
        if "action" not in step:
            raise ValueError(f"{path}: step {index} ({where}) has no action")
        nested = step.get("steps")
        if isinstance(nested, list):
            _validate_steps(nested, path, where=f"nested in step {index} ({where})")


def load_scenario(path: str | Path) -> dict:
    """Read one scenario and refuse it if it does not say what it needs."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [key for key in REQUIRED_SCENARIO_KEYS if key not in document]
    if missing:
        raise ValueError(f"{path}: scenario is missing {', '.join(missing)}")
    if not document["steps"]:
        raise ValueError(f"{path}: scenario has no steps")
    _validate_steps(document["steps"], path)
    acceptance_checks = sorted(
        set(document.get("checks", {}) or {}) & ACCEPTANCE_FAMILY_CHECK_NAMES
    )
    if acceptance_checks and document.get("kind") != ACCEPTANCE_SCENARIO_KIND:
        raise ValueError(
            f"{path}: names hardware-acceptance check(s) {', '.join(acceptance_checks)} "
            f"but kind is {document.get('kind')!r}, not {ACCEPTANCE_SCENARIO_KIND!r}"
        )
    if document.get("kind") == ACCEPTANCE_SCENARIO_KIND:
        _validate_acceptance_scenario(document, path)
    return document


def scenario_hours(scenario: dict) -> float:
    """How long the scenario says its run lasts, from the scenario itself."""
    for step in scenario.get("steps", ()):
        if step.get("action") == "run_for":
            return float(step.get("hours", 0.0))
    return 0.0


# --- the device ---------------------------------------------------------------


def open_device(port: str | None):
    """Open the U1 named by ``port``, or refuse.

    There is no fallback to the emulator here, deliberately. The emulator is
    the right tool for the UI tests and the wrong tool for this file: it has no
    input pipeline, no peripheral ports and no clock on either, so every number
    it could produce would be a number about a Python object.
    """
    if port is None:
        raise HardwareRequired(
            "no --port given; HIL scenarios run against a real U1, never the emulator"
        )
    try:
        from cdc_session import CdcSession
    except ImportError:  # pragma: no cover - running as a module
        from .cdc_session import CdcSession  # type: ignore[import-not-found]

    session = CdcSession(port)
    session.open()
    return session


# --- turning what the device reports into the checks the scenario names -------


def _histogram_json(histogram) -> dict:
    """One of the device's histograms, as a report can print it."""
    p95 = histogram.p95_upper_bound_us() if histogram.count else None
    return {
        "samples": histogram.count,
        "max_ms": round(histogram.max_us / 1000, 3),
        "p95_at_or_below_ms": None if p95 is None else round(p95 / 1000, 3),
        "bucket_edges_ms": [round(edge / 1000, 3) for edge in histogram.edges_us],
        "buckets": list(histogram.buckets),
    }


def _difference(histogram, earlier):
    """The samples taken between two readings of the same counter.

    Bucket counts only ever climb, so subtracting them is exact and gives the
    run rather than the lifetime. The maximum is not subtractable - a maximum
    is not a sum - so it is left as the lifetime value and labelled as one.
    """
    if earlier is None:
        return histogram
    from duo_input.device.transactions import LatencyHistogram

    return LatencyHistogram(
        edges_us=histogram.edges_us,
        buckets=tuple(
            later - before for later, before in zip(histogram.buckets, earlier.buckets)
        ),
        count=histogram.count - earlier.count,
        max_us=histogram.max_us,
    )


@dataclass
class _Context:
    """Everything the checks are decided from."""

    scenario: dict
    now: object
    baseline: object | None
    elapsed_seconds: float | None


def _latency_check(context: _Context, stream: str):
    """Did this stream's p95 meet the budget, as the device counted it?"""
    histogram = getattr(context.now, f"{stream}_latency", None)
    if histogram is None:
        return (
            None,
            "this firmware does not report input latency",
            "a U1 running a build that carries the latency block in GET_DIAGNOSTICS",
        )
    measured = _difference(histogram, getattr(context.baseline, f"{stream}_latency", None)
                           if context.baseline is not None else None)
    if measured.count == 0:
        return (
            None,
            "the device measured no "
            + stream
            + " events, so there is no percentile to report",
            "a "
            + stream
            + " attached to U1 and exercised between the baseline and this reading",
        )
    return (measured.within(int(INPUT_P95_BUDGET_MS * 1000)), "", "")


def _check_keyboard_p95(context: _Context):
    return _latency_check(context, "keyboard")


def _check_mouse_p95(context: _Context):
    return _latency_check(context, "mouse")


def _check_no_gap(context: _Context):
    """No input took longer than the stall threshold.

    Expressed as a maximum of the same measurement rather than as a gap between
    two events: the gap between one keystroke and the next is mostly the
    operator thinking, and calling that a stall would fail every honest run.
    """
    bound_us = int(MAX_ACCEPTABLE_GAP_MS * 1000)
    decided = False
    for stream in ("keyboard", "mouse"):
        histogram = getattr(context.now, f"{stream}_latency", None)
        if histogram is None:
            return (
                None,
                "this firmware does not report input latency",
                "a U1 running a build that carries the latency block in GET_DIAGNOSTICS",
            )
        measured = _difference(
            histogram,
            getattr(context.baseline, f"{stream}_latency", None)
            if context.baseline is not None
            else None,
        )
        if measured.count == 0:
            continue
        decided = True
        if measured.at_or_below(bound_us) != measured.count:
            return (False, "", "")
    if not decided:
        return (
            None,
            "the device measured no input at all, so nothing can be said about stalls",
            "a keyboard or a mouse attached to U1 and exercised during the run",
        )
    return (True, "", "")


def _drops_this_run(context: _Context):
    """How many times U2 let go between the baseline and now, or why unknown.

    Both of U2's fields are lifetime values - ``drops`` is a saturating count
    since U2 booted, ``last_release_ms`` is whichever release happened last,
    whenever that was. Neither says anything about this run on its own, and
    this bench proved it: a scenario that never went near SPI1 read
    ``endpoint_drops: 7, endpoint_release_ms: 100`` and would have reported a
    release within budget for a fault nobody caused.
    """
    drops = getattr(context.now, "endpoint_drops", None)
    if drops is None:
        return (
            None,
            "this firmware does not report what the endpoint saw when the link died",
            "a U1 whose GET_DIAGNOSTICS carries the endpoint report",
        )
    if context.baseline is None:
        return (
            None,
            "no baseline reading, so a lifetime drop count says nothing about "
            "whether the link was interrupted during this run",
            "a --phase baseline run before the link was cut",
        )
    before = getattr(context.baseline, "endpoint_drops", None)
    if before is None:
        return (
            None,
            "the baseline reading carries no endpoint drop count to subtract",
            "a --phase baseline run against a U1 that reports the endpoint block",
        )
    return (drops - before, "", "")


def _check_u2_release(context: _Context):
    """U2 let go within the budget of the link going quiet, during this run.

    U2 records the silence that caused its own release and reports it when the
    link comes back, which is the only way this is observable at all: the link
    that would carry the news live is the one that went away. It records the
    *last* one, so a run that cut the link more than once has one time for
    several cuts and cannot answer "every held key, within 100 ms of the cut".
    """
    release_ms = getattr(context.now, "endpoint_release_ms", None)
    if release_ms is None:
        return (
            None,
            "this firmware does not report what the endpoint saw when the link died",
            "a U1 whose GET_DIAGNOSTICS carries the endpoint report",
        )
    grew, reason, needs = _drops_this_run(context)
    if grew is None:
        return (None, reason, needs)
    if grew <= 0:
        return (
            None,
            "U2 did not release anything between the two readings, so this run "
            "caused no release to time; the count and the time on the device are "
            "from before it",
            "the SPI1 link physically interrupted while a key is held, between "
            "the baseline and the measure phase",
        )
    if grew > 1:
        return (
            None,
            f"U2 released {grew} times during this run and records only the last "
            "one, so every held key being released within 100 ms of its own cut "
            "cannot be decided from a single time",
            "one interruption per measure phase, or a U2 that records every "
            "release rather than the last",
        )
    return (release_ms <= U2_RELEASE_BUDGET_MS, "", "")


def _counter_grew(context: _Context, attribute: str):
    now = getattr(context.now, attribute, None)
    before = getattr(context.baseline, attribute, None) if context.baseline else None
    if now is None:
        return None
    if before is None:
        return now
    return now - before


def _check_crc_counter(context: _Context):
    """A corrupted frame was counted - decidable only when one was counted.

    Nothing here can see whether the operator grounded MOSI. A run in which no
    frame was corrupted and a firmware that counts no corruption produce the
    same reading, so zero growth is undecided rather than failed: calling it a
    failure would fail every honest run that skipped the step, and the
    scenario's other checks would then sit under a "failed" verdict. The cost
    is stated rather than hidden - a firmware that stopped counting is recorded
    as unmeasured here, and catching that needs a rig that corrupts on command.
    """
    if context.baseline is None:
        return (
            None,
            "no baseline reading, so a counter that only climbs says nothing about this run",
            "a --phase baseline run before the frames were corrupted",
        )
    grew = _counter_grew(context, "link_crc_errors")
    if grew is None:
        return (None, "this firmware does not report the link CRC counter", "a newer U1 build")
    if grew <= 0:
        return (
            None,
            "no CRC error was counted between the two readings, and this rig "
            "cannot tell a frame that was never corrupted from one that was "
            "corrupted and not counted",
            "a rig that corrupts a frame on command, so that the absence of a "
            "count is attributable to the firmware rather than to the bench",
        )
    return (True, "", "")


def _check_link_recovered(context: _Context):
    """The link answers again after something interrupted it in this run.

    A link that answers now is only evidence of a recovery if it went away
    first. Without a drop during the run this reads "the link is up", which was
    also true before the run started and proves nothing about recovering.
    """
    answering = getattr(context.now, "endpoint_answering", None)
    if answering is None:
        return (
            None,
            "this firmware does not say whether the endpoint is answering",
            "a U1 whose GET_DIAGNOSTICS carries the link state",
        )
    grew, reason, needs = _drops_this_run(context)
    if grew is None:
        return (None, reason, needs)
    if grew <= 0:
        return (
            None,
            "nothing interrupted the link during this run, so the link answering "
            "now is the state it was already in and not a recovery",
            "the SPI1 link physically interrupted and restored between the "
            "baseline and the measure phase",
        )
    return (bool(answering), "", "")


def _check_error_counters_stable(context: _Context):
    if context.baseline is None:
        return (
            None,
            "no baseline reading, so a counter that only climbs says nothing about this run",
            "a --phase baseline run before the soak began",
        )
    grew = [
        name
        for name in ("bad_crc", "timeout", "link_crc_errors")
        if (_counter_grew(context, name) or 0) > 0
    ]
    return (not grew, "", "")


def _check_every_device_enumerated(context: _Context):
    """Every peripheral U1 has a port for reached the ready state.

    This decides the ports that exist. How many devices the scenario asks for
    is a separate question, answered by the coverage block, and the two must
    not be confused: two ports both ready is not ten devices verified.
    """
    ports = getattr(context.now, "peripherals", None)
    if ports is None:
        return (
            None,
            "this firmware does not report what is on its peripheral ports",
            "a U1 whose GET_DIAGNOSTICS carries the peripheral block",
        )
    attached = [port for port in ports if port.attached]
    if not attached:
        return (
            None,
            "nothing is attached to either peripheral port",
            "a keyboard and a mouse plugged into U1",
        )
    return (all(port.ready for port in attached), "", "")


def _check_both_devices_ready(context: _Context):
    """A keyboard-role port and a mouse-role port are each attached and ready.

    Deliberately **not** ``_check_every_device_enumerated``'s decision, which
    is "every *attached* port is ready" - correct for that check's own name,
    and exactly wrong for this one. Fix round 1 measured it: with the mouse
    simply unplugged, the alias read ``{'both_devices_ready': True}`` on a
    bench with no mouse, and that was the reading the enumeration scenario -
    Task 14's very first hardware step - would have been accepted on. Role is
    read from the device's own descriptor (``PeripheralPort.kind``), never
    from which physical slot a port arrived in.
    """
    ports = getattr(context.now, "peripherals", None)
    if ports is None:
        return (
            None,
            "this firmware does not report what is on its peripheral ports",
            "a U1 whose GET_DIAGNOSTICS carries the peripheral block",
        )
    by_role: dict[str, list] = {"keyboard": [], "mouse": []}
    for port in ports:
        if port.attached and port.kind in by_role:
            by_role[port.kind].append(port)
    missing_roles = [role for role in ("keyboard", "mouse") if not by_role[role]]
    if missing_roles:
        attached_roles = [role for role in ("keyboard", "mouse") if by_role[role]]
        return (
            None,
            (
                "only " + " and ".join(attached_roles) + " is attached"
                if attached_roles
                else "neither a keyboard-role nor a mouse-role port is attached"
            )
            + f"; {' and '.join(missing_roles)} still has to reach this rig before "
            "both devices being ready is decidable",
            "a keyboard and a mouse both plugged into U1 at the same time",
        )
    return (
        all(port.ready for role in ("keyboard", "mouse") for port in by_role[role]),
        "",
        "",
    )


def _backend_counter_grew(context: _Context, name: str):
    """How much one of the backend's own counters grew since the baseline.

    The backend block is nested under ``DeviceDiagnostics.backend`` rather
    than sitting alongside ``link_crc_errors`` and the rest, so it needs its
    own accessor - ``_counter_grew`` reads straight off ``context.now``/
    ``context.baseline`` and would see nothing here. ``None`` here always
    means "this firmware did not send that counter", exactly as it does for
    the fields ``_counter_grew`` reads: never that it counted zero.
    """
    now_backend = getattr(context.now, "backend", None)
    if now_backend is None:
        return None
    now_value = getattr(now_backend, name, None)
    if now_value is None:
        return None
    baseline_backend = (
        getattr(context.baseline, "backend", None) if context.baseline is not None else None
    )
    before = getattr(baseline_backend, name, None) if baseline_backend is not None else None
    if before is None:
        return now_value
    return now_value - before


def _backend_counters_reset(context: _Context, names) -> bool:
    """True if any named backend counter reads lower now than at the
    baseline - the signature of the device having restarted in between.

    Every counter these checks watch only ever climbs while the firmware
    keeps running, so a negative difference is not "nothing grew"; it is a
    reset that zeroed the very things being watched. Fix round 1 measured
    the consequence of not checking this: a baseline of
    ``event_overflows=9, arm_failures=4, duplicate_mounts=7`` against a
    post-reset reading of all zeros produced
    ``backend_error_counters_stable: True`` and ``endpoint_reconnect_clean:
    True`` - both checks reading the exact brownout-reset event
    ``pio_usb_hub_recovery``'s RGB gate exists to catch as a clean pass.
    """
    return any((_backend_counter_grew(context, name) or 0) < 0 for name in names)


def _check_endpoint_reconnect(context: _Context):
    """The peripheral port that was detached and replugged is ready again,
    and the backend recorded no duplicate mount doing it.

    Unlike ``detach_releases_observed``, this one needs no computer watching:
    a duplicate mount is a defect in U1's own bookkeeping of its own bus, not
    a symptom that shows up at PC1 or PC2, so GET_DIAGNOSTICS is the whole
    story once a baseline exists to subtract.
    """
    backend = getattr(context.now, "backend", None)
    if backend is None:
        return (
            None,
            "this firmware does not report backend counters",
            "a U1 build that carries the backend block in GET_DIAGNOSTICS",
        )
    ports = getattr(context.now, "peripherals", None)
    if not ports:
        return (
            None,
            "this firmware does not report what is on its peripheral ports",
            "a U1 whose GET_DIAGNOSTICS carries the peripheral block",
        )
    attached = [port for port in ports if port.attached]
    if not attached:
        return (
            None,
            "nothing is attached to either peripheral port",
            "a keyboard or a mouse detached and replugged into U1 during this run",
        )
    if context.baseline is None:
        return (
            None,
            "no baseline reading, so a counter that only climbs says nothing about this run",
            "a --phase baseline run before the detach/replug",
        )
    grew = _backend_counter_grew(context, "duplicate_mounts")
    if grew is None:
        return (
            None,
            "this firmware does not report duplicate_mounts",
            "a newer U1 build",
        )
    if grew < 0:
        return (
            None,
            "duplicate_mounts is lower now than at the baseline, which only "
            "happens if the device restarted between the two readings - a "
            "reset zeroes exactly the counter this check watches, and reading "
            "that as nothing having grown would call the reset itself clean",
            "a U1 that did not reset between the baseline and this reading, or "
            "a fresh baseline taken right after the reset",
        )
    return (all(port.ready for port in attached) and grew == 0, "", "")


#: Backend counters whose growth during a run is itself the defect - the ones
#: Task 11 added and the binding decision names as what "stable error
#: counters" means for the PIO backend. ``ignored_interfaces`` and
#: ``ignored_role_already_claimed`` are deliberately excluded: V1 accepting
#: one keyboard and one mouse and refusing every other interface
#: deterministically is correct behaviour with a device like the Keychron
#: receiver attached (three interfaces, one logical keyboard and one logical
#: mouse), not a fault, and counting it as one would fail every honest run
#: with that receiver on the bench.
BACKEND_ERROR_COUNTER_NAMES = (
    "event_overflows",
    "detach_overflows",
    "stale_events_discarded",
    "arm_failures",
    "arm_escalations",
    "duplicate_mounts",
    "device_overflows",
    "interface_overflows",
    "callback_overflows",
)


def _check_backend_error_counters_stable(context: _Context):
    """None of the backend's own error counters grew during this run.

    Mirrors ``_check_error_counters_stable`` but reads the backend block
    instead of ``link_crc_errors`` - deliberately: ``link_crc_errors`` grows
    at the same rate as ``link_frames_sent`` whenever U2 is simply absent (a
    live measurement: 7428/7428, zero echoed frames), so a check that reads
    it as evidence of quality reads an absent U2 as a catastrophically broken
    one. None of the names below has that failure mode - they count what the
    backend itself refused or dropped, not the link's own silence.
    """
    backend = getattr(context.now, "backend", None)
    if backend is None:
        return (
            None,
            "this firmware does not report backend counters",
            "a U1 build that carries the backend block in GET_DIAGNOSTICS",
        )
    if context.baseline is None:
        return (
            None,
            "no baseline reading, so a counter that only climbs says nothing about this run",
            "a --phase baseline run before the scenario began",
        )
    reported = [
        name for name in BACKEND_ERROR_COUNTER_NAMES if getattr(backend, name, None) is not None
    ]
    if not reported:
        return (
            None,
            "this backend block carries none of the counters this check watches",
            "a U1 build whose backend block sends at least one of "
            + ", ".join(BACKEND_ERROR_COUNTER_NAMES),
        )
    if _backend_counters_reset(context, reported):
        return (
            None,
            "at least one backend counter reads lower now than at the baseline, "
            "which only happens if the device restarted between the two "
            "readings - a reset zeroes exactly the counters this check "
            "watches, and reading that as nothing having grown would call the "
            "reset itself a pass",
            "a U1 that did not reset between the baseline and this reading, or "
            "a fresh baseline taken right after the reset",
        )
    grew = [name for name in reported if (_backend_counter_grew(context, name) or 0) > 0]
    return (not grew, "", "")


#: Which checks this rig can decide, and how.
MEASURABLE_CHECKS = {
    "keyboard_p95_within_budget": _check_keyboard_p95,
    "mouse_p95_within_budget": _check_mouse_p95,
    "no_gap_over_50ms": _check_no_gap,
    "u2_released_within_budget": _check_u2_release,
    "crc_counter_incremented": _check_crc_counter,
    "link_recovered": _check_link_recovered,
    "error_counters_stable": _check_error_counters_stable,
    "every_device_enumerated": _check_every_device_enumerated,
    # Task 13: backend-aware names used by the PIO USB hardware-acceptance
    # scenarios. "both_devices_ready" is its own decision, not an alias for
    # "every_device_enumerated" - see _check_both_devices_ready's docstring
    # for why the two must never share one function.
    "both_devices_ready": _check_both_devices_ready,
    "endpoint_reconnect_clean": _check_endpoint_reconnect,
    "backend_error_counters_stable": _check_backend_error_counters_stable,
}

#: Checks nothing on this rig can decide, and exactly what each would take.
#:
#: Written out one by one rather than defaulted, because a default would let a
#: check added to a scenario later fall silently into "not measurable" without
#: anyone deciding that it is.
UNMEASURABLE_CHECKS = {
    "no_stuck_keys": (
        "whether a computer is still holding a key can only be seen at that computer",
        NEEDS_SECOND_COMPUTER,
    ),
    "no_misrouted_event": (
        "which computer an event arrived at can only be seen at that computer",
        NEEDS_SECOND_COMPUTER,
    ),
    "all_toggles_took_effect": (
        "a route change is observable as an event arriving somewhere else",
        NEEDS_SECOND_COMPUTER,
    ),
    "no_report_from_damaged_frame": (
        "whether a HID report followed a corrupted frame can only be seen at PC2",
        NEEDS_SECOND_COMPUTER,
    ),
    "resets_are_independent": (
        "whether one board kept serving while the other restarted is observed at the computers",
        NEEDS_SECOND_COMPUTER,
    ),
    "bindings_match_profile": (
        "what a binding produced is observed at the computer it was routed to",
        NEEDS_SECOND_COMPUTER,
    ),
    "routes_match_profile": (
        "which computer an event reached is observed at that computer",
        NEEDS_SECOND_COMPUTER,
    ),
    "active_profile_survives_power_cycle": (
        "this runner does not cut power, and reading the profile back proves nothing "
        "about a power cycle it did not cause",
        "U1 on a switchable supply the runner can command",
    ),
    "all_writes_verified": (
        "this runner does not write configuration; a write replaces what is on the "
        "device, and a read-only acceptance run must not",
        "a write-enabled run against a device whose configuration may be replaced",
    ),
    "no_torn_configuration": (
        "this runner does not cut power mid-write",
        "U1 on a switchable supply the runner can command",
    ),
    "never_unconfigured": (
        "this runner does not cut power mid-write",
        "U1 on a switchable supply the runner can command",
    ),
    "generation_monotonic": (
        "this runner does not write configuration, so there is no generation to watch",
        "a write-enabled run against a device whose configuration may be replaced",
    ),
    "no_watchdog_reset": (
        "the reset record is counted inside the firmware and GET_DIAGNOSTICS does not carry it",
        "a U1 build that reports its reset reason and watchdog count over CDC",
    ),
    "detach_releases_observed": (
        "whether a key or button held before a peripheral was detached was released can "
        "only be seen at the computer its route pointed to",
        NEEDS_SECOND_COMPUTER,
    ),
}


def evaluate(scenario: dict, now, baseline=None, elapsed_seconds: float | None = None):
    """Decide every check the scenario names, or say why it could not be.

    Returns ``(checks, unmeasured)``. A check the scenario names that appears in
    neither table is unmeasured with a reason saying so: an unknown check must
    never quietly become a pass.
    """
    context = _Context(scenario, now, baseline, elapsed_seconds)
    checks: dict[str, bool] = {}
    unmeasured: list[Unmeasured] = []

    required_hours = scenario_hours(scenario)
    short_run = (
        required_hours > 0
        and elapsed_seconds is not None
        and elapsed_seconds < required_hours * 3600
    )

    for name in scenario.get("checks", {}):
        if short_run:
            covered = 0.0 if elapsed_seconds is None else elapsed_seconds / 3600
            unmeasured.append(
                Unmeasured(
                    name,
                    f"the scenario specifies {required_hours:g} hours and this run "
                    f"covered {covered:.2f}",
                    f"a measure phase taken at least {required_hours:g} hours after "
                    "the baseline",
                )
            )
            continue
        if name in UNMEASURABLE_CHECKS:
            reason, needs = UNMEASURABLE_CHECKS[name]
            unmeasured.append(Unmeasured(name, reason, needs))
            continue
        decider = MEASURABLE_CHECKS.get(name)
        if decider is None:
            unmeasured.append(
                Unmeasured(
                    name,
                    "this runner has no way to decide this check and no record of why not",
                    "a decision about how it is measured, added to hil_runner.py",
                )
            )
            continue
        verdict, reason, needs = decider(context)
        if verdict is None:
            unmeasured.append(Unmeasured(name, reason, needs))
        else:
            checks[name] = bool(verdict)

    return checks, unmeasured


#: The two logical role slots U1 reports, in the order it reports them. A slot
#: name says which of the two roles a record is about and nothing at all about
#: what is plugged into it - see PeripheralRow.
SLOT_NAMES = ("keyboard slot", "mouse slot")


def peripheral_rows(now) -> list[PeripheralRow]:
    """One row per port U1 has, whether or not anything is on it.

    The role of each row is the device's own answer - the kind enumeration read
    out of its descriptor - and the slot it arrived in is a separate field.
    """
    ports = getattr(now, "peripherals", None)
    if not ports:
        return []
    rows: list[PeripheralRow] = []
    for index, port in enumerate(ports):
        slot = SLOT_NAMES[index] if index < len(SLOT_NAMES) else f"slot {index}"
        if not port.attached:
            rows.append(
                PeripheralRow(
                    port=slot,
                    # Not the slot's name: an empty slot holds no device, and
                    # naming one would be a claim about nothing.
                    role="none",
                    vendor_id="",
                    product_id="",
                    descriptor_hash="",
                    buttons=0,
                    passed=False,
                    reason="nothing attached to this port during the run",
                )
            )
            continue
        if port.ready:
            reason = (
                f"enumerated as {port.kind}"
                + (
                    f", {port.report_descriptor_bytes} bytes of report descriptor"
                    if port.report_descriptor_bytes
                    else ", boot protocol, no report descriptor read"
                )
                + (f", {port.buttons} buttons declared" if port.buttons else "")
            )
        else:
            reason = f"attached but never reached ready; enumeration saw it as {port.kind}"
        rows.append(
            PeripheralRow(
                port=slot,
                role=port.kind,
                vendor_id=f"0x{port.vendor_id:04X}",
                product_id=f"0x{port.product_id:04X}",
                descriptor_hash=port.descriptor_hash or "",
                buttons=port.buttons or 0,
                passed=bool(port.ready),
                reason=reason,
            )
        )
    return rows


def backend_of(now) -> tuple[str, dict[str, int]]:
    """Which backend the device named, and the counters it published with it.

    Firmware predating the appended backend block names none, and this must not
    fill that silence in with a guess: a report claiming CH375 about a board
    nobody asked would send its reader into the wrong half of the firmware.
    """
    backend = getattr(now, "backend", None)
    if backend is None:
        return ("unknown", {})
    return (str(getattr(backend, "name", "unknown")), dict(backend.counters()))


def coverage_of(scenario: dict, rows: list[PeripheralRow]) -> dict:
    """How much of what the scenario asks for this run actually touched.

    Separate from the checks on purpose. Two ports both working is a pass for
    the ports that exist and is not ten devices verified, and a report that
    ran the two together would imply the eight it never saw.
    """
    required = scenario.get("device_requirements")
    if not required:
        return {}
    total = sum(int(value) for value in required.values())
    exercised = sum(1 for row in rows if row.passed)
    return {
        "devices_required": total,
        "devices_required_by_role": dict(required),
        "devices_exercised": exercised,
        "note": (
            f"{exercised} device(s) verified of {total} the scenario requires. "
            "U1 has two peripheral ports, so one run can exercise at most one "
            "keyboard and one mouse; the rest need further runs with other devices."
        ),
    }


# --- running one scenario -----------------------------------------------------


def _read_state(path: Path | None) -> dict | None:
    if path is None or not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _diagnostics_from_state(state: dict | None):
    """Rebuild the baseline reading from what the baseline phase wrote."""
    if state is None:
        return None, None
    from duo_input.device.transactions import (
        DeviceDiagnostics,
        InputBackendReport,
        LatencyHistogram,
    )

    raw = state["diagnostics"]
    fields = dict(raw)
    for stream in ("keyboard_latency", "mouse_latency"):
        value = fields.get(stream)
        fields[stream] = (
            None
            if value is None
            else LatencyHistogram(
                edges_us=tuple(value["edges_us"]),
                buckets=tuple(value["buckets"]),
                count=value["count"],
                max_us=value["max_us"],
            )
        )
    fields.pop("peripherals", None)
    # The backend block round-trips as its own mapping, for the same reason the
    # histograms above do: asdict() flattens it to a plain dict on the way out,
    # and a field this did not rebuild would come back as a dict pretending to
    # be a report - readable right up to the first attribute access.
    backend = fields.get("backend")
    fields["backend"] = None if backend is None else InputBackendReport(**backend)
    return DeviceDiagnostics(**fields), state.get("taken_at")


def _state_document(diagnostics, taken_at: float) -> dict:
    raw = {
        key: value
        for key, value in asdict(diagnostics).items()
        if key != "peripherals"
    }
    for stream in ("keyboard_latency", "mouse_latency"):
        value = raw.get(stream)
        if value is not None:
            raw[stream] = {
                "edges_us": list(value["edges_us"]),
                "buckets": list(value["buckets"]),
                "count": value["count"],
                "max_us": value["max_us"],
            }
    return {"taken_at": taken_at, "diagnostics": raw}


#: What a `record` field reads when nothing on this rig can answer it -
#: toolchain revisions, hub model, and every human route/detach/RGB
#: observation the brief's Interfaces line names. Present in the report as
#: this string, never absent from it: an absent key reads as forgotten, a
#: present key marked unmeasured reads as a field someone has to fill in.
UNMEASURED_RECORD_VALUE = "unmeasured - needs a human at the bench"


def _record_field_value(name: str, result: ScenarioResult, elapsed_seconds) -> object:
    """What this rig itself can say about one of the scenario's declared
    ``record`` fields, or the explicit marker that it cannot.

    Only a handful of the fields a hardware-acceptance report has to state
    are ever visible to a CDC session with U1: which backend answered, how
    long the baseline-to-measure interval ran, and the identity of whichever
    peripheral actually enumerated. Everything else - Pico SDK/TinyUSB/
    Pico-PIO-USB revisions, hub model, PC1/PC2 route observations, detach-
    release observations, power/RGB symptoms - exists only in a human's own
    account of the bench session, and is named here as unmeasured rather
    than left for `to_json()` to simply not mention.
    """
    if name == "input_backend":
        return result.input_backend
    if name == "test_duration":
        if elapsed_seconds is not None:
            return f"{elapsed_seconds:.1f} s (baseline to this reading)"
        return (
            "unmeasured - no --phase baseline run was taken to time this "
            "reading against"
        )
    if name in (
        "keyboard_vendor_id_product_id_descriptor_hash",
        "mouse_vendor_id_product_id_descriptor_hash",
    ):
        role = name.split("_", 1)[0]
        for row in result.peripherals:
            if row.role == role and row.passed:
                return f"{row.vendor_id} {row.product_id} {row.descriptor_hash}"
        return UNMEASURED_RECORD_VALUE
    return UNMEASURED_RECORD_VALUE


def measure(scenario: dict, session, baseline=None, elapsed_seconds=None) -> ScenarioResult:
    """Read the device and decide what the scenario asks, against a fake or a board."""
    result = ScenarioResult(
        scenario=scenario["name"],
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )

    started_ns = time.perf_counter_ns()
    now = session.diagnostics()
    result.samples.append(
        Sample("cdc_round_trip", started_ns, time.perf_counter_ns())
    )

    result.peripherals = peripheral_rows(now)
    result.input_backend, result.input_backend_counters = backend_of(now)
    result.coverage = coverage_of(scenario, result.peripherals)
    result.checks, result.unmeasured = evaluate(scenario, now, baseline, elapsed_seconds)
    result.manual_observations = [
        dict(entry, recorded_by="unmeasured - needs a human at the bench")
        for entry in scenario.get("manual_observations", ())
    ]
    result.record = {
        name: _record_field_value(name, result, elapsed_seconds)
        for name in scenario.get("record", ())
    }

    for stream in ("keyboard", "mouse"):
        histogram = getattr(now, f"{stream}_latency", None)
        if histogram is None:
            continue
        measured = _difference(
            histogram,
            getattr(baseline, f"{stream}_latency", None) if baseline else None,
        )
        result.measurements[f"{stream}_latency_this_run"] = _histogram_json(measured)
        result.measurements[f"{stream}_latency_since_boot"] = _histogram_json(histogram)

    for name in ("endpoint_release_ms", "endpoint_drops", "link_crc_errors",
                 "dropped_commands", "runtime_fault"):
        value = getattr(now, name, None)
        if value is not None:
            result.measurements[name] = value

    if elapsed_seconds is not None:
        result.measurements["elapsed_hours"] = round(elapsed_seconds / 3600, 4)
    result.notes.append(MEASUREMENT_SCOPE)
    on_this_rig = scenario.get("on_this_rig")
    if on_this_rig:
        result.notes.append(on_this_rig)
    if baseline is None:
        result.notes.append(
            "no baseline: every counter here is since the device booted, not since "
            "this run began."
        )
    result.finished_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return result


def run(
    scenario_path: str | Path,
    port: str | None,
    *,
    phase: str = "measure",
    state: str | Path | None = None,
    session=None,
) -> ScenarioResult:
    """Execute one scenario phase against real hardware."""
    scenario = load_scenario(scenario_path)
    state_path = Path(state) if state is not None else None

    owned = session is None
    if owned:
        # Opening the device is what makes this a measurement rather than an
        # assertion about nothing.
        session = open_device(port)
    try:
        if phase == "baseline":
            taken_at = time.time()
            document = _state_document(session.diagnostics(), taken_at)
            if state_path is None:
                raise ValueError("--phase baseline needs --state to write the reading to")
            state_path.parent.mkdir(parents=True, exist_ok=True)
            state_path.write_text(
                json.dumps(document, indent=2, sort_keys=True), encoding="utf-8"
            )
            result = ScenarioResult(
                scenario=scenario["name"],
                started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            )
            result.notes.append(
                f"baseline written to {state_path}; exercise the device as the "
                "scenario describes, then run --phase measure with the same --state."
            )
            result.finished_at = result.started_at
            return result

        baseline, taken_at = _diagnostics_from_state(_read_state(state_path))
        elapsed = None if taken_at is None else max(0.0, time.time() - taken_at)
        return measure(scenario, session, baseline, elapsed)
    finally:
        if owned:
            close = getattr(session, "close", None)
            if close is not None:
                close()


EXIT_PASSED = 0
EXIT_FAILED = 1
EXIT_NO_HARDWARE = 2
EXIT_PARTIAL = 3
EXIT_INVALID_SCENARIO = 4


def exit_code(result: ScenarioResult) -> int:
    """Three outcomes get three codes, so a caller cannot conflate them."""
    if result.checks and not result.passed:
        return EXIT_FAILED
    if not result.complete:
        return EXIT_PARTIAL
    return EXIT_PASSED if result.checks else EXIT_PARTIAL


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path, help="scenario JSON to run")
    parser.add_argument("--port", help="serial port of the U1, e.g. COM7")
    parser.add_argument("--output", type=Path, help="where to write the report")
    parser.add_argument(
        "--phase",
        choices=("baseline", "measure"),
        default="measure",
        help="take the reading a run is measured against, or take the run's own",
    )
    parser.add_argument(
        "--state",
        type=Path,
        help="where the baseline reading is kept between the two phases",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="check the scenario file without touching hardware",
    )
    arguments = parser.parse_args(argv)

    if arguments.validate_only:
        try:
            scenario = load_scenario(arguments.scenario)
        except ValueError as error:
            # Fix round 1 measured this: an invalid scenario made
            # --validate-only exit with an uncaught traceback rather than the
            # clean rejection every other malformed input in this file gets.
            # A traceback is not more informative than the message the
            # validator already wrote - it is noise ahead of the same
            # sentence, and it panics an automation that just wanted a
            # nonzero exit code.
            print(f"invalid scenario: {error}", file=sys.stderr)
            return EXIT_INVALID_SCENARIO
        print(f"{scenario['name']}: {len(scenario['steps'])} steps, valid")
        return EXIT_PASSED

    try:
        result = run(
            arguments.scenario,
            arguments.port,
            phase=arguments.phase,
            state=arguments.state,
        )
    except HardwareRequired as error:
        print(f"hardware required: {error}", file=sys.stderr)
        return EXIT_NO_HARDWARE
    except ValueError as error:
        print(f"invalid scenario: {error}", file=sys.stderr)
        return EXIT_INVALID_SCENARIO

    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(result.to_json(), encoding="utf-8")
    print(result.to_json())
    if arguments.phase == "baseline":
        return EXIT_PASSED
    return exit_code(result)


if __name__ == "__main__":
    raise SystemExit(main())
