"""Publish a transfer manifest as Windows virtual files.

Two things live here: the ``FILEGROUPDESCRIPTORW`` blob that describes the
tree, and the ``IDataObject`` that serves it - descriptors, the drop effect,
the origin marker, and one pipe-backed ``IStream`` per file.

Manifest paths use ``/`` on the wire.  Windows Explorer expects ``\\`` in
``FILEDESCRIPTORW.cFileName``; this module is the single conversion point.

Здесь же живёт поток STA, который владеет буфером обмена, и
``post_to_service`` - единственная дорога с него обратно в Qt.

Именно поэтому этот модуль - в отличие от ``windows_com`` - Qt видеть ВПРАВЕ:
``WindowsFileClipboardBackend`` сам QObject. Настоящий инвариант не "модуль не
импортирует Qt", а "код, исполняемый в COM-потоке, не трогает Qt напрямую", и
держит его одна функция: всё, что COM-поток говорит сервису, проходит через
``post_to_service`` очередью Qt. Прямое обращение к слоту отсюда тронуло бы
QSslSocket из чужого потока, и дефект проявлялся бы раз в сто запусков.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections.abc import Callable
from ctypes import wintypes

from PySide6.QtCore import Q_ARG, QMetaObject, QObject, Qt, Signal, Slot

from .model import ENTRY_DIRECTORY, ENTRY_FILE, TransferEntry, TransferManifest

# Приватные имена - это прототипы vtable. Они живут в windows_com потому, что
# там же лежит весь остальной ABI, и импортируются сюда именно ими: свой
# WINFUNCTYPE с той же сигнатурой дал бы второй набор типов для одного и того
# же контракта, и расхождение между ними нашлось бы только на 0xC0000005.
from .windows_com import (
    _D_ADVISE,
    _D_UNADVISE,
    _END_OP,
    _ENUM_CLONE,
    _ENUM_D_ADVISE,
    _ENUM_FORMAT_ETC,
    _ENUM_NEXT,
    _ENUM_RESET,
    _ENUM_SKIP,
    _GET_ASYNC,
    _GET_CANONICAL,
    _GET_DATA,
    _GET_DATA_HERE,
    _IN_OP,
    _QUERY_GET_DATA,
    _QUERY_INTERFACE,
    _REF_COUNT,
    _SET_ASYNC,
    _SET_DATA,
    _START_OP,
    DATADIR_GET,
    DROPEFFECT_COPY,
    DV_E_FORMATETC,
    DV_E_TYMED,
    DVASPECT_CONTENT,
    E_FAIL,
    E_NOTIMPL,
    E_POINTER,
    FD_ATTRIBUTES,
    FD_FILESIZE,
    FD_PROGRESSUI,
    FD_WRITESTIME,
    FILE_ATTRIBUTE_DIRECTORY,
    FILEDESCRIPTORW,
    FORMATETC,
    GUID,
    IID_IASYNCCAPABILITY,
    IID_IDATAOBJECT,
    IID_IENUMFORMATETC,
    IID_IUNKNOWN,
    S_FALSE,
    S_OK,
    STGMEDIUM,
    TYMED_HGLOBAL,
    TYMED_ISTREAM,
    ComObject,
    PipeStream,
    filetime_from_ns,
    guid_from_string,
    make_vtable,
    register_clipboard_format,
    same_guid,
    to_hglobal,
)

logger = logging.getLogger(__name__)

_FILE_ATTRIBUTE_NORMAL = 0x80


def descriptor_entries(manifest: TransferManifest) -> tuple[TransferEntry, ...]:
    """Return entries in manifest order, which is the Explorer ``lindex`` order."""
    return manifest.entries


def group_descriptor_bytes(manifest: TransferManifest) -> bytes:
    """Build a ``FILEGROUPDESCRIPTORW`` blob from ``manifest``."""
    entries = descriptor_entries(manifest)
    blob = bytearray(len(entries).to_bytes(4, "little"))
    for entry in entries:
        blob.extend(bytes(memoryview(_descriptor_for(entry)).cast("B")))
    return bytes(blob)


def _descriptor_for(entry: TransferEntry) -> FILEDESCRIPTORW:
    descriptor = FILEDESCRIPTORW()
    descriptor.dwFlags = FD_ATTRIBUTES | FD_WRITESTIME | FD_PROGRESSUI
    descriptor.cFileName = entry.path.replace("/", "\\")
    descriptor.ftLastWriteTime = filetime_from_ns(entry.mtime_ns)
    if entry.kind == ENTRY_DIRECTORY:
        descriptor.dwFileAttributes = FILE_ATTRIBUTE_DIRECTORY
        return descriptor

    descriptor.dwFileAttributes = _FILE_ATTRIBUTE_NORMAL
    descriptor.dwFlags |= FD_FILESIZE
    descriptor.nFileSizeHigh = (entry.size >> 32) & 0xFFFFFFFF
    descriptor.nFileSizeLow = entry.size & 0xFFFFFFFF
    return descriptor


FORMAT_DESCRIPTOR_NAME = "FileGroupDescriptorW"
FORMAT_CONTENTS_NAME = "FileContents"
FORMAT_DROP_EFFECT_NAME = "Preferred DropEffect"

#: То же имя, что ORIGIN_MIME в clipboard/backend.py. Объявляется здесь,
#: чтобы наш собственный наблюдатель узнал свою же публикацию и не объявил
#: её обратно как локальное копирование - см. задачу 2.7.
FORMAT_ORIGIN_NAME = "application/x-duo-input-origin"


class FormatEnumerator(ComObject):
    """IEnumFORMATETC. Проводник вправе спросить, что мы вообще предлагаем.

    Спайк возвращал E_NOTIMPL и записал, обходится ли Проводник без
    перечислителя. Настоящий перечислитель здесь потому, что "обходится" и
    "обходится у всех и всегда" - разные утверждения, а цена его невелика.
    """

    def __init__(self, formats: list[tuple[int, int, int]]) -> None:
        super().__init__([IID_IUNKNOWN, IID_IENUMFORMATETC])
        self._formats = list(formats)
        self._cursor = 0
        #: Клоны держатся здесь: см. _clone.
        self._clones: list[FormatEnumerator] = []
        self.extend_vtable(
            [
                _ENUM_NEXT(self._next),
                _ENUM_SKIP(self._skip),
                _ENUM_RESET(self._reset),
                _ENUM_CLONE(self._clone),
            ]
        )

    def _next(self, _this, celt, rgelt, pcelt_fetched) -> int:
        if not rgelt:
            # Писать в NULL нельзя даже "ничего": целевой массив - это
            # единственное, что этот вызов возвращает.
            return E_POINTER
        available = self._formats[self._cursor : self._cursor + int(celt)]
        target = ctypes.cast(rgelt, ctypes.POINTER(FORMATETC))
        for index, (cf, lindex, tymed) in enumerate(available):
            target[index].cfFormat = cf
            target[index].ptd = None
            target[index].dwAspect = DVASPECT_CONTENT
            target[index].lindex = lindex
            target[index].tymed = tymed
        self._cursor += len(available)
        if pcelt_fetched:
            ctypes.cast(pcelt_fetched, ctypes.POINTER(wintypes.ULONG))[0] = len(available)
        return S_OK if len(available) == int(celt) else S_FALSE

    def _skip(self, _this, celt) -> int:
        self._cursor = min(self._cursor + int(celt), len(self._formats))
        return S_OK

    def _reset(self, _this) -> int:
        self._cursor = 0
        return S_OK

    def _clone(self, _this, out) -> int:
        if not out:
            return E_POINTER
        clone = FormatEnumerator(self._formats)
        clone._cursor = self._cursor
        # Клон обязан пережить возврат: держим его на себе, иначе Python
        # соберёт объект, а Проводник уйдёт по освобождённому адресу.
        self._clones.append(clone)
        ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p))[0] = clone.pointer
        return S_OK


class _AsyncCapability:
    """IDataObjectAsyncCapability как отдельная таблица на том же объекте.

    Этот интерфейс наследует IUnknown, а НЕ IDataObject: его слот 3 - это
    SetAsyncMode, а не GetData. Дописать пять методов в конец таблицы
    IDataObject и вернуть на неё указатель из QueryInterface означало бы,
    что Проводник, зовущий SetAsyncMode, попадёт в GetData и разберёт BOOL
    как указатель на FORMATETC: 0xC0000005 в фазе 4, без исключения и без
    красного теста здесь. Спайк (задача 0.4) отдавал именно отдельную
    таблицу, и именно в таком виде жизненный цикл измерен работающим.

    IUnknown у обеих таблиц общий - это требование COM к тождеству объекта:
    QueryInterface, AddRef и Release ведут в реализацию владельца, и
    счётчик ссылок остаётся один на весь объект.
    """

    def __init__(self, owner: ComObject) -> None:
        self._owner = owner
        self._callbacks = [
            _QUERY_INTERFACE(owner._query_interface),
            _REF_COUNT(owner._add_ref),
            _REF_COUNT(owner._release),
            _SET_ASYNC(owner._set_async_mode),
            _GET_ASYNC(owner._get_async_mode),
            _START_OP(owner._start_operation),
            _IN_OP(owner._in_operation_query),
            _END_OP(owner._end_operation),
        ]
        self._vtable = make_vtable(*self._callbacks)
        self._slot = ctypes.c_void_p(ctypes.addressof(self._vtable))
        self.pointer = ctypes.c_void_p(ctypes.addressof(self._slot))


class VirtualFilesDataObject(ComObject):
    """То, что лежит в буфере обмена вместо файлов, которых здесь нет."""

    def __init__(
        self,
        manifest: TransferManifest,
        open_pipe,
        request_read,
        close_pipe,
        origin_marker: bytes,
        async_capability: bool = True,
    ) -> None:
        super().__init__([IID_IUNKNOWN, IID_IDATAOBJECT])
        self._manifest = manifest
        self._open_pipe = open_pipe
        self._request_read = request_read
        self._close_pipe = close_pipe
        self._origin_marker = origin_marker
        self.async_mode = False
        self.in_operation = False
        #: Владелец подставляет сюда свой обработчик завершения сессии.
        self.on_operation_finished: Callable[[int], None] | None = None
        #: lindex -> последний выданный по этому индексу PipeStream.
        self.streams: dict[int, PipeStream] = {}
        #: ВСЕ выданные потоки, по порядку. Словарь выше хранит по одному на
        #: индекс, и второй GetData по тому же lindex вытеснил бы из него
        #: предыдущий поток - то есть освободил бы его, пока указатель на
        #: него ещё у Проводника. Здесь не вытесняется ничего.
        self._retained_streams: list[PipeStream] = []
        #: Для диагностики: что и в каком порядке спросил Проводник.
        self.get_data_calls: list[tuple[int, int]] = []
        self._enumerators: list[FormatEnumerator] = []
        #: Хэндлы, которые мы выдали. Владение ими перешло к вызывающему
        #: (ReleaseStgMedium освободит их), поэтому список - это запись о
        #: выданном для диагностики, а не право что-то из него освободить.
        self._handles: list[int] = []
        #: Исключение из open_pipe, для владельца: GetData возвращает HRESULT,
        #: а не поднимает его.
        self.last_open_error: BaseException | None = None
        #: То же для on_operation_finished - как last_release_error в базе.
        self.last_operation_error: BaseException | None = None
        self._async: _AsyncCapability | None = None
        self._async_iid = None

        self.cf_descriptor = register_clipboard_format(FORMAT_DESCRIPTOR_NAME)
        self.cf_contents = register_clipboard_format(FORMAT_CONTENTS_NAME)
        self.cf_drop_effect = register_clipboard_format(FORMAT_DROP_EFFECT_NAME)
        self.cf_origin = register_clipboard_format(FORMAT_ORIGIN_NAME)

        self.extend_vtable(
            [
                _GET_DATA(self._get_data),
                _GET_DATA_HERE(self._get_data_here),
                _QUERY_GET_DATA(self._query_get_data),
                _GET_CANONICAL(self._get_canonical),
                _SET_DATA(self._set_data),
                _ENUM_FORMAT_ETC(self._enum_format_etc),
                _D_ADVISE(self._d_advise),
                _D_UNADVISE(self._d_unadvise),
                _ENUM_D_ADVISE(self._enum_d_advise),
            ]
        )
        if async_capability:
            # add_interface здесь не зовётся намеренно: базовый
            # QueryInterface вернул бы на этот IID указатель таблицы
            # IDataObject. Ответ даёт _query_interface ниже.
            self._async_iid = guid_from_string(IID_IASYNCCAPABILITY)
            self._async = _AsyncCapability(self)

    # ------------------------------------------------------------------ IUnknown

    def _query_interface(self, _this, riid, ppv) -> int:
        if self._async is not None and ppv:
            requested = ctypes.cast(riid, ctypes.POINTER(GUID)).contents
            if same_guid(requested, self._async_iid):
                ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))[0] = self._async.pointer
                # Счётчик один на объект: отданный указатель - это ссылка.
                self._add_ref(self.pointer)
                return S_OK
        return super()._query_interface(_this, riid, ppv)

    # ---------------------------------------------------------------- IDataObject

    def _advertised(self) -> list[tuple[int, int, int]]:
        return [
            (self.cf_descriptor, -1, TYMED_HGLOBAL),
            # CFSTR_FILECONTENTS нумеруется с нуля; lindex -1 ставят только
            # форматы, у которых индекса нет вовсе.
            (self.cf_contents, 0, TYMED_ISTREAM),
            (self.cf_drop_effect, -1, TYMED_HGLOBAL),
            (self.cf_origin, -1, TYMED_HGLOBAL),
        ]

    def _query_get_data(self, _this, pformatetc) -> int:
        fmt = ctypes.cast(pformatetc, ctypes.POINTER(FORMATETC)).contents
        if fmt.cfFormat == self.cf_contents:
            refusal = self._contents_refusal(fmt)
            return S_OK if refusal is None else refusal
        if fmt.cfFormat in (self.cf_descriptor, self.cf_drop_effect, self.cf_origin):
            return S_OK
        return DV_E_FORMATETC

    def _get_data(self, _this, pformatetc, pmedium) -> int:
        fmt = ctypes.cast(pformatetc, ctypes.POINTER(FORMATETC)).contents
        self.get_data_calls.append((fmt.cfFormat, fmt.lindex))
        medium = ctypes.cast(pmedium, ctypes.POINTER(STGMEDIUM)).contents
        medium.pUnkForRelease = None

        if fmt.cfFormat == self.cf_descriptor:
            return self._hand_over_bytes(medium, group_descriptor_bytes(self._manifest))
        if fmt.cfFormat == self.cf_drop_effect:
            # Всегда COPY. Ctrl+X на источнике сюда не доходит.
            return self._hand_over_bytes(medium, DROPEFFECT_COPY.to_bytes(4, "little"))
        if fmt.cfFormat == self.cf_origin:
            return self._hand_over_bytes(medium, self._origin_marker)
        if fmt.cfFormat == self.cf_contents:
            return self._hand_over_stream(medium, fmt)
        return DV_E_FORMATETC

    def _contents_refusal(self, fmt) -> int | None:
        """Почему этот FileContents отдать нельзя - или None, если можно.

        GetData и QueryGetData обязаны отвечать на один и тот же FORMATETC
        одинаково: смысл QueryGetData в том, чтобы ПРЕДСКАЗАТЬ GetData.
        Разъехавшись, они дают самый дорогой вид дефекта - Проводник
        начинает копирование, показывает индикатор и получает отказ
        поштучно. Одно место решения - это то, что не даёт им разъехаться.
        """
        if not fmt.tymed & TYMED_ISTREAM:
            return DV_E_TYMED
        entries = descriptor_entries(self._manifest)
        index = int(fmt.lindex)
        if not 0 <= index < len(entries):
            # CFSTR_FILECONTENTS нумеруется с нуля: отдать entries[-1] на
            # lindex=-1 значило бы скрыть, какой файл спросили на самом деле.
            return DV_E_FORMATETC
        if entries[index].kind != ENTRY_FILE:
            return DV_E_FORMATETC
        return None

    def _hand_over_bytes(self, medium, payload: bytes) -> int:
        handle = to_hglobal(payload)
        self._handles.append(handle)
        medium.tymed = TYMED_HGLOBAL
        medium.data = handle
        return S_OK

    def _hand_over_stream(self, medium, fmt) -> int:
        refusal = self._contents_refusal(fmt)
        if refusal is not None:
            return refusal
        index = int(fmt.lindex)
        entry = descriptor_entries(self._manifest)[index]

        transfer_id = self._manifest.transfer_id
        try:
            # Чужой код в обратном вызове ctypes: исключение отсюда было бы
            # напечатано в stderr, а COM получил бы неопределённое значение -
            # измерено положительное, то есть успех по правилу SUCCEEDED, -
            # и Проводник пошёл бы читать STGMEDIUM, который никто не
            # заполнял. Владелец узнаёт причину из last_open_error,
            # оболочка - из HRESULT.
            pipe = self._open_pipe(transfer_id, index)
        except BaseException as error:  # noqa: BLE001 - см. ComObject._release
            self.last_open_error = error
            logger.warning(
                "open_pipe отказал для записи %d: %s", index, type(error).__name__
            )
            return E_FAIL
        stream = PipeStream(
            pipe,
            size=entry.size,
            request=lambda offset, length, i=index: self._request_read(
                transfer_id, i, offset, length
            ),
            on_release=lambda i=index: self._close_pipe(transfer_id, i, None),
        )
        self.streams[index] = stream
        self._retained_streams.append(stream)
        medium.tymed = TYMED_ISTREAM
        medium.data = stream.pointer
        return S_OK

    def _enum_format_etc(self, _this, direction, ppenum) -> int:
        if int(direction) != DATADIR_GET:
            return E_NOTIMPL
        if not ppenum:
            return E_POINTER
        enumerator = FormatEnumerator(self._advertised())
        # Держим на себе по той же причине, что и потоки.
        self._enumerators.append(enumerator)
        ctypes.cast(ppenum, ctypes.POINTER(ctypes.c_void_p))[0] = enumerator.pointer
        return S_OK

    def _get_data_here(self, _this, _fmt, _medium) -> int:
        return E_NOTIMPL

    def _get_canonical(self, _this, _fmt, _out) -> int:
        return E_NOTIMPL

    def _set_data(self, _this, _fmt, _medium, _release) -> int:
        return E_NOTIMPL

    def _d_advise(self, _this, _fmt, _flags, _sink, _conn) -> int:
        return E_NOTIMPL

    def _d_unadvise(self, _this, _connection) -> int:
        return E_NOTIMPL

    def _enum_d_advise(self, _this, _out) -> int:
        return E_NOTIMPL

    # ----------------------------------------- IDataObjectAsyncCapability

    def _set_async_mode(self, _this, do_op_async) -> int:
        # Измерено: Проводник не позвал этот метод ни разу ни в одном
        # прогоне. Значение запоминается для диагностики и НЕ влияет на
        # ответ GetAsyncMode.
        self.async_mode = bool(do_op_async)
        return S_OK

    def _get_async_mode(self, _this, out) -> int:
        """Объявить способность - не считать режим.

        Это первое, что спрашивает Проводник, ДО всякого SetAsyncMode.
        Ответ-считывание self.async_mode дал бы здесь "0 = асинхронный
        режим не нужен", после чего ни SetAsyncMode, ни StartOperation, ни
        EndOperation не придут вовсе - а EndOperation и есть единственный
        явный сигнал о завершении и об отмене. Ровно этот дефект обнулил
        один прогон измерений.
        """
        if not out:
            return E_POINTER
        out[0] = 1
        return S_OK

    def _start_operation(self, _this, _reserved) -> int:
        self.in_operation = True
        return S_OK

    def _in_operation_query(self, _this, out) -> int:
        if not out:
            return E_POINTER
        out[0] = 1 if self.in_operation else 0
        return S_OK

    def _end_operation(self, _this, result, _reserved, _effects) -> int:
        self.in_operation = False
        if self.on_operation_finished is not None:
            try:
                # Источник истины о завершении сессии - тот, что записал спайк 1
                # (спека §9). НЕ переопределяйте его из этого плана.
                self.on_operation_finished(int(result))
            except BaseException as error:  # noqa: BLE001 - см. ComObject._release
                # Ровно как у хука последнего Release: чужая ошибка не
                # выходит через ctypes (там она стала бы неопределённым
                # кодом возврата, измеренно положительным - успехом), а
                # достаётся владельцу из поля.
                self.last_operation_error = error
                logger.warning(
                    "обработчик завершения операции упал: %s", type(error).__name__
                )
        return S_OK


# ===================================================== поток STA и дорога в Qt

#: Требуется ли IDataObjectAsyncCapability. Значение поставлено по записи
#: спайка 1 (records/2026-09-12-explorer-virtual-files-spike.md), а не по
#: теории: EndOperation - единственный явный сигнал о завершении И об отмене,
#: и без объявленной способности он не приходит вовсе.
ASYNC_CAPABILITY_REQUIRED = True

#: Как часто поток STA проверяет, не пора ли остановиться и не ждёт ли
#: публикация. Между проверками он крутит насос сообщений.
_PUMP_INTERVAL_MS = 20

#: Сколько ждать, что поток доложит о поднятом апартаменте, и сколько - что
#: он завершился. Ожидание без потолка превратило бы регрессию в зависание.
_START_TIMEOUT_S = 5.0
_STOP_TIMEOUT_S = 5.0

#: Нижняя граница насоса ПОСЛЕ отдачи буфера и до гашения апартамента.
#: Погасить апартамент сразу означает оборвать на полпути работу, которую
#: чужие апартаменты этого же процесса (буфер обмена Qt в их числе) ведут с
#: нашим объектом; симптом - RPC_E_DISCONNECTED, а следом access violation,
#: уносящий процесс. Измерено чередующимися прогонами: без паузы 5 падений
#: из 36, с паузой 0 из 36.
#:
#: Именно НИЖНЯЯ граница, а не условие: сразу после OleFlushClipboard наш
#: собственный счётчик ссылок уже равен 1 (измерено), то есть COM с объектом
#: закончил, - а процесс всё равно падал. Незавершённая работа живёт в слое
#: RPC, и в refcount её не видно. Условие ниже - сверх этой границы, а не
#: вместо неё.
_TEARDOWN_PUMP_S = 0.25

#: Потолок для того, что видно: пока на наши объекты держат ссылки ИЗВНЕ
#: (refcount > 1), насос крутится дальше. Достигнутый потолок пишется в
#: журнал - иначе "константа оказалась мала" выглядело бы как то же самое
#: падение, вернувшееся молча.
_TEARDOWN_CEILING_S = 2.0

#: PeekMessageW(..., PM_REMOVE): взять сообщение из очереди, а не подсмотреть.
#: Подсмотренное остаётся в очереди, и внутренний цикл стал бы бесконечным.
_PM_REMOVE = 1

#: QS_ALLINPUT: проснуться на ЛЮБОМ сообщении, включая присланные из чужого
#: апартамента, - именно ими приходят вызовы COM.
_QS_ALLINPUT = 0x04FF

#: CLIPBRD_E_CANT_OPEN как знаковое число - именно так его отдаёт ctypes.
#: Это "буфер сейчас держит кто-то другой", а не "мы сделали что-то не так":
#: буфер обмена Windows - общий ресурс на весь рабочий стол.
CLIPBRD_E_CANT_OPEN = 0x800401D0 - (1 << 32)

#: Сколько раз повторить OleSetClipboard при занятом буфере и сколько ждать
#: между попытками. Измерено на настоящем рабочем столе: 3 отказа
#: CLIPBRD_E_CANT_OPEN из 24 публикаций - то есть с одной попытки примерно
#: каждая восьмая вставка не состоялась бы, и пользователь увидел бы
#: "буфер обмена не принял файлы" без всякой своей вины.
_CLIPBOARD_RETRIES = 5
_CLIPBOARD_RETRY_MS = 50


def _ole_initialize() -> None:
    """OleInitialize на ЭТОМ потоке.

    Не CoInitializeEx: OleSetClipboard требует именно инициализации OLE.
    OleInitialize делает CoInitializeEx(COINIT_APARTMENTTHREADED) и сверх
    того поднимает буфер обмена, drag-drop и скрытое окно апартамента - то,
    на что приходят входящие вызовы Проводника.

    oledll, а не windll: отказ обязан стать исключением, а не HRESULT,
    который никто не прочитал.
    """
    ctypes.oledll.ole32.OleInitialize(None)


def _ole_uninitialize() -> None:
    ctypes.windll.ole32.OleUninitialize()


def _ole_set_clipboard(pointer) -> int:
    """Один именованный шов вокруг OleSetClipboard.

    Функция, а не вызов по месту: так весь путь публикации - очередь, поток
    STA, удержание объекта, отчёт об отказе - проверяется без настоящего
    рабочего стола, а утверждение "буфер берётся с потока STA и только с
    него" вообще перестаёт зависеть от сессии.
    """
    return ctypes.windll.ole32.OleSetClipboard(pointer)


def _ole_flush_clipboard() -> int:
    return ctypes.windll.ole32.OleFlushClipboard()


def _wait_for_messages(timeout_ms: int) -> None:
    """Спать до сообщения, а не до конца интервала.

    sleep(20 мс) на пустой очереди означает, что вызов, пришедший через
    миллисекунду после разбора очереди, ждёт ещё девятнадцать. При
    последовательных чтениях по 64 КиБ это около пятидесяти чтений в
    секунду - примерно три мегабайта в секунду на фиче, чей заявленный
    случай - вставка на 20 ГБ. И проявилось бы это не красным тестом, а
    жалобой "вставка почему-то медленная".

    MsgWaitForMultipleObjects возвращается в тот же миг, когда сообщение
    появилось, и всё равно истекает через timeout_ms - то есть темп проверки
    "не пора ли остановиться" остаётся прежним.
    """
    ctypes.windll.user32.MsgWaitForMultipleObjects(
        0, None, False, timeout_ms, _QS_ALLINPUT
    )


def _declared_parameter_types(service, slot: str, count: int) -> tuple[str, ...] | None:
    """Объявленные типы слота - из метаобъекта получателя, а не из значений.

    Единственный источник истины о подписи - сам получатель. Разбор по
    isinstance её знать не может: у FileTransferService.request_read
    смещение объявлено ``qlonglong``, а Q_ARG(int, ...) - это 32-битный C++
    int. Совпадением это не становится даже на маленьком смещении -
    invokeMethod просто возвращает False, - а на 4 ГиБ значение ещё и не
    помещается. Ни то, ни другое не поднимает исключения в COM-потоке:
    чтение не выходит на провод, и единственный симптом - таймаут
    IStream::Read через тридцать секунд.
    """
    meta = service.metaObject()
    for index in range(meta.methodCount()):
        method = meta.method(index)
        if bytes(method.name()).decode("ascii", "replace") != slot:
            continue
        types = tuple(
            bytes(name).decode("ascii", "replace") for name in method.parameterTypes()
        )
        # Перегрузки различаются ариностью: берём ту, которой этот вызов
        # соответствует, а не первую попавшуюся.
        if len(types) == count:
            return types
    return None


def post_to_service(service, slot: str, *args) -> None:
    """ЕДИНСТВЕННАЯ дорога из COM-потока обратно в Qt.

    Одна функция, а не invokeMethod по месту, - чтобы границу можно было
    проверить чтением одной функции вместо ревизии каждой точки вызова.

    Ничего отсюда не вылетает: зовут её из обратного вызова ctypes, где
    исключение Python не становится ошибкой, а оставляет COM неопределённое
    возвращаемое значение - измеренно положительное, то есть успех по
    правилу SUCCEEDED.
    """
    try:
        types = _declared_parameter_types(service, slot, len(args))
        if types is None:
            # Молча потерянный вызов означал бы навсегда заблокированный Read.
            logger.error(
                "у получателя нет слота %s с %d аргументами - вызов не доставлен",
                slot,
                len(args),
            )
            return
        delivered = QMetaObject.invokeMethod(
            service,
            slot,
            Qt.ConnectionType.QueuedConnection,
            *[Q_ARG(declared, value) for declared, value in zip(types, args)],
        )
    except Exception:  # noqa: BLE001 - см. docstring: отсюда не вылетает ничего
        logger.exception("вызов %s не удалось передать в Qt-поток", slot)
        return
    if not delivered:
        logger.error("не удалось доставить %s в Qt-поток", slot)


class WindowsFileClipboardBackend(QObject):
    """Поток STA, владеющий буфером обмена и всеми COM-объектами.

    Выделенный поток, а не GUI-поток, по построению: STA сериализует входящие
    COM-вызовы через свой насос сообщений, поэтому наши объекты не обязаны
    быть потокобезопасными, а GUI-поток не блокируется на чтении - не как
    следствие тонкости маршалинга, а потому что COM-вызовы до него не доходят.

    Порядок вызовов задан и проверен: ``stop()`` до ``start()`` и второй
    ``stop()`` безвредны, второй ``start()`` не заводит второго апартамента,
    а ``publish()`` до ``start()`` отвергается ВСЛУХ - очередь без потока
    либо потеряла бы дерево совсем, либо выложила бы устаревшее при
    следующем старте, и оба исхода молчаливы.
    """

    publish_failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._thread_id: int | None = None
        self._lock = threading.Lock()
        #: Ровно одна отложенная публикация: в буфере обмена лежит одно, и
        #: список здесь означал бы, что следом за свежим деревом ляжет
        #: устаревшее.
        self._pending: tuple[TransferManifest, bytes] | None = None
        self._published: VirtualFilesDataObject | None = None
        #: Прежние публикации, которые оболочка ещё может держать. См.
        #: _retire: выбросить их в момент замены значило бы освободить
        #: vtable под работающей вставкой.
        self._retired: list[VirtualFilesDataObject] = []
        self._callbacks: dict[str, Callable] = {}
        self._start_error: BaseException | None = None

    # ------------------------------------------------------------------ свойства

    @property
    def thread_id(self) -> int | None:
        """Идентификатор потока STA - или None, пока апартамент не поднят."""
        return self._thread_id

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def pending_publications(self) -> int:
        with self._lock:
            return 0 if self._pending is None else 1

    @property
    def published_object(self) -> VirtualFilesDataObject | None:
        """Объект, лежащий сейчас в буфере, - его держим мы, а не буфер."""
        return self._published

    @property
    def retired_publications(self) -> int:
        """Сколько прежних публикаций мы ещё держим ради оболочки."""
        return len(self._retired)

    def set_callbacks(
        self, open_pipe, request_read, close_pipe, on_operation_finished
    ) -> None:
        """Колбэки, через которые COM-объекты достигают сервиса.

        Зовут их ИЗ COM-потока, поэтому всё, что они делают с Qt, обязано
        идти через post_to_service. Бэкенд их не оборачивает: подменить
        чужой колбэк своим значило бы спрятать эту обязанность.
        """
        self._callbacks = {
            "open_pipe": open_pipe,
            "request_read": request_read,
            "close_pipe": close_pipe,
            "on_operation_finished": on_operation_finished,
        }

    # -------------------------------------------------------------- запуск/останов

    def start(self) -> None:
        if self.is_running:
            return
        self._stopping.clear()
        self._ready.clear()
        self._start_error = None
        self._thread_id = None
        # Прошлый stop() мог завершиться по таймауту и оставить эти поля
        # заполненными. Новый апартамент буфера не брал, и flush по
        # унаследованному _published отдал бы чужое владение.
        self._published = None
        self._retired = []
        with self._lock:
            self._pending = None
        thread = threading.Thread(target=self._run, name="duo-input-com-sta", daemon=True)
        self._thread = thread
        thread.start()
        if not self._ready.wait(timeout=_START_TIMEOUT_S):
            logger.error("поток STA не доложил о готовности за %.0f с", _START_TIMEOUT_S)
        elif self._start_error is not None:
            # Тип, а не текст - ровно как в open_pipe выше. Сообщение
            # приходит из чужого кода и вполне может нести путь, а §15
            # запрещает путям попадать в журнал. Две соседние записи,
            # расходящиеся в этом, - это приглашение скопировать не ту.
            logger.error(
                "апартамент STA не поднялся: %s", type(self._start_error).__name__
            )

    def stop(self) -> None:
        thread = self._thread
        if thread is None:
            return
        self._stopping.set()
        thread.join(timeout=_STOP_TIMEOUT_S)
        if thread.is_alive():
            # Забыть про живой поток значило бы, что следующий start()
            # заведёт ВТОРОЙ апартамент и второго владельца буфера.
            logger.error(
                "поток STA не завершился за %.0f с - второй апартамент не заводим",
                _STOP_TIMEOUT_S,
            )
            return
        self._thread = None
        self._thread_id = None
        self._published = None
        with self._lock:
            self._pending = None

    # ------------------------------------------------------------------ публикация

    def publish(self, manifest: TransferManifest, origin_marker: bytes) -> None:
        """Поставить дерево в очередь на публикацию из потока STA.

        Не публикуем отсюда: OleSetClipboard обязан быть вызван на том
        потоке, который потом отвечает на GetData. Вызов с GUI-потока
        сделал бы владельцем буфера его, и все чтения пришли бы туда.
        """
        if not self.is_running:
            self._refuse("поток STA не запущен - публиковать некому")
            return
        if not self._callbacks:
            self._refuse("колбэки не установлены - Проводнику нечем отвечать")
            return
        with self._lock:
            self._pending = (manifest, origin_marker)

    def build_data_object(
        self, manifest: TransferManifest, origin_marker: bytes
    ) -> VirtualFilesDataObject:
        """Собрать объект с нашими колбэками - без буфера обмена.

        Отдельно от публикации, потому что проводка колбэков проверяется так
        без рабочего стола, а _publish_now зовёт ровно это.
        """
        if not self._callbacks:
            raise RuntimeError("колбэки не установлены")
        data_object = VirtualFilesDataObject(
            manifest,
            open_pipe=self._callbacks["open_pipe"],
            request_read=self._callbacks["request_read"],
            close_pipe=self._callbacks["close_pipe"],
            origin_marker=origin_marker,
            async_capability=ASYNC_CAPABILITY_REQUIRED,
        )
        data_object.on_operation_finished = self._callbacks["on_operation_finished"]
        return data_object

    def _refuse(self, reason: str) -> None:
        """Отказ на стороне Qt: и логом, и сигналом, чтобы его было видно в UI."""
        logger.error("публикация отклонена: %s", reason)
        self.publish_failed.emit(reason)

    # ------------------------------------------------------------------ поток STA

    def _run(self) -> None:
        try:
            _ole_initialize()
        except OSError as error:
            # Без апартамента публиковать нечем. Молчание здесь оставило бы
            # start() ждать пять секунд и вернуться как ни в чём не бывало.
            self._start_error = error
            logger.exception("OleInitialize отказал - буфер обмена недоступен")
            self._ready.set()
            return
        self._thread_id = threading.get_ident()
        self._ready.set()
        try:
            self._pump_until_stopped()
        finally:
            self._release_clipboard()
            self._pump_out_teardown()
            _ole_uninitialize()

    def _pump_until_stopped(self) -> None:
        """Насос сообщений апартамента. Без него объект в буфере вешает оболочку."""
        message = wintypes.MSG()
        while not self._stopping.is_set():
            pending = self._take_pending()
            if pending is not None:
                self._publish_now(*pending)
            self._prune_retired()
            self._drain_messages(message)
            if self._stopping.is_set():
                break
            _wait_for_messages(_PUMP_INTERVAL_MS)

    def _pump_out_teardown(self) -> None:
        """Докачать насос перед гашением апартамента.

        Нижняя граница - _TEARDOWN_PUMP_S, и она измерена (см. константу).
        Сверх неё крутимся, пока на наши объекты держат ссылки снаружи, и
        не дольше _TEARDOWN_CEILING_S: достигнутый потолок - это диагноз
        "константа мала", а не молча вернувшееся падение.
        """
        if self._published is None and not self._retired:
            # Буфер мы не брали - обрывать нечего и ждать нечего.
            return
        message = wintypes.MSG()
        started = time.monotonic()
        floor = started + _TEARDOWN_PUMP_S
        ceiling = started + _TEARDOWN_CEILING_S
        while True:
            self._drain_messages(message)
            now = time.monotonic()
            if now >= floor and not self._externally_referenced():
                return
            if now >= ceiling:
                logger.warning(
                    "через %.2f с после отдачи буфера на наши объекты всё ещё "
                    "держат ссылки - апартамент гасим с незавершёнными вызовами",
                    _TEARDOWN_CEILING_S,
                )
                return
            _wait_for_messages(_PUMP_INTERVAL_MS)

    def _held_objects(self) -> list[VirtualFilesDataObject]:
        published = [] if self._published is None else [self._published]
        return published + self._retired

    def _externally_referenced(self) -> bool:
        """Держит ли кто-то, кроме нас, ссылку хоть на один наш объект.

        Счётчик 1 - это наша собственная ссылка от конструктора: всё, что
        выше, выдано наружу.
        """
        return any(obj.refcount > 1 for obj in self._held_objects())

    def _prune_retired(self) -> None:
        """Отпустить прежние публикации, которые оболочка уже отпустила."""
        self._retired = [obj for obj in self._retired if obj.refcount > 1]

    @staticmethod
    def _drain_messages(message) -> None:
        while ctypes.windll.user32.PeekMessageW(
            ctypes.byref(message), None, 0, 0, _PM_REMOVE
        ):
            ctypes.windll.user32.TranslateMessage(ctypes.byref(message))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(message))

    def _take_pending(self) -> tuple[TransferManifest, bytes] | None:
        with self._lock:
            pending, self._pending = self._pending, None
        return pending

    def _take_the_clipboard(self, pointer) -> int:
        """OleSetClipboard с повторами, пока буфер занят кем-то другим.

        Насос между попытками крутится: пауза в 50 мс с мёртвой очередью
        задержала бы входящий COM-вызов ровно на эти 50 мс.
        """
        message = wintypes.MSG()
        result = CLIPBRD_E_CANT_OPEN
        for attempt in range(_CLIPBOARD_RETRIES):
            result = _ole_set_clipboard(pointer)
            if result != CLIPBRD_E_CANT_OPEN:
                # Любой другой код - это отказ по существу, и повторять его
                # значило бы тянуть время на ошибке, которая не пройдёт.
                return result
            logger.info(
                "буфер обмена занят, попытка %d из %d", attempt + 1, _CLIPBOARD_RETRIES
            )
            self._drain_messages(message)
            _wait_for_messages(_CLIPBOARD_RETRY_MS)
        return result

    def _publish_now(self, manifest: TransferManifest, origin_marker: bytes) -> None:
        try:
            data_object = self.build_data_object(manifest, origin_marker)
            result = self._take_the_clipboard(data_object.pointer)
            if result != S_OK:
                raise OSError(f"OleSetClipboard вернул 0x{result & 0xFFFFFFFF:08X}")
            # Держим объект: буфер обмена хранит только указатель.
            self._retire(self._published)
            self._published = data_object
        except Exception as error:  # noqa: BLE001 - падение потока STA убило бы фичу молча
            logger.exception("не удалось опубликовать файлы в буфер обмена")
            post_to_service(self, "_report_publish_failure", str(error))

    def _retire(self, previous: VirtualFilesDataObject | None) -> None:
        """Прежнюю публикацию отправить в отставку, а не выбросить.

        Присвоить self._published новый объект поверх старого значило бы
        освободить у старого и таблицу, и список колбэков, и саму память,
        на которую указывает pointer, - потому что ComObject живёт ровно
        столько, сколько на него есть ссылка из Python, и с COM-счётчиком
        она никак не связана. А вставка старого дерева к этому моменту
        вполне может идти: асинхронный режим (ASYNC_CAPABILITY_REQUIRED)
        для того и объявлен, чтобы она пережила само копирование. Очередной
        IStream::Read или Release попал бы в освобождённую память - тот же
        0xC0000005, что и в задаче 2.4, только этажом выше.
        """
        if previous is None:
            return
        self._retired.append(previous)

    def _release_clipboard(self) -> None:
        """Отдать буфер системе, иначе он умрёт вместе с потоком.

        Без этого вставка после выхода отдала бы пустоту. Если мы ничего не
        публиковали, звать нечего.
        """
        if self._published is None:
            return
        result = _ole_flush_clipboard()
        if result != S_OK:
            logger.warning("OleFlushClipboard вернул 0x%08X", result & 0xFFFFFFFF)

    @Slot(str)
    def _report_publish_failure(self, reason: str) -> None:
        self.publish_failed.emit(reason)


__all__ = [
    "ASYNC_CAPABILITY_REQUIRED",
    "FORMAT_CONTENTS_NAME",
    "FORMAT_DESCRIPTOR_NAME",
    "FORMAT_DROP_EFFECT_NAME",
    "FORMAT_ORIGIN_NAME",
    "FormatEnumerator",
    "VirtualFilesDataObject",
    "WindowsFileClipboardBackend",
    "descriptor_entries",
    "group_descriptor_bytes",
    "post_to_service",
]
