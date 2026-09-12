from __future__ import annotations

import ctypes
import sys

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


def test_enum_format_etc_advertises_a_zero_based_file_contents_lindex(monkeypatch):
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
        (11, spike.DVASPECT_CONTENT, 0, spike.TYMED_ISTREAM),
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


def test_format_name_resolves_predefined_registered_and_unknown_identifiers():
    registered = spike.register_format("DuoInputSpikeProbeFormat")

    assert spike.format_name(15) == "CF_HDROP"
    assert spike.format_name(registered) == "DuoInputSpikeProbeFormat"
    assert spike.format_name(0xBFFF) == "#49151"


def test_query_get_data_logs_the_resolved_format_name_and_tymed(monkeypatch):
    messages = []
    monkeypatch.setattr(spike, "log", messages.append)
    obj = spike.DataObject()
    fmt = spike.FORMATETC(obj.cf_descriptor, None, spike.DVASPECT_CONTENT, -1, spike.TYMED_HGLOBAL)

    assert obj._query_get_data(None, ctypes.byref(fmt)) == spike.S_OK

    assert (
        f"QueryGetData(cfFormat={obj.cf_descriptor} (FileGroupDescriptorW), "
        f"lindex=-1, tymed={spike.TYMED_HGLOBAL})" in messages
    )


def test_get_data_logs_the_resolved_format_name(monkeypatch):
    messages = []
    monkeypatch.setattr(spike, "log", messages.append)
    obj = spike.DataObject()
    fmt = spike.FORMATETC(obj.cf_drop_effect, None, spike.DVASPECT_CONTENT, -1, spike.TYMED_HGLOBAL)
    medium = spike.STGMEDIUM()

    assert obj._get_data(None, ctypes.byref(fmt), ctypes.byref(medium)) == spike.S_OK

    assert (
        f"GetData(cfFormat={obj.cf_drop_effect} (Preferred DropEffect), "
        f"lindex=-1, tymed={spike.TYMED_HGLOBAL})" in messages
    )


def test_enumerate_formats_collects_every_entry_and_releases_the_enumerator():
    obj = spike.DataObject()

    formats = spike.enumerate_formats(obj.pointer)

    assert [(fmt.cfFormat, fmt.lindex, fmt.tymed) for fmt in formats] == [
        (obj.cf_descriptor, -1, spike.TYMED_HGLOBAL),
        (obj.cf_contents, 0, spike.TYMED_ISTREAM),
        (obj.cf_drop_effect, -1, spike.TYMED_HGLOBAL),
    ]
    assert obj._enumerators[-1].refcount == 0


def test_enumerate_formats_keeps_calling_next_across_batches(monkeypatch):
    monkeypatch.setattr(spike, "INSPECT_BATCH", 2)
    obj = spike.DataObject()

    formats = spike.enumerate_formats(obj.pointer)

    assert len(formats) == 3


def test_probe_query_get_data_records_the_answer_for_every_probe(monkeypatch):
    obj = spike.DataObject()
    messages = []
    monkeypatch.setattr(spike, "log", messages.append)

    answers = dict(
        ((name, lindex, tymed), hresult)
        for name, lindex, tymed, hresult in spike.probe_query_get_data(obj.pointer)
    )

    # Each probed format is registered once, not once per probe row.
    assert len([m for m in messages if m.startswith("RegisterClipboardFormatW")]) == 2
    assert len(answers) == len(spike.INSPECT_PROBES)
    assert answers[("FileGroupDescriptorW", -1, spike.TYMED_HGLOBAL)] == spike.S_OK
    # Entry 0 is the Photos directory, so it has no contents; entry 1 is a file.
    assert answers[("FileContents", 0, spike.TYMED_ISTREAM)] == spike.DV_E_FORMATETC
    assert answers[("FileContents", 1, spike.TYMED_ISTREAM)] == spike.S_OK
    assert answers[("FileContents", 0, spike.TYMED_HGLOBAL)] == spike.DV_E_TYMED


