"""IDataObject целиком, в одном процессе. Проводник приходит в фазе 4."""

from __future__ import annotations

import ctypes
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="IDataObject - это Windows")

from ctypes import wintypes

from duo_input.transfer.model import ENTRY_DIRECTORY, ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.pipe import ChunkPipe
from duo_input.transfer.windows_com import (
    DATADIR_GET,
    DROPEFFECT_COPY,
    DV_E_FORMATETC,
    DV_E_TYMED,
    E_FAIL,
    S_FALSE,
    S_OK,
    TYMED_HGLOBAL,
    TYMED_ISTREAM,
    call_enum_format_etc_pointer,
    call_get_data,
    call_get_data_medium,
    call_query_get_data,
    call_stream_stat,
    register_clipboard_format,
)
from duo_input.transfer.windows_files import (
    FORMAT_CONTENTS_NAME,
    FORMAT_DESCRIPTOR_NAME,
    FORMAT_DROP_EFFECT_NAME,
    FORMAT_ORIGIN_NAME,
    VirtualFilesDataObject,
)

# Приватные имена нужны затем, что эти тесты ходят к объекту ТЕМ ЖЕ путём,
# которым придёт Проводник: через vtable, а не через методы Python. Прототип,
# перепутанный здесь, - это 0xC0000005, а не красный тест, поэтому берутся
# ровно те же прототипы, которыми объект заполняет свою таблицу.
from duo_input.transfer.windows_com import (
    _END_OP,
    _ENUM_CLONE,
    _ENUM_FORMAT_ETC,
    _ENUM_NEXT,
    _ENUM_RESET,
    _ENUM_SKIP,
    _GET_ASYNC,
    _IN_OP,
    _SET_ASYNC,
    _START_OP,
    E_NOINTERFACE,
    E_POINTER,
    FORMATETC,
    IID_IASYNCCAPABILITY,
    _slot,
    call_query_interface,
    guid_from_string,
)

#: Слоты vtable. IDataObjectAsyncCapability наследует IUnknown, а не
#: IDataObject, поэтому его методы начинаются с третьего слота СВОЕЙ таблицы.
_ASYNC_SET_MODE, _ASYNC_GET_MODE, _ASYNC_START, _ASYNC_IN, _ASYNC_END = 3, 4, 5, 6, 7
_NEXT, _SKIP, _RESET, _CLONE = 3, 4, 5, 6
_ENUM_FORMAT_ETC_SLOT = 8

#: 0x800704C7 = HRESULT_FROM_WIN32(ERROR_CANCELLED), в знаковом виде: HRESULT
#: объявлен как c_long, а отмена приходит именно этим числом (спайк, Run C).
_CANCELLED = 0x800704C7 - (1 << 32)


def _manifest():
    return TransferManifest(
        transfer_id="t-1",
        entries=(
            TransferEntry(path="Photos", kind=ENTRY_DIRECTORY, size=0, mtime_ns=1),
            TransferEntry(path="Photos/a.bin", kind=ENTRY_FILE, size=8, mtime_ns=2),
        ),
    )


def _make(**kwargs):
    pipes: dict[tuple[str, int], ChunkPipe] = {}
    reads: list[tuple[str, int, int, int]] = []
    closes: list[tuple[str, int, object]] = []

    def open_pipe(transfer_id, entry_index):
        pipe = ChunkPipe(capacity_chunks=1)
        pipes[(transfer_id, entry_index)] = pipe
        return pipe

    obj = VirtualFilesDataObject(
        _manifest(),
        open_pipe=open_pipe,
        request_read=lambda *args: reads.append(args),
        close_pipe=lambda *args: closes.append(args),
        origin_marker=b"origin:1",
        **kwargs,
    )
    return obj, pipes, reads, closes


@pytest.fixture
def data_object():
    return _make()


def _fmt(obj, name, lindex=-1, tymed=TYMED_HGLOBAL):
    return register_clipboard_format(name), lindex, tymed


def _async_pointer(obj):
    """QueryInterface(IDataObjectAsyncCapability) -> (hresult, указатель)."""
    out = ctypes.c_void_p()
    result = call_query_interface(obj.pointer, guid_from_string(IID_IASYNCCAPABILITY), out)
    return result, out


