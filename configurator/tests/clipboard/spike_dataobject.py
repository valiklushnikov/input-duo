"""SPIKE (одноразовый код, не production): IDataObject, отдающий
CFSTR_FILEDESCRIPTORW + CFSTR_FILECONTENTS. Чистый ctypes.
"""

from __future__ import annotations

import ctypes
import time
from ctypes import wintypes

from spike_com import (
    DV_E_FORMATETC,
    DV_E_LINDEX,
    DV_E_TYMED,
    E_INVALIDARG,
    E_NOTIMPL,
    OLE_E_ADVISENOTSUPPORTED,
    S_FALSE,
    S_OK,
    STG_E_INVALIDFUNCTION,
    STG_E_READFAULT,
    ComObject,
    HRESULT,
    IID_IDataObject,
    IID_IEnumFORMATETC,
    IID_ISequentialStream,
    IID_IStream,
    LPVOID,
    ULONG,
    kernel32,
    ole32,
)
from spike_shell_types import (
    CF_FILECONTENTS,
    CF_FILEDESCRIPTORW,
    CF_PREFERREDDROPEFFECT,
    DROPEFFECT_COPY,
    DVASPECT_CONTENT,
    FORMATETC,
    STATSTG,
    STGMEDIUM,
    STGTY_STREAM,
    STREAM_SEEK_CUR,
    STREAM_SEEK_END,
    STREAM_SEEK_SET,
    TYMED_HGLOBAL,
    TYMED_ISTREAM,
    file_group_descriptor,
    format_name,
    hglobal_from_bytes,
)

DWORD = wintypes.DWORD
LONG = wintypes.LONG

T0 = time.monotonic()

ole32.CoTaskMemAlloc.argtypes = [ctypes.c_size_t]
ole32.CoTaskMemAlloc.restype = ctypes.c_void_p


def tid() -> int:
    return kernel32.GetCurrentThreadId()


def stamp() -> str:
    return f"[{time.monotonic() - T0:8.3f}s tid={tid():<6}]"


class SourceError(Exception):
    """Искусственный сбой отдачи содержимого (имитация обрыва сети)."""


class ByteSource:
    def __init__(self, name, size, producer=None, directory=False):
        self.name = name
        self.size = size
        self.producer = producer
        self.directory = directory


