"""SPIKE: отдельный процесс — берёт IDataObject из буфера обмена и «бросает»
его на IDropTarget папки назначения. Это тот же код оболочки, что работает при
перетаскивании и при Ctrl+V из Outlook/WinSCP.

    python spike_drop_target.py <папка> [--wait 60] [--cancel-after 8]
"""

from __future__ import annotations

import argparse
import ctypes
import os
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spike_com import GUID, guid, kernel32, ole32, user32  # noqa: E402
from spike_shell_types import POINTL  # noqa: E402

HR = ctypes.c_long
LP = ctypes.c_void_p

IID_IShellItem = guid("{43826D1E-E718-42EE-BC55-A1E261C37BFE}")
BHID_SFUIObject = guid("{3981E225-F559-11D3-8E3A-00C04F6837D5}")
IID_IDropTarget = guid("{00000122-0000-0000-C000-000000000046}")

MK_LBUTTON = 0x0001
DROPEFFECT_COPY = 2
WM_CLOSE = 0x0010

shell32 = ctypes.WinDLL("shell32", use_last_error=True)


def vcall(ptr, index, restype, argtypes):
    vtbl = ctypes.cast(ptr, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))[0]
    fn = ctypes.WINFUNCTYPE(restype, ctypes.c_void_p, *argtypes)(vtbl[index])
    return lambda *a: fn(ptr, *a)


class MSG(ctypes.Structure):
    _fields_ = [("hwnd", wintypes.HWND), ("message", ctypes.c_uint),
                ("wParam", ctypes.c_size_t), ("lParam", ctypes.c_ssize_t),
                ("time", wintypes.DWORD), ("pt_x", ctypes.c_long),
                ("pt_y", ctypes.c_long), ("private", wintypes.DWORD)]


def pump(seconds: float) -> None:
    """Крутим очередь сообщений — оболочка живёт в этом процессе."""
    end = time.monotonic() + seconds
    msg = MSG()
    while time.monotonic() < end:
        while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))
        time.sleep(0.01)


def snapshot(root: Path):
    out = {}
    for path in root.rglob("*"):
        try:
            out[str(path.relative_to(root))] = (
                "DIR" if path.is_dir() else path.stat().st_size)
        except OSError:
            pass
    return out


def window_title(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, ctypes.c_void_p)


def find_progress_windows():
    """Все верхнеуровневые окна класса OperationStatusWindow: (hwnd, pid,
    видимо ли, заголовок)."""
    found = []

    def cb(hwnd, _):
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(hwnd, cls, 256)
        if cls.value == "OperationStatusWindow":
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append((hwnd, pid.value, bool(user32.IsWindowVisible(hwnd)),
                          window_title(hwnd)))
        return True

    user32.EnumWindows(ENUMPROC(cb), None)
    return found


