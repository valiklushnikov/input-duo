"""Framing and sequence bookkeeping used by the asynchronous device service."""

from __future__ import annotations

import struct

import pytest

from duo_input.device.transactions import (
    MAX_PENDING_FRAME_BYTES,
    FrameAssembler,
    FrameOverflowError,
    PayloadError,
    SequenceGenerator,
    parse_capture_event,
)
from duo_input.domain.models import TriggerSource
from duo_input.generated.protocol import (
    CDC_MAX_PAYLOAD,
    PROTOCOL_VERSION_MAJOR,
    PROTOCOL_VERSION_MINOR,
    CdcMessageType,
    TriggerKind,
)
from duo_input.protocol.cobs import cobs_encode
from duo_input.protocol.frame import CdcFrame, encode_cdc_frame


def _wire(sequence: int, payload: bytes) -> bytes:
    return encode_cdc_frame(CdcFrame(CdcMessageType.PING, sequence, payload))


def _ping(sequence: int, payload: bytes) -> CdcFrame:
    """The frame ``_wire`` encodes, as the assembler must hand it back."""
    return CdcFrame(CdcMessageType.PING, sequence, payload)


def test_assembler_returns_nothing_until_the_delimiter_arrives():
    assembler = FrameAssembler()
    wire = _wire(1, b"hello")

    assert assembler.push(wire[:-1]).frames == ()
    assert assembler.pending == len(wire) - 1
    assert assembler.push(wire[-1:]).frames == (_ping(1, b"hello"),)
    assert assembler.pending == 0


def test_assembler_retains_partial_cobs_bytes_across_single_byte_reads():
    assembler = FrameAssembler()
    wire = _wire(9, bytes(range(200)))

    collected: list[CdcFrame] = []
    for index in range(len(wire)):
        collected.extend(assembler.push(wire[index : index + 1]).frames)

    assert collected == [_ping(9, bytes(range(200)))]
    assert collected[0].payload == bytes(range(200))


def test_assembler_splits_multiple_frames_delivered_in_one_read():
    assembler = FrameAssembler()

    scan = assembler.push(_wire(1, b"a") + _wire(2, b"bb"))

    assert scan.frames == (_ping(1, b"a"), _ping(2, b"bb"))
    assert scan.discarded == ()


def test_assembler_keeps_the_tail_of_a_split_pair_of_frames():
    assembler = FrameAssembler()
    first, second = _wire(3, b"abc"), _wire(4, b"defgh")
    stream = first + second
    cut = len(first) + 3

    assert assembler.push(stream[:cut]).frames == (_ping(3, b"abc"),)
    assert assembler.push(stream[cut:]).frames == (_ping(4, b"defgh"),)


def test_assembler_clear_drops_retained_bytes():
    assembler = FrameAssembler()
    assembler.push(_wire(1, b"partial")[:-1])
    assembler.clear()

    assert assembler.pending == 0


# ------------------------------------------------------- junk in the stream
#
# The device's CDC endpoint is shared with things that are not frames: the
# reference target's plain-text trace, and the tail of whatever the previous
# session left behind. Finding a frame's boundaries in that stream is this
# class's job. The decoder stays strict - a candidate that does not decode is
# not a frame, is dropped, and must not take the frame behind it with it.


def test_assembler_drops_junk_ahead_of_a_frame_and_keeps_the_frame():
    assembler = FrameAssembler()
    wire = _wire(7, b"payload")

    scan = assembler.push(b"MOUNT a=1 i=0\r\n\0" + wire)

    assert scan.frames == (_ping(7, b"payload"),)
    assert len(scan.discarded) == 1
    assert scan.discarded[0]


def test_assembler_drops_junk_between_two_frames():
    assembler = FrameAssembler()

    scan = assembler.push(_wire(1, b"a") + b"REPORT len=8\r\n\0" + _wire(2, b"b"))

    assert scan.frames == (_ping(1, b"a"), _ping(2, b"b"))
    assert len(scan.discarded) == 1


def test_assembler_drops_a_frame_whose_crc_is_wrong_and_keeps_scanning():
    """Built by hand rather than by damaging an encoded frame: COBS output
    never contains the delimiter, so a flipped byte could otherwise split the
    candidate instead of corrupting it."""
    assembler = FrameAssembler()
    raw = bytearray(b"DI")
    raw.extend((PROTOCOL_VERSION_MAJOR, PROTOCOL_VERSION_MINOR, int(CdcMessageType.PING), 0))
    raw.extend((1).to_bytes(2, "little"))
    raw.extend((4).to_bytes(2, "little"))
    raw.extend(b"abcd")
    raw.extend(b"\xde\xad\xbe\xef")  # not the CRC of anything
    damaged = cobs_encode(bytes(raw)) + b"\0"

    scan = assembler.push(damaged + _wire(2, b"efgh"))

    assert scan.frames == (_ping(2, b"efgh"),)
    assert len(scan.discarded) == 1


