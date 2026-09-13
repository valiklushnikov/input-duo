"""Поток STA и единственная дорога обратно в Qt.

Тесты, которым нужна настоящая сессия Windows, отделены переменной окружения -
тем же приёмом, что DUO_INPUT_HIL_WRITE в tests/integration.

Почти всё здесь проверяется БЕЗ рабочего стола: ``_ole_set_clipboard`` -
именованный шов на модуле, и подмена его в тесте оставляет настоящими и поток
STA, и его насос, и очередь публикаций. Настоящей сессии требует ровно одно
утверждение - что наши форматы действительно видны в системном буфере.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import threading
import time

import pytest
from PySide6.QtCore import QObject, Signal, Slot

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="буфер обмена - это Windows")

NEEDS_SESSION = pytest.mark.skipif(
    os.environ.get("DUO_INPUT_COM_SESSION") != "1",
    reason="set DUO_INPUT_COM_SESSION=1 in a real desktop session",
)

from duo_input.clipboard.wire import (
    CAPABILITY_CLIPBOARD,
    CAPABILITY_FILES,
    Message,
    MessageType,
)
from duo_input.transfer import windows_files
from duo_input.transfer.model import ENTRY_DIRECTORY, ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.pipe import ChunkPipe
from duo_input.transfer.service import FileTransferService
from duo_input.transfer.windows_com import (
    IID_IASYNCCAPABILITY,
    S_OK,
    TYMED_HGLOBAL,
    TYMED_ISTREAM,
    call_get_data,
    call_get_data_medium,
    call_query_interface,
    call_release,
    call_stream_read,
    guid_from_string,
    register_clipboard_format,
)
from duo_input.transfer.windows_files import (
    FORMAT_CONTENTS_NAME,
    FORMAT_DESCRIPTOR_NAME,
    WindowsFileClipboardBackend,
    post_to_service,
)

#: Больше 2^31: смещение из прогона A фазы 0 (файл на 4 ГиБ). В 32-битный
#: C++ int оно не помещается вовсе, поэтому Q_ARG(int, ...) на нём не просто
#: не доставит вызов, а переполнится.
HUGE_OFFSET = 4294705152

#: CLIPBRD_E_CANT_OPEN как знаковое число - именно так его отдаёт ctypes.
CLIPBRD_E_CANT_OPEN = 0x800401D0 - (1 << 32)

#: Потолок для каждого ожидания в этом файле. Регрессия обязана краснеть, а
#: не висеть.
DEADLINE_S = 5.0


class _Recorder(QObject):
    got = Signal(str, int, int, int)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []
        self.thread_ids: list[int] = []

    @Slot(str, int, int, int)
    def request_read(self, transfer_id, entry_index, offset, length) -> None:
        self.calls.append((transfer_id, entry_index, offset, length))
        self.thread_ids.append(threading.get_ident())
        self.got.emit(transfer_id, entry_index, offset, length)


class _OverloadRecorder(QObject):
    """Один слот в двух подписях: ариность выбирает нужную."""

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []

    @Slot(str)
    @Slot(str, "qlonglong")
    def note(self, text, offset=None) -> None:
        self.calls.append((text, offset))


class _ObjectRecorder(QObject):
    """Слот с PyObject: в него можно передать то, что Qt не свернёт в C++."""

    @Slot(object)
    def take(self, value) -> None:  # pragma: no cover - вызов не должен дойти
        raise AssertionError("этот вызов не должен был доставиться")


class _FakeLink(QObject):
    message_received = Signal(object)
    disconnected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list = []

    def send(self, message) -> None:
        self.sent.append(message)

    def close(self) -> None: ...


def _manifest(transfer_id: str = "t-1", size: int = 4) -> TransferManifest:
    return TransferManifest(
        transfer_id=transfer_id,
        entries=(
            TransferEntry(path="a.bin", kind=ENTRY_FILE, size=size, mtime_ns=1),
            TransferEntry(path="dir", kind=ENTRY_DIRECTORY, size=0, mtime_ns=2),
        ),
    )


class _Callbacks:
    """Четыре колбэка бэкенда и запись о том, что через них прошло."""

    def __init__(self) -> None:
        self.opened: list[tuple[str, int]] = []
        self.closed: list[tuple[str, int, object]] = []
        self.reads: list[tuple] = []
        self.finished: list[int] = []
        self.pipes: list[ChunkPipe] = []

    def install(self, backend) -> "_Callbacks":
        backend.set_callbacks(
            self.open_pipe, self.request_read, self.close_pipe, self.on_operation_finished
        )
        return self

    def open_pipe(self, transfer_id: str, entry_index: int) -> ChunkPipe:
        self.opened.append((transfer_id, entry_index))
        pipe = ChunkPipe(capacity_chunks=1)
        self.pipes.append(pipe)
        return pipe

    def request_read(self, *args) -> None:
        self.reads.append(args)

    def close_pipe(self, transfer_id: str, entry_index: int, reason=None) -> None:
        self.closed.append((transfer_id, entry_index, reason))

    def on_operation_finished(self, result: int) -> None:
        self.finished.append(result)


@pytest.fixture
def backend(qapp):
    """Бэкенд, который остановят даже если тест упал на середине."""
    made = WindowsFileClipboardBackend()
    yield made
    made.stop()


@pytest.fixture
def started(backend, qtbot):
    backend.start()
    qtbot.waitUntil(lambda: backend.thread_id is not None, timeout=5000)
    return backend


def _origin_of(data_object) -> bytes:
    _result, payload = call_get_data(
        data_object.pointer, data_object.cf_origin, -1, TYMED_HGLOBAL
    )
    return payload


# --------------------------------------------------------------- post_to_service


def test_a_post_from_another_thread_arrives_on_the_qt_thread(qtbot):
    # Это и есть инвариант "COM-поток не трогает Qt напрямую": вызов
    # пересекает границу через очередь Qt, а не прямым обращением.
    recorder = _Recorder()
    qt_thread = threading.get_ident()

    def worker() -> None:
        post_to_service(recorder, "request_read", "t-1", 0, 4096, 65536)

    with qtbot.waitSignal(recorder.got, timeout=5000):
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=2.0)

    assert recorder.calls == [("t-1", 0, 4096, 65536)]
    assert recorder.thread_ids == [qt_thread], (
        "слот исполнился НЕ на Qt-потоке - queued connection не сработал, "
        "и следующий шаг тронул бы QSslSocket из чужого потока"
    )


def test_a_post_to_a_missing_slot_is_reported_rather_than_swallowed(qtbot, caplog):
    recorder = _Recorder()

    with caplog.at_level(logging.ERROR):
        post_to_service(recorder, "no_such_slot", "t", 0, 0, 0)

    assert any("no_such_slot" in record.message for record in caplog.records)


def test_a_post_with_the_wrong_number_of_arguments_is_reported(qtbot, caplog):
    # Ариность - часть подписи слота. Молча отброшенный вызов означал бы
    # Read, на который никто никогда не ответит.
    recorder = _Recorder()

    with caplog.at_level(logging.ERROR):
        post_to_service(recorder, "request_read", "t", 0)

    assert recorder.calls == []
    assert any("request_read" in record.message for record in caplog.records)


def test_an_undeliverable_value_is_reported_rather_than_swallowed(qtbot, caplog):
    # invokeMethod возвращает False, не поднимая исключения: Qt не умеет
    # скопировать set в C++. Без этой ветви потеря была бы беззвучной.
    recorder = _ObjectRecorder()

    with caplog.at_level(logging.ERROR):
        post_to_service(recorder, "take", {1, 2})

    assert any("take" in record.message for record in caplog.records)


def test_a_raising_conversion_is_reported_rather_than_escaping(qtbot, caplog):
    # post_to_service зовут ИЗ обратного вызова ctypes: исключение отсюда
    # вернуло бы COM неопределённое значение, измеренно положительное - то
    # есть успех по правилу SUCCEEDED.
    recorder = _Recorder()

    with caplog.at_level(logging.ERROR):
        post_to_service(recorder, "request_read", None, 0, 0, 0)

    assert recorder.calls == []
    assert any("request_read" in record.message for record in caplog.records)


def test_an_overloaded_slot_is_chosen_by_how_many_arguments_were_posted(qtbot):
    # Перегрузки различаются только ариностью. Взять первую попавшуюся
    # значило бы отдать qlonglong-подписи один аргумент вместо двух.
    recorder = _OverloadRecorder()

    assert windows_files._declared_parameter_types(recorder, "note", 1) == ("QString",)
    assert windows_files._declared_parameter_types(recorder, "note", 2) == (
        "QString",
        "qlonglong",
    )

    post_to_service(recorder, "note", "short")
    post_to_service(recorder, "note", "long", HUGE_OFFSET)
    qtbot.waitUntil(lambda: len(recorder.calls) == 2, timeout=5000)

    assert recorder.calls == [("short", None), ("long", HUGE_OFFSET)]


def test_the_argument_types_are_read_from_the_receiving_object(qapp):
    """Типы берутся из метаобъекта получателя, а не угадываются по значению.

    Разбор по isinstance не может знать объявленных типов слота и ошибается
    на qlonglong по построению - ровно этот дефект плана всплывал трижды.
    """
    service = FileTransferService()

    assert windows_files._declared_parameter_types(service, "request_read", 4) == (
        "QString",
        "int",
        "qlonglong",
        "int",
    )


def test_an_offset_above_two_to_the_thirtyone_reaches_the_real_service_intact(qtbot):
    """Смещение в 4 ГиБ доезжает до настоящего слота настоящего сервиса.

    Q_ARG(int, ...) - это 32-битный C++ int: на таком смещении он
    переполняется, а на маленьком просто не совпадает с объявленным
    qlonglong, и invokeMethod возвращает False. В обоих случаях чтение не
    выходит на провод, и единственный симптом - таймаут IStream::Read через
    тридцать секунд.
    """
    service = FileTransferService()
    link = _FakeLink()
    service.attach_link(link)
    service.set_peer_capabilities(frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES}))
    manifest = _manifest(size=8 * 1024 * 1024 * 1024)
    service.handle_message(Message(MessageType.FILE_OFFER, manifest.to_dict(), b""))
    service.open_pipe("t-1", 0)

    post_to_service(service, "request_read", "t-1", 0, HUGE_OFFSET, 65536)
    qtbot.waitUntil(
        lambda: bool([m for m in link.sent if m.type is MessageType.FILE_READ]),
        timeout=5000,
    )

    reads = [m for m in link.sent if m.type is MessageType.FILE_READ]
    assert reads[0].header["offset"] == HUGE_OFFSET, (
        "смещение приехало искажённым - значит его тип не qlonglong"
    )


# ------------------------------------------------------------ жизненный цикл потока


def test_the_backend_starts_a_thread_of_its_own_and_reports_its_id(backend, qtbot):
    backend.start()
    qtbot.waitUntil(lambda: backend.thread_id is not None, timeout=5000)

    assert backend.is_running
    assert backend.thread_id != threading.get_ident(), (
        "COM живёт на GUI-потоке - тогда 20 ГБ чтения заморозили бы интерфейс"
    )
    backend.stop()
    assert not backend.is_running


def test_stopping_a_backend_that_never_started_is_harmless(backend):
    backend.stop()

    assert not backend.is_running


def test_stopping_twice_is_harmless(started):
    started.stop()
    started.stop()

    assert not started.is_running
    assert started.thread_id is None


def test_starting_twice_does_not_create_a_second_thread(backend, qtbot):
    backend.start()
    qtbot.waitUntil(lambda: backend.thread_id is not None, timeout=5000)
    first = backend.thread_id

    backend.start()

    assert backend.thread_id == first
    backend.stop()


def test_a_stopped_backend_can_be_started_again_on_a_fresh_thread(started, qtbot):
    first = started.thread_id
    started.stop()

    started.start()
    qtbot.waitUntil(lambda: started.thread_id is not None, timeout=5000)

    assert started.thread_id != first
    assert started.is_running


def test_a_thread_that_will_not_die_is_not_forgotten(backend, qtbot, monkeypatch):
    """Незавершившийся поток остаётся учтённым - иначе будет второй апартамент.

    Забыть про живой поток значит, что следующий start() поднимет второй
    OLE-апартамент и второго владельца буфера обмена в одном процессе.
    """
    monkeypatch.setattr(windows_files, "_TEARDOWN_PUMP_S", 1.0)
    monkeypatch.setattr(windows_files, "_STOP_TIMEOUT_S", 0.05)
    backend.start()
    qtbot.waitUntil(lambda: backend.thread_id is not None, timeout=5000)
    alive = backend.thread_id

    backend.stop()

    assert backend.is_running, "поток ещё жив, а бэкенд уже считает себя свободным"
    assert backend.thread_id == alive
    qtbot.waitUntil(lambda: not backend.is_running, timeout=5000)


def test_a_failing_apartment_leaves_the_backend_visibly_unstarted(
    backend, qtbot, caplog, monkeypatch
):
    # OleInitialize отказывает, например, на потоке без оконной станции.
    # Тихий отказ оставил бы start() вернувшимся как ни в чём не бывало, а
    # первую публикацию - висящей в очереди навсегда.
    def refuse() -> None:
        raise OSError("apartment refused")

    monkeypatch.setattr(windows_files, "_ole_initialize", refuse)

    with caplog.at_level(logging.ERROR):
        backend.start()
        qtbot.waitUntil(lambda: not backend.is_running, timeout=5000)

    assert backend.thread_id is None
    assert any("apartment refused" in record.getMessage() for record in caplog.records)


# ----------------------------------------------------------------- публикация


def test_publishing_before_start_is_refused_out_loud(backend, qtbot, caplog):
    """Публикация без потока STA - это ошибка порядка, а не отложенная работа.

    Поставить её в очередь значило бы либо потерять совсем, либо выложить
    устаревшее дерево при следующем start() - и то и другое молча.
    """
    refusals: list[str] = []
    backend.publish_failed.connect(refusals.append)

    with caplog.at_level(logging.ERROR):
        backend.publish(_manifest(), origin_marker=b"origin:1")

    # Синхронно, ещё до возврата из publish(): отказ, доставленный очередью
    # Qt, требовал бы живого потока STA - то есть ровно того, чего нет.
    assert len(refusals) == 1 and "не запущен" in refusals[0]
    assert backend.pending_publications == 0
    assert caplog.records


def test_publishing_without_callbacks_is_refused_out_loud(started, qtbot):
    # Объект без колбэков отдал бы Проводнику интерфейс, который падает на
    # первом же GetData - уже после того, как вставка началась.
    refusals: list[str] = []
    started.publish_failed.connect(refusals.append)

    started.publish(_manifest(), origin_marker=b"origin:1")

    # Тоже синхронно. Поставить объект без колбэков в очередь значило бы
    # узнать об ошибке из трассировки в потоке STA, через два такта насоса.
    assert len(refusals) == 1 and "колбэк" in refusals[0]
    assert started.pending_publications == 0


def test_the_clipboard_is_taken_on_the_sta_thread_and_not_the_caller(
    started, qtbot, monkeypatch
):
    """OleSetClipboard обязан идти с того потока, который отвечает на GetData.

    Вызов с GUI-потока сделал бы владельцем буфера его, и все чтения
    Проводника пришли бы туда - то есть 20 ГБ передачи заморозили бы
    интерфейс.
    """
    seen: list[int] = []

    def fake_set_clipboard(pointer) -> int:
        seen.append(threading.get_ident())
        return S_OK

    monkeypatch.setattr(windows_files, "_ole_set_clipboard", fake_set_clipboard)
    _Callbacks().install(started)
    sta_thread = started.thread_id

    started.publish(_manifest(), origin_marker=b"origin:1")
    qtbot.waitUntil(lambda: bool(seen), timeout=5000)

    assert seen == [sta_thread]
    assert sta_thread != threading.get_ident()


def test_the_published_object_is_kept_alive_after_the_clipboard_takes_it(
    started, qtbot, monkeypatch
):
    # Буфер обмена держит указатель, а не ссылку Python: отпустить объект
    # значило бы отдать Проводнику освобождённую vtable.
    monkeypatch.setattr(windows_files, "_ole_set_clipboard", lambda pointer: S_OK)
    _Callbacks().install(started)

    started.publish(_manifest(), origin_marker=b"origin:1")
    qtbot.waitUntil(lambda: started.published_object is not None, timeout=5000)

    published = started.published_object
    assert _origin_of(published) == b"origin:1"


def test_a_refusing_olesetclipboard_is_reported_and_the_thread_survives(
    started, qtbot, monkeypatch
):
    # CLIPBRD_E_CANT_OPEN: чужое окно держит буфер. Упасть здесь значило бы
    # унести поток STA, и следующая публикация не состоялась бы никогда.
    monkeypatch.setattr(
        windows_files, "_ole_set_clipboard", lambda pointer: CLIPBRD_E_CANT_OPEN
    )
    _Callbacks().install(started)

    with qtbot.waitSignal(started.publish_failed, timeout=5000) as blocker:
        started.publish(_manifest(), origin_marker=b"origin:1")

    assert "800401D0" in blocker.args[0].upper()
    assert started.is_running, "поток STA умер вместе с неудачной публикацией"
    assert started.published_object is None


def test_only_the_newest_publication_reaches_the_clipboard(backend, qtbot, monkeypatch):
    """В буфере лежит одно. Опубликовать обе - значит отдать устаревшую.

    Первая публикация держится внутри OleSetClipboard, пока тест ставит в
    очередь ещё две: без этого шлюза порядок решал бы планировщик, и тест
    проходил бы по удаче.
    """
    gate = threading.Event()
    origins: list[bytes] = []

    def fake_set_clipboard(pointer) -> int:
        origins.append(b"seen")
        gate.wait(timeout=DEADLINE_S)
        return S_OK

    monkeypatch.setattr(windows_files, "_ole_set_clipboard", fake_set_clipboard)
    _Callbacks().install(backend)
    backend.start()
    qtbot.waitUntil(lambda: backend.thread_id is not None, timeout=5000)

    backend.publish(_manifest("t-first"), origin_marker=b"origin:first")
    qtbot.waitUntil(lambda: bool(origins), timeout=5000)
    backend.publish(_manifest("t-second"), origin_marker=b"origin:second")
    backend.publish(_manifest("t-third"), origin_marker=b"origin:third")

    assert backend.pending_publications == 1, (
        "устаревшая публикация всё ещё в очереди - буфер получит её после свежей"
    )
    gate.set()
    qtbot.waitUntil(lambda: len(origins) == 2, timeout=5000)
    qtbot.waitUntil(
        lambda: backend.published_object is not None
        and _origin_of(backend.published_object) == b"origin:third",
        timeout=5000,
    )


def test_the_apartment_keeps_pumping_after_the_clipboard_is_handed_back(
    backend, qtbot, monkeypatch
):
    """Между OleFlushClipboard и OleUninitialize насос ещё крутится.

    STA обязан обслуживать входящие вызовы, пока они есть: чужие апартаменты
    ЭТОГО же процесса - буфер обмена Qt в их числе - держат ссылку на наш
    объект и отпускают её своим Release. Погасить апартамент сразу значит
    оборвать этот вызов на полпути. Измерено: без паузы 5 падений процесса
    из 36 (RPC_E_DISCONNECTED и следом access violation), с паузой 0 из 36.
    """
    marks: list[tuple[str, float]] = []
    real_flush = windows_files._ole_flush_clipboard
    real_uninitialize = windows_files._ole_uninitialize

    def flush() -> int:
        marks.append(("flush", time.monotonic()))
        return real_flush()

    def uninitialize() -> None:
        marks.append(("uninitialize", time.monotonic()))
        real_uninitialize()

    monkeypatch.setattr(windows_files, "_ole_set_clipboard", lambda pointer: S_OK)
    monkeypatch.setattr(windows_files, "_ole_flush_clipboard", flush)
    monkeypatch.setattr(windows_files, "_ole_uninitialize", uninitialize)
    _Callbacks().install(backend)
    backend.start()
    qtbot.waitUntil(lambda: backend.thread_id is not None, timeout=5000)
    backend.publish(_manifest(), origin_marker=b"origin:1")
    qtbot.waitUntil(lambda: backend.published_object is not None, timeout=5000)

    backend.stop()

    assert [name for name, _ in marks] == ["flush", "uninitialize"]
    waited = marks[1][1] - marks[0][1]
    # Порог - литерал, а не _TEARDOWN_PUMP_S * 0.8: сравнение с самой
    # константой проходит и тогда, когда её обнулили.
    assert waited >= 0.2, (
        f"апартамент погашен через {waited:.3f} с после отдачи буфера - "
        "входящий Release из чужого апартамента оборвётся на полпути"
    )


def test_nothing_is_handed_back_when_nothing_was_published(backend, qtbot, monkeypatch):
    # OleFlushClipboard с апартамента, который буфер не брал, трогает чужое
    # владение. Звать его "на всякий случай" - это не осторожность.
    calls: list[int] = []
    real_flush = windows_files._ole_flush_clipboard
    monkeypatch.setattr(
        windows_files, "_ole_flush_clipboard", lambda: (calls.append(1), real_flush())[1]
    )
    backend.start()
    qtbot.waitUntil(lambda: backend.thread_id is not None, timeout=5000)

    backend.stop()

    assert calls == []


# ------------------------------------------------------- проводка колбэков


def test_every_callback_reaches_the_data_object_the_backend_builds(backend):
    callbacks = _Callbacks().install(backend)
    data_object = backend.build_data_object(_manifest(), b"origin:1")

    result, medium = call_get_data_medium(
        data_object.pointer, data_object.cf_contents, 0, TYMED_ISTREAM
    )
    assert result == S_OK

    def answer() -> None:
        deadline = time.monotonic() + DEADLINE_S
        while time.monotonic() < deadline:
            if callbacks.reads:
                callbacks.pipes[0].push(b"abcd")
                return
            time.sleep(0.005)

    responder = threading.Thread(target=answer, daemon=True)
    responder.start()
    payload, read_result = call_stream_read(ctypes.c_void_p(medium.data), 4)
    responder.join(timeout=DEADLINE_S)
    call_release(medium.data)
    data_object.on_operation_finished(0)

    assert (payload, read_result) == (b"abcd", S_OK)
    assert callbacks.opened == [("t-1", 0)]
    assert callbacks.reads == [("t-1", 0, 0, 4)]
    assert callbacks.closed == [("t-1", 0, None)]
    assert callbacks.finished == [0]


def test_a_second_read_of_one_entry_opens_and_closes_a_second_pipe(backend):
    """Два GetData по одной записи - это две трубы, и бэкенд их не склеивает.

    ChunkPipe не знает смещений, поэтому второй GetData по исчерпанной
    записи законно открывает ВТОРУЮ трубу, а подписи колбэков их не
    различают. Бэкенд намеренно не дедуплицирует: пара
    (transfer_id, entry_index) именует ЗАПИСЬ, а не одно открытие, и
    проглоченное второе открытие оставило бы Проводника с трубой, в которую
    никто не пишет.
    """
    callbacks = _Callbacks().install(backend)
    data_object = backend.build_data_object(_manifest(), b"origin:1")

    for _ in range(2):
        _result, medium = call_get_data_medium(
            data_object.pointer, data_object.cf_contents, 0, TYMED_ISTREAM
        )
        call_release(medium.data)

    assert callbacks.opened == [("t-1", 0), ("t-1", 0)]
    assert callbacks.closed == [("t-1", 0, None), ("t-1", 0, None)]
    assert len(callbacks.pipes) == 2
    assert callbacks.pipes[0] is not callbacks.pipes[1]


def test_the_published_object_advertises_the_async_capability(backend):
    """ASYNC_CAPABILITY_REQUIRED доезжает до объекта, а не остаётся константой.

    EndOperation - единственный явный сигнал о завершении И об отмене
    (запись спайка 1), и без объявленной способности он не приходит вовсе.
    """
    _Callbacks().install(backend)
    data_object = backend.build_data_object(_manifest(), b"origin:1")

    out = ctypes.c_void_p()
    result = call_query_interface(
        data_object.pointer, guid_from_string(IID_IASYNCCAPABILITY), out
    )

    assert (result, bool(out.value)) == (S_OK, True)
    call_release(data_object.pointer)


def test_building_a_data_object_without_callbacks_is_refused(backend):
    with pytest.raises(RuntimeError):
        backend.build_data_object(_manifest(), b"origin:1")


# ------------------------------------------------------------- настоящая сессия


@NEEDS_SESSION
def test_publishing_puts_our_data_object_on_the_real_clipboard(started, qtbot):
    _Callbacks().install(started)
    manifest = TransferManifest(
        transfer_id="t-1",
        entries=(TransferEntry(path="a.bin", kind=ENTRY_FILE, size=4, mtime_ns=1),),
    )

    started.publish(manifest, origin_marker=b"origin:1")
    qtbot.waitUntil(lambda: started.published_object is not None, timeout=5000)
    qtbot.wait(500)

    cf = register_clipboard_format(FORMAT_DESCRIPTOR_NAME)
    assert ctypes.windll.user32.IsClipboardFormatAvailable(cf), (
        "формат дескриптора отсутствует в буфере - Проводник не предложит вставку"
    )


@NEEDS_SESSION
def test_the_contents_format_is_available_on_the_real_clipboard(started, qtbot):
    _Callbacks().install(started)

    started.publish(_manifest(), origin_marker=b"origin:1")
    qtbot.waitUntil(lambda: started.published_object is not None, timeout=5000)
    qtbot.wait(500)

    cf = register_clipboard_format(FORMAT_CONTENTS_NAME)
    assert ctypes.windll.user32.IsClipboardFormatAvailable(cf), (
        "формат содержимого отсутствует - вставка отдала бы пустые файлы"
    )