def _enumerator_pointer(obj):
    """Указатель из out-параметра EnumFormatEtc - ровно то, что видит Проводник.

    Брать его из obj._enumerators значило бы держать на объект живую ссылку
    из Python и потом проверять, что он пережил сборку мусора: такая
    проверка не может провалиться.
    """
    _result, pointer = call_enum_format_etc_pointer(obj.pointer, DATADIR_GET)
    return pointer


def _stream_pointer(obj, lindex):
    """Указатель на IStream из STGMEDIUM - тоже без ссылок из Python."""
    result, medium = call_get_data_medium(
        obj.pointer, register_clipboard_format(FORMAT_CONTENTS_NAME), lindex, TYMED_ISTREAM
    )
    return result, ctypes.c_void_p(medium.data)


def _drain(enum_pointer) -> list[int]:
    """Вычерпать перечислитель через его vtable и вернуть cfFormat по порядку."""
    formats: list[int] = []
    buffer = (FORMATETC * 8)()
    fetched = wintypes.ULONG(0)
    while True:
        step = _slot(enum_pointer, _NEXT, _ENUM_NEXT)(
            enum_pointer, 8, buffer, ctypes.byref(fetched)
        )
        formats.extend(buffer[index].cfFormat for index in range(fetched.value))
        if step != S_OK or not fetched.value:
            return formats


def test_the_descriptor_format_is_offered(data_object):
    obj, *_ = data_object
    cf, lindex, tymed = _fmt(obj, FORMAT_DESCRIPTOR_NAME)

    assert call_query_get_data(obj.pointer, cf, lindex, tymed) == S_OK


def test_the_contents_format_is_offered_for_a_stream(data_object):
    obj, *_ = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)

    assert call_query_get_data(obj.pointer, cf, 1, TYMED_ISTREAM) == S_OK


def test_an_unknown_format_is_refused(data_object):
    obj, *_ = data_object

    assert call_query_get_data(obj.pointer, 0xC123, -1, TYMED_HGLOBAL) == DV_E_FORMATETC


def test_asking_for_the_descriptor_returns_the_manifest_blob(data_object):
    obj, *_ = data_object
    cf, lindex, tymed = _fmt(obj, FORMAT_DESCRIPTOR_NAME)

    result, payload = call_get_data(obj.pointer, cf, lindex, tymed)

    assert result == S_OK
    assert int.from_bytes(payload[:4], "little") == 2


def test_the_drop_effect_is_always_copy_even_after_a_cut(data_object):
    # Ctrl+X на источнике сюда не доходит: удаление файлов на другой машине
    # необратимо и отложено в отдельный milestone.
    obj, *_ = data_object
    cf, lindex, tymed = _fmt(obj, FORMAT_DROP_EFFECT_NAME)

    _result, payload = call_get_data(obj.pointer, cf, lindex, tymed)

    assert int.from_bytes(payload[:4], "little") == DROPEFFECT_COPY


def test_the_origin_marker_is_advertised_so_our_own_paste_is_recognised(data_object):
    # Без этого наша собственная публикация вернулась бы к нам как локальное
    # копирование - см. задачу 2.7.
    obj, *_ = data_object
    cf, lindex, tymed = _fmt(obj, FORMAT_ORIGIN_NAME)

    result, payload = call_get_data(obj.pointer, cf, lindex, tymed)

    assert result == S_OK
    assert payload.startswith(b"origin:1")


def test_asking_for_contents_opens_a_pipe_and_returns_a_stream(data_object):
    obj, pipes, _reads, _closes = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)

    result, medium_tymed, _payload = call_get_data(
        obj.pointer, cf, 1, TYMED_ISTREAM, want_medium=True
    )

    assert result == S_OK
    assert medium_tymed == TYMED_ISTREAM
    assert ("t-1", 1) in pipes
    assert 1 in obj.streams


def test_contents_are_refused_when_explorer_will_not_take_a_stream(data_object):
    # Спайк записал, просит ли Проводник HGLOBAL. Если просит - решение
    # принимается там, а не здесь молча.
    obj, *_ = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)

    result, _medium, _payload = call_get_data(
        obj.pointer, cf, 1, TYMED_HGLOBAL, want_medium=True
    )

    assert result == DV_E_TYMED


def test_contents_of_a_directory_entry_are_refused(data_object):
    obj, *_ = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)

    result, _medium, _payload = call_get_data(
        obj.pointer, cf, 0, TYMED_ISTREAM, want_medium=True
    )

    assert result == DV_E_FORMATETC


