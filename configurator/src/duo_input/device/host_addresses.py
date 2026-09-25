"""The wire form of a list of IPv4 addresses.

The same bytes travel in EXCHANGE_ADDRESSES over CDC and in HOST_ADDRESSES /
ENDPOINT_ADDRESSES over SPI: a count byte, then that many four-byte addresses
in network order. firmware/common/link/host_addresses.cpp is the other half,
and the two refuse exactly the same payloads.
"""

from __future__ import annotations

import ipaddress

from .transactions import PayloadError

MAX_HOST_ADDRESSES = 8


def _usable(address: str) -> ipaddress.IPv4Address | None:
    try:
        parsed = ipaddress.IPv4Address(address)
    except ValueError:
        return None
    return None if parsed.is_unspecified else parsed


def encode_host_addresses(addresses: list[str]) -> bytes:
    """Encode up to eight usable IPv4 addresses; anything else is left out."""
    usable = [parsed for parsed in map(_usable, addresses) if parsed is not None]
    usable = usable[:MAX_HOST_ADDRESSES]
    return bytes([len(usable)]) + b"".join(parsed.packed for parsed in usable)


def decode_host_addresses(payload: bytes) -> list[str]:
    if not payload:
        raise PayloadError("address list is empty")
    count = payload[0]
    if count > MAX_HOST_ADDRESSES:
        raise PayloadError(f"address list claims {count} entries")
    if len(payload) != 1 + 4 * count:
        raise PayloadError(f"address list of {count} is {len(payload)} bytes")
    addresses = []
    for index in range(count):
        parsed = ipaddress.IPv4Address(payload[1 + 4 * index : 5 + 4 * index])
        if parsed.is_unspecified:
            raise PayloadError("address list carries 0.0.0.0")
        addresses.append(str(parsed))
    return addresses


__all__ = ["MAX_HOST_ADDRESSES", "decode_host_addresses", "encode_host_addresses"]