class SpikeStream(ComObject):
    IIDS = (IID_IStream, IID_ISequentialStream)
    METHODS = (
        ("read", HRESULT, [LPVOID, ULONG, ctypes.POINTER(ULONG)]),
        ("write", HRESULT, [LPVOID, ULONG, ctypes.POINTER(ULONG)]),
        ("seek", HRESULT, [ctypes.c_longlong, DWORD,
                           ctypes.POINTER(ctypes.c_ulonglong)]),
        ("set_size", HRESULT, [ctypes.c_ulonglong]),
        ("copy_to", HRESULT, [LPVOID, ctypes.c_ulonglong,
                              ctypes.POINTER(ctypes.c_ulonglong),
                              ctypes.POINTER(ctypes.c_ulonglong)]),
        ("commit", HRESULT, [DWORD]),
        ("revert", HRESULT, []),
        ("lock_region", HRESULT, [ctypes.c_ulonglong, ctypes.c_ulonglong, DWORD]),
        ("unlock_region", HRESULT, [ctypes.c_ulonglong, ctypes.c_ulonglong, DWORD]),
        ("stat", HRESULT, [ctypes.POINTER(STATSTG), DWORD]),
        ("clone", HRESULT, [ctypes.POINTER(LPVOID)]),
    )

    def __init__(self, source, log, trace, ftm=False):
        self.source = source
        self.position = 0
        self.trace = trace
        super().__init__(log=log)
        if ftm:
            self.enable_free_threaded_marshaler()
        self._log(f"{stamp()} IStream создан: {source.name!r} size={source.size} "
                  f"ftm={ftm}")

    def read(self, pv, cb, pcb_read):
        started = time.monotonic()
        want = min(int(cb), max(self.source.size - self.position, 0))
        try:
            chunk = self.source.producer(self.position, want) if want else b""
        except SourceError as exc:
            self.trace.append(("read-error", tid(), time.monotonic() - T0,
                               self.position, str(exc)))
            self._log(f"{stamp()} IStream.Read СБОЙ на offset={self.position}: {exc}")
            if pcb_read:
                pcb_read[0] = 0
            return STG_E_READFAULT
        if chunk:
            ctypes.memmove(pv, chunk, len(chunk))
        self.position += len(chunk)
        if pcb_read:
            pcb_read[0] = len(chunk)
        self.trace.append(("read", tid(), started - T0, time.monotonic() - started,
                           int(cb), len(chunk), self.position))
        return S_OK

    def write(self, pv, cb, pcb_written):
        if pcb_written:
            pcb_written[0] = 0
        return STG_E_INVALIDFUNCTION

    def seek(self, move, origin, new_pos):
        if origin == STREAM_SEEK_SET:
            target = move
        elif origin == STREAM_SEEK_CUR:
            target = self.position + move
        elif origin == STREAM_SEEK_END:
            target = self.source.size + move
        else:
            return E_INVALIDARG
        target = max(0, target)
        if target != self.position:
            self.trace.append(("seek", tid(), time.monotonic() - T0,
                               self.position, target, origin))
            self._log(f"{stamp()} IStream.Seek {self.position} -> {target} "
                      f"(origin={origin})")
        self.position = target
        if new_pos:
            new_pos[0] = self.position
        return S_OK

    def set_size(self, size):
        return STG_E_INVALIDFUNCTION

    def copy_to(self, stm, cb, read_out, written_out):
        self.trace.append(("copyto", tid(), time.monotonic() - T0, int(cb)))
        self._log(f"{stamp()} IStream.CopyTo(cb={cb}) -> E_NOTIMPL")
        return E_NOTIMPL

    def commit(self, flags):
        return S_OK

    def revert(self):
        return S_OK

    def lock_region(self, off, cb, kind):
        return STG_E_INVALIDFUNCTION

    def unlock_region(self, off, cb, kind):
        return STG_E_INVALIDFUNCTION

    def stat(self, pstat, flag):
        self.trace.append(("stat", tid(), time.monotonic() - T0, int(flag)))
        self._log(f"{stamp()} IStream.Stat(flag={flag}) {self.source.name!r} "
                  f"-> cbSize={self.source.size}")
        ctypes.memset(pstat, 0, ctypes.sizeof(STATSTG))
        pstat[0].type = STGTY_STREAM
        pstat[0].cbSize = self.source.size
        if int(flag) == 0:  # STATFLAG_DEFAULT: имя обязательно, CoTaskMemAlloc
            name = self.source.name
            raw = name.encode("utf-16-le") + b"\x00\x00"
            ptr = ole32.CoTaskMemAlloc(len(raw))
            ctypes.memmove(ptr, raw, len(raw))
            pstat[0].pwcsName = ptr
        return S_OK

    def on_final_release(self):
        self.trace.append(("stream-released", tid(), time.monotonic() - T0,
                           self.position))
        self._log(f"{stamp()} IStream ОСВОБОЖДЁН на offset={self.position} "
                  f"из {self.source.size} — вот сигнал прекратить качать")

    def clone(self, out):
        self.trace.append(("clone", tid(), time.monotonic() - T0))
        self._log(f"{stamp()} IStream.Clone -> E_NOTIMPL")
        if out:
            out[0] = None
        return E_NOTIMPL


