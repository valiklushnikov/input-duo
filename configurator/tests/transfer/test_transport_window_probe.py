from __future__ import annotations

from collections.abc import Callable

import pytest

from duo_input.clipboard.wire import MAX_FILE_CHUNK_BYTES, Message, MessageType

from transport_window_probe import ProbeProtocolError, WindowRequester


MIB = MAX_FILE_CHUNK_BYTES


class Clock:
    def __init__(self) -> None:
        self.now_ns = 0

    def __call__(self) -> int:
        return self.now_ns

    def advance_ms(self, milliseconds: int) -> None:
        self.now_ns += milliseconds * 1_000_000


@pytest.fixture
def requester_factory(tmp_path):
    requesters = []

    def make(
        *,
        size: int = 8 * MIB,
        window: int = 4,
        clock: Callable[[], int] | None = None,
    ):
        sent: list[Message] = []
        output = tmp_path / f"output-{len(requesters)}.bin"
        requester = WindowRequester(
            transfer_id="transfer",
            entry_index=0,
            file_size=size,
            window=window,
            output=output,
            send=sent.append,
            clock=clock,
            run_name=f"run-{len(requesters)}",
        )
        requesters.append(requester)
        return requester, sent, output

    yield make

    for requester in requesters:
        requester.close()


def chunk_for(request: Message, payload: bytes | None = None) -> Message:
    length = request.header["length"]
    return Message(
        MessageType.FILE_CHUNK,
        {
            "transfer_id": request.header["transfer_id"],
            "entry_index": request.header["entry_index"],
            "read_id": request.header["read_id"],
            "offset": request.header["offset"],
        },
        payload if payload is not None else bytes([request.header["read_id"] % 251]) * length,
    )


def test_window_never_exceeds_configured_bound(requester_factory):
    requester, sent, _output = requester_factory(size=10 * MIB, window=4)

    requester.start()
    assert len(sent) == 4
    assert requester.max_outstanding_reads == 4
    assert requester.max_outstanding_bytes == 4 * MIB

    for request in list(sent):
        before = len(sent)
        requester.handle_message(chunk_for(request))
        assert requester.outstanding_reads <= 4
        assert len(sent) <= before + 1


def test_out_of_order_chunks_are_matched_and_written_by_offset(requester_factory):
    requester, sent, output = requester_factory(size=4 * MIB, window=4)
    requester.start()

    for request in (sent[2], sent[0], sent[3], sent[1]):
        requester.handle_message(chunk_for(request))

    assert requester.complete
    data = output.read_bytes()
    for request in sent:
        offset = request.header["offset"]
        assert data[offset : offset + MIB] == bytes([request.header["read_id"] % 251]) * MIB
    assert requester.missing_ranges == 0
    assert requester.overlapping_ranges == 0


def test_partial_final_chunk_is_requested_at_exact_remaining_length(requester_factory):
    requester, sent, output = requester_factory(size=2 * MIB + 17, window=4)
    requester.start()

    assert [message.header["length"] for message in sent] == [MIB, MIB, 17]
    for request in reversed(sent):
        requester.handle_message(chunk_for(request))

    assert requester.complete
    assert output.stat().st_size == 2 * MIB + 17


def test_duplicate_response_is_rejected(requester_factory):
    requester, sent, _output = requester_factory(size=2 * MIB, window=1)
    requester.start()
    response = chunk_for(sent[0])
    requester.handle_message(response)

    with pytest.raises(ProbeProtocolError, match="duplicate"):
        requester.handle_message(response)
    assert requester.duplicate_ranges == 1


def test_unknown_response_is_rejected(requester_factory):
    requester, _sent, _output = requester_factory(size=MIB, window=1)
    requester.start()
    unknown = Message(
        MessageType.FILE_CHUNK,
        {"transfer_id": "transfer", "entry_index": 0, "read_id": 999, "offset": 0},
        b"x",
    )

    with pytest.raises(ProbeProtocolError, match="unknown"):
        requester.handle_message(unknown)
    assert requester.unexpected_chunks == 1


def test_file_error_closes_only_when_it_matches_an_outstanding_request(requester_factory):
    requester, sent, _output = requester_factory(size=4 * MIB, window=4)
    requester.start()
    target = sent[2]
    unknown = Message(
        MessageType.FILE_ERROR,
        {
            "transfer_id": "transfer",
            "entry_index": 0,
            "read_id": 999,
            "offset": 0,
            "reason": "source_missing",
        },
        b"",
    )
    with pytest.raises(ProbeProtocolError, match="unknown"):
        requester.handle_message(unknown)
    assert not requester.failed

    correlated = Message(
        MessageType.FILE_ERROR,
        {
            "transfer_id": target.header["transfer_id"],
            "entry_index": target.header["entry_index"],
            "read_id": target.header["read_id"],
            "offset": target.header["offset"],
            "reason": "source_missing",
        },
        b"",
    )
    requester.handle_message(correlated)
    assert requester.failed
    assert requester.error_read_id == target.header["read_id"]


def test_disconnect_cancels_every_outstanding_read(requester_factory):
    requester, _sent, _output = requester_factory(size=8 * MIB, window=4)
    requester.start()
    assert requester.outstanding_reads == 4

    requester.disconnect("link lost")

    assert requester.failed
    assert requester.cancelled_read_count == 4
    assert requester.outstanding_reads == 0


def test_occupancy_accounts_for_time_at_each_outstanding_count(requester_factory):
    clock = Clock()
    requester, sent, _output = requester_factory(size=5 * MIB, window=4, clock=clock)
    requester.start()
    clock.advance_ms(10)
    requester.handle_message(chunk_for(sent[0]))
    clock.advance_ms(20)
    requester.handle_message(chunk_for(sent[1]))
    clock.advance_ms(30)
    for request in sent[2:]:
        if request.header["read_id"] in requester.pending_read_ids:
            requester.handle_message(chunk_for(request))

    occupancy = requester.occupancy_ns
    assert occupancy[4] >= 30_000_000
    assert requester.max_outstanding_reads == 4
