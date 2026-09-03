"""Маячок: что он несёт, чего не несёт и чьи маячки игнорируются."""

from __future__ import annotations

import json

from duo_input.clipboard.discovery import Beacon, decode_beacon, encode_beacon
from duo_input.clipboard.wire import PROTOCOL_MAJOR

OURS = "1" * 32
THEIRS = "2" * 32

BEACON = Beacon(
    origin_id=THEIRS,
    machine_name="LAPTOP-TWO",
    fingerprint="f" * 64,
    port=47654,
    protocol_major=PROTOCOL_MAJOR,
)


def test_a_beacon_survives_a_round_trip():
    assert decode_beacon(encode_beacon(BEACON), OURS) == BEACON


def test_our_own_beacon_is_ignored():
    ours = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)

    assert decode_beacon(encode_beacon(ours), OURS) is None


def test_a_beacon_from_another_protocol_generation_is_ignored():
    stranger = Beacon(THEIRS, "LAPTOP-TWO", "f" * 64, 47654, PROTOCOL_MAJOR + 1)

    assert decode_beacon(encode_beacon(stranger), OURS) is None


def test_rubbish_on_the_wire_is_ignored_rather_than_raising():
    assert decode_beacon(b"\x00\x01 not json", OURS) is None
    assert decode_beacon(json.dumps({"origin_id": THEIRS}).encode("utf-8"), OURS) is None


def test_a_beacon_never_carries_clipboard_content():
    raw = json.loads(encode_beacon(BEACON).decode("utf-8"))

    assert set(raw) == {"origin_id", "machine_name", "fingerprint", "port", "protocol_major"}


def test_beacon_with_wrong_origin_id_type_is_ignored():
    malformed = json.dumps({
        "origin_id": None,
        "machine_name": "LAPTOP-TWO",
        "fingerprint": "f" * 64,
        "port": 47654,
        "protocol_major": PROTOCOL_MAJOR,
    }).encode("utf-8")

    assert decode_beacon(malformed, OURS) is None


def test_beacon_with_wrong_port_type_is_ignored():
    malformed = json.dumps({
        "origin_id": THEIRS,
        "machine_name": "LAPTOP-TWO",
        "fingerprint": "f" * 64,
        "port": "not_a_number",
        "protocol_major": PROTOCOL_MAJOR,
    }).encode("utf-8")

    assert decode_beacon(malformed, OURS) is None


def test_beacon_with_wrong_protocol_major_type_is_ignored():
    malformed = json.dumps({
        "origin_id": THEIRS,
        "machine_name": "LAPTOP-TWO",
        "fingerprint": "f" * 64,
        "port": 47654,
        "protocol_major": "not_a_number",
    }).encode("utf-8")

    assert decode_beacon(malformed, OURS) is None


def test_beacon_with_missing_required_field_is_ignored():
    malformed = json.dumps({
        "origin_id": THEIRS,
        "machine_name": "LAPTOP-TWO",
        "fingerprint": "f" * 64,
        # Missing port
        "protocol_major": PROTOCOL_MAJOR,
    }).encode("utf-8")

    assert decode_beacon(malformed, OURS) is None