class SpikeEnumFormatEtc(ComObject):
    IIDS = (IID_IEnumFORMATETC,)
    METHODS = (
        ("next", HRESULT, [ULONG, ctypes.POINTER(FORMATETC), ctypes.POINTER(ULONG)]),
        ("skip", HRESULT, [ULONG]),
        ("reset", HRESULT, []),
        ("clone", HRESULT, [ctypes.POINTER(LPVOID)]),
    )

    def __init__(self, formats, log, index=0):
        self.formats = formats
        self.index = index
        super().__init__(log=log)

    def next(self, celt, rgelt, fetched):
        count = 0
        while count < int(celt) and self.index < len(self.formats):
            rgelt[count] = self.formats[self.index]
            self.index += 1
            count += 1
        if fetched:
            fetched[0] = count
        return S_OK if count == int(celt) else S_FALSE

    def skip(self, celt):
        self.index = min(self.index + int(celt), len(self.formats))
        return S_OK

    def reset(self):
        self.index = 0
        return S_OK

    def clone(self, out):
        dup = SpikeEnumFormatEtc(self.formats, self._log, self.index)
        out[0] = dup.pointer
        return S_OK


class SpikeDataObject(ComObject):
    IIDS = (IID_IDataObject,)
    METHODS = (
        ("get_data", HRESULT, [ctypes.POINTER(FORMATETC),
                               ctypes.POINTER(STGMEDIUM)]),
        ("get_data_here", HRESULT, [ctypes.POINTER(FORMATETC),
                                    ctypes.POINTER(STGMEDIUM)]),
        ("query_get_data", HRESULT, [ctypes.POINTER(FORMATETC)]),
        ("get_canonical", HRESULT, [ctypes.POINTER(FORMATETC),
                                    ctypes.POINTER(FORMATETC)]),
        ("set_data", HRESULT, [ctypes.POINTER(FORMATETC),
                               ctypes.POINTER(STGMEDIUM), wintypes.BOOL]),
        ("enum_format_etc", HRESULT, [DWORD, ctypes.POINTER(LPVOID)]),
        ("dadvise", HRESULT, [ctypes.POINTER(FORMATETC), DWORD, LPVOID,
                              ctypes.POINTER(DWORD)]),
        ("dunadvise", HRESULT, [DWORD]),
        ("enum_dadvise", HRESULT, [ctypes.POINTER(LPVOID)]),
    )

    def __init__(self, sources, log, trace, ftm_streams=False,
                 formats_mode="both"):
        self.sources = sources
        self.trace = trace
        self.ftm_streams = ftm_streams
        self.formats_mode = formats_mode
        self.streams = []
        super().__init__(log=log)
        self._formats = self._build_formats()

    def _build_formats(self):
        # обёртка OLE (OleGetClipboard в чужом процессе) отвечает на
        # QueryGetData сама, из кэша этого списка, и сравнивает lindex ТОЧНО.
        # Поэтому FileContents перечисляем и с -1, и с каждым индексом файла.
        out = [FORMATETC(CF_FILEDESCRIPTORW, None, DVASPECT_CONTENT, -1,
                         TYMED_HGLOBAL)]
        if self.formats_mode in ("both", "minus-one"):
            out.append(FORMATETC(CF_FILECONTENTS, None, DVASPECT_CONTENT, -1,
                                 TYMED_ISTREAM))
        if self.formats_mode in ("both", "index-only"):
            for i in range(len(self.sources)):
                out.append(FORMATETC(CF_FILECONTENTS, None, DVASPECT_CONTENT, i,
                                     TYMED_ISTREAM))
        out.append(FORMATETC(CF_PREFERREDDROPEFFECT, None, DVASPECT_CONTENT, -1,
                             TYMED_HGLOBAL))
        return out

    def _descriptor_blob(self):
        return file_group_descriptor(
            [{"name": s.name, "size": s.size, "directory": s.directory}
             for s in self.sources])

    def get_data(self, pformatetc, pmedium):
        fe = pformatetc[0]
        self.trace.append(("getdata", tid(), time.monotonic() - T0,
                           format_name(fe.cfFormat), fe.lindex, fe.tymed))
        self._log(f"{stamp()} IDataObject::GetData({format_name(fe.cfFormat)}, "
                  f"lindex={fe.lindex}, tymed={fe.tymed})")
        ctypes.memset(pmedium, 0, ctypes.sizeof(STGMEDIUM))
        if fe.cfFormat == CF_FILEDESCRIPTORW:
            if not fe.tymed & TYMED_HGLOBAL:
                return DV_E_TYMED
            pmedium[0].tymed = TYMED_HGLOBAL
            pmedium[0].data = hglobal_from_bytes(self._descriptor_blob())
            return S_OK
        if fe.cfFormat == CF_PREFERREDDROPEFFECT:
            if not fe.tymed & TYMED_HGLOBAL:
                return DV_E_TYMED
            pmedium[0].tymed = TYMED_HGLOBAL
            pmedium[0].data = hglobal_from_bytes(
                int(DROPEFFECT_COPY).to_bytes(4, "little"))
            return S_OK
        if fe.cfFormat == CF_FILECONTENTS:
            if not fe.tymed & TYMED_ISTREAM:
                return DV_E_TYMED
            idx = fe.lindex
            if idx < 0 or idx >= len(self.sources):
                return DV_E_LINDEX
            source = self.sources[idx]
            if source.directory:
                return DV_E_LINDEX
            stream = SpikeStream(source, self._log, self.trace,
                                 ftm=self.ftm_streams)
            self.streams.append(stream)
            pmedium[0].tymed = TYMED_ISTREAM
            pmedium[0].data = stream.pointer
            return S_OK
        return DV_E_FORMATETC

    def get_data_here(self, pformatetc, pmedium):
        return E_NOTIMPL

    def query_get_data(self, pformatetc):
        fe = pformatetc[0]
        result = DV_E_FORMATETC
        for known in self._formats:
            if known.cfFormat == fe.cfFormat and known.tymed & fe.tymed:
                result = S_OK
                break
        self.trace.append(("querygetdata", tid(), time.monotonic() - T0,
                           format_name(fe.cfFormat), fe.lindex, fe.tymed, result))
        self._log(f"{stamp()} IDataObject::QueryGetData("
                  f"{format_name(fe.cfFormat)}, lindex={fe.lindex}, "
                  f"tymed={fe.tymed}, aspect={fe.dwAspect}) -> "
                  f"{'S_OK' if result == S_OK else 'DV_E_FORMATETC'}")
        return result

    def get_canonical(self, pin, pout):
        if pout:
            ctypes.memset(pout, 0, ctypes.sizeof(FORMATETC))
        return E_NOTIMPL

    def set_data(self, pformatetc, pmedium, release):
        fe = pformatetc[0]
        name = format_name(fe.cfFormat)
        value = None
        try:
            if pmedium[0].tymed == TYMED_HGLOBAL and pmedium[0].data:
                addr = kernel32.GlobalLock(pmedium[0].data)
                if addr:
                    value = ctypes.cast(addr,
                                        ctypes.POINTER(ctypes.c_uint32))[0]
                    kernel32.GlobalUnlock(pmedium[0].data)
        except Exception:  # noqa: BLE001
            pass
        self.trace.append(("setdata", tid(), time.monotonic() - T0, name, value))
        self._log(f"{stamp()} IDataObject::SetData({name}) value={value}")
        return S_OK

    def enum_format_etc(self, direction, out):
        self._log(f"{stamp()} IDataObject::EnumFormatEtc(dir={direction})")
        if int(direction) != 1:
            out[0] = None
            return E_NOTIMPL
        out[0] = SpikeEnumFormatEtc(self._formats, self._log).pointer
        return S_OK

    def dadvise(self, fe, advf, sink, conn):
        if conn:
            conn[0] = 0
        return OLE_E_ADVISENOTSUPPORTED

    def dunadvise(self, conn):
        return OLE_E_ADVISENOTSUPPORTED

    def enum_dadvise(self, out):
        if out:
            out[0] = None
        return OLE_E_ADVISENOTSUPPORTED
