"""Чужое копирование посреди вставки не удаляет объект, который читает Windows.

Qt 6.10.1, qwindowsclipboard.cpp, QWindowsClipboard::clipboardViewerWndProc:
на WM_CLIPBOARDUPDATE (0x031D) окно "QtClipboardView" делает
``if (!owned && m_data) releaseIData();``, а releaseIData() - это
``delete m_data->mimeData()``. Если другая программа скопировала, пока наш
RemoteMimeData.retrieveData ждёт сеть во вложенном цикле, наш объект удаляется
у него из-под стека (probe_destroyed.py: DESTROYED через 4 мс после
обновления, за 4.5 с до возврата из чтения; probe_external_copy.py - access
violation 3/3).

Здесь всё настоящее, кроме окна Qt: настоящее message-only окно с заголовком
"QtClipboardView", настоящий PostMessageW, настоящая очередь сообщений и
диспетчер Qt. Фильтр-шпион установлен РАНЬШЕ проверяемого, а Qt зовёт
фильтры от последнего к первому - поэтому шпион видит ровно то, что дошло бы
до окна буфера Qt.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from ctypes import wintypes

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import QAbstractNativeEventFilter, QObject, Signal

from duo_input.clipboard.backend import RemoteMimeData
from duo_input.clipboard.offer import ClipboardOffer, describe
from duo_input.clipboard.windows_backend import WindowsClipboardBackend
from duo_input.clipboard.windows_clipboard_events import ClipboardUpdateDeferral

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows message queue")

WM_CLIPBOARDUPDATE = 0x031D
WM_USER = 0x0400
THEIRS = "2" * 32

user32 = ctypes.WinDLL("user32", use_last_error=True) if sys.platform == "win32" else None
if user32 is not None:
    user32.CreateWindowExW.restype = wintypes.HWND
    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
    ]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.PeekMessageW.argtypes = [
        ctypes.POINTER(wintypes.MSG), wintypes.HWND, wintypes.UINT, wintypes.UINT, wintypes.UINT
    ]


def _message_window(title: str) -> int:
    hwnd = user32.CreateWindowExW(
        0, "STATIC", title, 0, 0, 0, 0, 0, wintypes.HWND(-3), None, None, None
    )
    assert hwnd, ctypes.get_last_error()
    return hwnd


class _Spy(QAbstractNativeEventFilter):
    """Что дошло бы до окна: сообщения, которые проверяемый фильтр пропустил."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[tuple[int, int, int, int]] = []

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt API
        if bytes(event_type) == b"windows_generic_MSG":
            msg = wintypes.MSG.from_address(int(message))
            if msg.message in (WM_CLIPBOARDUPDATE, WM_USER):
                self.seen.append((msg.hWnd, msg.message, msg.wParam, msg.lParam))
        return False, 0


@pytest.fixture
def windows(qapp):
    qt_window = _message_window("QtClipboardView")
    other_window = _message_window("SomebodyElsesWindow")
    spy = _Spy()
    qapp.installNativeEventFilter(spy)
    yield qt_window, other_window, spy
    qapp.removeNativeEventFilter(spy)
    user32.DestroyWindow(qt_window)
    user32.DestroyWindow(other_window)


@pytest.fixture
def deferral(qapp, windows):
    deferral = ClipboardUpdateDeferral()
    deferral.install(qapp)
    yield deferral
    deferral.uninstall()


def _post(qapp, hwnd: int, message: int = WM_CLIPBOARDUPDATE, w: int = 1, l: int = 2) -> None:
    assert user32.PostMessageW(hwnd, message, w, l)
    qapp.processEvents()


def _while_reading(work) -> None:
    """Выполнить work внутри настоящего чтения RemoteMimeData (как вставка)."""

    def fetcher(mime: str) -> bytes:
        work()
        return b"x"

    data = RemoteMimeData(ClipboardOffer(THEIRS, 1, describe({"text/plain": b"x"})), fetcher)
    data.data("text/plain")


def _settle(qapp) -> None:
    for _ in range(3):  # singleShot(0), затем повторно отправленное сообщение
        qapp.processEvents()


def test_an_update_while_nothing_is_read_reaches_qt(qapp, windows, deferral):
    qt_window, _other, spy = windows

    _post(qapp, qt_window)

    assert spy.seen == [(qt_window, WM_CLIPBOARDUPDATE, 1, 2)]


def test_an_update_during_a_read_is_held_and_redelivered_once_after_it(qapp, windows, deferral, caplog):
    caplog.set_level(logging.INFO, logger="duo_input.clipboard")
    qt_window, _other, spy = windows
    during: list[list] = []

    def work() -> None:
        _post(qapp, qt_window, w=5, l=6)
        during.append(list(spy.seen))

    _while_reading(work)
    after_read = list(spy.seen)
    _settle(qapp)

    assert during == [[]]
    assert after_read == []  # никогда не синхронно из retrieveData
    assert spy.seen == [(qt_window, WM_CLIPBOARDUPDATE, 5, 6)]
    messages = [r.getMessage() for r in caplog.records]
    assert "clipboard_update_deferred reason=paste_in_progress" in messages
    assert "clipboard_update_redelivered" in messages


def test_several_updates_during_one_read_are_redelivered_once(qapp, windows, deferral):
    qt_window, _other, spy = windows

    def work() -> None:
        for n in range(3):
            _post(qapp, qt_window, w=n, l=n)

    _while_reading(work)
    _settle(qapp)

    assert [entry[1] for entry in spy.seen] == [WM_CLIPBOARDUPDATE]


