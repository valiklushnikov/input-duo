"""Примитивы COM. Ни одного импорта PySide6 в модуле, который они описывают.

Ошибка здесь роняет процесс, а не бросает исключение, поэтому проверяется
всё: смещения в структурах, подсчёт ссылок, хук последнего Release и
преобразование времени.

Список из брифа - это низ, а не потолок. Дописаны состязательные случаи для
GUID (смешанный порядок байтов, разница в один полубайт), краевые случаи
преобразования времени и HGLOBAL, отказ по NULL-указателю и демонстрация -
не обещание - того, что колбэки и vtable переживают сборку мусора.
"""

from __future__ import annotations

import ctypes
import gc
import sys
import threading
import weakref
from ctypes import wintypes

import pytest

from duo_input.transfer import windows_com

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="COM - это Windows")

from duo_input.transfer.windows_com import (
    DROPEFFECT_COPY,
    DV_E_FORMATETC,
    DV_E_TYMED,
    E_FAIL,
    E_NOINTERFACE,
    E_NOTIMPL,
    E_POINTER,
    FILE_ATTRIBUTE_DIRECTORY,
    IID_IDATAOBJECT,
    IID_ISTREAM,
    IID_IUNKNOWN,
    S_FALSE,
    S_OK,
    STG_E_INVALIDFUNCTION,
    STG_E_READFAULT,
    TYMED_ISTREAM,
    ComObject,
    FILEDESCRIPTORW,
    call_add_ref,
    call_query_interface,
    call_release,
    filetime_from_ns,
    guid_from_string,
    make_vtable,
    register_clipboard_format,
    same_guid,
    to_hglobal,
)


def test_a_fresh_object_holds_one_reference():
    assert ComObject([IID_IUNKNOWN]).refcount == 1


def test_the_reference_count_walks_up_and_back_down():
    obj = ComObject([IID_IUNKNOWN])

    assert call_add_ref(obj.pointer) == 2
    assert call_release(obj.pointer) == 1


def test_query_interface_grants_a_supported_iid_and_counts_the_reference():
    obj = ComObject([IID_IUNKNOWN, IID_IDATAOBJECT])
    out = ctypes.c_void_p()

    result = call_query_interface(obj.pointer, guid_from_string(IID_IDATAOBJECT), out)

    assert result == S_OK
    assert out.value == obj.pointer.value
    assert obj.refcount == 2


def test_query_interface_refuses_an_unsupported_iid_and_nulls_the_out_pointer():
    obj = ComObject([IID_IUNKNOWN])
    out = ctypes.c_void_p(0xDEAD)

    result = call_query_interface(
        obj.pointer, guid_from_string("{11111111-2222-3333-4444-555555555555}"), out
    )

    assert result == E_NOINTERFACE
    assert out.value is None


def test_the_last_release_fires_the_hook_exactly_once():
    # Release на потоке - это то, чем Проводник говорит "с этим файлом всё".
    # Спайк давал счётчику уйти в ноль и не делал ничего.
    released: list[int] = []
    obj = ComObject([IID_IUNKNOWN])
    obj.on_last_release = lambda: released.append(1)

    call_add_ref(obj.pointer)
    call_release(obj.pointer)
    assert released == []

    call_release(obj.pointer)
    assert released == [1]

    call_release(obj.pointer)
    assert released == [1], "хук сработал повторно на уже освобождённом объекте"


def test_guid_parses_from_its_braced_string_form():
    guid = guid_from_string("{0000000C-0000-0000-C000-000000000046}")

    assert guid.Data1 == 0x0000000C
    assert guid.Data4[7] == 0x46


def test_two_guids_compare_by_value_not_by_identity():
    assert same_guid(guid_from_string(IID_IUNKNOWN), guid_from_string(IID_IUNKNOWN))
    assert not same_guid(guid_from_string(IID_IUNKNOWN), guid_from_string(IID_IDATAOBJECT))