def test_a_contents_index_outside_the_manifest_is_refused(data_object):
    obj, *_ = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)

    result, _medium, _payload = call_get_data(
        obj.pointer, cf, 99, TYMED_ISTREAM, want_medium=True
    )

    assert result == DV_E_FORMATETC


def test_a_negative_contents_index_is_refused(data_object):
    obj, *_ = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)

    result, _medium, _payload = call_get_data(
        obj.pointer, cf, -1, TYMED_ISTREAM, want_medium=True
    )

    assert result == DV_E_FORMATETC


def test_the_stream_is_held_so_python_cannot_collect_it_under_explorer(data_object):
    # Собранный сборщиком мусора поток - это переход Проводника по
    # освобождённому адресу: падение без исключения и без записи в журнал.
    #
    # Указатель берётся ИЗ STGMEDIUM, как его берёт Проводник: живой ссылки
    # на объект Python у теста нет, поэтому после gc.collect() он жив только
    # если его держит сам объект данных. Проверка через obj.streams[1]
    # держала бы его тем самым обращением, которым проверяет.
    obj, *_ = data_object
    result, stream_pointer = _stream_pointer(obj, 1)
    assert result == S_OK

    import gc

    gc.collect()

    # Stat по указателю - тот же путь, которым Проводник спрашивает размер.
    size, answer = call_stream_stat(stream_pointer)
    assert (answer, size) == (S_OK, 8)


def test_a_second_ask_for_the_same_entry_does_not_free_the_first_stream(data_object):
    # Словарь .streams ключуется по lindex: второй GetData по тому же
    # индексу (вторая вставка из того же объекта буфера, повтор оболочки)
    # вытеснил бы из него первый поток, а вместе с ним и единственную
    # ссылку на его vtable - под указателем, который у Проводника ещё в
    # руках.
    obj, *_ = data_object
    first_result, first_pointer = _stream_pointer(obj, 1)
    second_result, second_pointer = _stream_pointer(obj, 1)

    assert (first_result, second_result) == (S_OK, S_OK)
    assert first_pointer.value != second_pointer.value, "второй GetData обязан дать новый поток"

    import gc

    gc.collect()

    assert call_stream_stat(first_pointer) == (8, S_OK)
    assert call_stream_stat(second_pointer) == (8, S_OK)
    # .streams остаётся картой "последний поток по индексу".
    assert obj.streams[1].pointer.value == second_pointer.value


def test_releasing_a_stream_tells_the_owner_which_entry_finished(data_object):
    obj, _pipes, _reads, closes = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)
    call_get_data(obj.pointer, cf, 1, TYMED_ISTREAM, want_medium=True)

    from duo_input.transfer.windows_com import call_release

    call_release(obj.streams[1].pointer)

    assert closes == [("t-1", 1, None)]


def test_a_read_on_the_stream_reaches_the_request_callback(data_object):
    obj, pipes, reads, _closes = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)
    call_get_data(obj.pointer, cf, 1, TYMED_ISTREAM, want_medium=True)
    pipes[("t-1", 1)].push(b"12345678")

    from duo_input.transfer.windows_com import call_stream_read

    payload, result = call_stream_read(obj.streams[1].pointer, 8)

    assert (payload, result) == (b"12345678", S_OK)
    assert reads == [], "данные уже лежали в очереди - запрос был лишним"


def test_the_enumerator_lists_the_formats_we_advertise(data_object):
    obj, *_ = data_object
    from duo_input.transfer.windows_com import call_enum_format_etc

    result, formats = call_enum_format_etc(obj.pointer, DATADIR_GET)

    assert result == S_OK
    assert register_clipboard_format(FORMAT_DESCRIPTOR_NAME) in formats
    assert register_clipboard_format(FORMAT_CONTENTS_NAME) in formats
    assert register_clipboard_format(FORMAT_DROP_EFFECT_NAME) in formats
    assert register_clipboard_format(FORMAT_ORIGIN_NAME) in formats


def test_the_enumerator_refuses_the_set_direction(data_object):
    obj, *_ = data_object
    from duo_input.transfer.windows_com import E_NOTIMPL, call_enum_format_etc

    result, _formats = call_enum_format_etc(obj.pointer, 2)  # DATADIR_SET

    assert result == E_NOTIMPL


