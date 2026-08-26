"""Hardware-in-the-loop acceptance: measure what the device actually does.

Nothing in this file estimates. Every number it reports comes from a timestamp
taken around a real event on real hardware, and a scenario that cannot reach
the hardware fails rather than returning a plausible-looking result. That rule
is the entire point of the file: a latency figure produced by a simulator is
not evidence about a device, and treating one as the other is how a product
ships with a defect its own test suite said was fine.

Usage:

    python tests/hil/hil_runner.py tests/hil/scenarios/route_toggle.json \\
        --port COM7 --output artifacts/route_toggle.json

Each run writes one JSON report: the scenario it ran, every sample it took,
the derived statistics, and a verdict per requirement.
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


class HardwareRequired(RuntimeError):
    """The scenario needs hardware that is not attached.

    Raised rather than returning a result. A HIL report that was produced
    without hardware is worse than no report: it looks like evidence.
    """


@dataclass(frozen=True)
class Sample:
    """One injected event and the moment it was observed on the far side."""

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
    latency the device never actually produced.
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
    """

    role: str
    vendor_id: str
    product_id: str
    descriptor_hash: str
    buttons: int
    passed: bool
    reason: str


@dataclass
class ScenarioResult:
    """Everything one scenario run produced."""

    scenario: str
    started_at: str
    finished_at: str = ""
    samples: list[Sample] = field(default_factory=list)
    peripherals: list[PeripheralRow] = field(default_factory=list)
    checks: dict[str, bool] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(self.checks.values())

    def to_json(self) -> str:
        latencies = [latency_report(kind, self.samples) for kind in ("keyboard", "mouse")]
        document = {
            "scenario": self.scenario,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "passed": self.passed,
            "checks": self.checks,
            "latency": [asdict(report) | {"passed": report.passed} for report in latencies],
            "gaps_over_50ms": gaps([sample.observed_ns for sample in self.samples]),
            "peripherals": [asdict(row) for row in self.peripherals],
            "samples": [asdict(sample) | {"latency_ms": round(sample.latency_ms, 3)}
                        for sample in self.samples],
            "notes": self.notes,
        }
        return json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True)


# --- scenarios ---------------------------------------------------------------


REQUIRED_SCENARIO_KEYS = ("name", "description", "requires", "steps")


def load_scenario(path: str | Path) -> dict:
    """Read one scenario and refuse it if it does not say what it needs."""
    document = json.loads(Path(path).read_text(encoding="utf-8"))
    missing = [key for key in REQUIRED_SCENARIO_KEYS if key not in document]
    if missing:
        raise ValueError(f"{path}: scenario is missing {', '.join(missing)}")
    if not document["steps"]:
        raise ValueError(f"{path}: scenario has no steps")
    for index, step in enumerate(document["steps"]):
        if "action" not in step:
            raise ValueError(f"{path}: step {index} has no action")
    return document


def open_device(port: str | None):
    """Open the U1 named by ``port``, or discover one.

    There is no fallback to the emulator here, deliberately. The emulator is
    the right tool for the UI tests and the wrong tool for this file.
    """
    if port is None:
        raise HardwareRequired(
            "no --port given; HIL scenarios run against a real U1, never the emulator"
        )
    try:
        from duo_input.device.qt_transport import QSerialPortTransport
    except ImportError as error:  # pragma: no cover - the configurator is a sibling
        raise HardwareRequired(
            "the configurator package is not importable; install it first"
        ) from error

    transport = QSerialPortTransport(port)
    if not transport.open():
        raise HardwareRequired(f"{port} did not open; is the U1 attached?")
    return transport


def run(scenario_path: str | Path, port: str | None) -> ScenarioResult:
    """Execute one scenario against real hardware."""
    scenario = load_scenario(scenario_path)
    result = ScenarioResult(
        scenario=scenario["name"],
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    )
    # Opening the device is what makes this a measurement rather than an
    # assertion about nothing.
    open_device(port)
    raise HardwareRequired(
        f"{scenario['name']} needs the two-board rig described in its "
        f"'requires' field: {', '.join(scenario['requires'])}. "
        "Wire it up and run this again; this runner will not invent results."
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("scenario", type=Path, help="scenario JSON to run")
    parser.add_argument("--port", help="serial port of the U1, e.g. COM7")
    parser.add_argument("--output", type=Path, help="where to write the report")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="check the scenario file without touching hardware",
    )
    arguments = parser.parse_args(argv)

    if arguments.validate_only:
        scenario = load_scenario(arguments.scenario)
        print(f"{scenario['name']}: {len(scenario['steps'])} steps, valid")
        return 0

    try:
        result = run(arguments.scenario, arguments.port)
    except HardwareRequired as error:
        print(f"hardware required: {error}", file=sys.stderr)
        return 2

    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(result.to_json(), encoding="utf-8")
    print(result.to_json())
    return 0 if result.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