def test_the_file_descriptor_structure_is_the_size_windows_expects():
    # 592 байта в 64-разрядной сборке. Расхождение здесь означает, что
    # Проводник прочитает наши поля по неверным смещениям и покажет мусорные
    # имена и размеры - без единой ошибки.
    assert ctypes.sizeof(FILEDESCRIPTORW) == 592


def test_the_file_name_field_holds_260_wide_characters():
    descriptor = FILEDESCRIPTORW()
    descriptor.cFileName = "a" * 259

    assert descriptor.cFileName == "a" * 259


def test_a_modification_time_round_trips_into_a_filetime():
    # 1 января 2020, 00:00:00 UTC в наносекундах Unix.
    filetime = filetime_from_ns(1_577_836_800_000_000_000)
    combined = (filetime.dwHighDateTime << 32) | filetime.dwLowDateTime

    # FILETIME считает интервалы по 100 нс от 1601-01-01.
    assert combined == (1_577_836_800 + 11_644_473_600) * 10_000_000


def test_a_zero_modification_time_does_not_become_a_negative_filetime():
    filetime = filetime_from_ns(0)

    assert filetime.dwHighDateTime >= 0
    assert filetime.dwLowDateTime >= 0


def test_registering_the_same_clipboard_format_twice_returns_the_same_id():
    first = register_clipboard_format("DuoInputTestFormat")
    second = register_clipboard_format("DuoInputTestFormat")

    assert first == second != 0


def test_a_payload_survives_the_trip_through_a_global_handle():
    payload = DROPEFFECT_COPY.to_bytes(4, "little")

    handle = to_hglobal(payload)

    address = ctypes.windll.kernel32.GlobalLock(handle)
    try:
        assert ctypes.string_at(address, 4) == payload
    finally:
        ctypes.windll.kernel32.GlobalUnlock(handle)
        ctypes.windll.kernel32.GlobalFree(handle)


def test_the_constants_have_the_values_the_shell_defines():
    assert (TYMED_ISTREAM, DROPEFFECT_COPY, FILE_ATTRIBUTE_DIRECTORY) == (4, 1, 0x10)


# --------------------------------------------------------------------------
# Дальше - то, чего в списке брифа не было.
# --------------------------------------------------------------------------

_KERNEL32 = ctypes.windll.kernel32
_GLOBAL_SIZE = _KERNEL32.GlobalSize
_GLOBAL_SIZE.argtypes = [wintypes.HGLOBAL]
_GLOBAL_SIZE.restype = ctypes.c_size_t
_GLOBAL_FREE = _KERNEL32.GlobalFree
_GLOBAL_FREE.argtypes = [wintypes.HGLOBAL]
_GLOBAL_FREE.restype = wintypes.HGLOBAL

#: Прототип для проверки чужого слота: HRESULT F(this).
_PROBE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)


def _call_slot(pointer, index: int) -> int:
    """Вызвать index-й слот vtable напрямую, минуя call_* хелперы."""
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p)).contents
    entries = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))
    return _PROBE(entries[index])(pointer)


def test_query_interface_refuses_a_null_out_pointer():
    # Проводник имеет право спросить интерфейс с ppv=NULL; разыменование
    # этого NULL - не исключение, а 0xC0000005 на весь процесс.
    obj = ComObject([IID_IUNKNOWN])

    result = call_query_interface(obj.pointer, guid_from_string(IID_IUNKNOWN), None)

    assert result == E_POINTER
    assert obj.refcount == 1, "отказ по NULL не имеет права считать ссылку"


def test_release_never_reports_a_negative_count():
    # Release объявлен как ULONG: -1 доедет до вызывающего как 4294967295,
    # то есть "объект держат четыре миллиарда раз", и он не освободится
    # никогда.
    obj = ComObject([IID_IUNKNOWN])

    assert call_release(obj.pointer) == 0
    assert call_release(obj.pointer) == 0


