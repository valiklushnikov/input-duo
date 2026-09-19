import json
from pathlib import Path

from duo_input.transfer.model import TransferManifest, TransferEntry
from duo_input.transfer.fileprovider_replica import build_generation_record


def _manifest():
    return TransferManifest("abc123", (TransferEntry("a.txt", "file", 3, 111),))


def test_record_matches_golden_vector():
    rec = json.loads(build_generation_record(_manifest(), state="active",
                                             created_ns=1, lease_deadline_ns=2))
    golden = json.loads(Path("tests/transfer/fixtures/generation_record.json").read_text())
    assert rec == golden


def test_record_is_metadata_only():
    rec = json.loads(build_generation_record(_manifest(), state="active",
                                             created_ns=1, lease_deadline_ns=2))
    assert "manifest" in rec and rec["schema"] == 1
    # No raw-content field ever. "total_bytes" is a legitimate scalar count
    # inherited verbatim from manifest.to_dict() (Task 2, unmodified), so the
    # substring check must not flag it as a payload leak: every occurrence of
    # "bytes" in the dump is required to be part of "total_bytes" - nothing
    # else (content_bytes, raw_bytes, chunk bytes, ...) is allowed to appear.
    dumped = json.dumps(rec)
    assert dumped.count("bytes") == dumped.count("total_bytes")
