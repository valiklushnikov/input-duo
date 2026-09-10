import json
from pathlib import Path

import pytest

from duo_input.generated.protocol import Capability, CdcMessageType, SpiMessageType
from duo_input.protocol.cobs import cobs_decode, cobs_encode
from duo_input.protocol.crc import crc16_ccitt, crc32_ieee
from duo_input.protocol.frame import (
    CdcFrame,
    FrameError,
    SpiFrame,
    decode_cdc_frame,
    decode_spi_frame,
    encode_cdc_frame,
    encode_spi_frame,
    is_minor_compatible,
)


def load_vectors():
    return json.loads(Path("tests", "vectors", "frame_vectors.json").read_text("utf-8"))


def test_hid_report_sets_round_trip_and_capability_negotiation():
    assert int(CdcMessageType.GET_HID_REPORT_SETS) == 23
    assert int(Capability.HID_REPORT_SET_DIAGNOSTICS) == 4096
    for payload in (b"", b"\x00\x01\x00", bytes(459)):
        frame = CdcFrame(type=CdcMessageType.GET_HID_REPORT_SETS, sequence=23, payload=payload)
        assert decode_cdc_frame(encode_cdc_frame(frame)) == frame
    assert is_minor_compatible(9, 4096, int(Capability.HID_REPORT_SET_DIAGNOSTICS))
    assert not is_minor_compatible(9, 4096, 4095)


def cdc_transport_with_crc(raw: bytes) -> bytes:
    assert len(raw) >= 10
    return cobs_encode(raw[:-4] + crc32_ieee(raw[:-4]).to_bytes(4, "little")) + b"\0"


def spi_with_crc(frame: bytes) -> bytes:
    assert len(frame) == 64
    return frame[:62] + crc16_ccitt(frame[:62]).to_bytes(2, "little")


def test_cdc_vector_encodes_and_decodes_exact_transport_bytes():
    vector = load_vectors()["cdc"]
    frame = CdcFrame(
        type=CdcMessageType[vector["type"]],
        sequence=vector["sequence"],
        payload=bytearray.fromhex(vector["payload"]),
        minor=vector["minor"],
    )

    assert frame.payload == bytes.fromhex(vector["payload"])
    assert encode_cdc_frame(frame).hex() == vector["transport"]
    assert decode_cdc_frame(bytes.fromhex(vector["transport"])) == frame


def test_spi_vector_encodes_and_decodes_exact_64_byte_frame():
    vector = load_vectors()["spi"]
    frame = SpiFrame(
        type=SpiMessageType[vector["type"]],
        sequence=vector["sequence"],
        payload=bytearray.fromhex(vector["payload"]),
        minor=vector["minor"],
    )

    encoded = encode_spi_frame(frame)

    assert len(encoded) == 64
    assert encoded.hex() == vector["frame"]
    assert decode_spi_frame(encoded) == frame


def test_cdc_rejects_crc_damage():
    valid_cdc_bytes = bytes.fromhex(load_vectors()["cdc"]["transport"])
    damaged = valid_cdc_bytes[:-2] + bytes([valid_cdc_bytes[-2] ^ 1]) + valid_cdc_bytes[-1:]

    with pytest.raises(FrameError, match="CRC"):
        decode_cdc_frame(damaged)


@pytest.mark.parametrize("payload", [b"", b"x", bytes(range(256)) * 4])
def test_cdc_accepts_payload_lengths_up_to_1024(payload):
    frame = CdcFrame(type=CdcMessageType.PING, sequence=7, payload=payload)

    assert decode_cdc_frame(encode_cdc_frame(frame)) == frame


def test_cdc_rejects_payload_length_1025():
    frame = CdcFrame(type=CdcMessageType.PING, sequence=7, payload=b"x" * 1025)

    with pytest.raises(FrameError, match="payload"):
        encode_cdc_frame(frame)


def test_cdc_rejects_one_byte_truncation_at_every_transport_boundary():
    transport = bytes.fromhex(load_vectors()["cdc"]["transport"])

    for boundary in range(len(transport)):
        with pytest.raises(FrameError):
            decode_cdc_frame(transport[:boundary])


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        (lambda raw: bytes([0]) + raw[1:], "magic"),
        (lambda raw: raw[:2] + bytes([2]) + raw[3:], "major"),
        (lambda raw: raw[:5] + bytes([1]) + raw[6:], "flags"),
        (lambda raw: raw[:4] + bytes([255]) + raw[5:], "type"),
        (lambda raw: raw[:8] + b"\x04\0" + raw[10:], "length"),
    ],
)
def test_cdc_rejects_invalid_header_fields(mutation, message):
    raw = bytearray.fromhex(load_vectors()["cdc"]["raw"])

    with pytest.raises(FrameError, match=message):
        decode_cdc_frame(cdc_transport_with_crc(mutation(bytes(raw))))


@pytest.mark.parametrize("transport", [b"", b"\x01", b"\x01\0\x01\0", b"\x02\0"])
def test_cdc_rejects_missing_delimiter_trailing_data_and_malformed_cobs(transport):
    with pytest.raises(FrameError):
        decode_cdc_frame(transport)


def test_spi_rejects_invalid_size_padding_crc_and_header_fields():
    vector = bytes.fromhex(load_vectors()["spi"]["frame"])
    cases = [
        (vector[:-1], "size"),
        (spi_with_crc(b"\0" + vector[1:]), "magic"),
        (spi_with_crc(vector[:2] + b"\x02" + vector[3:]), "major"),
        (spi_with_crc(vector[:5] + b"\x01" + vector[6:]), "flags"),
        (spi_with_crc(vector[:4] + b"\xff" + vector[5:]), "type"),
        (spi_with_crc(vector[:8] + b"\x35\0" + vector[10:]), "length"),
        (spi_with_crc(vector[:20] + b"\x01" + vector[21:]), "padding"),
        (vector[:-1] + bytes([vector[-1] ^ 1]), "CRC"),
    ]

    for malformed, message in cases:
        with pytest.raises(FrameError, match=message):
            decode_spi_frame(malformed)


def test_minor_mismatch_requires_supported_capabilities_and_is_preserved():
    raw = bytearray.fromhex(load_vectors()["cdc"]["raw"])
    raw[3] = 9
    transport = cdc_transport_with_crc(bytes(raw))

    assert is_minor_compatible(9, int(Capability.KEYBOARD_HID), int(Capability.KEYBOARD_HID))
    assert not is_minor_compatible(9, int(Capability.CAPTURE), int(Capability.KEYBOARD_HID))
    assert decode_cdc_frame(
        transport,
        required_capabilities=int(Capability.KEYBOARD_HID),
        supported_capabilities=int(Capability.KEYBOARD_HID),
    ).minor == 9
    with pytest.raises(FrameError, match="minor"):
        decode_cdc_frame(
            transport,
            required_capabilities=int(Capability.CAPTURE),
            supported_capabilities=int(Capability.KEYBOARD_HID),
        )