def test_the_hresults_are_the_values_the_headers_define():
    # Знак считается вручную ровно один раз, в _hresult; здесь лежат числа
    # из заголовков, посчитанные независимо от него.
    assert (S_OK, S_FALSE) == (0, 1)
    assert (E_NOTIMPL, E_NOINTERFACE, E_POINTER, E_FAIL) == (
        -2147467263,
        -2147467262,
        -2147467261,
        -2147467259,
    )
    assert (STG_E_INVALIDFUNCTION, STG_E_READFAULT) == (-2147287039, -2147287010)
    assert (DV_E_FORMATETC, DV_E_TYMED) == (-2147221404, -2147221399)


def test_a_well_known_iid_keeps_the_mixed_byte_order_of_the_guid_format():
    # Классическое место, где рукописный разбор ломается: первые три поля
    # хранятся little-endian, последние восемь байт - как записаны.
    raw = bytes(memoryview(guid_from_string("{01020304-0506-0708-090A-0B0C0D0E0F10}")).cast("B"))

    assert raw == bytes.fromhex("04030201" "0605" "0807" "090A0B0C0D0E0F10")


def test_a_guid_whose_first_field_bytes_are_swapped_is_a_different_guid():
    # Если бы сравнение шло по полям с ручным разворотом байтов, эти два
    # GUID слились бы в один.
    assert not same_guid(
        guid_from_string("{01020304-0506-0708-090A-0B0C0D0E0F10}"),
        guid_from_string("{04030201-0506-0708-090A-0B0C0D0E0F10}"),
    )


@pytest.mark.parametrize(
    "other",
    [
        "{0000000D-0000-0000-C000-000000000046}",  # полубайт в Data1
        "{0000000C-0001-0000-C000-000000000046}",  # полубайт в Data2
        "{0000000C-0000-0001-C000-000000000046}",  # полубайт в Data3
        "{0000000C-0000-0000-C000-000000000047}",  # полубайт в последнем байте
        "{0000000C-0000-0000-C100-000000000046}",  # полубайт в первом байте Data4
    ],
)
def test_two_guids_differing_by_one_nibble_are_not_the_same(other):
    assert not same_guid(guid_from_string(IID_ISTREAM), guid_from_string(other))


def test_a_well_known_iid_round_trips_through_both_helpers():
    for iid in (IID_IUNKNOWN, IID_IDATAOBJECT, IID_ISTREAM):
        assert same_guid(guid_from_string(iid), guid_from_string(iid))


@pytest.mark.parametrize(
    "text", ["", "not-a-guid", "00000000-0000-0000-C000-000000000046", "{}"]
)
def test_a_string_that_is_not_a_braced_guid_is_refused(text):
    # OSError с системным локализованным текстом здесь не годится: в логе
    # должно быть видно, ЧТО не разобралось.
    with pytest.raises(ValueError):
        guid_from_string(text)


def test_an_added_interface_is_granted_where_it_was_refused_a_moment_ago():
    obj = ComObject([IID_IUNKNOWN])
    out = ctypes.c_void_p()
    assert call_query_interface(obj.pointer, guid_from_string(IID_ISTREAM), out) == E_NOINTERFACE

    obj.add_interface(IID_ISTREAM)

    assert call_query_interface(obj.pointer, guid_from_string(IID_ISTREAM), out) == S_OK


def test_the_vtable_holds_the_addresses_of_the_callbacks_it_was_given():
    first = _PROBE(lambda _this: 0)
    second = _PROBE(lambda _this: 0)

    table = make_vtable(first, second)

    assert len(table) == 2
    assert [table[0], table[1]] == [
        ctypes.cast(first, ctypes.c_void_p).value,
        ctypes.cast(second, ctypes.c_void_p).value,
    ]


def test_a_derived_slot_lands_after_the_three_iunknown_slots():
    # Порядок слотов - весь контракт интерфейса. Промах на один слот - это
    # вызов AddRef вместо Read, и COM об этом не сообщает.
    calls: list[int] = []
    obj = ComObject([IID_IUNKNOWN])

    def probe(_this) -> int:
        calls.append(1)
        return S_OK

    obj.extend_vtable([_PROBE(probe)])

    assert _call_slot(obj.pointer, 3) == S_OK
    assert calls == [1]
    assert call_add_ref(obj.pointer) == 2, "слоты IUnknown уехали со своих мест"


