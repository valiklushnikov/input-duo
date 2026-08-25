#!/usr/bin/env python3
"""Generate and verify the deterministic parser-fuzz seed corpus."""

from __future__ import annotations

import argparse
import json
import struct
import sys
import zlib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CORPUS = Path(__file__).resolve().parent / "corpus"
CDC_MAX_PAYLOAD = 1024
SPI_PAYLOAD_MAX = 52
CONFIG_MAX_BYTES = 368640


def crc16_ccitt(data: bytes) -> int:
    crc = 0xFFFF
    for value in data:
        crc ^= value << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def cobs_encode(data: bytes) -> bytes:
    encoded = bytearray([0])
    code_index = 0
    code = 1
    for value in data:
        if value == 0:
            encoded[code_index] = code
            code_index = len(encoded)
            encoded.append(0)
            code = 1
        else:
            encoded.append(value)
            code += 1
            if code == 0xFF:
                encoded[code_index] = code
                code_index = len(encoded)
                encoded.append(0)
                code = 1
    encoded[code_index] = code
    encoded.append(0)
    return bytes(encoded)


def cdc_frame(declared_length: int, payload_length: int, *, message_type: int = 0x15,
              flags: int = 0, corrupt_crc: bool = False) -> bytes:
    payload = bytes((index % 251) + 1 for index in range(payload_length))
    raw = bytearray(b"DI\x01\x00")
    raw.extend((message_type, flags))
    raw.extend(struct.pack("<H", 0x1234))
    raw.extend(struct.pack("<H", declared_length))
    raw.extend(payload)
    raw.extend(struct.pack("<I", zlib.crc32(raw)))
    if corrupt_crc:
        raw[-1] ^= 0x80
    return cobs_encode(raw)


def spi_frame(declared_length: int, payload_length: int, *, message_type: int = 0x06,
              flags: int = 0, corrupt_crc: bool = False) -> bytes:
    frame = bytearray(64)
    frame[:10] = b"DS\x01\x00" + bytes((message_type, flags)) + struct.pack("<H", 0x002A) + struct.pack(
        "<H", declared_length
    )
    frame[10 : 10 + payload_length] = bytes((index % 251) + 1 for index in range(payload_length))
    frame[62:64] = struct.pack("<H", crc16_ccitt(frame[:62]))
    if corrupt_crc:
        frame[63] ^= 0x80
    return bytes(frame)


def config_with_length(valid: bytes, declared_length: int, *, corrupt_crc: bool = False,
                       flags: int | None = None) -> bytes:
    result = bytearray(valid)
    result[8:12] = struct.pack("<I", declared_length)
    if flags is not None:
        result[6] = flags
    result[12:16] = b"\0\0\0\0"
    result[12:16] = struct.pack("<I", zlib.crc32(result))
    if corrupt_crc:
        result[15] ^= 0x80
    return bytes(result)


def shared_vectors() -> tuple[bytes, bytes, bytes, bytes]:
    frame_vectors = json.loads((ROOT / "tests/vectors/frame_vectors.json").read_text(encoding="utf-8"))
    return (
        bytes.fromhex(frame_vectors["cdc"]["transport"]),
        bytes.fromhex(frame_vectors["spi"]["frame"]),
        (ROOT / "tests/vectors/config_vectors/valid_minimal.bin").read_bytes(),
        bytes.fromhex(frame_vectors["cdc"]["raw"]),
    )


