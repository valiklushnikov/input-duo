"""Спайк: процесс B — читает буфер обмена Windows напрямую через ctypes.

Не тест, не импортируется ниоткуда. Запускается как отдельный процесс из
spike_lazy_mimedata.py (процесс A, Qt), чтобы чтение буфера пересекало
границу процессов. Именно межпроцессное чтение запускает у Windows
отложенную отрисовку (WM_RENDERFORMAT) для delay-rendered форматов — чтение
QMimeData в том же процессе, где он был положен, ничего не доказывает, т.к.
Qt может просто вернуть тот же Python-объект напрямую.

Никаких новых зависимостей: только ctypes и стандартная библиотека.

Запуск (обычно не руками, а из процесса A):
    ../.venv/Scripts/python.exe tests/clipboard/spike_read_clipboard.py
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes

CF_UNICODETEXT = 13

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

user32.OpenClipboard.argtypes = [wintypes.HWND]
user32.OpenClipboard.restype = wintypes.BOOL
user32.GetClipboardData.argtypes = [wintypes.UINT]
user32.GetClipboardData.restype = wintypes.HANDLE
user32.CloseClipboard.restype = wintypes.BOOL

kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalLock.restype = wintypes.LPVOID
kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
kernel32.GlobalUnlock.restype = wintypes.BOOL


def read_clipboard_text() -> str | None:
    """Читает CF_UNICODETEXT из системного буфера обмена через Win32 API."""
    if not user32.OpenClipboard(None):
        raise OSError("OpenClipboard failed")
    try:
        handle = user32.GetClipboardData(CF_UNICODETEXT)
        if not handle:
            return None
        pointer = kernel32.GlobalLock(handle)
        if not pointer:
            return None
        try:
            text = ctypes.wstring_at(pointer)
        finally:
            kernel32.GlobalUnlock(handle)
        return text
    finally:
        user32.CloseClipboard()


def main() -> int:
    text = read_clipboard_text()
    print(f"process B read from Win32 clipboard: {text!r}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
