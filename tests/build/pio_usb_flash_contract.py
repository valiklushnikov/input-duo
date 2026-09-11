"""Shared linked-image checks for the flash-time PIO USB path."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path


_BRANCH_TO_SYMBOL = re.compile(
    r"\b(?:bl|blx|bx|b|b\.n|b\.w|bl\.w|beq|bne|bcc|bcs|bmi|bpl|bhi|bls|bge|blt|bgt|ble)"
    r"(?:\.[nw])?\s+(?:0x)?[0-9a-fA-F]+\s+<(?P<symbol>[^>+]+)(?:\+0x[0-9a-fA-F]+)?>"
)

_REQUIRED_SRAM_DATA = (
    "crc5_tbl",
    "crc16_tbl",
    "sof_packet",
    "sof_packet_encoded",
    "sof_packet_encoded_len",
    "keepalive_encoded",
    "usb_tx_dpdm_program",
    "usb_tx_pre_dpdm_program",
    "usb_tx_dmdp_program",
    "usb_tx_pre_dmdp_program",
    "usb_tx_dpdm_program_instructions",
    "usb_tx_pre_dpdm_program_instructions",
    "usb_tx_dmdp_program_instructions",
    "usb_tx_pre_dmdp_program_instructions",
)


def _tool(build_dir: Path, name: str) -> str:
    cache = (build_dir / "CMakeCache.txt").read_text(encoding="utf-8", errors="replace")
    match = re.search(rf"^{re.escape(name)}:[^=]*=(.+)$", cache, re.MULTILINE)
    assert match, f"{name} is absent from {build_dir / 'CMakeCache.txt'}"
    return match.group(1).strip()


def _disassembly(build_dir: Path, elf: Path) -> str:
    return subprocess.run(
        [_tool(build_dir, "CMAKE_OBJDUMP"), "-d", "-C", str(elf)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def _function_bodies(disassembly: str) -> tuple[dict[str, str], dict[str, int]]:
    bodies: dict[str, list[str]] = {}
    addresses: dict[str, int] = {}
    current: list[str] | None = None
    for line in disassembly.splitlines():
        header = re.match(r"^(?P<address>[0-9a-fA-F]+) <(?P<name>.*)>:$", line)
        if header:
            name = header.group("name")
            current = bodies.setdefault(name, [])
            addresses[name] = int(header.group("address"), 16)
        elif current is not None:
            current.append(line)
    return {name: "\n".join(lines) for name, lines in bodies.items()}, addresses


def _reachable(bodies: dict[str, str], root: str) -> set[str]:
    assert root in bodies, f"ELF disassembly has no {root}"
    seen: set[str] = set()
    pending = [root]
    while pending:
        name = pending.pop()
        if name in seen:
            continue
        seen.add(name)
        pending.extend(
            match.group("symbol")
            for match in _BRANCH_TO_SYMBOL.finditer(bodies.get(name, ""))
            if match.group("symbol") not in seen
        )
    return seen


def _nm_addresses(build_dir: Path, elf: Path) -> dict[str, list[int]]:
    output = subprocess.run(
        [_tool(build_dir, "CMAKE_NM"), "-C", "-n", str(elf)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    result: dict[str, list[int]] = {}
    for line in output.splitlines():
        match = re.match(r"^(?P<address>[0-9a-fA-F]+)\s+\S\s+(?P<name>.+)$", line)
        if match:
            result.setdefault(match.group("name"), []).append(int(match.group("address"), 16))
    return result


def assert_flash_path_sram_safe(build_dir: Path, elf: Path, park_symbol: str) -> None:
    disassembly = _disassembly(build_dir, elf)
    bodies, addresses = _function_bodies(disassembly)
    closure = _reachable(bodies, park_symbol)
    keepalive = "pio_usb_host_flash_keepalive"
    assert keepalive in closure, "the RAM park loop no longer sends USB keepalives"
    endpoint_service = "pio_usb_host_flash_service_endpoints"
    assert any(symbol.startswith(endpoint_service) for symbol in closure), (
        "the RAM flash loop no longer services already-queued endpoint transfers"
    )

    for symbol in closure:
        assert symbol in addresses, f"linked branch target {symbol} has no function body"
        address = addresses[symbol]
        assert address < 0x00004000 or 0x20000000 <= address < 0x20042000, (
            f"flash-time call graph reaches {symbol} at 0x{address:08x}, outside SRAM/boot ROM"
        )
        body = bodies[symbol]
        assert not re.search(r"\bblx\s+r(?:[0-9]|1[0-5])\b", body), (
            f"flash-time function {symbol} has an unresolved indirect call"
        )
        assert not re.search(r"\bbx\s+r(?:[0-9]|1[0-5])\b", body), (
            f"flash-time function {symbol} has an unresolved indirect tail call"
        )
        xip_literals = [
            int(match.group("address"), 16)
            for match in re.finditer(r"\.word\s+0x(?P<address>[0-9a-fA-F]+)", body)
            if 0x10000000 <= int(match.group("address"), 16) < 0x10200000
        ]
        assert not xip_literals, f"flash-time function {symbol} references XIP data {xip_literals}"

    symbols = _nm_addresses(build_dir, elf)
    for symbol in _REQUIRED_SRAM_DATA:
        assert symbol in symbols, f"ELF has no required flash-time USB datum {symbol}"
        assert all(0x20000000 <= address < 0x20042000 for address in symbols[symbol]), (
            f"{symbol} is not entirely resident in SRAM: {symbols[symbol]}"
        )