def expected_corpus() -> dict[str, dict[str, bytes]]:
    valid_cdc, valid_spi, valid_config, valid_cdc_raw = shared_vectors()
    cdc = {"valid-shared-vector.bin": valid_cdc}
    for label, declared, payload_size in (
        ("0000", 0, 0),
        ("max", CDC_MAX_PAYLOAD, CDC_MAX_PAYLOAD),
        ("max-plus-one", CDC_MAX_PAYLOAD + 1, CDC_MAX_PAYLOAD + 1),
        ("ffff", 0xFFFF, 0),
    ):
        cdc[f"length-{label}-crc-valid.bin"] = cdc_frame(declared, payload_size)
        cdc[f"length-{label}-crc-corrupt.bin"] = cdc_frame(declared, payload_size, corrupt_crc=True)
    cdc.update({
        "truncated-before-header.bin": cobs_encode(valid_cdc_raw[:9]),
        "truncated-header.bin": cobs_encode(valid_cdc_raw[:10]),
        "truncated-payload.bin": cobs_encode(valid_cdc_raw[:11]),
        "truncated-before-crc.bin": cobs_encode(valid_cdc_raw[:-4]),
        "truncated-crc.bin": cobs_encode(valid_cdc_raw[:-1]),
        "unknown-type-crc-valid.bin": cdc_frame(0, 0, message_type=0xFF),
        "invalid-flags-crc-valid.bin": cdc_frame(0, 0, flags=1),
    })

    spi = {"valid-shared-vector.bin": valid_spi}
    for label, declared, payload_size in (
        ("0000", 0, 0),
        ("max", SPI_PAYLOAD_MAX, SPI_PAYLOAD_MAX),
        ("max-plus-one", SPI_PAYLOAD_MAX + 1, 0),
        ("ffff", 0xFFFF, 0),
    ):
        spi[f"length-{label}-crc-valid.bin"] = spi_frame(declared, payload_size)
        spi[f"length-{label}-crc-corrupt.bin"] = spi_frame(declared, payload_size, corrupt_crc=True)
    spi.update({
        "truncated-before-header.bin": valid_spi[:9],
        "truncated-header.bin": valid_spi[:10],
        "truncated-payload.bin": valid_spi[:11],
        "truncated-before-crc.bin": valid_spi[:62],
        "truncated-crc.bin": valid_spi[:63],
        "unknown-type-crc-valid.bin": spi_frame(0, 0, message_type=0xFF),
        "invalid-flags-crc-valid.bin": spi_frame(0, 0, flags=1),
    })

    config = {"valid-minimal-shared-vector.bin": valid_config}
    for label, declared in (
        ("0000", 0),
        ("max", CONFIG_MAX_BYTES),
        ("max-plus-one", CONFIG_MAX_BYTES + 1),
        ("ffff", 0xFFFF),
    ):
        config[f"length-{label}-crc-valid.bin"] = config_with_length(valid_config, declared)
        config[f"length-{label}-crc-corrupt.bin"] = config_with_length(valid_config, declared, corrupt_crc=True)
    config.update({
        "crc-corrupt-valid-header.bin": config_with_length(
            valid_config, len(valid_config), corrupt_crc=True
        ),
        "truncated-before-header.bin": valid_config[:63],
        "truncated-header.bin": valid_config[:64],
        "truncated-profile-table.bin": valid_config[:351],
        "truncated-data.bin": valid_config[:-1],
        "invalid-flags-crc-valid.bin": config_with_length(valid_config, len(valid_config), flags=1),
    })
    return {"cdc": cdc, "spi": spi, "config": config}


def verify_or_write(expected: dict[str, dict[str, bytes]], check: bool) -> int:
    failures: list[str] = []
    for parser, seeds in expected.items():
        directory = CORPUS / parser
        existing = {path.name for path in directory.glob("*")} if directory.exists() else set()
        expected_names = set(seeds)
        for name, content in seeds.items():
            path = directory / name
            if check:
                if not path.is_file() or path.read_bytes() != content:
                    failures.append(str(path))
            else:
                directory.mkdir(parents=True, exist_ok=True)
                path.write_bytes(content)
        unexpected = existing - expected_names
        if unexpected:
            failures.extend(str(directory / name) for name in sorted(unexpected))
    if failures:
        print("stale or missing fuzz corpus files:", *failures, sep="\n", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="fail when the committed corpus is stale")
    return verify_or_write(expected_corpus(), check=parser.parse_args().check)


if __name__ == "__main__":
    raise SystemExit(main())
