#!/usr/bin/env python3
"""CLI for File Provider performance trace analysis."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from fp_perf_metrics import ParsedEvents, analyze, parse_perf_lines, write_artifacts


_CONNECTION_LEVEL_EVENTS = frozenset({"transport_configuration"})


def _paths(groups: list[list[str]] | None) -> list[Path]:
    return [Path(value) for group in (groups or []) for value in group]


def _load_events(groups: list[list[str]] | None, source: str) -> ParsedEvents:
    combined = ParsedEvents()
    for path in _paths(groups):
        parsed = parse_perf_lines(path.read_text(errors="replace").splitlines(), source)
        combined.extend(parsed)
        combined.parse_errors.extend(parsed.parse_errors)
    return combined


def filter_transfer_events(events: ParsedEvents, transfer_id: str) -> ParsedEvents:
    matching_tokens = {
        event.fields["fetch_token"]
        for event in events
        if (
            event.fields.get("transfer_id") == transfer_id
            or event.fields.get("generation_id") == transfer_id
            or event.fields.get("item_identifier", "").startswith(transfer_id + ":")
        )
        and event.fields.get("fetch_token") not in (None, "none")
    }
    return ParsedEvents(
        (
            event
            for event in events
            if event.name in _CONNECTION_LEVEL_EVENTS
            or event.fields.get("transfer_id") == transfer_id
            or event.fields.get("generation_id") == transfer_id
            or event.fields.get("item_identifier", "").startswith(transfer_id + ":")
            or event.fields.get("fetch_token") in matching_tokens
        ),
        events.parse_errors,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--extension-log", action="append", nargs="+", required=True)
    parser.add_argument("--mac-log", action="append", nargs="+", required=True)
    parser.add_argument("--windows-log", action="append", nargs="+")
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--correctness", type=Path)
    parser.add_argument("--transfer-id")
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
    if args.transfer_id:
        all_events = filter_transfer_events(all_events, args.transfer_id)
    dataset = json.loads(args.dataset.read_text())
    correctness = json.loads(args.correctness.read_text()) if args.correctness else {}
    result = analyze(all_events, dataset, correctness)
    paths = write_artifacts(result, args.output_dir)
    print(paths.report)
    # A first pass without correctness is intentional: its run.json supplies
    # measured refetch/error counts to fp_perf_run verify. The second pass,
    # with --correctness, enforces the final baseline status via exit code.
    return 0 if args.correctness is None or result.performance_baseline_valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
