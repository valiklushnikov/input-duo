from __future__ import annotations

import ctypes

import pytest

import spike_virtual_files as spike


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
