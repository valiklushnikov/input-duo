from __future__ import annotations

import ctypes
from ctypes import wintypes

IID_IUNKNOWN = "{00000000-0000-0000-C000-000000000046}"

S_OK = 0
E_NOINTERFACE = -2147467262
E_POINTER = -2147467261


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def guid_from_string(text: str) -> GUID:
    guid = GUID()
    result = ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
    if result != S_OK:
        raise ValueError(f"invalid GUID: {text!r}")
    return guid


def _same_guid(a: GUID, b: GUID) -> bool:
    return bytes(memoryview(a).cast("B")) == bytes(memoryview(b).cast("B"))


_QUERY = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_ADDREF = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


def make_vtable(*methods) -> ctypes.Array:
    table = (ctypes.c_void_p * len(methods))()
    for index, method in enumerate(methods):
        table[index] = ctypes.cast(method, ctypes.c_void_p)
    return table


class COMObject:
    def __init__(self, supported_iids: list[str]) -> None:
        self._supported = [guid_from_string(iid) for iid in supported_iids]
        self.refcount = 1
        self._callbacks = [
            _QUERY(self._query_interface),
            _ADDREF(self._add_ref),
            _ADDREF(self._release),
        ]
        self._vtable = make_vtable(*self._callbacks)
        self._slot = ctypes.c_void_p(ctypes.addressof(self._vtable))
        self.pointer = ctypes.c_void_p(ctypes.addressof(self._slot))

    def _query_interface(self, _this, riid, ppv) -> int:
        if not ppv:
            return E_POINTER
        out = ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))
        requested = ctypes.cast(riid, ctypes.POINTER(GUID)).contents
        if any(_same_guid(requested, supported) for supported in self._supported):
            out[0] = self.pointer
            self.refcount += 1
            return S_OK
        out[0] = None
        return E_NOINTERFACE

    def _add_ref(self, _this) -> int:
        self.refcount += 1
        return self.refcount

    def _release(self, _this) -> int:
        self.refcount -= 1
        return self.refcount


def _call(pointer: ctypes.c_void_p, slot: int, prototype, *args):
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p)).contents
    entries = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))
    return prototype(entries[slot])(pointer, *args)


def query_interface(pointer, riid, ppv) -> int:
    return _call(pointer, 0, _QUERY, ctypes.byref(riid), ppv)


def add_ref(pointer) -> int:
    return _call(pointer, 1, _ADDREF)


def release(pointer) -> int:
    return _call(pointer, 2, _ADDREF)
