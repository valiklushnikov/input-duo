from __future__ import annotations

import ctypes

import spike_virtual_files as spike

from spike_com_vtable import (
    IID_IUNKNOWN,
    COMObject,
    add_ref,
    guid_from_string,
    query_interface,
    release,
)

S_OK = 0
E_NOINTERFACE = -2147467262  # 0x80004002


def test_a_fresh_object_starts_with_one_reference():
    obj = COMObject([IID_IUNKNOWN])

    assert obj.refcount == 1


def test_add_ref_and_release_walk_the_count_back_down():
    obj = COMObject([IID_IUNKNOWN])

    assert add_ref(obj.pointer) == 2
    assert add_ref(obj.pointer) == 3
    assert release(obj.pointer) == 2
    assert release(obj.pointer) == 1


def test_query_interface_for_a_supported_iid_succeeds_and_adds_a_reference():
    obj = COMObject([IID_IUNKNOWN])
    out = ctypes.c_void_p()

    result = query_interface(obj.pointer, guid_from_string(IID_IUNKNOWN), ctypes.byref(out))

    assert result == S_OK
    assert out.value == obj.pointer.value
    assert obj.refcount == 2


def test_query_interface_for_an_unsupported_iid_refuses_and_nulls_the_out_pointer():
    obj = COMObject([IID_IUNKNOWN])
    out = ctypes.c_void_p(0xDEAD)

    result = query_interface(
        obj.pointer,
        guid_from_string("{11111111-2222-3333-4444-555555555555}"),
        ctypes.byref(out),
    )

    assert result == E_NOINTERFACE
    assert out.value is None


def test_guid_round_trips_through_its_string_form():
    text = "{0000000C-0000-0000-C000-000000000046}"  # IID_IStream

    guid = guid_from_string(text)

    assert guid.Data1 == 0x0000000C
    assert guid.Data4[7] == 0x46


def test_the_synthetic_stream_returns_the_pattern_it_promises():
    stream = spike.StreamObject(entry_index=1, size=10)

    first = spike.stream_read(stream.pointer, 4)
    second = spike.stream_read(stream.pointer, 4)

    assert first == spike.synthetic_bytes(1, 0, 4)
    assert second == spike.synthetic_bytes(1, 4, 4)


def test_a_read_past_the_end_returns_only_what_is_left():
    stream = spike.StreamObject(entry_index=0, size=6)

    spike.stream_read(stream.pointer, 4)
    tail = spike.stream_read(stream.pointer, 4)

    assert len(tail) == 2, "Read may return fewer bytes than requested at end of stream."


def test_a_read_at_the_end_returns_nothing_rather_than_blocking():
    stream = spike.StreamObject(entry_index=0, size=2)

    spike.stream_read(stream.pointer, 2)

    assert spike.stream_read(stream.pointer, 2) == b""


def test_every_read_is_recorded_with_its_requested_and_returned_size():
    spike.READ_LOG.clear()
    stream = spike.StreamObject(entry_index=3, size=5)

    spike.stream_read(stream.pointer, 4)
    spike.stream_read(stream.pointer, 4)

    assert spike.READ_LOG == [(3, 0, 4, 4), (3, 4, 4, 1)]


def test_seeking_to_the_end_reports_the_size_explorer_was_promised():
    stream = spike.StreamObject(entry_index=2, size=4096)

    position = spike.stream_seek(stream.pointer, 0, spike.STREAM_SEEK_END)

    assert position == 4096
