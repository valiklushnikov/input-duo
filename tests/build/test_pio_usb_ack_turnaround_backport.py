"""Provenance contract for the full upstream EP0 ACK-turnaround backport.

Software tests cannot measure the sub-microsecond full-speed handshake window.
They can, however, prevent the reviewed upstream correction from being trimmed
or silently replaced by a locally invented variant.  The behavioral proof is
the U1 hardware gate recorded beside this contract.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[2]
BACKPORT = (
    ROOT
    / "patches"
    / "pico-pio-usb"
    / "0001-upstream-ep0-ack-turnaround.patch"
)

# Exact LF-normalized diff of upstream commit
# 38ed543d5a5f7a4f4ec72dcb2c4bcd4f5c8001e7, whose parent is the pinned
# Pico-PIO-USB 0.7.2 base 3c1eec341a5232640e4c00628b889b641af34b28.
UPSTREAM_DIFF_SHA256 = (
    "22474d37eb325ce980a96b1dba44f5a3199a81248cc3aae2d3c10fc712dc4135"
)
PATCHED_REVISION = "c219ccab8ba7b83a1b502d0fe42c1eda0a586f9a"


def _normalized_payload() -> bytes:
    return BACKPORT.read_bytes().replace(b"\r\n", b"\n")


def _is_exact_upstream_diff(payload: bytes) -> bool:
    return hashlib.sha256(payload).hexdigest() == UPSTREAM_DIFF_SHA256


def test_the_exact_full_upstream_ack_turnaround_diff_is_applied_first():
    payload = _normalized_payload()

    assert _is_exact_upstream_diff(payload)


@pytest.mark.parametrize(
    "needle,replacement",
    (
        (b"-void __no_inline_not_in_flash_func(pio_usb_bus_send_handshake)(",
         b" void __no_inline_not_in_flash_func(pio_usb_bus_send_handshake)("),
        (b"+  PIO pio_usb_rx = pp->pio_usb_rx;",
         b"+  PIO pio_usb_rx = pio0;"),
        (b"+  uint sm_rx =  pp->sm_rx;", b"+  uint sm_rx =  0;"),
        (b"+  uint8_t *usb_rx_buffer = pp->usb_rx_buffer;",
         b"+  uint8_t *usb_rx_buffer = NULL;"),
        (b"+  while (1) {", b"+  while (get_time_us_32() - start <= 7) {"),
        (b"-        crc_receive_inverse = crc_receive ^ 0xffff;",
         b"         crc_receive_inverse = crc_receive ^ 0xffff;"),
        (b"+          pio_usb_bus_usb_transfer(pp, ack_encoded, 5);",
         b"+          pio_usb_bus_send_handshake(pp, USB_PID_ACK);"),
        (b"+        pio_usb_bus_usb_transfer(pp, nak_encoded, 5);",
         b"+        pio_usb_bus_usb_transfer(pp, nak_encoded, 4);"),
        (b"+        pio_usb_bus_usb_transfer(pp, stall_encoded, 5);",
         b"+        pio_usb_bus_usb_transfer(pp, stall_encoded, 4);"),
        (b"-void pio_usb_bus_send_handshake(pio_port_t *pp, uint8_t pid);",
         b" void pio_usb_bus_send_handshake(pio_port_t *pp, uint8_t pid);"),
    ),
)
def test_the_provenance_contract_rejects_each_latency_regression(
    needle: bytes, replacement: bytes
):
    payload = _normalized_payload()
    assert needle in payload

    mutated = payload.replace(needle, replacement, 1)

    assert not _is_exact_upstream_diff(mutated)


def test_the_bootstrapped_source_contains_the_reviewed_hot_path():
    dependency = ROOT / ".deps" / "pico-pio-usb"
    revision = subprocess.run(
        ["git", "-C", str(dependency), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert revision == PATCHED_REVISION
    status = subprocess.run(
        ["git", "-C", str(dependency), "status", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert status == ""

    source = (dependency / "src" / "pio_usb.c").read_text(encoding="utf-8")
    header = (dependency / "src" / "pio_usb_ll.h").read_text(encoding="utf-8")
    assert "void __no_inline_not_in_flash_func(pio_usb_bus_send_handshake)" not in source
    assert "pio_usb_bus_send_handshake(pio_port_t *pp" not in header
    for required in (
        "PIO pio_usb_rx = pp->pio_usb_rx;",
        "uint sm_rx =  pp->sm_rx;",
        "uint8_t *usb_rx_buffer = pp->usb_rx_buffer;",
        "while (1)",
        "pio_usb_bus_usb_transfer(pp, ack_encoded, 5);",
        "pio_usb_bus_usb_transfer(pp, nak_encoded, 5);",
        "pio_usb_bus_usb_transfer(pp, stall_encoded, 5);",
        "else if (get_time_us_32() - start > 7)",
    ):
        assert required in source
