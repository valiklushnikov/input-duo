from __future__ import annotations

import ctypes
import pytest

import spike_virtual_files as spike
from spike_com_vtable import E_NOINTERFACE, guid_from_string, query_interface, release


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


def _async_capability_pointer(obj):
    out = ctypes.c_void_p()

    assert query_interface(
        obj.pointer, guid_from_string(spike.IID_IASYNCCAPABILITY), ctypes.byref(out)
    ) == spike.S_OK
    assert out.value != obj.pointer.value
    return out


def test_async_capability_is_a_distinct_iunknown_derived_interface(monkeypatch):
    monkeypatch.setattr(spike, "register_format", lambda _name: 1)
    enabled = spike.DataObject(async_capability=True)
    disabled = spike.DataObject()

    async_pointer = _async_capability_pointer(enabled)
    assert async_pointer.value != enabled.pointer.value
    out = ctypes.c_void_p()
    assert query_interface(
        disabled.pointer, guid_from_string(spike.IID_IASYNCCAPABILITY), ctypes.byref(out)
    ) == E_NOINTERFACE
    assert out.value is None


def test_async_capability_uses_32_bit_win32_bool_abi():
    bool_pointer = ctypes.POINTER(ctypes.wintypes.BOOL)

    assert ctypes.sizeof(ctypes.wintypes.BOOL) == 4
    assert spike._SETASYNC._argtypes_ == (ctypes.c_void_p, ctypes.wintypes.BOOL)
    assert spike._GETASYNC._argtypes_ == (ctypes.c_void_p, bool_pointer)
    assert spike._INOP._argtypes_ == (ctypes.c_void_p, bool_pointer)


def test_async_capability_vtable_slots_track_mode_and_every_lifecycle_call(monkeypatch):
    monkeypatch.setattr(spike, "register_format", lambda _name: 1)
    spike.LIFECYCLE_LOG.clear()
    obj = spike.DataObject(async_capability=True)
    async_pointer = _async_capability_pointer(obj)
    mode = ctypes.wintypes.BOOL()

    assert spike._stream_slot(async_pointer, 3, spike._SETASYNC)(
        async_pointer, ctypes.wintypes.BOOL(1)
    ) == spike.S_OK
    assert spike._stream_slot(async_pointer, 4, spike._GETASYNC)(
        async_pointer, ctypes.byref(mode)
    ) == spike.S_OK
    assert mode.value == 1
    assert spike._stream_slot(async_pointer, 5, spike._STARTOP)(async_pointer, None) == spike.S_OK
    assert spike._stream_slot(async_pointer, 6, spike._INOP)(
        async_pointer, ctypes.byref(mode)
    ) == spike.S_OK
    assert mode.value == 1
    assert spike._stream_slot(async_pointer, 7, spike._ENDOP)(
        async_pointer, spike.S_OK, None, spike.DROPEFFECT_COPY
    ) == spike.S_OK
    assert spike._stream_slot(async_pointer, 6, spike._INOP)(
        async_pointer, ctypes.byref(mode)
    ) == spike.S_OK

    assert mode.value == 0
    assert [event for event, _thread_id in spike.LIFECYCLE_LOG] == [
        "SetAsyncMode(1)",
        "GetAsyncMode",
        "StartOperation",
        "InOperation",
        "EndOperation(hResult=0x00000000, effects=1)",
        "InOperation",
    ]


def test_async_mode_retains_one_reference_until_end_operation(monkeypatch):
    monkeypatch.setattr(spike, "register_format", lambda _name: 1)
    obj = spike.DataObject(async_capability=True)
    async_pointer = _async_capability_pointer(obj)

    assert obj.refcount == 2
    assert spike._stream_slot(async_pointer, 3, spike._SETASYNC)(
        async_pointer, ctypes.wintypes.BOOL(1)
    ) == spike.S_OK
    assert obj.refcount == 3
    assert spike._stream_slot(async_pointer, 3, spike._SETASYNC)(
        async_pointer, ctypes.wintypes.BOOL(1)
    ) == spike.S_OK
    assert obj.refcount == 3
    assert spike._stream_slot(async_pointer, 7, spike._ENDOP)(
        async_pointer, spike.S_OK, None, spike.DROPEFFECT_COPY
    ) == spike.S_OK
    assert obj.refcount == 2
    assert spike._stream_slot(async_pointer, 7, spike._ENDOP)(
        async_pointer, spike.S_OK, None, spike.DROPEFFECT_COPY
    ) == spike.S_OK
    assert obj.refcount == 2
    assert release(async_pointer) == 1