def test_async_capability_probe_detects_support_and_releases_the_interface():
    enabled = spike.DataObject(async_capability=True)
    start = enabled.refcount

    assert spike.supports_async_capability(enabled.pointer) is True

    assert enabled.refcount == start
    assert spike.supports_async_capability(spike.DataObject().pointer) is False


def test_inspect_clipboard_dumps_the_foreign_shape_and_releases_the_object(monkeypatch):
    messages = []
    monkeypatch.setattr(spike, "log", messages.append)
    obj = spike.DataObject()
    start = obj.refcount

    def fake_get_data_object():
        # OleGetClipboard hands back a reference the caller must release.
        spike._data_object_slot(obj.pointer, 1, spike._REFCOUNT)(obj.pointer)
        return spike.S_OK, obj.pointer

    assert spike.inspect_clipboard(get_data_object=fake_get_data_object) == 0

    assert obj.refcount == start
    dump = "\n".join(messages)
    assert f"cfFormat={obj.cf_descriptor} (FileGroupDescriptorW)" in dump
    assert f"cfFormat={obj.cf_contents} (FileContents)" in dump
    assert "dwAspect=1 lindex=0 tymed=4" in dump
    assert "QueryGetData(FileContents, lindex=1, tymed=4) -> 0x00000000" in dump
    assert "QueryGetData(FileContents, lindex=0, tymed=4) -> 0x80040064" in dump
    assert "QueryGetData(FileContents, lindex=0, tymed=1) -> 0x80040069" in dump
    assert "IDataObjectAsyncCapability: False" in dump


def test_inspect_clipboard_reports_a_failed_ole_get_clipboard(monkeypatch):
    monkeypatch.setattr(spike, "log", lambda _message: None)

    assert spike.inspect_clipboard(get_data_object=lambda: (spike.E_POINTER, ctypes.c_void_p())) == 1


def test_inspect_clipboard_flag_is_reachable_from_the_real_argument_parser(monkeypatch):
    calls = []
    monkeypatch.setattr(spike, "ole_initialize", lambda: calls.append("ole_initialize"))
    monkeypatch.setattr(spike, "inspect_clipboard", lambda: calls.append("inspect_clipboard") or 7)
    monkeypatch.setattr(spike, "DataObject", lambda **_kwargs: pytest.fail("must not publish"))
    monkeypatch.setattr(sys, "argv", ["spike_virtual_files.py", "--inspect-clipboard"])

    assert spike.main() == 7

    assert calls == ["ole_initialize", "inspect_clipboard"]


def test_settle_pumps_then_marks_the_automatic_probe_off_from_human_requests(monkeypatch):
    messages = []
    monkeypatch.setattr(spike, "log", messages.append)
    monkeypatch.setattr(spike, "SETTLE_SECONDS", 0.02)
    obj = spike.DataObject()
    obj.get_data_calls.append((obj.cf_drop_effect, -1))
    pumped = []

    automatic = spike.settle(obj, lambda: pumped.append("pump"), sleep=lambda _seconds: None)

    assert automatic == [(obj.cf_drop_effect, -1)]
    assert pumped
    assert any("clipboard monitors settled" in message for message in messages)
    assert any("every call below is yours" in message for message in messages)


def test_main_settles_before_inviting_a_paste(monkeypatch):
    order = []
    ole32 = ctypes.windll.ole32
    monkeypatch.setattr(spike, "ole_initialize", lambda: None)
    monkeypatch.setattr(spike, "register_format", lambda _name: 1)
    monkeypatch.setattr(ole32, "OleSetClipboard", lambda _pointer: 0)
    monkeypatch.setattr(ole32, "OleFlushClipboard", lambda: 0)
    monkeypatch.setattr(spike, "settle", lambda _obj, _pump: order.append("settle") or [])
    monkeypatch.setattr(spike, "run", lambda _seconds, _pump: order.append("run") or "measured")
    monkeypatch.setattr(sys, "argv", ["spike_virtual_files.py", "--seconds", "0"])

    assert spike.main() == 0

    assert order == ["settle", "run"]


