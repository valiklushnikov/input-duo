#!/usr/bin/env python3
"""CLI for File Provider performance trace analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fp_perf_metrics import ParsedEvents, analyze, parse_perf_lines, write_artifacts


def _paths(groups: list[list[str]] | None) -> list[Path]:
    return [Path(value) for group in (groups or []) for value in group]


def _load_events(groups: list[list[str]] | None, source: str) -> ParsedEvents:
    combined = ParsedEvents()
    for path in _paths(groups):
        parsed = parse_perf_lines(path.read_text(errors="replace").splitlines(), source)
        combined.extend(parsed)
        combined.parse_errors.extend(parsed.parse_errors)
    return combined


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension-log", action="append", nargs="+", required=True)
    parser.add_argument("--mac-log", action="append", nargs="+", required=True)
    parser.add_argument("--windows-log", action="append", nargs="+")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--correctness", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    all_events = ParsedEvents()
    for parsed in (
        _load_events(args.extension_log, "extension"),
        _load_events(args.mac_log, "mac"),
        _load_events(args.windows_log, "windows"),
    ):
        all_events.extend(parsed)
        all_events.parse_errors.extend(parsed.parse_errors)
    dataset = json.loads(args.dataset.read_text())
    correctness = json.loads(args.correctness.read_text()) if args.correctness else {}
    result = analyze(all_events, dataset, correctness)
    paths = write_artifacts(result, args.output_dir)
    print(paths.report)
    return 0 if result.performance_baseline_valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