def test_the_object_keeps_its_callbacks_alive_across_a_collection():
    # Показано, а не объявлено: сначала контроль - неудержанный колбэк
    # действительно умирает на сборке мусора (иначе этот тест не увидел бы
    # ничего и при мёртвых колбэках), потом то же наблюдение на колбэках
    # объекта.
    loose = _PROBE(lambda _this: 0)
    loose_watcher = weakref.ref(loose)
    del loose
    gc.collect()
    assert loose_watcher() is None, "контроль не сработал: колбэк переживает сборку и без ссылки"

    obj = ComObject([IID_IUNKNOWN])
    watchers = [weakref.ref(callback) for callback in obj._callbacks]
    assert len(watchers) == 3

    for _ in range(3):
        gc.collect()

    assert [watcher() is not None for watcher in watchers] == [True, True, True]
    assert call_add_ref(obj.pointer) == 2
    assert call_release(obj.pointer) == 1


def test_the_vtable_keeps_pointing_at_the_same_callbacks_across_a_collection():
    obj = ComObject([IID_IUNKNOWN])
    before = ([obj._vtable[index] for index in range(3)], obj.pointer.value)

    for _ in range(3):
        gc.collect()

    assert ([obj._vtable[index] for index in range(3)], obj.pointer.value) == before
    assert before[0] == [
        ctypes.cast(callback, ctypes.c_void_p).value for callback in obj._callbacks
    ]


def test_the_unix_epoch_lands_exactly_on_the_filetime_epoch_offset():
    filetime = filetime_from_ns(0)

    assert (filetime.dwHighDateTime << 32) | filetime.dwLowDateTime == (
        11_644_473_600 * 10_000_000
    )


def test_a_timestamp_between_1601_and_1970_keeps_its_real_value():
    # 1960-01-01. Отрицательное время Unix, но положительный FILETIME:
    # обрезать его было бы ошибкой, файл 1960 года - не файл 1601 года.
    mtime_ns = -315_619_200_000_000_000

    filetime = filetime_from_ns(mtime_ns)

    assert (filetime.dwHighDateTime << 32) | filetime.dwLowDateTime == (
        mtime_ns // 100 + 11_644_473_600 * 10_000_000
    )


def test_a_timestamp_before_the_filetime_epoch_is_clamped_to_zero():
    # Без max(0, ...) число интервалов уходит в минус, а FILETIME
    # беззнаковый: Проводник показал бы дату из далёкого будущего.
    filetime = filetime_from_ns(-2 * 11_644_473_600 * 1_000_000_000)

    assert ((filetime.dwHighDateTime << 32) | filetime.dwLowDateTime) == 0


def test_a_large_timestamp_splits_across_both_halves_of_the_filetime():
    # 2100-01-01: число интервалов давно не влезает в 32 бита, и обе
    # половины обязаны быть разрядными полями, а не одним сдвинутым числом.
    mtime_ns = 4_102_444_800_000_000_000

    filetime = filetime_from_ns(mtime_ns)

    assert filetime.dwHighDateTime > 0
    assert filetime.dwLowDateTime <= 0xFFFFFFFF
    assert filetime.dwHighDateTime <= 0xFFFFFFFF
    assert (filetime.dwHighDateTime << 32) | filetime.dwLowDateTime == (
        4_102_444_800 + 11_644_473_600
    ) * 10_000_000


def test_an_empty_payload_gives_a_handle_of_length_zero():
    # GlobalLock на блоке нулевой длины возвращает NULL (измерено), поэтому
    # пустую нагрузку нельзя проверять чтением - только размером.
    handle = to_hglobal(b"")

    try:
        assert handle != 0
        assert _GLOBAL_SIZE(handle) == 0
    finally:
        _GLOBAL_FREE(handle)


