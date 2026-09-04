"""SPIKE: открывает настоящее окно Проводника на папке, надёжно передаёт ему
фокус и жмёт Ctrl+V. Следит за окном прогресса и за файлами.

    python spike_explorer_paste.py <папка> [--wait 70] [--cancel-after 12]
"""

from __future__ import annotations

import argparse
import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from spike_com import kernel32, user32  # noqa: E402

ENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, ctypes.c_void_p)
WM_CLOSE = 0x0010
VK_CONTROL = 0x11
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002


def class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def window_text(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(512)
    user32.GetWindowTextW(hwnd, buf, 512)
    return buf.value


def top_windows(cls: str):
    found = []

    def cb(hwnd, _):
        if class_name(hwnd) == cls:
            pid = wintypes.DWORD()
            tid = user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            found.append((hwnd, pid.value, tid,
                          bool(user32.IsWindowVisible(hwnd)), window_text(hwnd)))
        return True

    user32.EnumWindows(ENUMPROC(cb), None)
    return found


def child_of(parent, cls: str):
    return user32.FindWindowExW(parent, None, cls, None)


def find_descendant(root, cls: str):
    """Проводник прячет SHELLDLL_DefView под ShellTabWindowClass — ищем вглубь."""
    hit = []

    def cb(hwnd, _):
        if class_name(hwnd) == cls:
            hit.append(hwnd)
            return False
        return True

    user32.EnumChildWindows(root, ENUMPROC(cb), None)
    return hit[0] if hit else 0


def focus_explorer(hwnd) -> bool:
    """Отдаём Проводнику настоящий фокус клавиатуры."""
    target_tid = user32.GetWindowThreadProcessId(hwnd, None)
    my_tid = kernel32.GetCurrentThreadId()
    user32.AttachThreadInput(my_tid, target_tid, True)
    try:
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        view = find_descendant(hwnd, "SHELLDLL_DefView")
        inner = child_of(view, "DirectUIHWND") if view else 0
        if not inner and view:
            inner = child_of(view, "SysListView32")
        if inner:
            user32.SetFocus(inner)
        elif view:
            user32.SetFocus(view)
        focused = user32.GetFocus()
        fore = user32.GetForegroundWindow()
        print(f"  фокус: view={view} inner={inner} GetFocus={focused} "
              f"foreground={fore} (нужно {hwnd})", flush=True)
        return fore == hwnd
    finally:
        user32.AttachThreadInput(my_tid, target_tid, False)


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


def click_view(hwnd) -> None:
    """Настоящий щелчок по пустому месту списка: только так Проводник
    по-честному забирает фокус клавиатуры."""
    view = find_descendant(hwnd, "SHELLDLL_DefView")
    rect = RECT()
    user32.GetWindowRect(view or hwnd, ctypes.byref(rect))
    x = rect.left + (rect.right - rect.left) // 2
    y = rect.top + (rect.bottom - rect.top) // 2
    print(f"  rect списка: ({rect.left},{rect.top})-({rect.right},{rect.bottom})",
          flush=True)
    user32.SetCursorPos(x, y)
    time.sleep(0.2)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFTDOWN
    time.sleep(0.05)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFTUP
    time.sleep(0.4)
    print(f"  щелчок по списку в ({x},{y}), view={view}", flush=True)


def send_ctrl_v() -> None:
    user32.keybd_event(VK_CONTROL, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_V, 0, 0, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_V, 0, KEYEVENTF_KEYUP, 0)
    time.sleep(0.05)
    user32.keybd_event(VK_CONTROL, 0, KEYEVENTF_KEYUP, 0)


def put_real_hdrop(paths) -> None:
    """КОНТРОЛЬ: кладём настоящий CF_HDROP (без отложенной отрисовки), чтобы
    проверить саму автоматику Ctrl+V. Иначе отсутствие файла ничего не значит."""
    from spike_shell_types import GMEM_MOVEABLE, hglobal_from_bytes

    listing = "".join(str(Path(p).resolve()) + "\x00" for p in paths) + "\x00"
    body = listing.encode("utf-16-le")
    header = (int(20).to_bytes(4, "little")      # pFiles
              + b"\x00" * 8                      # pt
              + int(0).to_bytes(4, "little")     # fNC
              + int(1).to_bytes(4, "little"))    # fWide
    handle = hglobal_from_bytes(header + body)
    if not user32.OpenClipboard(None):
        raise OSError("OpenClipboard failed")
    user32.EmptyClipboard()
    CF_HDROP = 15
    user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
    user32.SetClipboardData(CF_HDROP, ctypes.c_void_p(handle))
    user32.CloseClipboard()
    print(f"КОНТРОЛЬ: положил настоящий CF_HDROP: {list(paths)}", flush=True)


def snapshot(root: Path):
    out = {}
    for path in root.rglob("*"):
        try:
            out[str(path.relative_to(root))] = (
                "DIR" if path.is_dir() else path.stat().st_size)
        except OSError:
            pass
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dest")
    parser.add_argument("--wait", type=float, default=70)
    parser.add_argument("--cancel-after", type=float, default=0)
    parser.add_argument("--control", default=None,
                        help="контроль: положить этот файл как настоящий CF_HDROP")
    args = parser.parse_args()
    dest = Path(args.dest)
    leaf = dest.name
    if args.control:
        put_real_hdrop([args.control])

    print(f"EXPLORER-PASTE pid={os.getpid()} dest={dest}", flush=True)
    before = {h for h, *_ in top_windows("CabinetWClass")}
    subprocess.Popen(["explorer.exe", str(dest)])

    hwnd = 0
    for _ in range(40):
        time.sleep(0.4)
        for h, pid, tid, visible, title in top_windows("CabinetWClass"):
            if visible and (title == leaf or h not in before):
                hwnd = h
                break
        if hwnd:
            break
    print(f"окно Проводника hwnd={hwnd} title={window_text(hwnd)!r}", flush=True)
    if not hwnd:
        print("EXPLORER-WINDOW-NOT-FOUND", flush=True)
        return 3

    time.sleep(1.0)
    ok = focus_explorer(hwnd)
    click_view(hwnd)
    print(f"FOREGROUND-OK={ok} foreground-now={user32.GetForegroundWindow()}",
          flush=True)
    t0 = time.monotonic()
    send_ctrl_v()
    print("CTRL+V отправлен", flush=True)

    cancelled = False
    seen = False
    last_state = None
    last_files = None
    stable = 0
    while time.monotonic() - t0 < args.wait:
        elapsed = time.monotonic() - t0
        progress = top_windows("OperationStatusWindow")
        visible = [p for p in progress if p[3]]
        if visible and not seen:
            seen = True
            print(f"PROGRESS-APPEARED на {elapsed:.1f}s", flush=True)
        state = tuple((p[1], p[3], p[4]) for p in progress)
        if state != last_state:
            print(f"  [{elapsed:6.1f}s] окна прогресса: {state}", flush=True)
            last_state = state
        if args.cancel_after and not cancelled and elapsed >= args.cancel_after:
            cancelled = True
            if visible:
                user32.PostMessageW(visible[0][0], WM_CLOSE, 0, 0)
                print(f"CANCEL: WM_CLOSE -> hwnd={visible[0][0]} "
                      f"pid={visible[0][1]} на {elapsed:.1f}s", flush=True)
            else:
                print(f"CANCEL: видимого окна прогресса нет на {elapsed:.1f}s",
                      flush=True)
        files = snapshot(dest)
        if files != last_files:
            print(f"  [{elapsed:6.1f}s] файлы: {files}", flush=True)
            last_files = files
            stable = 0
        else:
            stable += 1
        if stable >= 10 and (seen and not visible):
            break
        if stable >= 24:
            break
        time.sleep(0.5)

    print(f"PROGRESS-SEEN={seen}", flush=True)
    print("RESULT-BEGIN", flush=True)
    for rel, size in sorted(snapshot(dest).items()):
        print(f"  {rel} -> {size}", flush=True)
    print("RESULT-END", flush=True)
    user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
    print(f"EXPLORER-PASTE done, {time.monotonic() - t0:.1f}s", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
