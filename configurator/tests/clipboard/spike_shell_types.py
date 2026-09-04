"""СПАЙК: структуры Win32/OLE, нужные для CFSTR_FILEDESCRIPTORW/FILECONTENTS."""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from spike_com import GUID, kernel32, user32

DWORD = wintypes.DWORD
LONG = wintypes.LONG
WORD = wintypes.WORD
MAX_PATH = 260

TYMED_HGLOBAL = 1
TYMED_ISTREAM = 4
DVASPECT_CONTENT = 1
DATADIR_GET = 1
DATADIR_SET = 2

FD_CLSID = 0x00000001
FD_ATTRIBUTES = 0x00000004
FD_CREATETIME = 0x00000008
FD_ACCESSTIME = 0x00000010
FD_WRITESTIME = 0x00000020
FD_FILESIZE = 0x00000040
FD_PROGRESSUI = 0x00004000
FD_UNICODE = 0x80000000

FILE_ATTRIBUTE_NORMAL = 0x80
FILE_ATTRIBUTE_DIRECTORY = 0x10

GMEM_MOVEABLE = 0x0002
DROPEFFECT_COPY = 2

STGTY_STREAM = 2
STREAM_SEEK_SET = 0
STREAM_SEEK_CUR = 1
STREAM_SEEK_END = 2


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", DWORD), ("dwHighDateTime", DWORD)]


class SIZEL(ctypes.Structure):
    _fields_ = [("cx", LONG), ("cy", LONG)]


class POINTL(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class FILEDESCRIPTORW(ctypes.Structure):
    _fields_ = [
        ("dwFlags", DWORD),
        ("clsid", GUID),
        ("sizel", SIZEL),
        ("pointl", POINTL),
        ("dwFileAttributes", DWORD),
        ("ftCreationTime", FILETIME),
        ("ftLastAccessTime", FILETIME),
        ("ftLastWriteTime", FILETIME),
        ("nFileSizeHigh", DWORD),
        ("nFileSizeLow", DWORD),
        ("cFileName", ctypes.c_wchar * MAX_PATH),
    ]


class FORMATETC(ctypes.Structure):
    _fields_ = [
        ("cfFormat", WORD),
        ("ptd", ctypes.c_void_p),
        ("dwAspect", DWORD),
        ("lindex", LONG),
        ("tymed", DWORD),
    ]


class STGMEDIUM(ctypes.Structure):
    _fields_ = [
        ("tymed", DWORD),
        ("data", ctypes.c_void_p),
        ("pUnkForRelease", ctypes.c_void_p),
    ]


class STATSTG(ctypes.Structure):
    _fields_ = [
        ("pwcsName", ctypes.c_void_p),
        ("type", DWORD),
        ("cbSize", ctypes.c_ulonglong),
        ("mtime", FILETIME),
        ("ctime", FILETIME),
        ("atime", FILETIME),
        ("grfMode", DWORD),
        ("grfLocksSupported", DWORD),
        ("clsid", GUID),
        ("grfStateBits", DWORD),
        ("reserved", DWORD),
    ]


user32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
user32.RegisterClipboardFormatW.restype = ctypes.c_uint
user32.GetClipboardFormatNameW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_int]
user32.GetClipboardFormatNameW.restype = ctypes.c_int

CF_FILEDESCRIPTORW = user32.RegisterClipboardFormatW("FileGroupDescriptorW")
CF_FILECONTENTS = user32.RegisterClipboardFormatW("FileContents")
CF_PREFERREDDROPEFFECT = user32.RegisterClipboardFormatW("Preferred DropEffect")
CF_PERFORMEDDROPEFFECT = user32.RegisterClipboardFormatW("Performed DropEffect")
CF_LOGICALPERFORMEDDROPEFFECT = user32.RegisterClipboardFormatW(
    "Logical Performed DropEffect"
)
CF_PASTESUCCEEDED = user32.RegisterClipboardFormatW("Paste Succeeded")

_BUILTIN = {
    1: "CF_TEXT", 2: "CF_BITMAP", 3: "CF_METAFILEPICT", 8: "CF_DIB",
    13: "CF_UNICODETEXT", 15: "CF_HDROP", 17: "CF_DIBV5",
}


def format_name(cf: int) -> str:
    if cf in _BUILTIN:
        return _BUILTIN[cf]
    buf = ctypes.create_unicode_buffer(256)
    n = user32.GetClipboardFormatNameW(cf, buf, 256)
    return buf.value if n else f"#{cf}"


kernel32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
kernel32.GlobalAlloc.restype = ctypes.c_void_p
kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
kernel32.GlobalLock.restype = ctypes.c_void_p
kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.argtypes = [ctypes.c_void_p]
kernel32.GlobalFree.restype = ctypes.c_void_p


def hglobal_from_bytes(payload: bytes) -> int:
    handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    if not handle:
        raise MemoryError("GlobalAlloc failed")
    address = kernel32.GlobalLock(handle)
    ctypes.memmove(address, payload, len(payload))
    kernel32.GlobalUnlock(handle)
    return handle


def file_group_descriptor(entries: list[dict]) -> bytes:
    """entries: [{name: 'a/b.txt', size: int|None, directory: bool}]"""
    head = ctypes.c_uint32(len(entries))
    blob = bytearray(bytes(head))
    for entry in entries:
        fd = FILEDESCRIPTORW()
        ctypes.memset(ctypes.byref(fd), 0, ctypes.sizeof(fd))
        fd.dwFlags = FD_ATTRIBUTES | FD_PROGRESSUI
        if entry.get("directory"):
            fd.dwFileAttributes = FILE_ATTRIBUTE_DIRECTORY
        else:
            fd.dwFileAttributes = FILE_ATTRIBUTE_NORMAL
            size = entry.get("size")
            if size is not None:
                fd.dwFlags |= FD_FILESIZE
                fd.nFileSizeHigh = (size >> 32) & 0xFFFFFFFF
                fd.nFileSizeLow = size & 0xFFFFFFFF
        fd.cFileName = entry["name"]
        blob += bytes(fd)
    return bytes(blob)
