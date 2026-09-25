"""The wire form of a list of IPv4 addresses, the same on CDC and SPI."""

from __future__ import annotations

import pytest

from duo_input.device.host_addresses import (
    MAX_HOST_ADDRESSES,
    decode_host_addresses,
    encode_host_addresses,
)
from duo_input.device.transactions import PayloadError


def test_a_list_round_trips():
    wire = encode_host_addresses(["192.168.1.7", "10.0.0.2"])
    assert wire == bytes([2, 192, 168, 1, 7, 10, 0, 0, 2])
    assert decode_host_addresses(wire) == ["192.168.1.7", "10.0.0.2"]


def test_an_empty_list_is_one_byte():
    assert encode_host_addresses([]) == b"\x00"
    assert decode_host_addresses(b"\x00") == []


def test_only_the_first_eight_are_sent():
    many = [f"10.0.0.{n}" for n in range(1, 12)]
    assert decode_host_addresses(encode_host_addresses(many)) == many[:MAX_HOST_ADDRESSES]


def test_unusable_entries_are_left_out_rather_than_sent():
    assert encode_host_addresses(["nonsense", "0.0.0.0", "::1", "192.168.1.7"]) == bytes(
        [1, 192, 168, 1, 7]
    )


@pytest.mark.parametrize(
    "payload",
    [b"", bytes([1, 192, 168, 1]), bytes([1, 192, 168, 1, 7, 0]), bytes([9]) + bytes(36), bytes([1, 0, 0, 0, 0])],
)
def test_a_malformed_list_is_refused(payload):
    with pytest.raises(PayloadError):
        decode_host_addresses(payload)
