"""Throwaway spike: virtual-file IDataObject on the clipboard.

Run in a real interactive Windows session (not offscreen)::

    .venv\\Scripts\\python.exe configurator/tests/transfer/spike_virtual_files.py --seconds 300

The script places an object advertising three virtual-file entries on the
clipboard and logs descriptor negotiation plus synthetic IStream reads.  The
4 GiB entry is deterministic and is never stored or fetched from a network.
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
    guid_from_string,
    make_vtable,
)
from spike_qt_responsiveness import run

IID_IDATAOBJECT = "{0000010E-0000-0000-C000-000000000046}"
IID_ISTREAM = "{0000000C-0000-0000-C000-000000000046}"
IID_IASYNCCAPABILITY = "{3D8B0590-F691-11D2-8EA9-006097DF5BD4}"

DV_E_FORMATETC = -2147221404  # 0x80040064
DV_E_TYMED = -2147221399  # 0x80040069
E_NOTIMPL = -2147467263
STG_E_INVALIDFUNCTION = -2147287039  # 0x80030001

TYMED_HGLOBAL = 1
TYMED_ISTREAM = 4
DATADIR_GET = 1

STREAM_SEEK_SET = 0
STREAM_SEEK_CUR = 1
STREAM_SEEK_END = 2

DROPEFFECT_COPY = 1
FILE_ATTRIBUTE_DIRECTORY = 0x10
FD_FILESIZE = 0x40
FD_ATTRIBUTES = 0x04
FD_PROGRESSUI = 0x4000

_START = time.perf_counter()

# (entry_index, offset, requested_cb, returned), once per IStream::Read.
READ_LOG: list[tuple[int, int, int, int]] = []
# (entry_index, origin, offset), once per IStream::Seek.
SEEK_LOG: list[tuple[int, int, int]] = []
# entry_index, once per IStream::Stat.
STAT_LOG: list[int] = []
# Every asynchronous-capability call, in arrival order, with its thread id.
LIFECYCLE_LOG: list[tuple[str, int]] = []


def log(message: str) -> None:
    elapsed = time.perf_counter() - _START
    print(f"[{elapsed:8.3f}s tid={threading.get_ident():>6}] {message}", flush=True)


def note(event: str) -> None:
    LIFECYCLE_LOG.append((event, threading.get_ident()))
    log(f"lifecycle: {event}")


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


def synthetic_bytes(entry_index: int, offset: int, count: int) -> bytes:
    """Return deterministic data without storing the virtual file."""
    return bytes((entry_index * 7 + (offset + index) * 31) & 0xFF for index in range(count))


_READ = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p
)
_WRITE = _READ
_SEEK = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_longlong, wintypes.DWORD, ctypes.c_void_p
)
_SETSIZE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_longlong)
_COPYTO = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_longlong,
    ctypes.c_void_p, ctypes.c_void_p,
)
_COMMIT = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.DWORD)
_REVERT = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
_LOCK = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_longlong, ctypes.c_longlong, wintypes.DWORD
)
_STAT = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD)
_CLONE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)


class STATSTG(ctypes.Structure):
    _fields_ = [
        ("pwcsName", ctypes.c_wchar_p),
        ("type", wintypes.DWORD),
        ("cbSize", ctypes.c_ulonglong),
        ("mtime", FILETIME),
        ("ctime", FILETIME),
        ("atime", FILETIME),
        ("grfMode", wintypes.DWORD),
        ("grfLocksSupported", wintypes.DWORD),
        ("clsid", GUID),
        ("grfStateBits", wintypes.DWORD),
        ("reserved", wintypes.DWORD),
    ]


class StreamObject(COMObject):
    """IStream over a synthetic generator; it uses neither disk nor network."""

    def __init__(self, entry_index: int, size: int) -> None:
        super().__init__([IID_IUNKNOWN, IID_ISTREAM])
        self.entry_index = entry_index
        self.size = size
        self.position = 0
        self._own = [
            _READ(self._read), _WRITE(self._write), _SEEK(self._seek),
            _SETSIZE(self._set_size), _COPYTO(self._copy_to), _COMMIT(self._commit),
            _REVERT(self._revert), _LOCK(self._lock), _LOCK(self._unlock),
            _STAT(self._stat), _CLONE(self._clone),
        ]
        self._vtable = make_vtable(*self._callbacks, *self._own)
        self._slot = ctypes.c_void_p(ctypes.addressof(self._vtable))
        self.pointer = ctypes.c_void_p(ctypes.addressof(self._slot))

    def _read(self, _this, pv, cb, pcb_read) -> int:
        available = max(0, self.size - self.position)
        count = min(int(cb), available)
        payload = synthetic_bytes(self.entry_index, self.position, count)
        READ_LOG.append((self.entry_index, self.position, int(cb), count))
        log(f"IStream::Read(entry={self.entry_index}, off={self.position}, cb={cb}) -> {count}")
        if count:
            ctypes.memmove(pv, payload, count)
        self.position += count
        if pcb_read:
            ctypes.cast(pcb_read, ctypes.POINTER(wintypes.ULONG))[0] = count
        return S_OK

    def _seek(self, _this, offset, origin, new_position) -> int:
        SEEK_LOG.append((self.entry_index, int(origin), int(offset)))
        log(f"IStream::Seek(entry={self.entry_index}, origin={origin}, offset={offset})")
        if origin == STREAM_SEEK_SET:
            target = int(offset)
        elif origin == STREAM_SEEK_CUR:
            target = self.position + int(offset)
        elif origin == STREAM_SEEK_END:
            target = self.size + int(offset)
        else:
            return STG_E_INVALIDFUNCTION
        if target < 0:
            return STG_E_INVALIDFUNCTION
        self.position = target
        if new_position:
            ctypes.cast(new_position, ctypes.POINTER(ctypes.c_ulonglong))[0] = target
        return S_OK

    def _stat(self, _this, pstatstg, _flags) -> int:
        STAT_LOG.append(self.entry_index)
        log(f"IStream::Stat(entry={self.entry_index})")
        if not pstatstg:
            return E_POINTER
        stat = ctypes.cast(pstatstg, ctypes.POINTER(STATSTG)).contents
        ctypes.memset(ctypes.byref(stat), 0, ctypes.sizeof(STATSTG))
        stat.type = 2
        stat.cbSize = self.size
        return S_OK

    def _write(self, _this, _pv, _cb, _written) -> int: return STG_E_INVALIDFUNCTION
    def _set_size(self, _this, _size) -> int: return STG_E_INVALIDFUNCTION
    def _copy_to(self, _this, _dest, _cb, _read, _written) -> int: return E_NOTIMPL
    def _commit(self, _this, _flags) -> int: return S_OK
    def _revert(self, _this) -> int: return S_OK
    def _lock(self, _this, _offset, _cb, _type) -> int: return E_NOTIMPL
    def _unlock(self, _this, _offset, _cb, _type) -> int: return E_NOTIMPL
    def _clone(self, _this, _out) -> int: return E_NOTIMPL


def _stream_slot(pointer, index, prototype):
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p)).contents
    entries = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))
    return prototype(entries[index])


def stream_read(pointer: ctypes.c_void_p, count: int) -> bytes:
    buffer = (ctypes.c_char * count)()
    read = wintypes.ULONG(0)
    _stream_slot(pointer, 3, _READ)(pointer, buffer, count, ctypes.byref(read))
    return bytes(buffer[:read.value])


def stream_seek(pointer: ctypes.c_void_p, offset: int, origin: int) -> int:
    position = ctypes.c_ulonglong(0)
    _stream_slot(pointer, 5, _SEEK)(pointer, offset, origin, ctypes.byref(position))
    return position.value


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
_QUERYINTERFACE = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
)
_REFCOUNT = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)
_SETASYNC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.BOOL)
_GETASYNC = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL)
)
_STARTOP = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)
_INOP = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL)
)
_ENDOP = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, wintypes.DWORD
)


def _is_iid(riid, expected_iid: str) -> bool:
    requested = ctypes.cast(riid, ctypes.POINTER(GUID)).contents
    expected = guid_from_string(expected_iid)
    return bytes(memoryview(requested).cast("B")) == bytes(memoryview(expected).cast("B"))


class AsyncCapabilityInterface:
    """The distinct IUnknown-derived IDataObjectAsyncCapability interface."""

    def __init__(self, owner) -> None:
        self.owner = owner
        self._callbacks = [
            _QUERYINTERFACE(self._query_interface),
            _REFCOUNT(self._add_ref),
            _REFCOUNT(self._release),
        ]
        self._methods = [
            _SETASYNC(owner._set_async_mode),
            _GETASYNC(owner._get_async_mode),
            _STARTOP(owner._start_operation),
            _INOP(owner._in_operation_query),
            _ENDOP(owner._end_operation),
        ]
        self._vtable = make_vtable(*self._callbacks, *self._methods)
        self._slot = ctypes.c_void_p(ctypes.addressof(self._vtable))
        self.pointer = ctypes.c_void_p(ctypes.addressof(self._slot))

    def _query_interface(self, _this, riid, ppv) -> int:
        return self.owner._query_interface(self.owner.pointer, riid, ppv)

    def _add_ref(self, _this) -> int:
        return self.owner._add_ref(self.owner.pointer)

    def _release(self, _this) -> int:
        return self.owner._release(self.owner.pointer)


class DataObject(COMObject):
    """IDataObject advertising descriptors and synthetic IStream file contents."""

    def __init__(self, async_capability: bool = False) -> None:
        super().__init__([IID_IUNKNOWN, IID_IDATAOBJECT])
        self.cf_descriptor = register_format("FileGroupDescriptorW")
        self.cf_contents = register_format("FileContents")
        self.cf_drop_effect = register_format("Preferred DropEffect")
        self.get_data_calls: list[tuple[int, int]] = []
        self.streams: list[StreamObject] = []
        self.async_mode = False
        self.in_operation = False
        self._retained_async_pointer: ctypes.c_void_p | None = None

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
        self._async_capability = (
            AsyncCapabilityInterface(self) if async_capability else None
        )

    def _query_interface(self, _this, riid, ppv) -> int:
        if not ppv:
            return E_POINTER
        if self._async_capability is not None and _is_iid(riid, IID_IASYNCCAPABILITY):
            ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))[0] = self._async_capability.pointer
            self._add_ref(self.pointer)
            return S_OK
        return super()._query_interface(_this, riid, ppv)

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
        if fmt.cfFormat == self.cf_contents:
            if not fmt.tymed & TYMED_ISTREAM:
                log(f"  -> DV_E_TYMED: requested tymed={fmt.tymed}, not ISTREAM")
                return DV_E_TYMED
            _name, is_directory, size = ENTRIES[fmt.lindex]
            if is_directory:
                return DV_E_FORMATETC
            stream = StreamObject(fmt.lindex, size)
            # Keep the Python-owned callbacks alive while Explorer owns the COM pointer.
            self.streams.append(stream)
            medium.tymed = TYMED_ISTREAM
            medium.data = stream.pointer
            medium.pUnkForRelease = None
            return S_OK
        log("  -> DV_E_FORMATETC")
        return DV_E_FORMATETC

    def _query_get_data(self, _this, pformatetc) -> int:
        fmt = ctypes.cast(pformatetc, ctypes.POINTER(FORMATETC)).contents
        log(f"QueryGetData(cfFormat={fmt.cfFormat}, lindex={fmt.lindex})")
        if fmt.cfFormat == self.cf_contents and not fmt.tymed & TYMED_ISTREAM:
            return DV_E_TYMED
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

    def _set_async_mode(self, _this, do_op_async) -> int:
        note(f"SetAsyncMode({do_op_async})")
        self.async_mode = bool(do_op_async)
        if self.async_mode:
            self._retain_async_interface()
        else:
            self._release_retained_async_interface()
        return S_OK

    def _get_async_mode(self, _this, out) -> int:
        note("GetAsyncMode")
        if not out:
            return E_POINTER
        out[0] = 1 if self.async_mode else 0
        return S_OK

    def _start_operation(self, _this, _reserved) -> int:
        note("StartOperation")
        self.in_operation = True
        return S_OK

    def _in_operation_query(self, _this, out) -> int:
        note("InOperation")
        if not out:
            return E_POINTER
        out[0] = 1 if self.in_operation else 0
        return S_OK

    def _end_operation(self, _this, result, _reserved, effects) -> int:
        note(f"EndOperation(hResult=0x{result & 0xFFFFFFFF:08X}, effects={effects})")
        self.in_operation = False
        self._release_retained_async_interface()
        return S_OK

    def _retain_async_interface(self) -> None:
        if self._retained_async_pointer is not None:
            return
        if self._async_capability is None:
            return
        self._retained_async_pointer = self._async_capability.pointer
        self._async_capability._add_ref(self._retained_async_pointer)

    def _release_retained_async_interface(self) -> None:
        if self._retained_async_pointer is None or self._async_capability is None:
            return
        self._async_capability._release(self._retained_async_pointer)
        self._retained_async_pointer = None


def make_pump():
    message = wintypes.MSG()

    def pump() -> None:
        while ctypes.windll.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            ctypes.windll.user32.TranslateMessage(ctypes.byref(message))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(message))

    return pump


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=int, default=120)
    parser.add_argument("--async-capability", action="store_true")
    arguments = parser.parse_args()

    ctypes.oledll.ole32.OleInitialize(None)
    obj = DataObject(async_capability=arguments.async_capability)
    result = ctypes.windll.ole32.OleSetClipboard(obj.pointer)
    log(f"OleSetClipboard -> 0x{result & 0xFFFFFFFF:08X}")
    log("Press Ctrl+V in Explorer. Ctrl+C in this window exits.")

    log(f"Qt responsiveness: {run(arguments.seconds, make_pump())}")

    log(f"GetData calls: {obj.get_data_calls}")
    log(f"Read calls: {READ_LOG}")
    log(f"Seek calls: {SEEK_LOG}")
    log(f"Stat calls: {STAT_LOG}")
    log(f"Lifecycle calls: {LIFECYCLE_LOG}")
    ctypes.windll.ole32.OleFlushClipboard()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