def test_every_get_data_call_is_recorded_for_diagnostics(data_object):
    obj, *_ = data_object
    cf, lindex, tymed = _fmt(obj, FORMAT_DESCRIPTOR_NAME)

    call_get_data(obj.pointer, cf, lindex, tymed)

    assert obj.get_data_calls == [(cf, lindex)]


# ---------------------------------------------------------------------------
# Перечислитель: остальные слоты, отказы и время жизни
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("lindex", [0, 99, -1])
def test_query_get_data_predicts_get_data_for_every_refused_index(data_object, lindex):
    # Смысл QueryGetData - предсказать GetData. Разойдясь, они дают самый
    # дорогой вид дефекта: Проводник начинает копирование, которому мы затем
    # отказываем поштучно, уже показав пользователю индикатор.
    obj, *_ = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)

    predicted = call_query_get_data(obj.pointer, cf, lindex, TYMED_ISTREAM)
    actual, _medium, _payload = call_get_data(
        obj.pointer, cf, lindex, TYMED_ISTREAM, want_medium=True
    )

    assert predicted == actual == DV_E_FORMATETC


def test_the_enumerator_is_not_handed_out_without_somewhere_to_put_it(data_object):
    obj, *_ = data_object

    result = _slot(obj.pointer, _ENUM_FORMAT_ETC_SLOT, _ENUM_FORMAT_ETC)(
        obj.pointer, DATADIR_GET, None
    )

    assert result == E_POINTER


def test_the_enumerator_refuses_to_fill_a_null_array(data_object):
    obj, *_ = data_object
    enum_pointer = _enumerator_pointer(obj)

    result = _slot(enum_pointer, _NEXT, _ENUM_NEXT)(enum_pointer, 1, None, None)

    assert result == E_POINTER


def test_skip_and_reset_move_the_enumerator_cursor(data_object):
    obj, *_ = data_object
    enum_pointer = _enumerator_pointer(obj)

    assert _slot(enum_pointer, _RESET, _ENUM_RESET)(enum_pointer) == S_OK
    assert _slot(enum_pointer, _SKIP, _ENUM_SKIP)(enum_pointer, 3) == S_OK

    assert _drain(enum_pointer) == [register_clipboard_format(FORMAT_ORIGIN_NAME)]


def test_next_says_s_false_only_when_it_could_not_fill_the_request(data_object):
    # S_FALSE - это то, чем перечислитель говорит "больше нет". Всегда
    # отвечая S_OK, он заставил бы вызывающего спрашивать снова, а
    # вычерпывающий цикл, доверяющий коду возврата, не остановился бы.
    obj, *_ = data_object
    enum_pointer = _enumerator_pointer(obj)
    _slot(enum_pointer, _RESET, _ENUM_RESET)(enum_pointer)
    buffer = (FORMATETC * 8)()
    fetched = wintypes.ULONG(0)

    exact = _slot(enum_pointer, _NEXT, _ENUM_NEXT)(
        enum_pointer, 4, buffer, ctypes.byref(fetched)
    )
    assert (exact, fetched.value) == (S_OK, 4)

    empty = _slot(enum_pointer, _NEXT, _ENUM_NEXT)(
        enum_pointer, 4, buffer, ctypes.byref(fetched)
    )
    assert (empty, fetched.value) == (S_FALSE, 0)


def test_next_reports_a_short_read_even_without_a_fetched_counter(data_object):
    # pceltFetched вправе быть NULL при celt=1, и тогда единственный ответ -
    # код возврата. Запись в NULL съела бы его: исключение из обратного
    # вызова ctypes превращается в 0, то есть в S_OK.
    obj, *_ = data_object
    enum_pointer = _enumerator_pointer(obj)
    _slot(enum_pointer, _RESET, _ENUM_RESET)(enum_pointer)
    _slot(enum_pointer, _SKIP, _ENUM_SKIP)(enum_pointer, 3)
    buffer = (FORMATETC * 8)()

    result = _slot(enum_pointer, _NEXT, _ENUM_NEXT)(enum_pointer, 2, buffer, None)

    assert result == S_FALSE
    assert buffer[0].cfFormat == register_clipboard_format(FORMAT_ORIGIN_NAME)


