"""Bridge IStream -> ChunkPipe without touching Qt or a socket.

There is no COM thread or Explorer here: the queue and request callback are
supplied directly so each bridge rule remains deterministic.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="COM is Windows-only")

from duo_input.transfer import windows_com
from duo_input.transfer.pipe import ChunkPipe
from duo_input.transfer.windows_com import (
    E_NOTIMPL,
    E_POINTER,
    S_OK,
    STG_E_INVALIDFUNCTION,
    STG_E_READFAULT,
    STREAM_SEEK_CUR,
    STREAM_SEEK_END,
    STREAM_SEEK_SET,
    PipeStream,
    call_stream_read,
    call_stream_seek,
    call_stream_stat,
)


def _stream(size=10, timeout=2.0):
    pipe = ChunkPipe(capacity_chunks=1)
    requested: list[tuple[int, int]] = []
    stream = PipeStream(
        pipe,
        size=size,
        request=lambda offset, length: requested.append((offset, length)),
        timeout=timeout,
    )
    return stream, pipe, requested


def test_a_read_asks_for_the_bytes_before_it_blocks():
    stream, pipe, requested = _stream()
    order: list[str] = []

    def responder() -> None:
        while not requested:
            time.sleep(0.005)
        order.append("requested")
        pipe.push(b"0123")

    thread = threading.Thread(target=responder, daemon=True)
    thread.start()
    payload, result = call_stream_read(stream.pointer, 4)
    thread.join(timeout=2.0)

    assert result == S_OK
    assert payload == b"0123"
    assert order == ["requested"]
    assert requested == [(0, 4)]


def test_bytes_already_in_the_pipe_are_taken_without_a_new_request():
    stream, pipe, requested = _stream()
    pipe.push(b"abcd")

    payload, result = call_stream_read(stream.pointer, 4)

    assert (payload, result) == (b"abcd", S_OK)
    assert requested == [], "a request was sent although the data was already queued"


def test_the_position_advances_by_what_was_actually_returned():
    stream, pipe, _requested = _stream()
    pipe.push(b"ab")

    call_stream_read(stream.pointer, 4)

    assert stream.position == 2


def test_a_partial_read_returns_S_OK_and_a_smaller_count():
    stream, pipe, _requested = _stream()
    pipe.push(b"ab")

    payload, result = call_stream_read(stream.pointer, 8)

    assert (len(payload), result) == (2, S_OK)


def test_a_read_at_the_end_of_the_file_returns_nothing_without_asking():
    stream, _pipe, requested = _stream(size=4)
    stream.position = 4

    payload, result = call_stream_read(stream.pointer, 8)

    assert (payload, result) == (b"", S_OK)
    assert requested == [], "a request was sent beyond the end of the file"


def test_a_position_beyond_the_end_also_returns_nothing_without_asking():
    stream, _pipe, requested = _stream(size=4)
    stream.position = 40

    payload, result = call_stream_read(stream.pointer, 8)

    assert (payload, result) == (b"", S_OK)
    assert requested == []


def test_an_end_of_file_read_clears_the_returned_byte_count():
    stream, _pipe, _requested = _stream(size=0)
    read = windows_com.wintypes.ULONG(99)

    result = windows_com._slot(stream.pointer, 3, windows_com._STREAM_READ)(
        stream.pointer, None, 4, ctypes.byref(read)
    )

    assert result == S_OK
    assert read.value == 0


def test_a_read_never_asks_for_more_than_remains_in_the_file():
    stream, _pipe, requested = _stream(size=6, timeout=0.1)
    stream.position = 4

    call_stream_read(stream.pointer, 1024)

    assert requested == [(4, 2)], "more bytes were requested than remain in the file"


def test_the_public_request_log_matches_external_callback_invocations():
    stream, _pipe, requested = _stream(size=6, timeout=0.01)

    call_stream_read(stream.pointer, 4)

    assert stream.requested == [(0, 4)]
    assert requested == stream.requested


def test_the_public_request_log_is_updated_as_the_callback_is_invoked():
    observations: list[tuple[list[tuple[int, int]], tuple[int, int]]] = []
    holder: list[PipeStream] = []

    def request(offset: int, length: int) -> None:
        observations.append((list(holder[0].requested), (offset, length)))

    stream = PipeStream(ChunkPipe(), size=4, request=request, timeout=0.01)
    holder.append(stream)

    call_stream_read(stream.pointer, 4)

    assert observations == [([(0, 4)], (0, 4))]


def test_a_closed_pipe_becomes_an_hresult_and_never_an_exception():
    stream, pipe, _requested = _stream()
    pipe.close("cancelled")

    payload, result = call_stream_read(stream.pointer, 4)

    assert result == STG_E_READFAULT
    assert payload == b""


def test_a_closed_pipe_clears_the_returned_byte_count():
    stream, pipe, _requested = _stream()
    pipe.close("cancelled")
    read = windows_com.wintypes.ULONG(99)
    buffer = (ctypes.c_char * 4)()

    result = windows_com._slot(stream.pointer, 3, windows_com._STREAM_READ)(
        stream.pointer, buffer, 4, ctypes.byref(read)
    )

    assert result == STG_E_READFAULT
    assert read.value == 0


def test_a_pipe_closed_while_a_read_is_blocked_unblocks_it_at_once():
    stream, pipe, _requested = _stream(timeout=30.0)
    results: list[int] = []

    def reader() -> None:
        _payload, result = call_stream_read(stream.pointer, 4)
        results.append(result)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    time.sleep(0.1)
    started = time.perf_counter()
    pipe.close("cancelled")
    thread.join(timeout=3.0)

    assert results == [STG_E_READFAULT]
    assert time.perf_counter() - started < 1.0, "closing waited for the timeout"


def test_a_read_that_times_out_fails_rather_than_hanging_for_ever():
    stream, _pipe, _requested = _stream(timeout=0.05)

    _payload, result = call_stream_read(stream.pointer, 4)

    assert result == STG_E_READFAULT


def test_the_default_timeout_is_passed_to_the_pipe_wait(monkeypatch):
    pipe = ChunkPipe()
    waited: list[float] = []
    stream = PipeStream(pipe, size=4, request=lambda *_: None)
    monkeypatch.setattr(pipe, "wait", lambda timeout: waited.append(timeout) or False)

    _payload, result = call_stream_read(stream.pointer, 4)

    assert result == STG_E_READFAULT
    assert waited == [30.0]


def test_a_finished_pipe_ends_the_stream_without_an_error():
    stream, pipe, _requested = _stream(size=1024)
    pipe.finish()

    payload, result = call_stream_read(stream.pointer, 4)

    assert (payload, result) == (b"", S_OK)


def test_seeking_to_the_end_reports_the_size_from_the_manifest():
    stream, _pipe, _requested = _stream(size=4096)

    position, result = call_stream_seek(stream.pointer, 0, STREAM_SEEK_END)

    assert (position, result) == (4096, S_OK)


def test_seeking_changes_where_the_next_read_asks_from():
    stream, _pipe, requested = _stream(size=100, timeout=0.05)

    call_stream_seek(stream.pointer, 40, STREAM_SEEK_SET)
    call_stream_read(stream.pointer, 8)

    assert requested == [(40, 8)]


def test_a_current_seek_is_relative_to_the_current_position():
    stream, _pipe, _requested = _stream(size=100)
    call_stream_seek(stream.pointer, 40, STREAM_SEEK_SET)

    position, result = call_stream_seek(stream.pointer, -5, STREAM_SEEK_CUR)

    assert (position, result) == (35, S_OK)
    assert stream.position == 35


def test_an_unknown_seek_origin_is_refused_without_moving():
    stream, _pipe, _requested = _stream(size=100)
    stream.position = 12

    _position, result = call_stream_seek(stream.pointer, 20, 99)

    assert result == STG_E_INVALIDFUNCTION
    assert stream.position == 12


def test_a_seek_before_the_start_is_refused_without_moving():
    stream, _pipe, _requested = _stream(size=100)
    stream.position = 12

    _position, result = call_stream_seek(stream.pointer, -20, STREAM_SEEK_CUR)

    assert result == STG_E_INVALIDFUNCTION
    assert stream.position == 12


def test_stat_reports_the_size_explorer_was_promised():
    stream, _pipe, _requested = _stream(size=7777)

    size, result = call_stream_stat(stream.pointer)

    assert (size, result) == (7777, S_OK)


def test_stat_refuses_a_null_output_pointer():
    stream, _pipe, _requested = _stream()

    result = windows_com._slot(stream.pointer, 12, windows_com._STREAM_STAT)(
        stream.pointer, None, 0
    )

    assert result == E_POINTER


def test_stat_identifies_the_object_as_a_stream():
    stream, _pipe, _requested = _stream()
    stat = windows_com.STATSTG()

    result = windows_com._slot(stream.pointer, 12, windows_com._STREAM_STAT)(
        stream.pointer, ctypes.byref(stat), 0
    )

    assert result == S_OK
    assert stat.type == 2


def test_optional_read_and_seek_output_pointers_may_be_null(monkeypatch):
    unraisable = []
    monkeypatch.setattr(sys, "unraisablehook", unraisable.append)
    stream, _pipe, _requested = _stream(size=0)

    read_result = windows_com._slot(stream.pointer, 3, windows_com._STREAM_READ)(
        stream.pointer, None, 0, None
    )
    seek_result = windows_com._slot(stream.pointer, 5, windows_com._STREAM_SEEK)(
        stream.pointer, 0, STREAM_SEEK_SET, None
    )

    assert (read_result, seek_result) == (S_OK, S_OK)
    assert unraisable == []


def test_a_read_with_data_may_omit_the_optional_count_pointer(monkeypatch):
    unraisable = []
    monkeypatch.setattr(sys, "unraisablehook", unraisable.append)
    stream, pipe, _requested = _stream()
    pipe.push(b"ab")
    buffer = (ctypes.c_char * 2)()

    result = windows_com._slot(stream.pointer, 3, windows_com._STREAM_READ)(
        stream.pointer, buffer, 2, None
    )

    assert result == S_OK
    assert bytes(buffer) == b"ab"
    assert unraisable == []


def test_the_istream_slots_keep_the_standard_read_only_results():
    stream, _pipe, _requested = _stream()
    calls = [
        (4, windows_com._STREAM_READ, (None, 0, None), STG_E_INVALIDFUNCTION),
        (6, windows_com._STREAM_SETSIZE, (0,), STG_E_INVALIDFUNCTION),
        (7, windows_com._STREAM_COPYTO, (None, 0, None, None), E_NOTIMPL),
        (8, windows_com._STREAM_COMMIT, (0,), S_OK),
        (9, windows_com._STREAM_REVERT, (), S_OK),
        (10, windows_com._STREAM_LOCK, (0, 0, 0), E_NOTIMPL),
        (11, windows_com._STREAM_LOCK, (0, 0, 0), E_NOTIMPL),
        (13, windows_com._STREAM_CLONE, (None,), E_NOTIMPL),
    ]

    results = [
        windows_com._slot(stream.pointer, slot, prototype)(stream.pointer, *args)
        for slot, prototype, args, _expected in calls
    ]

    assert results == [expected for *_call, expected in calls]


def test_istream_lock_slots_do_not_replace_the_reference_count_mutex():
    stream, _pipe, _requested = _stream()

    acquired = stream._lock.acquire(blocking=False)
    if acquired:
        stream._lock.release()

    assert acquired
    assert callable(stream._lock_region)
    assert callable(stream._unlock_region)


def test_the_last_release_notifies_the_owner_that_this_stream_is_done():
    pipe = ChunkPipe()
    done: list[int] = []
    stream = PipeStream(
        pipe,
        size=4,
        request=lambda *_: None,
        on_release=lambda: done.append(1),
    )

    from duo_input.transfer.windows_com import call_release

    call_release(stream.pointer)

    assert done == [1]


def test_the_stream_module_touches_no_qt_object():
    import duo_input.transfer.windows_com as module

    assert not hasattr(module, "QObject")
    assert "PySide6" not in str(module.__dict__.keys())
