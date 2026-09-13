"""Кадрирование: длина, тип, заголовок, сырые байты - и сборка из кусков."""

from __future__ import annotations

import pytest

from duo_input.clipboard.wire import (
    FrameAssembler,
    MAX_FRAME_BYTES,
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
    # Frame with declared payload length 1 but only 1 byte of payload (type byte only)
    raw = (1).to_bytes(4, "big") + bytes([MessageType.PING])

    with pytest.raises(WireError):
        FrameAssembler().feed(raw)


def test_empty_payload_is_refused():
    # Frame with declared payload length 0: just 4 bytes of length field with value 0
    raw = (0).to_bytes(4, "big")

    with pytest.raises(WireError):
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


from duo_input.clipboard.wire import MAX_FILE_CHUNK_BYTES


def test_the_six_file_message_types_have_the_numbers_the_protocol_promises():
    assert (
        MessageType.FILE_OFFER,
        MessageType.TRANSFER_BEGIN,
        MessageType.FILE_READ,
        MessageType.FILE_CHUNK,
        MessageType.FILE_ERROR,
        MessageType.TRANSFER_END,
    ) == (10, 11, 12, 13, 14, 15)


def test_no_message_type_number_is_used_twice():
    numbers = [int(member) for member in MessageType]

    assert len(numbers) == len(set(numbers))


def test_a_file_chunk_survives_the_round_trip_with_its_bytes_intact():
    payload = bytes(range(256)) * 16
    message = Message(
        MessageType.FILE_CHUNK,
        {"transfer_id": "t", "entry_index": 2, "offset": 4096},
        payload,
    )

    assembler = FrameAssembler()
    [decoded] = assembler.feed(encode(message))

    assert decoded == message


def test_the_chunk_ceiling_is_far_below_the_frame_ceiling():
    # Потолок кадра ловит испорченное поле длины; потолок чанка ограничивает
    # память. Если бы это было одно число, один FILE_CHUNK нёс бы 32 МиБ.
    assert MAX_FILE_CHUNK_BYTES == 1_048_576
    assert MAX_FILE_CHUNK_BYTES * 8 < MAX_FRAME_BYTES


def test_a_chunk_at_the_ceiling_still_fits_in_one_frame():
    message = Message(MessageType.FILE_CHUNK, {"offset": 0}, b"x" * MAX_FILE_CHUNK_BYTES)

    assembler = FrameAssembler()
    [decoded] = assembler.feed(encode(message))

    assert len(decoded.blob) == MAX_FILE_CHUNK_BYTES