def test_a_clone_continues_from_the_cursor_and_outlives_the_collector(data_object):
    obj, *_ = data_object
    enum_pointer = _enumerator_pointer(obj)
    _slot(enum_pointer, _RESET, _ENUM_RESET)(enum_pointer)
    _slot(enum_pointer, _SKIP, _ENUM_SKIP)(enum_pointer, 3)

    clone = ctypes.c_void_p()
    result = _slot(enum_pointer, _CLONE, _ENUM_CLONE)(enum_pointer, ctypes.byref(clone))

    import gc

    gc.collect()

    assert result == S_OK
    # Клона, которого никто не держит, не станет сразу после возврата - и
    # следующая строка ушла бы по освобождённому адресу.
    assert _drain(clone) == [register_clipboard_format(FORMAT_ORIGIN_NAME)]


def test_the_enumerator_is_not_cloned_without_somewhere_to_put_it(data_object):
    obj, *_ = data_object
    enum_pointer = _enumerator_pointer(obj)

    result = _slot(enum_pointer, _CLONE, _ENUM_CLONE)(enum_pointer, None)

    assert result == E_POINTER


def test_the_enumerator_is_held_so_python_cannot_collect_it_under_explorer(data_object):
    # Как и у потока: указатель получен из out-параметра, ссылки из Python
    # на перечислитель нет, и уцелеть после сборки он может только потому,
    # что его держит объект данных.
    obj, *_ = data_object
    enum_pointer = _enumerator_pointer(obj)

    import gc

    gc.collect()

    _slot(enum_pointer, _RESET, _ENUM_RESET)(enum_pointer)
    assert _drain(enum_pointer) == [
        register_clipboard_format(FORMAT_DESCRIPTOR_NAME),
        register_clipboard_format(FORMAT_CONTENTS_NAME),
        register_clipboard_format(FORMAT_DROP_EFFECT_NAME),
        register_clipboard_format(FORMAT_ORIGIN_NAME),
    ]


# ---------------------------------------------------------------------------
# IDataObjectAsyncCapability
# ---------------------------------------------------------------------------


def test_the_async_capability_is_offered_when_it_is_advertised(data_object):
    obj, *_ = data_object

    result, pointer = _async_pointer(obj)

    assert result == S_OK
    assert pointer.value is not None


def test_the_async_capability_is_refused_when_it_is_not_advertised():
    obj, *_ = _make(async_capability=False)

    result, pointer = _async_pointer(obj)

    assert result == E_NOINTERFACE
    assert pointer.value is None


def test_the_async_pointer_carries_its_own_vtable_not_the_data_objects(data_object):
    # IDataObjectAsyncCapability наследует IUnknown, а не IDataObject: отдать
    # на него указатель нашей таблицы IDataObject значит вызвать GetDataHere
    # там, где Проводник звал GetAsyncMode, и разобрать BOOL как FORMATETC*.
    # Слот 4 чужой таблицы вернул бы E_NOTIMPL, не записав ничего.
    obj, *_ = data_object
    _result, pointer = _async_pointer(obj)

    flag = wintypes.BOOL(0)
    answer = _slot(pointer, _ASYNC_GET_MODE, _GET_ASYNC)(pointer, ctypes.byref(flag))

    assert (answer, flag.value) == (S_OK, 1)


def test_get_async_mode_declares_the_capability_it_never_reads_one_back(data_object):
    # Проводник спрашивает GetAsyncMode ПЕРВЫМ, до всякого SetAsyncMode
    # (спайк, Run B: это первый вызов жизненного цикла). Ответ-считывание
    # режима вернул бы "0 - асинхронный режим не нужен", и ни StartOperation,
    # ни EndOperation не пришли бы вовсе - ровно это обнулило один прогон
    # измерений. Здесь режим сначала выставлен в "нет", и ответ обязан
    # остаться утвердительным.
    obj, *_ = data_object
    _result, pointer = _async_pointer(obj)
    _slot(pointer, _ASYNC_SET_MODE, _SET_ASYNC)(pointer, 0)
    assert obj.async_mode is False

    flag = wintypes.BOOL(0)
    answer = _slot(pointer, _ASYNC_GET_MODE, _GET_ASYNC)(pointer, ctypes.byref(flag))

    assert (answer, flag.value) == (S_OK, 1)


