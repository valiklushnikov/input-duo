"""Примитивы COM на голом ctypes. Ни PySide6, ни duo_input.ui, ни сокетов.

Этот модуль - буквальная граница "COM-поток не трогает Qt". Правило проверяется
boundary-тестом (задача 2.6), и оно не стилистическое: без него однажды
кто-нибудь дёрнет QSslSocket из COM-потока, потому что так короче, и получит
дефект, который воспроизводится раз в сто запусков.

Нужны только СТАНДАРТНЫЕ интерфейсы - IUnknown, IDataObject, IEnumFORMATETC,
IStream, IDataObjectAsyncCapability. Ни typelib, ни кодогенерации, ни записи в
реестр: OLE уже содержит готовые proxy/stub для IDataObject и IStream, и
именно поэтому межпроцессный маршалинг работает без нашего участия.

Обратные вызовы удерживаются в self._callbacks: ctypes не держит CFUNCTYPE за
нас, а собранный сборщиком мусора колбэк - это переход по освобождённому
адресу, то есть падение процесса без исключения и без записи в журнал.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

# --------------------------------------------------------------------------
# Коды возврата
# --------------------------------------------------------------------------


def _hresult(code: int) -> int:
    """0x80004005 -> -2147467259.

    HRESULT объявлен как c_long, то есть в Python приходит знаковым. Числа
    записаны так, как их пишут заголовки Windows, а знак получается здесь -
    вручную переписанное отрицательное значение это опечатка, которую потом
    не с чем сверить.
    """
    return code - (1 << 32) if code >= (1 << 31) else code


S_OK = 0
S_FALSE = 1
E_NOTIMPL = _hresult(0x80004001)
E_NOINTERFACE = _hresult(0x80004002)
E_POINTER = _hresult(0x80004003)
E_FAIL = _hresult(0x80004005)
STG_E_INVALIDFUNCTION = _hresult(0x80030001)
STG_E_READFAULT = _hresult(0x8003001E)
DV_E_FORMATETC = _hresult(0x80040064)
DV_E_TYMED = _hresult(0x80040069)

# --------------------------------------------------------------------------
# Идентификаторы интерфейсов
# --------------------------------------------------------------------------

IID_IUNKNOWN = "{00000000-0000-0000-C000-000000000046}"
IID_IDATAOBJECT = "{0000010E-0000-0000-C000-000000000046}"
IID_IENUMFORMATETC = "{00000103-0000-0000-C000-000000000046}"
IID_ISTREAM = "{0000000C-0000-0000-C000-000000000046}"
IID_IASYNCCAPABILITY = "{3D8B0590-F691-11D2-8EA9-006097DF5BD4}"

# --------------------------------------------------------------------------
# Константы оболочки
# --------------------------------------------------------------------------

TYMED_HGLOBAL = 1
TYMED_ISTREAM = 4
TYMED_ISTORAGE = 8

DATADIR_GET = 1
DVASPECT_CONTENT = 1

STREAM_SEEK_SET = 0
STREAM_SEEK_CUR = 1
STREAM_SEEK_END = 2

DROPEFFECT_COPY = 1

FILE_ATTRIBUTE_DIRECTORY = 0x10

#: Флаги FILEDESCRIPTORW.dwFlags: какие поля дескриптора Проводник обязан
#: прочитать. Поле, заполненное без своего флага, для него не существует -
#: размер без FD_FILESIZE не даёт ни процента прогресса, ни отказа.
FD_CLSID = 0x0001
FD_SIZEPOINT = 0x0002
FD_ATTRIBUTES = 0x0004
FD_CREATETIME = 0x0008
FD_ACCESSTIME = 0x0010
FD_WRITESTIME = 0x0020
FD_FILESIZE = 0x0040
FD_PROGRESSUI = 0x4000
FD_LINKUI = 0x8000
FD_UNICODE = 0x80000000

# --------------------------------------------------------------------------
# Структуры
# --------------------------------------------------------------------------


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


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
    """592 байта в 64-разрядной сборке; порядок полей - это и есть контракт.

    Проводник читает наши поля по смещениям, а не по именам: одно лишнее или
    пропущенное поле не даёт ошибки, оно даёт мусорные имена и размеры.
    """

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


# --------------------------------------------------------------------------
# GUID
# --------------------------------------------------------------------------


def guid_from_string(text: str) -> GUID:
    """Разобрать GUID в фигурных скобках через CLSIDFromString.

    Разбирает система, а не мы: первые три поля GUID хранятся little-endian,
    последние восемь байт - как записаны, и рукописный парсер ошибается
    ровно здесь.
    """
    guid = GUID()
    try:
        # oledll сам поднимает OSError на любом HRESULT со старшим битом,
        # поэтому проверять код возврата не нужно - но текст у него системный
        # и локализованный, а нам нужна строка, которую видно в логе.
        ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
    except OSError as error:
        raise ValueError(f"не GUID: {text!r}") from error
    return guid


def same_guid(a: GUID, b: GUID) -> bool:
    """Сравнить GUID по всем 16 байтам.

    Сравниваются байты структуры, а не поля: поэлементное сравнение забывает
    про Data4, а сравнение через is сравнивает адреса двух копий одного и
    того же IID и всегда даёт False.
    """
    return bytes(memoryview(a).cast("B")) == bytes(memoryview(b).cast("B"))


# --------------------------------------------------------------------------
# vtable
# --------------------------------------------------------------------------

_QUERY_INTERFACE = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p
)
_REF_COUNT = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


def make_vtable(*callbacks) -> ctypes.Array:
    """Массив указателей на функции - ровно то, чем COM считает интерфейс.

    Возвращённый массив обязан пережить объект: указатель, который получает
    Windows, ведёт в эту память. Вызывающий кладёт его в поле экземпляра, а
    не в локальную переменную.
    """
    table = (ctypes.c_void_p * len(callbacks))()
    for index, callback in enumerate(callbacks):
        table[index] = ctypes.cast(callback, ctypes.c_void_p)
    return table


def _slot(pointer: ctypes.c_void_p, index: int, prototype):
    """Достать index-й метод из vtable, на которую смотрит pointer."""
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p)).contents
    entries = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))
    return prototype(entries[index])


def call_query_interface(pointer, riid: GUID, out) -> int:
    """IUnknown::QueryInterface через vtable. out=None означает NULL."""
    ppv = ctypes.byref(out) if out is not None else None
    return _slot(pointer, 0, _QUERY_INTERFACE)(pointer, ctypes.byref(riid), ppv)


def call_add_ref(pointer) -> int:
    """IUnknown::AddRef через vtable."""
    return _slot(pointer, 1, _REF_COUNT)(pointer)


def call_release(pointer) -> int:
    """IUnknown::Release через vtable."""
    return _slot(pointer, 2, _REF_COUNT)(pointer)


class ComObject:
    """IUnknown плюс хук последнего Release.

    Хук - не удобство. Release на IStream - это то, чем Проводник сообщает
    "с этим файлом всё"; спайк давал счётчику уйти в ноль и не делал ничего,
    а production обязан узнать (спека §9).

    Счётчик живёт под замком. AddRef и Release приходят из потоков
    Проводника, а "self.refcount += 1" - это три байт-кода с переключением
    потока между ними: без замка два одновременных последних Release оба
    видят ноль, оба ставят флаг и оба зовут хук. GIL делает это редким, а не
    невозможным (измерено, см. task-2.1-report.md).

    Хук вызывается ВНЕ замка: он чужой, в поздних задачах он уходит в код
    приложения, и звать чужой код с захваченным замком - это готовый
    взаимоблок.

    Публичный .refcount читается без замка. Это снимок для утверждения в
    тесте или для журнала, а не средство синхронизации.
    """

    def __init__(self, supported_iids: list[str]) -> None:
        self._supported = [guid_from_string(iid) for iid in supported_iids]
        self._lock = threading.Lock()
        self.refcount = 1
        self._released = False
        #: Вызывается ровно один раз, когда счётчик впервые достигает нуля.
        self.on_last_release = None

        self._callbacks: list = [
            _QUERY_INTERFACE(self._query_interface),
            _REF_COUNT(self._add_ref),
            _REF_COUNT(self._release),
        ]
        self._rebuild()

    def add_interface(self, iid: str) -> None:
        self._supported.append(guid_from_string(iid))

    def extend_vtable(self, callbacks: list) -> None:
        """Дописать слоты производного интерфейса ПОСЛЕ трёх слотов IUnknown.

        Порядок в vtable - это и есть контракт интерфейса. Дописать не в конец
        означает вызвать не ту функцию, и COM об этом не сообщит.
        """
        self._callbacks.extend(callbacks)
        self._rebuild()

    def _rebuild(self) -> None:
        # Пересборка меняет .pointer, поэтому производный класс расширяет
        # vtable в своём __init__ - до того, как указатель уйдёт наружу.
        self._vtable = make_vtable(*self._callbacks)
        self._slot = ctypes.c_void_p(ctypes.addressof(self._vtable))
        self.pointer = ctypes.c_void_p(ctypes.addressof(self._slot))

    def _query_interface(self, _this, riid, ppv) -> int:
        if not ppv:
            return E_POINTER
        out = ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))
        requested = ctypes.cast(riid, ctypes.POINTER(GUID)).contents
        if any(same_guid(requested, supported) for supported in self._supported):
            out[0] = self.pointer
            with self._lock:
                self.refcount += 1
            return S_OK
        out[0] = None
        return E_NOINTERFACE

    def _add_ref(self, _this) -> int:
        with self._lock:
            self.refcount += 1
            # Возвращается снимок, сделанный под замком: повторное чтение
            # self.refcount отдало бы значение, уже изменённое чужим потоком.
            return self.refcount

    def _release(self, _this) -> int:
        with self._lock:
            self.refcount -= 1
            remaining = max(self.refcount, 0)
            # Решение "этот вызов - последний" принимается под замком, а
            # действие по нему - снаружи.
            last = self.refcount <= 0 and not self._released
            if last:
                self._released = True
        if last and self.on_last_release is not None:
            self.on_last_release()
        return remaining


# --------------------------------------------------------------------------
# Буфер обмена и глобальная память
# --------------------------------------------------------------------------

_USER32 = ctypes.windll.user32
_KERNEL32 = ctypes.windll.kernel32

_REGISTER_CLIPBOARD_FORMAT = _USER32.RegisterClipboardFormatW
_REGISTER_CLIPBOARD_FORMAT.argtypes = [ctypes.c_wchar_p]
_REGISTER_CLIPBOARD_FORMAT.restype = wintypes.UINT

# argtypes здесь обязательны, а не для красоты: HGLOBAL не влезает в int по
# умолчанию, и без них вызов падает с OverflowError на первом же хэндле.
_GLOBAL_ALLOC = _KERNEL32.GlobalAlloc
_GLOBAL_ALLOC.argtypes = [wintypes.UINT, ctypes.c_size_t]
_GLOBAL_ALLOC.restype = wintypes.HGLOBAL
_GLOBAL_LOCK = _KERNEL32.GlobalLock
_GLOBAL_LOCK.argtypes = [wintypes.HGLOBAL]
_GLOBAL_LOCK.restype = ctypes.c_void_p
_GLOBAL_UNLOCK = _KERNEL32.GlobalUnlock
_GLOBAL_UNLOCK.argtypes = [wintypes.HGLOBAL]
_GLOBAL_UNLOCK.restype = wintypes.BOOL
_GLOBAL_FREE = _KERNEL32.GlobalFree
_GLOBAL_FREE.argtypes = [wintypes.HGLOBAL]
_GLOBAL_FREE.restype = wintypes.HGLOBAL

#: GMEM_MOVEABLE - то, чего требует буфер обмена от любого HGLOBAL.
_GMEM_MOVEABLE = 0x0002


def register_clipboard_format(name: str) -> int:
    """Зарегистрировать формат буфера обмена; для одного имени - один id.

    Ноль - это отказ. Без проверки объект стал бы объявлять формат 0, и
    вставка просто ничего бы не делала: ни ошибки, ни записи в журнал.
    """
    value = _REGISTER_CLIPBOARD_FORMAT(ctypes.c_wchar_p(name))
    if not value:
        raise OSError(f"RegisterClipboardFormatW отказал для имени {name!r}")
    return int(value)


def to_hglobal(payload: bytes) -> int:
    """Скопировать байты в GMEM_MOVEABLE-блок и вернуть его хэндл.

    Пустая полезная нагрузка не блокируется: GlobalLock на блоке нулевой
    длины возвращает NULL (измерено), и это не ошибка, а единственный
    законный способ отдать пустой блок. Копировать в него всё равно нечего.

    Владение переходит вызывающему: буфер обмена или STGMEDIUM освободят
    хэндл сами, а вот на своём пути ошибки мы за собой убираем - утёкший
    GMEM_MOVEABLE живёт до конца процесса.
    """
    handle = _GLOBAL_ALLOC(_GMEM_MOVEABLE, len(payload))
    if not handle:
        raise MemoryError("GlobalAlloc не выделил память")
    if not payload:
        return int(handle)
    address = _GLOBAL_LOCK(handle)
    if not address:
        _GLOBAL_FREE(handle)
        raise MemoryError("GlobalLock не заблокировал только что выделенный блок")
    try:
        ctypes.memmove(address, payload, len(payload))
    finally:
        _GLOBAL_UNLOCK(handle)
    return int(handle)


# --------------------------------------------------------------------------
# Время
# --------------------------------------------------------------------------

#: Разница между эпохой FILETIME (1601-01-01) и эпохой Unix, в секундах.
_FILETIME_EPOCH_DELTA_SECONDS = 11_644_473_600


def filetime_from_ns(mtime_ns: int) -> FILETIME:
    """st_mtime_ns в FILETIME (интервалы по 100 нс от 1601-01-01).

    max(0, ...) не косметика: файл со временем до 1970 года дал бы
    отрицательное число, а FILETIME беззнаковый - Проводник показал бы дату
    из далёкого будущего.

    Измерено: порог отсечения - 1601-01-01, а не 1970-01-01. Время между
    1601 и 1970 годом переносится как есть (у него положительное число
    интервалов), а вот всё, что раньше 1601-го, обрезается в ноль - и
    обрезается именно здесь.
    """
    intervals = max(0, mtime_ns // 100 + _FILETIME_EPOCH_DELTA_SECONDS * 10_000_000)
    result = FILETIME()
    result.dwLowDateTime = intervals & 0xFFFFFFFF
    result.dwHighDateTime = (intervals >> 32) & 0xFFFFFFFF
    return result