def test_assembler_returns_a_device_initiated_frame_with_no_request_outstanding():
    """Nothing about a frame's arrival depends on a request having been sent:
    CAPTURE_EVENT is written by the device on its own."""
    assembler = FrameAssembler()
    wire = encode_cdc_frame(CdcFrame(CdcMessageType.CAPTURE_EVENT, 12, b"\x00\x01\x02"))

    scan = assembler.push(b"LINK ans=1\r\n\0" + wire)

    assert scan.frames == (CdcFrame(CdcMessageType.CAPTURE_EVENT, 12, b"\x00\x01\x02"),)
    assert len(scan.discarded) == 1


def test_sequence_generator_increments_and_wraps_within_u16():
    generator = SequenceGenerator(0xFFFE)

    assert generator.next() == 0xFFFE
    assert generator.next() == 0xFFFF
    assert generator.next() == 0x0000
    assert generator.next() == 0x0001


def test_sequence_generator_resynchronises_after_a_device_initiated_frame():
    generator = SequenceGenerator(5)
    generator.next()
    generator.align_after(900)

    assert generator.next() == 901


def test_sequence_generator_rejects_out_of_range_start():
    with pytest.raises(ValueError):
        SequenceGenerator(0x10000)


def test_assembler_discards_a_stream_that_never_delimits():
    """A device emitting endless non-zero noise must not grow the buffer forever."""
    assembler = FrameAssembler()

    with pytest.raises(FrameOverflowError):
        assembler.push(b"\x01" * (MAX_PENDING_FRAME_BYTES + 1))

    assert assembler.pending == 0


def test_assembler_discards_noise_accumulated_over_many_reads():
    assembler = FrameAssembler()
    read = b"\x01" * 256

    with pytest.raises(FrameOverflowError):
        for _ in range(MAX_PENDING_FRAME_BYTES // len(read) + 1):
            assembler.push(read)

    assert assembler.pending == 0


def test_assembler_reads_back_to_back_delimiters_as_nothing_at_all():
    """Two delimiters in a row carry no bytes, so they are not a damaged
    frame - there is nothing there to be damaged. The reference target makes
    this reachable: every trace line now ends with a zero byte, and the
    device writes one more when it clears whatever was already in flight at
    the start of a conversation."""
    assembler = FrameAssembler()
    wire = _wire(5, b"reply")

    scan = assembler.push(b"LINK ans=1\r\n\0" + b"\0" + wire)

    assert scan.frames == (_ping(5, b"reply"),)
    assert len(scan.discarded) == 1, "the empty candidate is nothing, not junk"


def test_assembler_ignores_a_lone_delimiter_with_a_request_outstanding():
    """The empty candidate must not even be reported as discarded: the
    service fails an operation when a read carries junk and no frame, and a
    device that politely closed its trace has not damaged anything."""
    assembler = FrameAssembler()

    scan = assembler.push(b"\0\0\0")

    assert scan.frames == ()
    assert scan.discarded == ()
    assert assembler.pending == 0


def test_assembler_bound_survives_discarding_junk_candidate_after_candidate():
    """Junk that *is* delimited must not accumulate either: each candidate is
    dropped as it completes, so an endless trace never reaches the bound."""
    assembler = FrameAssembler()

    for _ in range(64):
        scan = assembler.push(b"REPORT a=1 i=0 len=8 00 00\r\n\0")
        assert scan.frames == ()
        assert len(scan.discarded) == 1
        assert assembler.pending == 0


def test_assembler_still_accepts_a_maximum_length_frame_split_across_reads():
    assembler = FrameAssembler()
    payload = bytes(range(256)) * (CDC_MAX_PAYLOAD // 256)
    wire = _wire(1, payload)

    collected: list[CdcFrame] = []
    for index in range(len(wire)):
        collected.extend(assembler.push(wire[index : index + 1]).frames)
        assert assembler.pending <= MAX_PENDING_FRAME_BYTES

    assert len(wire) <= MAX_PENDING_FRAME_BYTES
    assert collected == [_ping(1, payload)]
    assert len(collected[0].payload) == CDC_MAX_PAYLOAD


# ----------------------------------------------------------- capture parsing


def test_capture_event_carries_its_source():
    payload = struct.pack("<BBBHHB", TriggerKind.KEYBOARD_USAGE, 0x4F, 0x01,
                          0x3434, 0xD030, 1)
    trigger = parse_capture_event(payload)
    assert trigger.source == TriggerSource(0x3434, 0xD030, 1)


def test_short_capture_event_still_parses():
    # The emulator and older firmware send three bytes; refusing them would
    # break the compatibility matrix this repository ships.
    trigger = parse_capture_event(struct.pack("<BBB", TriggerKind.MOUSE_BUTTON, 4, 0))
    assert trigger.source is None


def test_an_all_zero_source_in_the_full_payload_reads_as_unknown():
    # The firmware sends exactly this whenever its source table could not
    # resolve the press - the same "unknown" the three-byte form means, and
    # the same convention the stored-binding source already uses.
    payload = struct.pack("<BBBHHB", TriggerKind.MOUSE_BUTTON, 4, 0, 0, 0, 0)

    trigger = parse_capture_event(payload)

    assert trigger.source is None


def test_a_partially_zero_source_is_refused():
    payload = struct.pack("<BBBHHB", TriggerKind.KEYBOARD_USAGE, 0x4F, 0x01,
                          0x3434, 0, 1)

    with pytest.raises(PayloadError):
        parse_capture_event(payload)