def test_get_async_mode_refuses_a_null_out_parameter(data_object):
    obj, *_ = data_object
    _result, pointer = _async_pointer(obj)

    assert _slot(pointer, _ASYNC_GET_MODE, _GET_ASYNC)(pointer, None) == E_POINTER


def test_in_operation_answers_whether_the_shell_started_one(data_object):
    obj, *_ = data_object
    _result, pointer = _async_pointer(obj)
    flag = wintypes.BOOL(1)

    _slot(pointer, _ASYNC_IN, _IN_OP)(pointer, ctypes.byref(flag))
    assert flag.value == 0

    _slot(pointer, _ASYNC_START, _START_OP)(pointer, None)
    _slot(pointer, _ASYNC_IN, _IN_OP)(pointer, ctypes.byref(flag))
    assert flag.value == 1


def test_in_operation_refuses_a_null_out_parameter(data_object):
    obj, *_ = data_object
    _result, pointer = _async_pointer(obj)

    assert _slot(pointer, _ASYNC_IN, _IN_OP)(pointer, None) == E_POINTER


def test_ending_the_operation_hands_the_owner_the_shells_hresult(data_object):
    # Это ЕДИНСТВЕННЫЙ явный сигнал завершения: 0 - готово, 0x800704C7 -
    # отменено. Без него отменённая сессия неотличима от зависшей.
    obj, *_ = data_object
    finished: list[int] = []
    obj.on_operation_finished = finished.append
    _result, pointer = _async_pointer(obj)
    _slot(pointer, _ASYNC_START, _START_OP)(pointer, None)
    assert obj.in_operation is True

    answer = _slot(pointer, _ASYNC_END, _END_OP)(pointer, _CANCELLED, None, 1)

    assert answer == S_OK
    assert finished == [_CANCELLED]
    assert obj.in_operation is False


def test_an_owner_whose_open_pipe_fails_is_reported_to_the_shell():
    # Чужой код зовётся из обратного вызова ctypes: исключение оттуда
    # печатается в stderr, а COM получает 0, то есть S_OK. Проводник пошёл
    # бы читать STGMEDIUM, которого никто не заполнил.
    def open_pipe(_transfer_id, _entry_index):
        raise RuntimeError("передача уже разобрана")

    obj = VirtualFilesDataObject(
        _manifest(),
        open_pipe=open_pipe,
        request_read=lambda *args: None,
        close_pipe=lambda *args: None,
        origin_marker=b"origin:1",
    )

    result, medium = call_get_data_medium(
        obj.pointer, register_clipboard_format(FORMAT_CONTENTS_NAME), 1, TYMED_ISTREAM
    )

    assert result == E_FAIL
    assert medium.tymed == 0, "отказавший GetData не смеет объявлять носитель"
    assert obj.streams == {}
    assert isinstance(obj.last_open_error, RuntimeError)


def test_an_owner_whose_completion_hook_fails_does_not_take_the_shell_with_it(data_object):
    # Та же ловушка с другой стороны: упавший обработчик вернул бы оболочке
    # 0 (S_OK) и молча потерял бы единственный сигнал о завершении.
    obj, *_ = data_object

    def boom(_result):
        raise RuntimeError("владелец упал")

    obj.on_operation_finished = boom
    _result, pointer = _async_pointer(obj)
    _slot(pointer, _ASYNC_START, _START_OP)(pointer, None)

    answer = _slot(pointer, _ASYNC_END, _END_OP)(pointer, 0, None, 1)

    assert answer == S_OK
    assert obj.in_operation is False
    assert isinstance(obj.last_operation_error, RuntimeError)


def test_ending_the_operation_without_an_owner_hook_is_not_a_crash():
    # Вызов делается напрямую, а не через vtable: исключение из обратного
    # вызова ctypes печатается в stderr, а COM получает неопределённое
    # значение (наблюдалось положительное, то есть успех по правилу
    # SUCCEEDED). Проверка через vtable осталась бы зелёной с удалённой
    # защитой и не проверяла бы ничего.
    obj, *_ = _make()

    assert obj._end_operation(None, 0, None, 1) == S_OK
    # И это НЕ ошибка владельца: обработчика просто нет. Без проверки на
    # None его отсутствие попало бы в поле ошибок как TypeError - владелец
    # увидел бы отказ там, где ничего не случилось.
    assert obj.last_operation_error is None