def test_file_contents_rejects_an_index_outside_the_entry_list(monkeypatch):
    messages = []
    monkeypatch.setattr(spike, "log", messages.append)
    obj = spike.DataObject()
    medium = spike.STGMEDIUM()

    for lindex in (-1, len(spike.ENTRIES)):
        fmt = spike.FORMATETC(
            obj.cf_contents, None, spike.DVASPECT_CONTENT, lindex, spike.TYMED_ISTREAM
        )
        assert obj._get_data(None, ctypes.byref(fmt), ctypes.byref(medium)) == spike.DV_E_FORMATETC

    assert obj.streams == []
    assert any("lindex=-1 is not a zero-based entry index" in message for message in messages)


def test_query_get_data_agrees_with_get_data_on_every_file_contents_index(monkeypatch):
    monkeypatch.setattr(spike, "log", lambda _message: None)
    obj = spike.DataObject()
    medium = spike.STGMEDIUM()

    for lindex in (-2, -1, 0, 1, 2, len(spike.ENTRIES)):
        fmt = spike.FORMATETC(
            obj.cf_contents, None, spike.DVASPECT_CONTENT, lindex, spike.TYMED_ISTREAM
        )
        promised = obj._query_get_data(None, ctypes.byref(fmt))
        delivered = obj._get_data(None, ctypes.byref(fmt), ctypes.byref(medium))
        assert promised == delivered, f"lindex={lindex} promised {promised}, delivered {delivered}"


def test_query_get_data_rejects_a_file_contents_index_outside_the_entry_list(monkeypatch):
    messages = []
    monkeypatch.setattr(spike, "log", messages.append)
    obj = spike.DataObject()
    fmt = spike.FORMATETC(
        obj.cf_contents, None, spike.DVASPECT_CONTENT, -1, spike.TYMED_ISTREAM
    )

    assert obj._query_get_data(None, ctypes.byref(fmt)) == spike.DV_E_FORMATETC

    assert any("lindex=-1 is not a zero-based entry index" in message for message in messages)


def test_enumerate_formats_stops_and_says_so_when_an_enumerator_never_ends(monkeypatch):
    messages = []
    monkeypatch.setattr(spike, "log", messages.append)
    monkeypatch.setattr(spike, "INSPECT_BATCH", 1)
    monkeypatch.setattr(spike, "INSPECT_MAX_BATCHES", 3)

    def endless_next(_self, _this, celt, rgelt, fetched):
        target = ctypes.cast(rgelt, ctypes.POINTER(spike.FORMATETC))
        for index in range(celt):
            target[index] = spike.FORMATETC(1, None, spike.DVASPECT_CONTENT, -1, 1)
        ctypes.cast(fetched, ctypes.POINTER(ctypes.wintypes.ULONG))[0] = celt
        return spike.S_OK

    monkeypatch.setattr(spike.FormatEnumerator, "_next", endless_next)
    obj = spike.DataObject()

    formats = spike.enumerate_formats(obj.pointer)

    assert len(formats) == 3
    assert any("enumeration truncated" in message for message in messages)


def test_file_contents_refuses_a_directory_entry_on_both_entry_points(monkeypatch):
    messages = []
    monkeypatch.setattr(spike, "log", messages.append)
    obj = spike.DataObject()
    medium = spike.STGMEDIUM()
    assert spike.ENTRIES[0][1] is True, "entry 0 is meant to be the Photos directory"
    fmt = spike.FORMATETC(
        obj.cf_contents, None, spike.DVASPECT_CONTENT, 0, spike.TYMED_ISTREAM
    )

    assert obj._query_get_data(None, ctypes.byref(fmt)) == spike.DV_E_FORMATETC
    assert obj._get_data(None, ctypes.byref(fmt), ctypes.byref(medium)) == spike.DV_E_FORMATETC

    assert obj.streams == []
    assert any("is a directory" in message for message in messages)
