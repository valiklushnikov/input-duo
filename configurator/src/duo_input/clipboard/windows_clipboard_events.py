"""Задержать «буфер больше не наш», пока Windows читает наш объект.

Qt 6.10.1, src/plugins/platforms/windows/qwindowsclipboard.cpp,
QWindowsClipboard::clipboardViewerWndProc:

    case wMClipboardUpdate:  // 0x031D, AddClipboardFormatListener
    case WM_DRAWCLIPBOARD: {
        const bool owned = ownsClipboard();
        emitChanged(QClipboard::Clipboard);
        if (!owned && m_data)
            releaseIData();          // delete m_data->mimeData()

Окно - message-only "QtClipboardView" (createDummyWindow). Если другая
программа скопировала, пока наш RemoteMimeData.retrieveData ждёт сеть во
вложенном цикле _fetch, этот вложенный цикл и доставляет WM_CLIPBOARDUPDATE:
наш объект удаляется у retrieveData из-под стека. Замер на этой машине
(probe_destroyed.py): DESTROYED через 4 мс после обновления, за 4.5 с до
возврата из чтения; probe_external_copy.py - access violation 3/3.

WM_CLIPBOARDUPDATE ставится в очередь (Post), поэтому проходит через
нативный фильтр диспетчера Qt ("windows_generic_MSG") - это проверено тем же
замером. Пока идёт хоть одно чтение RemoteMimeData, фильтр забирает это
сообщение для окна Qt и после последнего чтения отправляет его тому же окну
ещё раз - один раз, сколько бы их ни пришло: обработчик Qt параметров не
читает. Тогда Qt удаляет объект, который уже никто не читает, и только
тогда же испускает dataChanged - локальная копия обрабатывается после
вставки, а не теряется.

Единственный ctypes в clipboard/ (исключение в test_boundaries.py): MSG
читается по адресу, который Qt передаёт фильтру, и окно узнаётся по
заголовку через user32.

Известный предел: это нативный фильтр ПРИЛОЖЕНИЯ Qt
(QAbstractNativeEventFilter), и он видит только то, что проходит через
диспетчер событий Qt. Сообщение, которое доставил ЧУЖОЙ модальный цикл
(например, COM во время исходящего межпроцессного вызова - тот самый
вложенный цикл, которым OLE крутит retrieveData), идёт прямо в оконную
процедуру Qt, минуя этот фильтр: задержки не будет. Если это когда-нибудь
проявится на практике, эскалация - SetWindowSubclass на самом окне
"QtClipboardView" (перехват на уровне WNDPROC, а не диспетчера Qt).
"""

from __future__ import annotations

import ctypes
import logging
import sys

from PySide6.QtCore import QAbstractNativeEventFilter, QTimer

from .backend import call_when_no_reads, reads_in_progress

logger = logging.getLogger(__name__)

WM_CLIPBOARDUPDATE = 0x031D

#: Заголовок окна буфера Qt: createDummyWindow(..., L"QtClipboardView", ...).
QT_CLIPBOARD_WINDOW_TITLE = "QtClipboardView"

#: HWND_MESSAGE: псевдо-хэндл родителя для окон message-only (winuser.h).
_HWND_MESSAGE = -3

#: Два имени события, которыми диспетчер Qt на Windows несёт MSG* фильтру
#: (QAbstractEventDispatcher::filterNativeEvent); всё остальное - не MSG*,
#: и лезть в message по адресу для него нельзя.
_MSG_EVENT_TYPES = (b"windows_generic_MSG", b"windows_dispatcher_MSG")


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_int32), ("y", ctypes.c_int32)]


