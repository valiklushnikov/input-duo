from __future__ import annotations

import ctypes
import threading

import pytest

import spike_virtual_files as spike
from spike_com_vtable import E_NOINTERFACE, guid_from_string, query_interface


def test_global_memory_api_is_pointer_sized_and_rejects_allocation_failures():
    assert spike._GLOBAL_ALLOC.restype is ctypes.wintypes.HGLOBAL
    assert spike._GLOBAL_ALLOC.argtypes == [ctypes.wintypes.UINT, ctypes.c_size_t]
    assert spike._GLOBAL_LOCK.restype is ctypes.c_void_p
    assert spike._GLOBAL_LOCK.argtypes == [ctypes.wintypes.HGLOBAL]

    class FailingKernel32:
        GlobalAlloc = staticmethod(lambda *_args: 0)

    with pytest.raises(MemoryError, match="GlobalAlloc"):
        spike.to_hglobal(b"payload", kernel32=FailingKernel32())

    class FailingLockKernel32:
        GlobalAlloc = staticmethod(lambda *_args: 0x1234)
        GlobalLock = staticmethod(lambda *_args: 0)

    with pytest.raises(MemoryError, match="GlobalLock"):
        spike.to_hglobal(b"payload", kernel32=FailingLockKernel32())


def test_unimplemented_data_object_callbacks_are_logged(monkeypatch):
    messages = []
    monkeypatch.setattr(spike, "log", messages.append)
    obj = object.__new__(spike.DataObject)

    assert obj._get_data_here(None, None, None) == spike.E_NOTIMPL
    assert obj._get_canonical(None, None, None) == spike.E_NOTIMPL
    assert obj._set_data(None, None, None, None) == spike.E_NOTIMPL
    assert obj._d_advise(None, None, 0, None, None) == spike.E_NOTIMPL
    assert obj._d_unadvise(None, None) == spike.E_NOTIMPL
    assert obj._enum_d_advise(None, None) == spike.E_NOTIMPL

    assert [message.split("()", 1)[0] for message in messages] == [
        "GetDataHere",
        "GetCanonicalFormatEtc",
        "SetData",
        "DAdvise",
        "DUnadvise",
        "EnumDAdvise",
    ]


def test_query_get_data_rejects_file_contents_without_an_istream_medium():
    obj = object.__new__(spike.DataObject)
    obj.cf_descriptor = 97
    obj.cf_drop_effect = 98
    obj.cf_contents = 99
    fmt = spike.FORMATETC(99, None, 0, 1, spike.TYMED_HGLOBAL)

    result = obj._query_get_data(None, ctypes.byref(fmt))

    assert result == spike.DV_E_TYMED


def test_stat_reports_the_virtual_size_and_records_the_entry():
    spike.STAT_LOG.clear()
    stream = spike.StreamObject(entry_index=2, size=4096)
    stat = spike.STATSTG()

    result = spike._stream_slot(stream.pointer, 12, spike._STAT)(
        stream.pointer, ctypes.byref(stat), 0
    )

    assert result == spike.S_OK
    assert stat.type == 2
    assert stat.cbSize == 4096
    assert spike.STAT_LOG == [2]


def test_seek_records_the_entry_origin_and_offset():
    spike.SEEK_LOG.clear()
    stream = spike.StreamObject(entry_index=1, size=100)

    position = spike.stream_seek(stream.pointer, 13, spike.STREAM_SEEK_CUR)

    assert position == 13
    assert spike.SEEK_LOG == [(1, spike.STREAM_SEEK_CUR, 13)]


def test_file_contents_returns_an_istream_medium():
    obj = object.__new__(spike.DataObject)
    obj.cf_descriptor = 97
    obj.cf_drop_effect = 98
    obj.cf_contents = 99
    obj.get_data_calls = []
    obj.streams = []
    fmt = spike.FORMATETC(99, None, 0, 1, spike.TYMED_ISTREAM)
    medium = spike.STGMEDIUM()

    result = obj._get_data(None, ctypes.byref(fmt), ctypes.byref(medium))

    assert result == spike.S_OK
    assert medium.tymed == spike.TYMED_ISTREAM
    assert medium.data == obj.streams[0].pointer.value
    assert medium.pUnkForRelease is None


def test_file_contents_keeps_the_returned_stream_alive():
    obj = object.__new__(spike.DataObject)
    obj.cf_descriptor = 97
    obj.cf_drop_effect = 98
    obj.cf_contents = 99
    obj.get_data_calls = []
    obj.streams = []
    fmt = spike.FORMATETC(99, None, 0, 1, spike.TYMED_ISTREAM)
    medium = spike.STGMEDIUM()

    assert obj._get_data(None, ctypes.byref(fmt), ctypes.byref(medium)) == spike.S_OK

    retained = obj.streams[0]
    assert spike.stream_read(medium.data, 4) == spike.synthetic_bytes(1, 0, 4)
    assert retained.pointer.value == medium.data


def test_async_capability_is_advertised_only_when_the_flag_enables_it(monkeypatch):
    monkeypatch.setattr(spike, "register_format", lambda _name: 1)
    enabled = spike.DataObject(async_capability=True)
    disabled = spike.DataObject()
    out = ctypes.c_void_p()

    assert query_interface(
        enabled.pointer, guid_from_string(spike.IID_IASYNCCAPABILITY), ctypes.byref(out)
    ) == spike.S_OK
    assert out.value == enabled.pointer.value
    assert query_interface(
        disabled.pointer, guid_from_string(spike.IID_IASYNCCAPABILITY), ctypes.byref(out)
    ) == E_NOINTERFACE
    assert out.value is None


def test_async_capability_vtable_tracks_mode_and_operation_lifecycle(monkeypatch):
    monkeypatch.setattr(spike, "register_format", lambda _name: 1)
    spike.LIFECYCLE_LOG.clear()
    obj = spike.DataObject(async_capability=True)
    mode = ctypes.c_short()

    assert spike._stream_slot(obj.pointer, 12, spike._SETASYNC)(
        obj.pointer, spike.VARIANT_TRUE
    ) == spike.S_OK
    assert spike._stream_slot(obj.pointer, 13, spike._GETASYNC)(
        obj.pointer, ctypes.byref(mode)
    ) == spike.S_OK
    assert mode.value == spike.VARIANT_TRUE
    assert spike._stream_slot(obj.pointer, 14, spike._STARTOP)(obj.pointer, None) == spike.S_OK
    assert spike._stream_slot(obj.pointer, 15, spike._INOP)(
        obj.pointer, ctypes.byref(mode)
    ) == spike.S_OK
    assert mode.value == spike.VARIANT_TRUE
    assert spike._stream_slot(obj.pointer, 16, spike._ENDOP)(
        obj.pointer, spike.S_OK, None, spike.DROPEFFECT_COPY
    ) == spike.S_OK
    assert spike._stream_slot(obj.pointer, 15, spike._INOP)(
        obj.pointer, ctypes.byref(mode)
    ) == spike.S_OK

    assert mode.value == spike.VARIANT_FALSE
    assert [event for event, _thread_id in spike.LIFECYCLE_LOG] == [
        "SetAsyncMode(-1)",
        "GetAsyncMode",
        "StartOperation",
        "EndOperation(hResult=0x00000000, effects=1)",
    ]
    assert {thread_id for _event, thread_id in spike.LIFECYCLE_LOG} == {threading.get_ident()}
