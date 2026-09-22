from __future__ import annotations

import hashlib

import pytest

from fp_perf_run import (
    DestinationNotEmpty,
    ensure_empty_destination,
    snapshot_dataset,
    verify_dataset,
)


def write(path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def test_snapshot_records_hand_checked_size_distribution_and_hashes(tmp_path):
    write(tmp_path / "source/a", b"a")
    write(tmp_path / "source/b", b"bbb")

    manifest = snapshot_dataset(tmp_path / "source")

    assert manifest["FILE_COUNT"] == 2
    assert manifest["TOTAL_BYTES"] == 4
    assert manifest["MIN_FILE_SIZE"] == 1
    assert manifest["MEDIAN_FILE_SIZE"] == 1
    assert manifest["P95_FILE_SIZE"] == 3
    assert manifest["MAX_FILE_SIZE"] == 3
    assert manifest["files"][0]["sha256"] == hashlib.sha256(b"a").hexdigest()
    assert [file["entry_index"] for file in manifest["files"]] == [0, 1]


def test_destination_guard_creates_only_the_exact_empty_directory(tmp_path):
    destination = tmp_path / "destination"
    assert ensure_empty_destination(destination) == destination
    assert destination.is_dir()
    assert list(destination.iterdir()) == []


def test_destination_guard_refuses_a_nonempty_directory_without_deleting_it(tmp_path):
    destination = tmp_path / "destination"
    write(destination / "keep.txt", b"keep")

    with pytest.raises(DestinationNotEmpty):
        ensure_empty_destination(destination)

    assert (destination / "keep.txt").read_bytes() == b"keep"


def exact_run_fixture(tmp_path, file_count=35):
    source = tmp_path / "source"
    destination = tmp_path / "destination"
    for index in range(file_count):
        payload = f"payload-{index}".encode()
        write(source / f"f{index:02d}.bin", payload)
        write(destination / f"f{index:02d}.bin", payload)
    dataset = snapshot_dataset(source)
    events = {
        "classification": {"REFETCH_COUNT": 0},
        "correctness": {"-1005": 0, "-1004": 0, "-1000": 0},
    }
    return dataset, destination, events


def test_verify_marks_exact_destination_valid(tmp_path):
    dataset, destination, events = exact_run_fixture(tmp_path)

    result = verify_dataset(dataset, destination, events, finder_error=None)

    assert result == {
        "EXPECTED_FILE_COUNT": 35,
        "ACTUAL_FILE_COUNT": 35,
        "BYTE_EXACT": True,
        "MISSING": [],
        "DIFF": [],
        "REFETCH_COUNT": 0,
        "-1005": 0,
        "-1004": 0,
        "-1000": 0,
        "Finder -36": 0,
        "PERFORMANCE_BASELINE_VALID": True,
    }


@pytest.mark.parametrize(
    "mutation", ["missing", "different", "extra", "refetch", "-1005", "finder-36"]
)
def test_any_correctness_regression_invalidates_baseline(mutation, tmp_path):
    dataset, destination, events = exact_run_fixture(tmp_path)
    finder_error = None
    if mutation == "missing":
        (destination / "f00.bin").unlink()
    elif mutation == "different":
        (destination / "f00.bin").write_bytes(b"different")
    elif mutation == "extra":
        (destination / "extra.bin").write_bytes(b"extra")
    elif mutation == "refetch":
        events["classification"]["REFETCH_COUNT"] = 1
    elif mutation == "-1005":
        events["correctness"]["-1005"] = 1
    elif mutation == "finder-36":
        finder_error = -36

    result = verify_dataset(dataset, destination, events, finder_error=finder_error)

    assert result["PERFORMANCE_BASELINE_VALID"] is False


def test_missing_error_evidence_is_unknown_and_invalid(tmp_path):
    dataset, destination, _events = exact_run_fixture(tmp_path)

    result = verify_dataset(dataset, destination, {}, finder_error=None)

    assert result["-1005"] == "UNKNOWN"
    assert result["REFETCH_COUNT"] == "UNKNOWN"
    assert result["PERFORMANCE_BASELINE_VALID"] is False