def test_a_payload_whose_length_is_not_a_multiple_of_the_word_size_round_trips():
    payload = b"\x01\x02\x03\x04\x05\x06\x07"

    handle = to_hglobal(payload)

    try:
        assert _GLOBAL_SIZE(handle) == 7
        address = ctypes.windll.kernel32.GlobalLock(handle)
        try:
            assert ctypes.string_at(address, 7) == payload
        finally:
            ctypes.windll.kernel32.GlobalUnlock(handle)
    finally:
        _GLOBAL_FREE(handle)


def test_a_failed_allocation_is_reported_instead_of_returning_a_null_handle(monkeypatch):
    # Отказ GlobalAlloc нельзя вызвать по-настоящему, поэтому подменяется
    # приватная привязка модуля: без этого условие "if not handle" остаётся
    # единственным в файле, которое проходит мутационную проверку зелёным.
    monkeypatch.setattr(windows_com, "_GLOBAL_ALLOC", lambda flags, size: 0)

    # Сообщение проверяется не для красоты: без него тест проходит и с
    # удалённым условием - NULL-хэндл просто доезжает до следующей проверки,
    # и MemoryError всё равно бросается, только про GlobalLock.
    with pytest.raises(MemoryError, match="GlobalAlloc"):
        to_hglobal(b"payload")


def test_a_failed_lock_frees_the_block_before_raising(monkeypatch):
    freed: list[int] = []
    monkeypatch.setattr(windows_com, "_GLOBAL_LOCK", lambda handle: 0)
    monkeypatch.setattr(
        windows_com, "_GLOBAL_FREE", lambda handle: freed.append(int(handle)) or 0
    )

    with pytest.raises(MemoryError):
        to_hglobal(b"payload")

    # Утёкший GMEM_MOVEABLE живёт до конца процесса; спайк на этом пути
    # хэндл терял.
    assert len(freed) == 1 and freed[0] != 0


def test_a_clipboard_format_windows_refuses_is_not_returned_as_zero():
    # Формат 0 в FORMATETC - это объект, который молча ничего не отдаёт.
    with pytest.raises(OSError):
        register_clipboard_format("")


# --------------------------------------------------------------------------
# Замок на счётчике ссылок (fix round 1).
#
# AddRef и Release приходят из потоков Проводника. Гонку на CPython 3.12
# воспроизвести не удалось (см. task-2.1-report.md: между LOAD_ATTR и
# STORE_ATTR интерпретатор не проверяет eval breaker, то есть сам он поток
# там не переключит), поэтому наличие и МЕСТО замка проверяются детерминированно:
# замок захватывается извне, и вызов обязан его дождаться.
# --------------------------------------------------------------------------

#: Сколько ждать, чтобы считать вызов заблокированным. Не измерение
#: производительности: цена ошибки здесь - ложное "заблокирован".
_BLOCKED_WINDOW_SECONDS = 0.25
#: Потолок ожидания того, что должно произойти. Если не произошло - это
#: взаимоблок, и он обязан стать красным тестом, а не висящим прогоном.
_DEADLINE_SECONDS = 5.0


def _iunknown_calls():
    """Три способа тронуть счётчик - ровно те, что идут через vtable."""
    return {
        "add_ref": lambda obj: call_add_ref(obj.pointer),
        "release": lambda obj: call_release(obj.pointer),
        "query_interface": lambda obj: call_query_interface(
            obj.pointer, guid_from_string(IID_IDATAOBJECT), ctypes.c_void_p()
        ),
    }


@pytest.mark.parametrize("call_name", sorted(_iunknown_calls()))
def test_each_iunknown_body_waits_for_the_lock(call_name):
    call = _iunknown_calls()[call_name]
    obj = ComObject([IID_IUNKNOWN, IID_IDATAOBJECT])
    started = threading.Event()
    finished = threading.Event()

    def worker() -> None:
        started.set()
        call(obj)
        finished.set()

    obj._lock.acquire()
    thread = threading.Thread(target=worker, daemon=True)
    try:
        thread.start()
        assert started.wait(_DEADLINE_SECONDS), "поток не запустился"
        blocked = not finished.wait(_BLOCKED_WINDOW_SECONDS)
    finally:
        obj._lock.release()

    assert blocked, f"{call_name} тронул счётчик, не дожидаясь замка"
    assert finished.wait(_DEADLINE_SECONDS), f"{call_name} не завершился и после освобождения замка"
    thread.join(_DEADLINE_SECONDS)