def test_other_messages_to_qts_window_are_untouched_during_a_read(qapp, windows, deferral):
    qt_window, _other, spy = windows
    during: list[list] = []

    def work() -> None:
        _post(qapp, qt_window, message=WM_USER)
        during.append(list(spy.seen))

    _while_reading(work)

    assert during == [[(qt_window, WM_USER, 1, 2)]]


def test_updates_to_other_windows_are_untouched_during_a_read(qapp, windows, deferral):
    _qt, other_window, spy = windows
    during: list[list] = []

    def work() -> None:
        _post(qapp, other_window)
        during.append(list(spy.seen))

    _while_reading(work)
    _settle(qapp)

    assert during == [[(other_window, WM_CLIPBOARDUPDATE, 1, 2)]]
    assert len(spy.seen) == 1  # и ничего не отправлено повторно


def test_nothing_is_redelivered_when_nothing_was_held(qapp, windows, deferral):
    _qt, _other, spy = windows

    _while_reading(lambda: None)
    _settle(qapp)

    assert spy.seen == []


def test_a_read_starting_before_the_redelivery_postpones_it(qapp, windows, deferral):
    qt_window, _other, spy = windows
    during_second: list[list] = []

    _while_reading(lambda: _post(qapp, qt_window))

    def second_read() -> None:
        _settle(qapp)  # отложенная доставка уже в очереди - и должна ждать
        during_second.append(list(spy.seen))

    _while_reading(second_read)
    _settle(qapp)

    assert during_second == [[]]
    assert [entry[1] for entry in spy.seen] == [WM_CLIPBOARDUPDATE]


def test_after_uninstall_updates_pass_even_during_a_read(qapp, windows):
    qt_window, _other, spy = windows
    deferral = ClipboardUpdateDeferral()
    deferral.install(qapp)
    deferral.uninstall()
    during: list[list] = []

    def work() -> None:
        _post(qapp, qt_window)
        during.append(list(spy.seen))

    _while_reading(work)

    assert during == [[(qt_window, WM_CLIPBOARDUPDATE, 1, 2)]]


# ----------------------------------------------------------- граница буфера


class _Clipboard(QObject):
    dataChanged = Signal()


def test_the_backend_holds_updates_between_start_and_stop(qapp, windows):
    qt_window, _other, spy = windows
    backend = WindowsClipboardBackend(_Clipboard())
    during: list[list] = []

    def work() -> None:
        _post(qapp, qt_window)
        during.append(list(spy.seen))

    backend.start()
    _while_reading(work)
    _settle(qapp)
    backend.stop()
    _while_reading(work)

    assert during[0] == []  # после start - задержано
    assert len(during[1]) == 2  # после stop - доходит сразу


def test_each_read_gets_its_own_redelivery(qapp, windows, deferral):
    qt_window, _other, spy = windows

    _while_reading(lambda: _post(qapp, qt_window, w=1, l=1))
    _settle(qapp)
    _while_reading(lambda: _post(qapp, qt_window, w=2, l=2))
    _settle(qapp)

    assert spy.seen == [
        (qt_window, WM_CLIPBOARDUPDATE, 1, 1),
        (qt_window, WM_CLIPBOARDUPDATE, 2, 2),
    ]


def test_the_hold_is_logged_once_per_read(qapp, windows, deferral, caplog):
    caplog.set_level(logging.INFO, logger="duo_input.clipboard")
    qt_window, _other, _spy = windows

    def work() -> None:
        for _ in range(3):
            _post(qapp, qt_window)

    _while_reading(work)
    _settle(qapp)

    messages = [r.getMessage() for r in caplog.records]
    assert messages.count("clipboard_update_deferred reason=paste_in_progress") == 1
    assert messages.count("clipboard_update_redelivered") == 1


def test_off_windows_install_does_nothing(qapp, windows, monkeypatch):
    qt_window, _other, spy = windows
    monkeypatch.setattr(sys, "platform", "darwin")
    deferral = ClipboardUpdateDeferral()
    deferral.install(qapp)
    during: list[list] = []

    def work() -> None:
        _post(qapp, qt_window)
        during.append(list(spy.seen))

    try:
        _while_reading(work)
    finally:
        deferral.uninstall()

    assert len(during[0]) == 1


def test_install_without_an_application_does_nothing():
    deferral = ClipboardUpdateDeferral()

    deferral.install(None)
    deferral.uninstall()


def test_the_redelivery_is_never_posted_from_inside_the_read(qapp, windows, deferral):
    """Повторная отправка - из очереди событий, не из retrieveData: OLE ещё
    внутри своего вызова, и его собственный цикл мог бы выбрать сообщение."""
    qt_window, _other, _spy = windows
    msg = wintypes.MSG()

    _while_reading(lambda: _post(qapp, qt_window))
    queued_right_after_the_read = user32.PeekMessageW(
        ctypes.byref(msg), qt_window, WM_CLIPBOARDUPDATE, WM_CLIPBOARDUPDATE, 0  # PM_NOREMOVE
    )
    _settle(qapp)

    assert queued_right_after_the_read == 0


def test_waiting_for_no_reads_is_one_shot_and_deduplicated(qapp):
    from duo_input.clipboard.backend import call_when_no_reads

    calls: list[str] = []

    def callback() -> None:
        calls.append("idle")

    call_when_no_reads(callback)
    call_when_no_reads(callback)
    _while_reading(lambda: None)
    _while_reading(lambda: None)

    assert calls == ["idle"]
