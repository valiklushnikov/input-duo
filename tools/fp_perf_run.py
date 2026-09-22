#!/usr/bin/env python3
"""Prepare and verify a correctness-safe File Provider profiling run."""

from __future__ import annotations

import argparse
import hashlib
import json
import stat
from pathlib import Path

from fp_perf_metrics import nearest_rank


UNKNOWN = "UNKNOWN"


class DestinationNotEmpty(RuntimeError):
    pass


def _regular_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(root.rglob("*"), key=lambda value: value.relative_to(root).as_posix()):
        try:
            mode = path.lstat().st_mode
        except OSError:
            continue
        if stat.S_ISREG(mode):
            files.append(path)
    return files


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def snapshot_dataset(root: Path | str) -> dict:
    source = Path(root)
    if not source.is_dir() or source.is_symlink():
        raise ValueError("source root must be a real directory")
    files = _regular_files(source)
    entries = [
        {
            "entry_index": index,
            "relative_path": path.relative_to(source).as_posix(),
            "size": path.stat().st_size,
            "sha256": _sha256(path),
        }
        for index, path in enumerate(files)
    ]
    sizes = [entry["size"] for entry in entries]
    return {
        "FILE_COUNT": len(entries),
        "TOTAL_BYTES": sum(sizes),
        "MIN_FILE_SIZE": min(sizes) if sizes else None,
        "MEDIAN_FILE_SIZE": nearest_rank(sizes, 50),
        "P95_FILE_SIZE": nearest_rank(sizes, 95),
        "MAX_FILE_SIZE": max(sizes) if sizes else None,
        "files": entries,
    }


def ensure_empty_destination(path: Path | str) -> Path:
    destination = Path(path)
    if destination == Path(destination.anchor) or destination.name in {"", ".", ".."}:
        raise ValueError("destination must name a specific child directory")
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or not destination.is_dir():
            raise ValueError("destination must be a real directory")
        if next(destination.iterdir(), None) is not None:
            raise DestinationNotEmpty(f"destination is not empty: {destination}")
        return destination
    parent = destination.parent
    if not parent.is_dir() or parent.is_symlink():
        raise ValueError("destination parent must already be a real directory")
    destination.mkdir(mode=0o700)
    return destination


def _evidence(run: dict, key: str) -> int | str:
    for section_name in ("classification", "aggregates", "correctness", "observations"):
        section = run.get(section_name)
        if isinstance(section, dict) and key in section:
            value = section[key]
            if isinstance(value, bool):
                return int(value)
            if isinstance(value, int) and value >= 0:
                return value
            if isinstance(value, str) and value.isdigit():
                return int(value)
            return UNKNOWN
    return UNKNOWN


def verify_dataset(
    dataset: dict,
    destination: Path | str,
    run: dict,
    *,
    finder_error: int | None | str,
) -> dict:
    target = Path(destination)
    actual_files = _regular_files(target) if target.is_dir() else []
    actual_by_name = {path.relative_to(target).as_posix(): path for path in actual_files}
    expected_entries = list(dataset.get("files", []))
    expected_names = {str(entry["relative_path"]) for entry in expected_entries}
    missing: list[str] = []
    different: list[str] = []
    for entry in expected_entries:
        relative = str(entry["relative_path"])
        actual = actual_by_name.get(relative)
        if actual is None:
            missing.append(relative)
        elif actual.stat().st_size != int(entry["size"]) or _sha256(actual) != entry["sha256"]:
            different.append(relative)

    expected_count = int(dataset.get("FILE_COUNT", len(expected_entries)))
    actual_count = len(actual_files)
    byte_exact = (
        not missing
        and not different
        and actual_count == expected_count
        and set(actual_by_name) == expected_names
    )
    refetch = _evidence(run, "REFETCH_COUNT")
    errors = {key: _evidence(run, key) for key in ("-1005", "-1004", "-1000")}
    if finder_error is None or finder_error == "none":
        finder_minus_36: int | str = 0
    elif finder_error == -36 or finder_error == "-36":
        finder_minus_36 = 1
    else:
        finder_minus_36 = UNKNOWN
    evidence_values = [refetch, *errors.values(), finder_minus_36]
    valid = (
        byte_exact
        and all(isinstance(value, int) and value == 0 for value in evidence_values)
    )
    return {
        "EXPECTED_FILE_COUNT": expected_count,
        "ACTUAL_FILE_COUNT": actual_count,
        "BYTE_EXACT": byte_exact,
        "MISSING": missing,
        "DIFF": different,
        "REFETCH_COUNT": refetch,
        **errors,
        "Finder -36": finder_minus_36,
        "PERFORMANCE_BASELINE_VALID": valid,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    snapshot = subparsers.add_parser("snapshot")
    snapshot.add_argument("--root", type=Path, required=True)
    snapshot.add_argument("--output", type=Path, required=True)
    snapshot.add_argument("--allow-test-count", action="store_true")
    check = subparsers.add_parser("check-destination")
    check.add_argument("--path", type=Path, required=True)
    verify = subparsers.add_parser("verify")
    verify.add_argument("--dataset", type=Path, required=True)
    verify.add_argument("--destination", type=Path, required=True)
    verify.add_argument("--events", type=Path, required=True)
    verify.add_argument("--finder-error", choices=("none", "-36", "unknown"), required=True)
    verify.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "snapshot":
        manifest = snapshot_dataset(args.root)
        if not args.allow_test_count and not 35 <= manifest["FILE_COUNT"] <= 50:
            raise SystemExit("profiling dataset must contain 35–50 regular files")
        args.output.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
        return 0
    if args.command == "check-destination":
        ensure_empty_destination(args.path)
        return 0
    result = verify_dataset(
        json.loads(args.dataset.read_text()),
        args.destination,
        json.loads(args.events.read_text()),
        finder_error=None if args.finder_error == "none" else args.finder_error,
    )
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0 if result["PERFORMANCE_BASELINE_VALID"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
