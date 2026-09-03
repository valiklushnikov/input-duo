"""Кадрирование: длина, тип, заголовок, сырые байты - и сборка из кусков."""

from __future__ import annotations

import pytest

from duo_input.clipboard.wire import (
    FrameAssembler,
    Message,
    MessageType,
    WireError,
    encode,
)


def test_a_message_survives_a_round_trip():
    message = Message(MessageType.OFFER, {"seq": 3}, b"")

    assembled = FrameAssembler().feed(encode(message))

    assert assembled == [message]


def test_content_carries_both_a_header_and_raw_bytes():
    message = Message(MessageType.CONTENT, {"seq": 3, "mime": "text/plain"}, b"\x00\xffhello")

    assembled = FrameAssembler().feed(encode(message))

    assert assembled[0].blob == b"\x00\xffhello"
    assert assembled[0].header["mime"] == "text/plain"


def test_a_frame_split_across_three_chunks_still_arrives():
    raw = encode(Message(MessageType.PING, {}, b""))
    assembler = FrameAssembler()

    assert assembler.feed(raw[:1]) == []
    assert assembler.feed(raw[1:4]) == []
    assert [message.type for message in assembler.feed(raw[4:])] == [MessageType.PING]


def test_two_frames_in_one_chunk_both_arrive():
    raw = encode(Message(MessageType.PING, {}, b"")) + encode(Message(MessageType.PONG, {}, b""))

    assert [m.type for m in FrameAssembler().feed(raw)] == [MessageType.PING, MessageType.PONG]


def test_an_oversized_length_is_refused_rather_than_allocated():
    raw = (99_999_999).to_bytes(4, "big") + bytes([MessageType.PING]) + b"\x00\x00"

    with pytest.raises(WireError, match="длин"):
        FrameAssembler().feed(raw)


def test_an_unknown_type_is_refused():
    raw = encode(Message(MessageType.PING, {}, b""))
    broken = raw[:4] + bytes([200]) + raw[5:]

    with pytest.raises(WireError, match="тип"):
        FrameAssembler().feed(broken)


def test_empty_header_is_accepted():
    message = Message(MessageType.PING, {}, b"")

    assembled = FrameAssembler().feed(encode(message))

    assert assembled[0].header == {}


def test_empty_blob_is_accepted():
    message = Message(MessageType.CONTENT, {"seq": 1, "mime": "text/plain"}, b"")

    assembled = FrameAssembler().feed(encode(message))

    assert assembled[0].blob == b""


def test_frame_shorter_than_its_own_header_is_refused():
    # Frame with declared payload length 2 but only 1 byte of payload (type byte only)
    raw = (1).to_bytes(4, "big") + bytes([MessageType.PING])

    with pytest.raises(WireError, match="короче"):
        FrameAssembler().feed(raw)


def test_header_that_does_not_fit_is_refused():
    raw = encode(Message(MessageType.PING, {}, b""))
    broken = raw[:6] + bytes([0xFF, 0xFF]) + raw[8:]

    with pytest.raises(WireError, match="не помещается"):
        FrameAssembler().feed(broken)


def test_non_json_header_is_refused():
    raw = (5).to_bytes(4, "big") + bytes([MessageType.PING]) + bytes([0, 2]) + b"xx"

    with pytest.raises(WireError, match="не разбирается"):
        FrameAssembler().feed(raw)


def test_non_object_header_is_refused():
    # Header that parses as a list instead of a dict
    header_bytes = b'[1]'
    payload = bytes([MessageType.PING]) + len(header_bytes).to_bytes(2, "big") + header_bytes
    raw = len(payload).to_bytes(4, "big") + payload

    with pytest.raises(WireError, match="не является объектом"):
        FrameAssembler().feed(raw)