def test_the_release_hook_runs_with_the_lock_free():
    # Хук - чужой код; в поздних задачах он уходит в приложение. Звать его
    # с захваченным замком - это заготовка взаимоблока.
    obj = ComObject([IID_IUNKNOWN])
    observed: list[bool] = []
    counted = threading.Event()

    def hook() -> None:
        # 1. Замок свободен прямо сейчас.
        acquired = obj._lock.acquire(blocking=False)
        observed.append(acquired)
        if acquired:
            obj._lock.release()
        # 2. И другой поток может пройти через vtable, пока хук ещё работает.
        helper = threading.Thread(target=lambda: (call_add_ref(obj.pointer), counted.set()), daemon=True)
        helper.start()
        counted.wait(_DEADLINE_SECONDS)
        helper.join(_DEADLINE_SECONDS)

    obj.on_last_release = hook
    call_release(obj.pointer)

    assert observed == [True], "хук вызван с захваченным замком"
    assert counted.is_set(), "пока хук работал, другой поток не смог тронуть счётчик"


def test_a_hook_that_calls_back_into_the_object_does_not_deadlock():
    # Реентрантность по-настоящему: замок не рекурсивный, и хук, дёрнувший
    # AddRef на том же объекте из того же потока, повесил бы поток намертво.
    obj = ComObject([IID_IUNKNOWN])
    seen: list[int] = []
    done = threading.Event()

    obj.on_last_release = lambda: seen.append(call_add_ref(obj.pointer))

    def scenario() -> None:
        call_release(obj.pointer)
        done.set()

    # Сценарий живёт в отдельном потоке: регресс здесь - это вечное
    # ожидание, и оно обязано стать красным тестом, а не висящим прогоном.
    thread = threading.Thread(target=scenario, daemon=True)
    thread.start()

    assert done.wait(_DEADLINE_SECONDS), "хук с обратным вызовом заблокировал сам себя"
    assert seen == [1]


def test_the_count_survives_many_threads_racing_add_ref_and_release():
    # Сеть, а не доказательство: на CPython 3.12 этот тест проходит и без
    # замка (измерено). Он поймает free-threading-сборку и любое будущее
    # изменение точек проверки eval breaker.
    threads, iterations = 12, 3000
    obj = ComObject([IID_IUNKNOWN])
    fired: list[int] = []
    obj.on_last_release = lambda: fired.append(1)
    barrier = threading.Barrier(threads)

    def worker() -> None:
        barrier.wait()
        for _ in range(iterations):
            call_add_ref(obj.pointer)
            call_release(obj.pointer)

    workers = [threading.Thread(target=worker, daemon=True) for _ in range(threads)]
    for thread in workers:
        thread.start()
    for thread in workers:
        thread.join(_DEADLINE_SECONDS * 12)

    assert obj.refcount == 1, f"{threads} потоков потеряли обновления счётчика: {obj.refcount}"
    assert fired == [], "хук сработал, пока объект ещё держали"


def test_threads_racing_the_last_release_fire_the_hook_once():
    # Та же сеть для check-then-set: восемь потоков делят последний Release.
    trials, threads = 50, 8
    firings = []
    for _ in range(trials):
        obj = ComObject([IID_IUNKNOWN])
        fired: list[int] = []
        obj.on_last_release = lambda captured=fired: captured.append(1)
        for _ in range(threads - 1):
            call_add_ref(obj.pointer)
        barrier = threading.Barrier(threads)

        def worker(target=obj, gate=barrier) -> None:
            gate.wait()
            call_release(target.pointer)

        workers = [threading.Thread(target=worker, daemon=True) for _ in range(threads)]
        for thread in workers:
            thread.start()
        for thread in workers:
            thread.join(_DEADLINE_SECONDS)
        firings.append(len(fired))

    assert set(firings) == {1}, f"распределение срабатываний хука по {trials} попыткам: {firings}"
