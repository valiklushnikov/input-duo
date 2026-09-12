"""Throwaway spike: descriptor-only IDataObject on the clipboard.

Run in a real interactive Windows session (not offscreen)::

    .venv\\Scripts\\python.exe configurator/tests/transfer/spike_virtual_files.py --descriptor-only

The script places an object advertising three virtual-file entries on the
clipboard and logs every QueryGetData/GetData call.  No file content is
provided at this stage; the probe isolates descriptor negotiation.
"""

from __future__ import annotations

import argparse
import ctypes
import threading
import time
from ctypes import wintypes

from spike_com_vtable import (
    E_POINTER,
    IID_IUNKNOWN,
    S_OK,
    GUID,
    COMObject,
    make_vtable,
)

IID_IDATAOBJECT = "{0000010E-0000-0000-C000-000000000046}"

DV_E_FORMATETC = -2147221404  # 0x80040064
E_NOTIMPL = -2147467263

TYMED_HGLOBAL = 1
DATADIR_GET = 1

DROPEFFECT_COPY = 1
FILE_ATTRIBUTE_DIRECTORY = 0x10
FD_FILESIZE = 0x40
FD_ATTRIBUTES = 0x04
FD_PROGRESSUI = 0x4000

_START = time.perf_counter()


def log(message: str) -> None:
    elapsed = time.perf_counter() - _START
    print(f"[{elapsed:8.3f}s tid={threading.get_ident():>6}] {message}", flush=True)


class FORMATETC(ctypes.Structure):
    _fields_ = [
        ("cfFormat", wintypes.WORD),
        ("ptd", ctypes.c_void_p),
        ("dwAspect", wintypes.DWORD),
        ("lindex", ctypes.c_long),
        ("tymed", wintypes.DWORD),
    ]


class STGMEDIUM(ctypes.Structure):
    _fields_ = [
        ("tymed", wintypes.DWORD),
        ("data", ctypes.c_void_p),
        ("pUnkForRelease", ctypes.c_void_p),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class FILEDESCRIPTORW(ctypes.Structure):
    _fields_ = [
        ("dwFlags", wintypes.DWORD),
        ("clsid", GUID),
        ("sizel", ctypes.c_long * 2),
        ("pointl", ctypes.c_long * 2),
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", FILETIME),
        ("ftLastAccessTime", FILETIME),
        ("ftLastWriteTime", FILETIME),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("cFileName", ctypes.c_wchar * 260),
    ]


ENTRIES = [
    ("Photos", True, 0),
    ("Photos\\img1.bin", False, 3 * 1024 * 1024),
    ("big.bin", False, 4 * 1024 * 1024 * 1024),
]


def register_format(name: str) -> int:
    value = ctypes.windll.user32.RegisterClipboardFormatW(ctypes.c_wchar_p(name))
    log(f"RegisterClipboardFormatW({name!r}) -> {value}")
    return value


_KERNEL32 = ctypes.windll.kernel32
_GLOBAL_ALLOC = _KERNEL32.GlobalAlloc
_GLOBAL_ALLOC.argtypes = [wintypes.UINT, ctypes.c_size_t]
_GLOBAL_ALLOC.restype = wintypes.HGLOBAL
_GLOBAL_LOCK = _KERNEL32.GlobalLock
_GLOBAL_LOCK.argtypes = [wintypes.HGLOBAL]
_GLOBAL_LOCK.restype = ctypes.c_void_p
_GLOBAL_UNLOCK = _KERNEL32.GlobalUnlock
_GLOBAL_UNLOCK.argtypes = [wintypes.HGLOBAL]
_GLOBAL_UNLOCK.restype = wintypes.BOOL


def build_group_descriptor() -> bytes:
    """Build a FILEGROUPDESCRIPTORW for the three probe entries."""
    blob = bytearray(ctypes.sizeof(wintypes.DWORD))
    ctypes.memmove(
        (ctypes.c_char * len(blob)).from_buffer(blob),
        ctypes.byref(wintypes.DWORD(len(ENTRIES))),
        ctypes.sizeof(wintypes.DWORD),
    )
    for name, is_directory, size in ENTRIES:
        descriptor = FILEDESCRIPTORW()
        descriptor.dwFlags = FD_ATTRIBUTES | FD_PROGRESSUI
        descriptor.cFileName = name
        if is_directory:
            descriptor.dwFileAttributes = FILE_ATTRIBUTE_DIRECTORY
        else:
            descriptor.dwFlags |= FD_FILESIZE
            descriptor.nFileSizeHigh = size >> 32
            descriptor.nFileSizeLow = size & 0xFFFFFFFF
        blob.extend(bytes(memoryview(descriptor).cast("B")))
    return bytes(blob)


def to_hglobal(payload: bytes, kernel32=None) -> int:
    GMEM_MOVEABLE = 0x0002
    kernel32 = kernel32 or _KERNEL32
    if kernel32 is _KERNEL32:
        handle = _GLOBAL_ALLOC(GMEM_MOVEABLE, len(payload))
    else:
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    if not handle:
        raise MemoryError("GlobalAlloc failed")
    address = _GLOBAL_LOCK(handle) if kernel32 is _KERNEL32 else kernel32.GlobalLock(handle)
    if not address:
        raise MemoryError("GlobalLock failed")
    ctypes.memmove(address, payload, len(payload))
    if kernel32 is _KERNEL32:
        _GLOBAL_UNLOCK(handle)
    else:
        kernel32.GlobalUnlock(handle)
    return handle


_GETDATA = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_GETDATAHERE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_QUERYGET = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)
_ENUM = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
_SETDATA = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
)
_DADVISE = ctypes.WINFUNCTYPE(
    ctypes.c_long,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.c_void_p,
    ctypes.c_void_p,
)