class _MSG(ctypes.Structure):
    """winuser.h MSG; ctypes.wintypes не годится для импорта вне Windows.

    В заголовке у MSG есть ещё поле ``lPrivate`` - но оно существует только
    под ``#ifdef _MAC`` и на Windows в реальном winuser.h его нет вовсе.
    Здесь структура читается только до ``pt`` включительно: Qt заполняет MSG
    целиком, но нам нужны лишь первые четыре поля (hwnd, message, wParam,
    lParam) - ``time``/``pt`` в разметке только ради правильных смещений.
    """

    _fields_ = [
        ("hwnd", ctypes.c_void_p),
        ("message", ctypes.c_uint32),
        ("wParam", ctypes.c_size_t),
        ("lParam", ctypes.c_ssize_t),
        ("time", ctypes.c_uint32),
        ("pt", _POINT),
    ]


def _user32():
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetWindowTextW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_int]
    user32.PostMessageW.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_size_t, ctypes.c_ssize_t
    ]
    user32.PostMessageW.restype = ctypes.c_int
    user32.FindWindowExW.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p
    ]
    user32.FindWindowExW.restype = ctypes.c_void_p
    return user32


def _window_title(user32, hwnd: int) -> str:
    buffer = ctypes.create_unicode_buffer(64)
    user32.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


class ClipboardUpdateDeferral(QAbstractNativeEventFilter):
    """Нативный фильтр приложения; ставится и снимается вместе с границей буфера."""

    def __init__(self) -> None:
        super().__init__()
        self._application = None
        self._user32 = _user32() if sys.platform == "win32" else None
        #: Забранное сообщение (hwnd, wParam, lParam) - не больше одного.
        self._held: tuple[int, int, int] | None = None

    def install(self, application) -> None:
        if self._user32 is None or application is None:
            return
        if not self._user32.FindWindowExW(_HWND_MESSAGE, None, None, QT_CLIPBOARD_WINDOW_TITLE):
            # Будущая Qt могла переименовать окно - тогда задержка молча не
            # сработает ни разу. Это не повод отказаться ставить фильтр.
            logger.warning("clipboard_update_deferral_window_missing")
        application.installNativeEventFilter(self)
        self._application = application

    def uninstall(self) -> None:
        if self._application is None:
            return
        self._application.removeNativeEventFilter(self)
        self._application = None

    def nativeEventFilter(self, event_type, message):  # noqa: N802 - Qt API
        # Оба типа событий Qt на Windows ("windows_generic_MSG" и
        # "windows_dispatcher_MSG") передают MSG*; всё остальное - не MSG*
        # и трогать message по адресу для него нельзя.
        if bytes(event_type) not in _MSG_EVENT_TYPES:
            return False, 0
        # reads_in_progress() - дешёвый счётчик; до него никакого ctypes на
        # КАЖДОЕ сообщение, которое видит приложение (а это почти все):
        # должен быть хоть один открытый RemoteMimeData.retrieveData.
        if not reads_in_progress():
            return False, 0
        msg = _MSG.from_address(int(message))
        if msg.message != WM_CLIPBOARDUPDATE:
            return False, 0
        if _window_title(self._user32, msg.hwnd) != QT_CLIPBOARD_WINDOW_TITLE:
            return False, 0
        if self._held is None:
            logger.info("clipboard_update_deferred reason=paste_in_progress")
            call_when_no_reads(self._on_no_reads)
        self._held = (msg.hwnd, msg.wParam, msg.lParam)
        return True, 0

    def _on_no_reads(self) -> None:
        # Изнутри retrieveData: только в очередь, доставка - после возврата.
        QTimer.singleShot(0, self._redeliver)

    def _redeliver(self) -> None:
        # Если новая вставка началась раньше, чем очередь дошла сюда, фильтр
        # заберёт это сообщение снова и дождётся конца уже её.
        if self._held is None:
            return  # случайная повторная доставка - нет-оп, а не падение
        hwnd, w_param, l_param = self._held
        self._held = None
        if not self._user32.PostMessageW(hwnd, WM_CLIPBOARDUPDATE, w_param, l_param):
            logger.warning("clipboard_update_redelivery_failed err=%s", ctypes.get_last_error())
            return
        logger.info("clipboard_update_redelivered")


__all__ = ["QT_CLIPBOARD_WINDOW_TITLE", "WM_CLIPBOARDUPDATE", "ClipboardUpdateDeferral"]
