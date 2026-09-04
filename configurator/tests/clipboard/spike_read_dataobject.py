"""SPIKE: отдельный процесс — читает IDataObject из буфера обмена через
OleGetClipboard и вытягивает CFSTR_FILEDESCRIPTORW и CFSTR_FILECONTENTS.

Проверяет мою реализацию COM независимо от оболочки Windows.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spike_com import GUID, kernel32, ole32  # noqa: E402
from spike_shell_types import (  # noqa: E402
    CF_FILECONTENTS,
    CF_FILEDESCRIPTORW,
    DVASPECT_CONTENT,
    FILEDESCRIPTORW,
    FORMATETC,
    STATSTG,
    STGMEDIUM,
    TYMED_HGLOBAL,
    TYMED_ISTREAM,
    format_name,
)


def vcall(ptr, index, restype, argtypes):
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    proto = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)
    fn = proto(vtbl[index])
    return lambda *a: fn(ptr, *a)


HR = ctypes.c_long
LP = ctypes.c_void_p


def main() -> int:
    ole32.OleInitialize(None)
    print(f"reader pid={os.getpid()} tid={kernel32.GetCurrentThreadId()}")

    obj = LP()
    hr = ole32.OleGetClipboard(ctypes.byref(obj))
    print(f"OleGetClipboard -> 0x{hr & 0xFFFFFFFF:08X}, ptr={obj.value:#x}")
    if hr != 0:
        return 1

    # --- EnumFormatEtc
    enum = LP()
    hr = vcall(obj, 8, HR, [wintypes.DWORD, ctypes.POINTER(LP)])(1,
                                                                 ctypes.byref(enum))
    print(f"EnumFormatEtc -> 0x{hr & 0xFFFFFFFF:08X}")
    if hr == 0 and enum.value:
        nxt = vcall(enum, 3, HR, [ctypes.c_ulong, ctypes.POINTER(FORMATETC),
                                  ctypes.POINTER(ctypes.c_ulong)])
        fe = FORMATETC()
        got = ctypes.c_ulong()
        while nxt(1, ctypes.byref(fe), ctypes.byref(got)) == 0 and got.value:
            print(f"  формат: {format_name(fe.cfFormat):<28} lindex={fe.lindex} "
                  f"tymed={fe.tymed} aspect={fe.dwAspect}")
        vcall(enum, 2, ctypes.c_ulong, [])()

    get_data = vcall(obj, 3, HR, [ctypes.POINTER(FORMATETC),
                                  ctypes.POINTER(STGMEDIUM)])
    query = vcall(obj, 5, HR, [ctypes.POINTER(FORMATETC)])

    # --- QueryGetData на дескриптор
    fe = FORMATETC(CF_FILEDESCRIPTORW, None, DVASPECT_CONTENT, -1, TYMED_HGLOBAL)
    print(f"QueryGetData(FileGroupDescriptorW) -> "
          f"0x{query(ctypes.byref(fe)) & 0xFFFFFFFF:08X}")

    # --- дескриптор
    med = STGMEDIUM()
    hr = get_data(ctypes.byref(fe), ctypes.byref(med))
    print(f"GetData(FileGroupDescriptorW) -> 0x{hr & 0xFFFFFFFF:08X} "
          f"tymed={med.tymed}")
    count = 0
    if hr == 0:
        addr = kernel32.GlobalLock(med.data)
        size = ctypes.windll.kernel32.GlobalSize(ctypes.c_void_p(med.data))
        count = ctypes.cast(addr, ctypes.POINTER(ctypes.c_uint32))[0]
        print(f"  GlobalSize={size} cItems={count} "
              f"(ожидается 4+592*cItems = {4 + 592 * count})")
        base = addr + 4
        for i in range(count):
            fd = ctypes.cast(base + i * 592,
                             ctypes.POINTER(FILEDESCRIPTORW))[0]
            size64 = (fd.nFileSizeHigh << 32) | fd.nFileSizeLow
            print(f"  [{i}] name={fd.cFileName!r} flags=0x{fd.dwFlags:08X} "
                  f"attrs=0x{fd.dwFileAttributes:08X} size={size64}")
        kernel32.GlobalUnlock(med.data)
        ole32.ReleaseStgMedium(ctypes.byref(med))

    # --- сетка QueryGetData: по каким правилам обёртка OLE отвечает?
    print("--- сетка QueryGetData(FileContents) ---")
    for lindex in (-1, 0, 1):
        row = []
        for tymed in (1, 2, 4, 8, 16, 32, 64, 5, 0xFFFFFFFF):
            probe = FORMATETC(CF_FILECONTENTS, None, DVASPECT_CONTENT, lindex,
                              tymed)
            hrq = query(ctypes.byref(probe)) & 0xFFFFFFFF
            row.append(f"tymed={tymed}:{'OK' if hrq == 0 else hex(hrq)}")
        print(f"  lindex={lindex:<3} " + "  ".join(row))
    print("--- сетка QueryGetData(FileGroupDescriptorW) ---")
    for lindex in (-1, 0):
        probe = FORMATETC(CF_FILEDESCRIPTORW, None, DVASPECT_CONTENT, lindex, 1)
        hrq = query(ctypes.byref(probe)) & 0xFFFFFFFF
        print(f"  lindex={lindex} -> {'OK' if hrq == 0 else hex(hrq)}")

    # --- содержимое каждого файла
    for i in range(max(count, 1)):
        fe2 = FORMATETC(CF_FILECONTENTS, None, DVASPECT_CONTENT, i, TYMED_ISTREAM)
        print(f"QueryGetData(FileContents, lindex={i}) -> "
              f"0x{query(ctypes.byref(fe2)) & 0xFFFFFFFF:08X}")
        med2 = STGMEDIUM()
        hr = get_data(ctypes.byref(fe2), ctypes.byref(med2))
        print(f"GetData(FileContents, lindex={i}) -> 0x{hr & 0xFFFFFFFF:08X} "
              f"tymed={med2.tymed}")
        if hr != 0 or med2.tymed != TYMED_ISTREAM:
            continue
        stream = med2.data
        st = STATSTG()
        hrs = vcall(stream, 12, HR, [ctypes.POINTER(STATSTG), wintypes.DWORD])(
            ctypes.byref(st), 1)
        print(f"  IStream::Stat -> 0x{hrs & 0xFFFFFFFF:08X} cbSize={st.cbSize}")
        read = vcall(stream, 3, HR, [LP, ctypes.c_ulong,
                                     ctypes.POINTER(ctypes.c_ulong)])
        buf = ctypes.create_string_buffer(65536)
        got = ctypes.c_ulong()
        digest = hashlib.sha256()
        total = 0
        first = b""
        while True:
            hrr = read(ctypes.cast(buf, LP), 65536, ctypes.byref(got))
            if hrr != 0:
                print(f"  IStream::Read -> 0x{hrr & 0xFFFFFFFF:08X} (остановка)")
                break
            if got.value == 0:
                break
            chunk = buf.raw[:got.value]
            if not first:
                first = chunk[:60]
            digest.update(chunk)
            total += got.value
            if total > 8 * 1024 * 1024:
                print("  ... обрываю чтение на 8 МиБ")
                break
        print(f"  прочитано {total} байт sha256={digest.hexdigest()[:16]} "
              f"начало={first!r}")
        ole32.ReleaseStgMedium(ctypes.byref(med2))

    vcall(obj, 2, ctypes.c_ulong, [])()
    ole32.OleUninitialize()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