class DataObject(COMObject):
    """IDataObject advertising group descriptors, but no file contents."""

    def __init__(self) -> None:
        super().__init__([IID_IUNKNOWN, IID_IDATAOBJECT])
        self.cf_descriptor = register_format("FileGroupDescriptorW")
        self.cf_contents = register_format("FileContents")
        self.cf_drop_effect = register_format("Preferred DropEffect")
        self.get_data_calls: list[tuple[int, int]] = []

        self._own = [
            _GETDATA(self._get_data),
            _GETDATAHERE(self._get_data_here),
            _QUERYGET(self._query_get_data),
            _GETDATA(self._get_canonical),
            _SETDATA(self._set_data),
            _ENUM(self._enum_format_etc),
            _DADVISE(self._d_advise),
            _QUERYGET(self._d_unadvise),
            _QUERYGET(self._enum_d_advise),
        ]
        self._vtable = make_vtable(*self._callbacks, *self._own)
        self._slot = ctypes.c_void_p(ctypes.addressof(self._vtable))
        self.pointer = ctypes.c_void_p(ctypes.addressof(self._slot))

    def _get_data(self, _this, pformatetc, pmedium) -> int:
        fmt = ctypes.cast(pformatetc, ctypes.POINTER(FORMATETC)).contents
        self.get_data_calls.append((fmt.cfFormat, fmt.lindex))
        log(f"GetData(cfFormat={fmt.cfFormat}, lindex={fmt.lindex}, tymed={fmt.tymed})")
        medium = ctypes.cast(pmedium, ctypes.POINTER(STGMEDIUM)).contents
        if fmt.cfFormat == self.cf_descriptor:
            medium.tymed = TYMED_HGLOBAL
            medium.data = to_hglobal(build_group_descriptor())
            medium.pUnkForRelease = None
            return S_OK
        if fmt.cfFormat == self.cf_drop_effect:
            medium.tymed = TYMED_HGLOBAL
            medium.data = to_hglobal(DROPEFFECT_COPY.to_bytes(4, "little"))
            medium.pUnkForRelease = None
            return S_OK
        log("  -> DV_E_FORMATETC (content intentionally unavailable)")
        return DV_E_FORMATETC

    def _query_get_data(self, _this, pformatetc) -> int:
        fmt = ctypes.cast(pformatetc, ctypes.POINTER(FORMATETC)).contents
        log(f"QueryGetData(cfFormat={fmt.cfFormat}, lindex={fmt.lindex})")
        if fmt.cfFormat in (self.cf_descriptor, self.cf_drop_effect, self.cf_contents):
            return S_OK
        return DV_E_FORMATETC

    def _get_data_here(self, _this, _fmt, _medium) -> int:
        log("GetDataHere() -> E_NOTIMPL")
        return E_NOTIMPL

    def _get_canonical(self, _this, _fmt, _out) -> int:
        log("GetCanonicalFormatEtc() -> E_NOTIMPL")
        return E_NOTIMPL

    def _set_data(self, _this, _fmt, _medium, _release) -> int:
        log("SetData() -> E_NOTIMPL")
        return E_NOTIMPL

    def _enum_format_etc(self, _this, direction, ppenum) -> int:
        log(f"EnumFormatEtc(direction={direction})")
        if direction != DATADIR_GET:
            return E_NOTIMPL
        if not ppenum:
            return E_POINTER
        return E_NOTIMPL

    def _d_advise(self, _this, _fmt, _flags, _sink, _connection) -> int:
        log("DAdvise() -> E_NOTIMPL")
        return E_NOTIMPL

    def _d_unadvise(self, _this, _connection) -> int:
        log("DUnadvise() -> E_NOTIMPL")
        return E_NOTIMPL

    def _enum_d_advise(self, _this, _out) -> int:
        log("EnumDAdvise() -> E_NOTIMPL")
        return E_NOTIMPL


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--descriptor-only", action="store_true")
    parser.add_argument("--seconds", type=int, default=120)
    arguments = parser.parse_args()
    if not arguments.descriptor_only:
        parser.error("pass --descriptor-only")

    ctypes.oledll.ole32.OleInitialize(None)
    obj = DataObject()
    result = ctypes.windll.ole32.OleSetClipboard(obj.pointer)
    log(f"OleSetClipboard -> 0x{result & 0xFFFFFFFF:08X}")
    log("Press Ctrl+V in Explorer. Ctrl+C in this window exits.")

    deadline = time.perf_counter() + arguments.seconds
    message = wintypes.MSG()
    while time.perf_counter() < deadline:
        while ctypes.windll.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            ctypes.windll.user32.TranslateMessage(ctypes.byref(message))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(message))
        time.sleep(0.01)

    log(f"GetData calls: {obj.get_data_calls}")
    ctypes.windll.ole32.OleFlushClipboard()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
