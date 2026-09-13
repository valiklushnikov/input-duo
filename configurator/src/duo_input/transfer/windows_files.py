"""Publish a transfer manifest as Windows virtual files.

Two things live here: the ``FILEGROUPDESCRIPTORW`` blob that describes the
tree, and the ``IDataObject`` that serves it - descriptors, the drop effect,
the origin marker, and one pipe-backed ``IStream`` per file.

Manifest paths use ``/`` on the wire.  Windows Explorer expects ``\\`` in
``FILEDESCRIPTORW.cFileName``; this module is the single conversion point.

Как и windows_com, этот модуль исполняется В COM-потоке и поэтому не смеет
видеть Qt - ни прямо, ни через чужой импорт. Правило проверяется
``test_boundary_shared_modules_stay_qt_free.py``, а не обещано в прозе.
"""

from __future__ import annotations

import ctypes
import logging
from collections.abc import Callable
from ctypes import wintypes

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


__all__ = [
    "FORMAT_CONTENTS_NAME",
    "FORMAT_DESCRIPTOR_NAME",
    "FORMAT_DROP_EFFECT_NAME",
    "FORMAT_ORIGIN_NAME",
    "FormatEnumerator",
    "VirtualFilesDataObject",
    "descriptor_entries",
    "group_descriptor_bytes",
]