def test_enum_format_etc_pointer_enumerates_advertised_formats_with_com_semantics(monkeypatch):
    monkeypatch.setattr(spike, "register_format", lambda name: {"FileGroupDescriptorW": 10, "FileContents": 11, "Preferred DropEffect": 12}[name])
    obj = spike.DataObject()
    out = ctypes.c_void_p()
    assert spike._data_object_slot(obj.pointer, 8, spike._ENUM)(obj.pointer, spike.DATADIR_GET, ctypes.byref(out)) == spike.S_OK
    enum_pointer = out
    fetched = ctypes.wintypes.ULONG()
    formats = (spike.FORMATETC * 3)()
    assert spike._enum_slot(enum_pointer, 3, spike._ENUM_NEXT)(enum_pointer, 3, formats, ctypes.byref(fetched)) == spike.S_OK
    assert fetched.value == 3
    assert [(fmt.cfFormat, fmt.dwAspect, fmt.lindex, fmt.tymed) for fmt in formats] == [
        (10, spike.DVASPECT_CONTENT, -1, spike.TYMED_HGLOBAL),
        (11, spike.DVASPECT_CONTENT, -1, spike.TYMED_ISTREAM),
        (12, spike.DVASPECT_CONTENT, -1, spike.TYMED_HGLOBAL),
    ]


def test_enum_format_etc_supports_partial_next_skip_reset_clone_and_pointer_validation(monkeypatch):
    monkeypatch.setattr(spike, "register_format", lambda _name: 1)
    obj = spike.DataObject()
    out = ctypes.c_void_p()
    assert spike._data_object_slot(obj.pointer, 8, spike._ENUM)(obj.pointer, spike.DATADIR_GET, ctypes.byref(out)) == spike.S_OK
    enum_pointer = out
    one = (spike.FORMATETC * 1)()
    fetched = ctypes.wintypes.ULONG()
    assert spike._enum_slot(enum_pointer, 3, spike._ENUM_NEXT)(enum_pointer, 1, one, ctypes.byref(fetched)) == spike.S_OK
    assert spike._enum_slot(enum_pointer, 4, spike._ENUM_SKIP)(enum_pointer, 99) == spike.S_FALSE
    assert spike._enum_slot(enum_pointer, 5, spike._ENUM_RESET)(enum_pointer) == spike.S_OK
    clone = ctypes.c_void_p()
    assert spike._enum_slot(enum_pointer, 6, spike._ENUM_CLONE)(enum_pointer, ctypes.byref(clone)) == spike.S_OK
    assert clone.value
    assert spike._enum_slot(enum_pointer, 3, spike._ENUM_NEXT)(enum_pointer, 2, one, None) == spike.E_POINTER
    assert spike._enum_slot(enum_pointer, 3, spike._ENUM_NEXT)(enum_pointer, 1, None, ctypes.byref(fetched)) == spike.E_POINTER


def test_enum_format_etc_uses_shell_normalized_single_file_contents_format(monkeypatch):
    monkeypatch.setattr(spike, "register_format", lambda name: {"FileGroupDescriptorW": 10, "FileContents": 11, "Preferred DropEffect": 12}[name])
    obj = spike.DataObject()
    out = ctypes.c_void_p()
    assert spike._data_object_slot(obj.pointer, 8, spike._ENUM)(obj.pointer, spike.DATADIR_GET, ctypes.byref(out)) == spike.S_OK
    formats = (spike.FORMATETC * 3)()
    fetched = ctypes.wintypes.ULONG()
    assert spike._enum_slot(out, 3, spike._ENUM_NEXT)(out, 3, formats, ctypes.byref(fetched)) == spike.S_OK
    assert fetched.value == 3
    assert [(fmt.cfFormat, fmt.lindex, fmt.tymed) for fmt in formats] == [
        (10, -1, spike.TYMED_HGLOBAL),
        (11, -1, spike.TYMED_ISTREAM),
        (12, -1, spike.TYMED_HGLOBAL),
    ]


def test_enum_format_etc_methods_log_thread_tagged_events(monkeypatch):
    messages = []
    monkeypatch.setattr(spike, "log", messages.append)
    monkeypatch.setattr(spike, "register_format", lambda _name: 1)
    obj = spike.DataObject()
    out = ctypes.c_void_p()
    assert spike._data_object_slot(obj.pointer, 8, spike._ENUM)(obj.pointer, spike.DATADIR_GET, ctypes.byref(out)) == spike.S_OK
    formats = (spike.FORMATETC * 1)()
    fetched = ctypes.wintypes.ULONG()
    spike._enum_slot(out, 3, spike._ENUM_NEXT)(out, 1, formats, ctypes.byref(fetched))
    spike._enum_slot(out, 4, spike._ENUM_SKIP)(out, 1)
    spike._enum_slot(out, 5, spike._ENUM_RESET)(out)
    clone = ctypes.c_void_p()
    spike._enum_slot(out, 6, spike._ENUM_CLONE)(out, ctypes.byref(clone))
    assert [message.split("(", 1)[0] for message in messages if message.startswith(("IEnumFORMATETC",))] == [
        "IEnumFORMATETC::Next",
        "IEnumFORMATETC::Skip",
        "IEnumFORMATETC::Reset",
        "IEnumFORMATETC::Clone",
    ]