def do_drop(dest: Path, result: dict) -> None:
    """Весь COM — в рабочем потоке помощника, чтобы главный поток мог
    следить за окном прогресса, пока Drop блокирует."""
    ole32.OleInitialize(None)
    print(f"  drop-thread tid={kernel32.GetCurrentThreadId()}", flush=True)
    data = LP()
    hr = ole32.OleGetClipboard(ctypes.byref(data))
    print(f"OleGetClipboard -> 0x{hr & 0xFFFFFFFF:08X}", flush=True)
    if hr != 0:
        result["done"] = True
        return

    item = LP()
    shell32.SHCreateItemFromParsingName.argtypes = [
        ctypes.c_wchar_p, LP, ctypes.POINTER(GUID), ctypes.POINTER(LP)]
    hr = shell32.SHCreateItemFromParsingName(
        str(dest), None, ctypes.byref(IID_IShellItem), ctypes.byref(item))
    print(f"SHCreateItemFromParsingName -> 0x{hr & 0xFFFFFFFF:08X}", flush=True)
    if hr != 0:
        result["done"] = True
        return

    target = LP()
    bind = vcall(item, 3, HR, [LP, ctypes.POINTER(GUID), ctypes.POINTER(GUID),
                               ctypes.POINTER(LP)])
    hr = bind(None, ctypes.byref(BHID_SFUIObject), ctypes.byref(IID_IDropTarget),
              ctypes.byref(target))
    print(f"BindToHandler(BHID_SFUIObject, IDropTarget) -> "
          f"0x{hr & 0xFFFFFFFF:08X}", flush=True)
    if hr != 0:
        result["done"] = True
        return

    point = POINTL(20, 20)
    effect = wintypes.DWORD(DROPEFFECT_COPY)
    drag_enter = vcall(target, 3, HR, [LP, wintypes.DWORD, POINTL,
                                       ctypes.POINTER(wintypes.DWORD)])
    hr = drag_enter(data, MK_LBUTTON, point, ctypes.byref(effect))
    print(f"IDropTarget::DragEnter -> 0x{hr & 0xFFFFFFFF:08X} "
          f"effect={effect.value}", flush=True)
    if hr != 0 or effect.value == 0:
        print("DROP-REFUSED: цель не принимает этот объект", flush=True)
        result["done"] = True
        return

    effect = wintypes.DWORD(DROPEFFECT_COPY)
    started = time.monotonic()
    result["started"] = started
    drop = vcall(target, 6, HR, [LP, wintypes.DWORD, POINTL,
                                 ctypes.POINTER(wintypes.DWORD)])
    hr = drop(data, MK_LBUTTON, point, ctypes.byref(effect))
    print(f"IDropTarget::Drop -> 0x{hr & 0xFFFFFFFF:08X} effect={effect.value} "
          f"через {time.monotonic() - started:.3f}s", flush=True)
    result["drop_hr"] = hr
    result["done"] = True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dest")
    parser.add_argument("--wait", type=float, default=60)
    parser.add_argument("--cancel-after", type=float, default=0)
    args = parser.parse_args()
    dest = Path(args.dest)

    ole32.OleInitialize(None)
    print(f"DROP pid={os.getpid()} tid={kernel32.GetCurrentThreadId()} "
          f"dest={dest}", flush=True)

    result: dict = {"done": False}
    worker = threading.Thread(target=do_drop, args=(dest, result), daemon=True)
    worker.start()

    state_dialogs = [None]
    cancelled = False
    seen_progress = False
    stable = 0
    last = None
    t0 = time.monotonic()
    deadline = t0 + args.wait
    while time.monotonic() < deadline:
        pump(0.25)
        started = result.get("started", t0)
        elapsed = time.monotonic() - started
        dialogs = []

        def dcb(hwnd, _):
            if not user32.IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cls, 256)
            # все видимые окна ЭТОГО процесса + любые диалоги оболочки
            if pid.value == os.getpid() or cls.value in ("#32770",
                                                         "OperationStatusWindow"):
                dialogs.append((pid.value, cls.value, window_title(hwnd)))
            return True

        user32.EnumWindows(ENUMPROC(dcb), None)
        if dialogs != state_dialogs[0]:
            print(f"  [{time.monotonic() - t0:6.1f}s] видимые диалоги: {dialogs}",
                  flush=True)
            state_dialogs[0] = dialogs
        # окно прогресса оболочки в НАШЕМ процессе — это обычный диалог #32770
        own_dialog = 0

        def ocb(hwnd2, _):
            nonlocal own_dialog
            if not user32.IsWindowVisible(hwnd2):
                return True
            pid = wintypes.DWORD()
            user32.GetWindowThreadProcessId(hwnd2, ctypes.byref(pid))
            if pid.value != os.getpid():
                return True
            cls = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd2, cls, 256)
            if cls.value in ("#32770", "OperationStatusWindow"):
                own_dialog = hwnd2
                return False
            return True

        user32.EnumWindows(ENUMPROC(ocb), None)
        windows = find_progress_windows()
        hwnd = own_dialog
        for h, pid, visible, title in windows:
            if pid == os.getpid():
                hwnd = h
        state = tuple((pid, visible, title) for _, pid, visible, title in windows)
        if windows and not seen_progress:
            seen_progress = True
            print(f"PROGRESS: окно(а) прогресса появились на {elapsed:.1f}s",
                  flush=True)
        if state != last:
            print(f"  [{elapsed:6.1f}s] окна прогресса: {state} "
                  f"(наш pid={os.getpid()})", flush=True)
            last = state
        if args.cancel_after and not cancelled and elapsed >= args.cancel_after:
            cancelled = True
            print(f"CANCEL: OperationStatusWindow={hwnd} на {elapsed:.1f}s",
                  flush=True)
            if hwnd:
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
                print("CANCEL: послал WM_CLOSE окну прогресса", flush=True)
            else:
                print("CANCEL: окно прогресса не найдено", flush=True)
        if result["done"]:
            stable += 1
            if stable >= 6:
                break

    if not seen_progress:
        print("PROGRESS: окно прогресса ни разу не появилось", flush=True)
    print("RESULT-BEGIN", flush=True)
    for rel, size in sorted(snapshot(dest).items()):
        print(f"  {rel} -> {size}", flush=True)
    print("RESULT-END", flush=True)
    print(f"DROP done, {time.monotonic() - t0:.1f}s, "
          f"вернулся={result.get('done')}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
