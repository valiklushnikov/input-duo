"""Framing and sequence bookkeeping used by the asynchronous device service."""

from __future__ import annotations

import pytest

from duo_input.device.transactions import (
    MAX_PENDING_FRAME_BYTES,
    FrameAssembler,
    FrameOverflowError,
    SequenceGenerator,
)
from duo_input.generated.protocol import CDC_MAX_PAYLOAD, CdcMessageType
from duo_input.protocol.frame import CdcFrame, decode_cdc_frame, encode_cdc_frame


def _frame(sequence: int, payload: bytes) -> bytes:
    return encode_cdc_frame(CdcFrame(CdcMessageType.PING, sequence, payload))


def test_assembler_returns_nothing_until_the_delimiter_arrives():
    assembler = FrameAssembler()
    wire = _frame(1, b"hello")

    assert assembler.push(wire[:-1]) == []
    assert assembler.pending == len(wire) - 1
    assert assembler.push(wire[-1:]) == [wire]
    assert assembler.pending == 0


def test_assembler_retains_partial_cobs_bytes_across_single_byte_reads():
    assembler = FrameAssembler()
    wire = _frame(9, bytes(range(200)))

    collected: list[bytes] = []
    for index in range(len(wire)):
        collected.extend(assembler.push(wire[index : index + 1]))

    assert collected == [wire]
    assert decode_cdc_frame(collected[0]).payload == bytes(range(200))


def test_assembler_splits_multiple_frames_delivered_in_one_read():
    assembler = FrameAssembler()
    first, second = _frame(1, b"a"), _frame(2, b"bb")

    assert assembler.push(first + second) == [first, second]


def test_assembler_keeps_the_tail_of_a_split_pair_of_frames():
    assembler = FrameAssembler()
    first, second = _frame(3, b"abc"), _frame(4, b"defgh")
    stream = first + second
    cut = len(first) + 3

    assert assembler.push(stream[:cut]) == [first]
    assert assembler.push(stream[cut:]) == [second]


def test_assembler_clear_drops_retained_bytes():
    assembler = FrameAssembler()
    assembler.push(_frame(1, b"partial")[:-1])
    assembler.clear()

    assert assembler.pending == 0


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


def test_assembler_still_accepts_a_maximum_length_frame_split_across_reads():
    assembler = FrameAssembler()
    wire = _frame(1, bytes(range(256)) * (CDC_MAX_PAYLOAD // 256))

    collected: list[bytes] = []
    for index in range(len(wire)):
        collected.extend(assembler.push(wire[index : index + 1]))
        assert assembler.pending <= MAX_PENDING_FRAME_BYTES

    assert len(wire) <= MAX_PENDING_FRAME_BYTES
    assert collected == [wire]
    assert len(decode_cdc_frame(collected[0]).payload) == CDC_MAX_PAYLOAD
