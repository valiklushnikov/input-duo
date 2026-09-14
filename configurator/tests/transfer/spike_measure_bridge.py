# configurator/tests/transfer/spike_measure_bridge.py
"""Throwaway: замер настоящей цепочки, от Проводника до файла-источника.

Запуск в НАСТОЯЩЕЙ сессии Windows:
    .venv\\Scripts\\python.exe configurator/tests/transfer/spike_measure_bridge.py --size-mib 2048

Отдельный от спайка 1 файл намеренно: там неизвестной была семантика
Проводника, здесь - поведение моста под нагрузкой. Смешивать две неизвестности
в одном эксперименте означает не узнать ни одну. Поэтому здесь нет реального
Проводника: за него читает выделенный поток, вызывающий настоящий COM
IStream (``duo_input.transfer.windows_com.call_stream_read``) напрямую через
vtable - той же функцией, которой пользуется ``test_windows_publisher.py``.
IStream, ChunkPipe, FileTransferService и PeerLink здесь настоящие, боевые;
не настоящий только оператор с Ctrl+V.

Мост публикуется на настоящий буфер обмена (``OleSetClipboard`` через
настоящий ``WindowsFileClipboardBackend`` на настоящем потоке STA) - иначе
измерение обошло бы стороной ровно тот код, который держит на себе вся
архитектура. Но объект, с которым говорит поток-имитатор Проводника, взят не
через ``OleGetClipboard``, а напрямую (``backend.published_object``): цель
этого спайка - не заново измерить, согласится ли Проводник забрать наш
IDataObject (это спайк 1), а увидеть, что происходит ПОСЛЕ того, как он уже
согласился.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import statistics
import tempfile
import threading
import time
import tracemalloc
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

from PySide6.QtCore import QEventLoop, QTimer
from PySide6.QtWidgets import QApplication

from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.listener import PeerListener
from duo_input.clipboard.peer import PeerLink
from duo_input.clipboard.service import HEARTBEAT_MS
from duo_input.clipboard.wire import CAPABILITY_CLIPBOARD, CAPABILITY_FILES, Message, MessageType
from duo_input.transfer.service import FileTransferService
from duo_input.transfer.windows_com import (
    S_OK,
    TYMED_ISTREAM,
    call_get_data_medium,
    call_release,
    call_stream_read,
)
from duo_input.transfer.windows_files import WindowsFileClipboardBackend, post_to_service

from spike_qt_responsiveness import Instrument

MIB = 1024 * 1024

#: Размер одного запроса, которым поток-имитатор Проводника читает IStream.
#: То же число, которым тест 100-мегабайтного файла режет провод
#: (test_end_to_end.WIRE_CHUNK_BYTES) - не протокольная константа (потолок
#: кадра - MAX_FILE_CHUNK_BYTES, 1 МиБ), а размер запроса читателя.
READ_CHUNK_BYTES = 65536

#: Как часто сэмплируется bytesToWrite сокета.
SAMPLE_INTERVAL_MS = 50

#: Сроки ожидания для того, что обязано случиться быстро на loopback.
CONNECT_TIMEOUT_S = 10.0
OFFER_TIMEOUT_S = 10.0
PUBLISH_TIMEOUT_S = 10.0

#: Потолок на саму передачу - щедрый, чтобы не путать "мост медленный" с
#: "мост завис". На 2048 МиБ и разумной сети это на два порядка больше нужного.
TRANSFER_CEILING_S = 1800.0

#: Сколько ждём приёма кадров пропуска-моста, прежде чем признать этот проход
#: зависшим, а не медленным.
BYPASS_CEILING_S = 60.0

#: Пауза после публикации, прежде чем поток-имитатор возьмёт поток. Спайк 1
#: измерил: в первые ~100 мс после OleSetClipboard систему трогает не
#: Проводник, а сторонний наблюдатель буфера обмена (история буфера обмена
#: Windows и т. п.), зовущий GetData на других форматах. Наш собственный
#: FileContents он в измерениях спайка 1 не трогал ни разу, но пауза здесь
#: дешева, а её отсутствие когда-нибудь объяснится часом отладки чужого кода.
SETTLE_SECONDS = 0.5

CAPS = frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES})


@dataclass(frozen=True)
class Measurement:
    bytes_transferred: int
    elapsed_seconds: float
    peak_rss_bytes: int
    peak_python_bytes: int
    pipe_high_water: int
    cancel_latency_seconds: float | None
    gui_tick_p99_ms: float
    gui_tick_max_ms: float
    socket_bytes_to_write_max: int
    #: Локализация узкого места. Без этих величин гейт задачи 3.2 не может
    #: отличить медленный круг от медленного транспорта, а он стоит ровно на
    #: этом различии.
    rtt_median_ms: float
    rtt_p99_ms: float
    disk_read_median_ms: float
    disk_read_p99_ms: float
    #: Пропускная способность ТОГО ЖЕ соединения с обойдённым мостом: кадры
    #: FILE_CHUNK подряд, без IStream и без очереди.
    bypassed_transport_mib_s: float
    heartbeat_gaps_seconds: list[float] = field(default_factory=list)

    @property
    def throughput_mib_s(self) -> float:
        if self.elapsed_seconds <= 0:
            return 0.0
        return self.bytes_transferred / MIB / self.elapsed_seconds

    def rtt_bound_mib_s(self, chunk_bytes: int) -> float:
        """Сколько дал бы один последовательный круг и больше ничего.

        Если это число близко к throughput_mib_s, а bypassed_transport_mib_s
        заметно выше - узкое место действительно в круге, и только тогда окно
        разрешено (задача 3.2, шаг 3).
        """
        if self.rtt_median_ms <= 0:
            return 0.0
        return chunk_bytes / MIB / (self.rtt_median_ms / 1000)

    def as_table(self) -> str:
        cancel = (
            "не измерялась"
            if self.cancel_latency_seconds is None
            else f"{self.cancel_latency_seconds * 1000:.0f} мс"
        )
        gaps = (
            f"{max(self.heartbeat_gaps_seconds):.1f} с"
            if self.heartbeat_gaps_seconds
            else "не измерялись"
        )
        return "\n".join(
            [
                "| величина | значение |",
                "|---|---|",
                f"| передано | {self.bytes_transferred / MIB:.0f} МиБ |",
                f"| время | {self.elapsed_seconds:.1f} с |",
                f"| пропускная способность | {self.throughput_mib_s:.1f} МиБ/с |",
                f"| пик RSS | {self.peak_rss_bytes / MIB:.0f} МиБ |",
                f"| пик Python (tracemalloc) | {self.peak_python_bytes / MIB:.1f} МиБ |",
                f"| pipe high_water | {self.pipe_high_water} чанков |",
                f"| задержка отмены | {cancel} |",
                f"| GUI tick p99 | {self.gui_tick_p99_ms:.1f} мс |",
                f"| GUI tick максимум | {self.gui_tick_max_ms:.1f} мс |",
                f"| socket bytesToWrite максимум | {self.socket_bytes_to_write_max} Б |",
                f"| RTT медиана / p99 | {self.rtt_median_ms:.2f} / {self.rtt_p99_ms:.2f} мс |",
                f"| чтение с диска медиана / p99 | {self.disk_read_median_ms:.2f} / {self.disk_read_p99_ms:.2f} мс |",
                f"| транспорт без моста | {self.bypassed_transport_mib_s:.1f} МиБ/с |",
                f"| наибольший промежуток heartbeat | {gaps} |",
            ]
        )


# ============================================================== пик RSS


class _ProcessMemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
    ]


#: Без явных argtypes/restype ctypes передаёт HANDLE (псевдо-хэндл текущего
#: процесса, -1 - все биты единицы) через свёртку по умолчанию для c_int, и
#: на 64-разрядной сборке GetProcessMemoryInfo получает битую ширину и молча
#: отказывает. Измерено здесь же: без этих трёх строк вызов возвращает 0
#: (FALSE) без исключения - то есть именно тот тихий сбой, от которого
#: предостерегает докстрока _peak_rss_bytes ниже.
_GET_CURRENT_PROCESS = ctypes.windll.kernel32.GetCurrentProcess
_GET_CURRENT_PROCESS.restype = wintypes.HANDLE
_GET_PROCESS_MEMORY_INFO = ctypes.windll.psapi.GetProcessMemoryInfo
_GET_PROCESS_MEMORY_INFO.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(_ProcessMemoryCounters),
    wintypes.DWORD,
]
_GET_PROCESS_MEMORY_INFO.restype = wintypes.BOOL


def _peak_rss_bytes() -> int:
    """Пик RSS процесса за всё его время жизни - от самой ОС, не сэмплом.

    PeakWorkingSetSize копится ядром непрерывно с момента старта процесса,
    поэтому, в отличие от bytesToWrite выше, промах между двумя чтениями
    здесь структурно невозможен: либо ОС отдаёт число, либо вызов
    проваливается и мы об этом узнаём - молчаливого нуля нет.
    """
    counters = _ProcessMemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    handle = _GET_CURRENT_PROCESS()
    ok = _GET_PROCESS_MEMORY_INFO(handle, ctypes.byref(counters), counters.cb)
    if not ok:
        raise OSError("GetProcessMemoryInfo отказал - пик RSS недоступен")
    return int(counters.PeakWorkingSetSize)


# ============================================================== вспомогательное


def _pump(app: QApplication, condition, timeout_s: float, interval_ms: int = 20) -> bool:
    """Прокачать насос Qt, пока не сбудется ``condition()`` или не выйдет срок.

    Замена qtbot.waitUntil: measure() - не тест, у него нет фикстуры qtbot,
    а насос всё равно обязан крутиться, пока идёт передача - иначе ни
    QSslSocket, ни QueuedConnection-вызовы из потока-имитатора не продвинутся.
    """
    deadline = time.perf_counter() + timeout_s
    while time.perf_counter() < deadline:
        app.processEvents(QEventLoop.ProcessEventsFlag.AllEvents, interval_ms)
        if condition():
            return True
    return bool(condition())


def _write_generated_file(path: Path, size: int, seed: int = 0) -> str:
    """Записать ``size`` детерминированных байт на диск и вернуть их sha256.

    Блоки строятся тем же приёмом, что ``unique_wire_chunk`` в
    test_end_to_end.py (sha256(seed:индекс), растянутый на длину блока) - не
    криптостойкость, а дешёвая гарантия, что переставленные, задвоенные или
    укороченные чанки на проводе дали бы другой дайджест. Собственная копия,
    а не импорт оттуда: тот файл - про семантику Проводника, этот - про
    поведение моста, и общий генератор незаметно связал бы два эксперимента.
    """
    hasher = hashlib.sha256()
    remaining = size
    index = 0
    with open(path, "wb") as handle:
        while remaining > 0:
            take = min(READ_CHUNK_BYTES, remaining)
            digest = hashlib.sha256(f"{seed}:{index}".encode("ascii")).digest()
            repeats = -(-take // len(digest))  # ceil без импорта math
            block = (digest * repeats)[:take]
            handle.write(block)
            hasher.update(block)
            remaining -= take
            index += 1
    return hasher.hexdigest()


def _percentile(samples_ms: list[float], fraction: float) -> float:
    if not samples_ms:
        return 0.0
    ordered = sorted(samples_ms)
    index = min(len(ordered) - 1, int(len(ordered) * fraction))
    return ordered[index]


# ============================================================== измерение


def measure(size: int, cancel_after_bytes: int | None = None) -> Measurement:
    """Провести один прогон моста и вернуть его показания.

    ``size`` - размер единственного файла в передаче, в байтах.
    ``cancel_after_bytes`` - если задан, после того как имитатор Проводника
    прочтёт столько байт, вызывается настоящая отмена сессии, и измеряется,
    сколько блокированный IStream::Read после этого ждал. Значение обязано
    быть строго меньше ``size`` - иначе передача успела бы закончиться раньше,
    чем сработает отмена, и cancel_latency_seconds осталась бы неопределённой
    не потому, что путь отмены быстр, а потому, что его никто не прошёл.
    """
    if size <= 0:
        raise ValueError(
            "size обязан быть положительным - нулевая передача не касается ни "
            "IStream::Read, ни FILE_READ, ни SnapshotRegistry.read, и это "
            "измерение не сообщило бы ни о чём из того, что оно должно"
        )
    if cancel_after_bytes is not None:
        if cancel_after_bytes < 0:
            raise ValueError("cancel_after_bytes не может быть отрицательным")
        if cancel_after_bytes >= size:
            raise ValueError(
                "cancel_after_bytes обязан быть меньше size - иначе передача "
                "закончилась бы раньше, чем сработает отмена"
            )

    app = QApplication.instance()
    if app is None:
        app = QApplication([])

    tracemalloc.start()
    work_dir = Path(tempfile.mkdtemp(prefix="duo-input-bridge-measure-"))
    try:
        return _measure(app, work_dir, size, cancel_after_bytes)
    finally:
        # rmtree, а не TemporaryDirectory: на Windows каталог с открытыми
        # сертификатами identity иногда отпускается на тик позже, чем нужно
        # __exit__, и ignore_errors здесь дешевле, чем гонка с ним.
        import shutil

        shutil.rmtree(work_dir, ignore_errors=True)
        if not tracemalloc.is_tracing():
            # Инструмент нем ровно тогда, когда его молчание неотличимо от
            # честного нуля: обе строки ниже никогда не должны стоять рядом
            # с "не измерялось", поэтому останов проверяется здесь же.
            raise AssertionError(
                "tracemalloc перестал трассировать раньше срока - "
                "peak_python_bytes измерил бы не то"
            )
        tracemalloc.stop()


def _measure(
    app: QApplication, work_dir: Path, size: int, cancel_after_bytes: int | None
) -> Measurement:
    sender_identity = load_or_create(work_dir / "sender")
    receiver_identity = load_or_create(work_dir / "receiver")

    listener = PeerListener(receiver_identity)
    listener.expect(sender_identity.fingerprint)
    if not listener.listen(0):
        raise RuntimeError("не удалось занять порт на loopback")

    incoming: list[PeerLink] = []
    listener.link_ready.connect(incoming.append)

    outgoing = PeerLink(sender_identity)
    try:
        outgoing.connect_to("127.0.0.1", listener.port, receiver_identity.fingerprint)
        if not _pump(app, lambda: bool(incoming) and outgoing.is_open, CONNECT_TIMEOUT_S):
            raise RuntimeError("TLS-соединение не поднялось за отведённое время")
        incoming_link = incoming[0]

        sender = FileTransferService()
        sender.attach_link(outgoing)
        sender.set_peer_capabilities(CAPS)
        outgoing.message_received.connect(sender.handle_message)

        receiver = FileTransferService()
        receiver.attach_link(incoming_link)
        receiver.set_peer_capabilities(CAPS)
        incoming_link.message_received.connect(receiver.handle_message)

        backend = WindowsFileClipboardBackend()
        try:
            return _run(
                app, work_dir, size, cancel_after_bytes,
                sender, receiver, outgoing, incoming_link, backend,
            )
        finally:
            backend.stop()
    finally:
        outgoing.close()
        listener.stop()


def _run(
    app: QApplication,
    work_dir: Path,
    size: int,
    cancel_after_bytes: int | None,
    sender: FileTransferService,
    receiver: FileTransferService,
    outgoing: PeerLink,
    incoming_link: PeerLink,
    backend: WindowsFileClipboardBackend,
) -> Measurement:
    # ---------------------------------------------------------- чтение с диска
    disk_read_samples_ms: list[float] = []
    original_read = sender.snapshots.read

    def _timed_read(*args, **kwargs):
        started = time.perf_counter()
        try:
            return original_read(*args, **kwargs)
        finally:
            disk_read_samples_ms.append((time.perf_counter() - started) * 1000.0)

    sender.snapshots.read = _timed_read  # type: ignore[method-assign]

    # ---------------------------------------------------------- heartbeat/PING
    heartbeat_arrivals: list[float] = []

    def _on_message_for_heartbeat(message: Message) -> None:
        if message.type is MessageType.PING:
            heartbeat_arrivals.append(time.perf_counter())

    incoming_link.message_received.connect(_on_message_for_heartbeat)
    heartbeat_timer = QTimer()
    heartbeat_timer.setInterval(HEARTBEAT_MS)
    heartbeat_timer.timeout.connect(lambda: outgoing.send(Message(MessageType.PING, {}, b"")))
    heartbeat_timer.start()

    # ---------------------------------------------------------- файл-источник
    source = work_dir / "payload.bin"
    expected_digest = _write_generated_file(source, size)

    transfer_id = sender.offer_local_files([source])
    if transfer_id is None:
        raise RuntimeError("offer_local_files отказал - проверьте peer_supports_files")
    if not _pump(app, lambda: receiver.offered_manifest is not None, OFFER_TIMEOUT_S):
        raise RuntimeError("объявление не дошло до получателя за отведённое время")

    # ------------------------------------------------------ колбэки моста
    held_pipe: list = []
    rtt_starts: dict[tuple[str, int, int], float] = {}
    rtt_samples_ms: list[float] = []

    def _open_pipe(t_id: str, entry_index: int):
        # Синхронно и БЕЗ post_to_service - по контракту: open_pipe уже
        # объявлен синхронным (см. бриф задачи), и это тот самый известный
        # дефект (TRANSFER_BEGIN и сигнал уходят прямо с чужого потока),
        # который эта задача не чинит, а измеряет как есть.
        pipe = receiver.open_pipe(t_id, entry_index)
        held_pipe.append(pipe)
        return pipe

    def _request_read(t_id: str, entry_index: int, offset: int, length: int) -> None:
        # request_read - Slot; вызывается из потока-имитатора Проводника,
        # поэтому единственная законная дорога в Qt - post_to_service.
        rtt_starts[(t_id, entry_index, offset)] = time.perf_counter()
        post_to_service(receiver, "request_read", t_id, entry_index, offset, length)

    def _close_pipe(t_id: str, entry_index: int, reason=None) -> None:
        # close_pipe тоже объявлен синхронным - никакого Qt внутри него нет
        # (см. service.py): только словарь и pipe.finish()/close().
        receiver.close_pipe(t_id, entry_index, reason)

    def _on_operation_finished(result: int) -> None:
        # Не задействуется этим спайком (см. модульный docstring: мы не
        # проходим EndOperation), но set_callbacks требует все четыре.
        status = "completed" if result == S_OK else "failed"
        post_to_service(receiver, "finish_session", status)

    def _on_message_for_rtt(message: Message) -> None:
        if message.type is not MessageType.FILE_CHUNK:
            return
        header = message.header
        key = (header.get("transfer_id"), header.get("entry_index"), header.get("offset"))
        started = rtt_starts.pop(key, None)
        if started is not None:
            rtt_samples_ms.append((time.perf_counter() - started) * 1000.0)

    incoming_link.message_received.connect(_on_message_for_rtt)

    backend.start()
    if not _pump(app, lambda: backend.thread_id is not None, CONNECT_TIMEOUT_S):
        raise RuntimeError("поток STA не поднялся за отведённое время")
    backend.set_callbacks(_open_pipe, _request_read, _close_pipe, _on_operation_finished)

    publish_failures: list[str] = []
    backend.publish_failed.connect(publish_failures.append)
    backend.publish(receiver.offered_manifest, receiver_origin_marker(receiver))
    if not _pump(
        app,
        lambda: backend.published_object is not None or publish_failures,
        PUBLISH_TIMEOUT_S,
    ):
        raise RuntimeError("публикация в буфер обмена не случилась за отведённое время")
    if publish_failures:
        raise RuntimeError(f"публикация в буфер обмена отказала: {publish_failures[0]}")

    # Дать возможным сторонним наблюдателям буфера обмена опросить объект
    # ДО того, как за него возьмётся наш собственный поток-имитатор - иначе
    # их вызовы (см. модульный docstring) попали бы в один список с нашими.
    _pump(app, lambda: False, SETTLE_SECONDS)

    data_object = backend.published_object
    assert data_object is not None  # проверено выше; здесь - для читателя

    # ---------------------------------------------------------- инструмент GUI
    instrument = Instrument()
    instrument.start()

    # ---------------------------------------------------------- сэмплер сокета
    #: pipe_high_water НЕ сэмплируется здесь: ChunkPipe сам копит свой
    #: максимум (см. pipe.py), и опрашивать его по таймеру значило бы либо
    #: повторять то же число, либо - хуже - ловить его с опозданием на
    #: интервал сэмплера. Читаем один раз после конца передачи, у самого
    #: объекта. bytesToWrite() у QSslSocket своего максимума не помнит -
    #: единственный способ узнать пик, а не последнее значение, это опрос.
    socket_bytes_to_write_max = 0

    def _sample() -> None:
        nonlocal socket_bytes_to_write_max
        socket_bytes_to_write_max = max(socket_bytes_to_write_max, outgoing.bytes_to_write)

    sampler = QTimer()
    sampler.setInterval(SAMPLE_INTERVAL_MS)
    sampler.timeout.connect(_sample)
    sampler.start()

    # ---------------------------------------------------------- поток-имитатор
    progress = {"bytes": 0}
    cancel_state = {"requested_at": None, "latency": None}
    emulator_error: list[BaseException] = []
    emulator_digest: list[str] = []
    stream_holder: list = []

    def _emulator() -> None:
        try:
            # GetData тоже вызывается ЗДЕСЬ, на потоке-имитаторе, а не на
            # Qt-потоке заранее: настоящий Проводник берёт IStream с СВОЕГО
            # потока, и это ровно то место, где известный дефект open_pipe
            # (Qt-сигнал прямо с чужого потока - см. комментарий у
            # _open_pipe выше) на самом деле возникает. Вызвав GetData
            # заранее с Qt-потока, эта проверка обошла бы дефект стороной,
            # вместо того чтобы измерить систему как она есть.
            result, medium = call_get_data_medium(
                data_object.pointer, data_object.cf_contents, 0, TYMED_ISTREAM
            )
            if result != S_OK or medium.tymed != TYMED_ISTREAM or not medium.data:
                raise RuntimeError(
                    f"GetData(FileContents) отказал: HRESULT=0x{result & 0xFFFFFFFF:08X}"
                )
            # Та же форма вызова, что test_windows_publisher.py уже проверил:
            # call_stream_read берёт ctypes.c_void_p, call_release - сырой int.
            stream_pointer = ctypes.c_void_p(medium.data)
            stream_holder.append(medium.data)

            hasher = hashlib.sha256()
            position = 0
            while position < size:
                payload, hresult = call_stream_read(stream_pointer, READ_CHUNK_BYTES)
                if hresult != S_OK:
                    if cancel_state["requested_at"] is not None:
                        cancel_state["latency"] = (
                            time.perf_counter() - cancel_state["requested_at"]
                        )
                        return
                    raise RuntimeError(
                        f"IStream::Read отказал неожиданно: "
                        f"HRESULT=0x{hresult & 0xFFFFFFFF:08X}"
                    )
                if not payload:
                    return  # EOF раньше срока - тоже конец потока
                hasher.update(payload)
                position += len(payload)
                progress["bytes"] = position
            emulator_digest.append(hasher.hexdigest())
        except BaseException as error:  # noqa: BLE001 - переносим в основной поток
            emulator_error.append(error)

    emulator = threading.Thread(target=_emulator, name="duo-input-explorer-emulator", daemon=True)
    started_at = time.perf_counter()
    emulator.start()

    def _maybe_cancel() -> None:
        if (
            cancel_after_bytes is not None
            and cancel_state["requested_at"] is None
            and progress["bytes"] >= cancel_after_bytes
        ):
            cancel_state["requested_at"] = time.perf_counter()
            receiver.finish_session("cancelled")

    def _condition() -> bool:
        _maybe_cancel()
        return not emulator.is_alive()

    if not _pump(app, _condition, TRANSFER_CEILING_S):
        raise RuntimeError(
            f"передача не завершилась за {TRANSFER_CEILING_S:.0f} с - мост завис, "
            "а не просто медленный"
        )
    emulator.join(timeout=5.0)
    ended_at = time.perf_counter()

    sampler.stop()
    instrument.stop()

    if emulator_error:
        raise emulator_error[0]

    delivered = progress["bytes"]
    cancelled = cancel_state["requested_at"] is not None
    # ChunkPipe копит свой максимум сам (см. комментарий у сэмплера выше);
    # читаем его после того, как поток-имитатор точно закончил класть в
    # него запросы, - раньше этого момента число ещё могло вырасти.
    pipe_high_water = held_pipe[0].high_water if held_pipe else 0

    if not cancelled:
        if emulator_digest and emulator_digest[0] != expected_digest:
            raise RuntimeError(
                "содержимое, дошедшее через мост, не совпало с исходным sha256 - "
                "измерение недействительно, мост что-то потерял или переставил"
            )
        receiver.finish_session("completed")

    if stream_holder:
        call_release(stream_holder[0])

    if cancel_after_bytes is not None and cancel_state["latency"] is None:
        # Отмена была запрошена явно (проверено в measure()), но путь
        # отмены ни разу не сработал - молчаливый None здесь означал бы
        # "отмена мгновенна", а не "отмену не удалось провести".
        raise RuntimeError(
            "cancel_after_bytes был задан, но отмена не сработала - "
            "cancel_latency_seconds не может остаться неопределённой"
        )

    incoming_link.message_received.disconnect(_on_message_for_rtt)
    incoming_link.message_received.disconnect(_on_message_for_heartbeat)
    heartbeat_timer.stop()
    sender.snapshots.read = original_read  # type: ignore[method-assign]

    # -------------------------------------------------------------- инструмент GUI - контроль
    if not instrument.intervals:
        # Тишина инструмента - это либо "GUI-поток был свободен и снял пробы
        # рано" (невозможно: таймер тикает независимо от нагрузки, пока цикл
        # событий жив) либо "цикл событий не разу не отдавался инструменту" -
        # оба варианта отличимы от настоящей отзывчивости только явной
        # проверкой; молчаливые 0.0/0.0 в отчёте выглядели бы как "идеально
        # отзывчиво", что и было дефектом фазы 0.
        raise AssertionError(
            "инструмент GUI-отзывчивости не сделал ни одного тика - "
            "gui_tick_p99_ms/gui_tick_max_ms измерили бы молчание инструмента, "
            "а не отзывчивость системы"
        )
    tick_samples_ms = [interval * 1000.0 for interval in instrument.intervals]

    if not disk_read_samples_ms:
        raise AssertionError(
            "SnapshotRegistry.read ни разу не был вызван, хотя данные "
            "передавались - что-то читало байты в обход учтённого пути"
        )
    if not rtt_samples_ms:
        raise AssertionError(
            "ни один FILE_CHUNK не был сопоставлен со своим FILE_READ - "
            "rtt_median_ms/rtt_p99_ms измерили бы пустую выборку, а не круг"
        )

    heartbeat_gaps = [
        later - earlier
        for earlier, later in zip(heartbeat_arrivals, heartbeat_arrivals[1:])
    ]

    bypassed_transport_mib_s = _measure_bypassed_transport(app, outgoing, incoming_link, size)

    return Measurement(
        bytes_transferred=delivered,
        elapsed_seconds=(
            (cancel_state["requested_at"] - started_at) if cancelled else (ended_at - started_at)
        ),
        peak_rss_bytes=_peak_rss_bytes(),
        peak_python_bytes=tracemalloc.get_traced_memory()[1],
        pipe_high_water=pipe_high_water,
        cancel_latency_seconds=cancel_state["latency"],
        gui_tick_p99_ms=_percentile(tick_samples_ms, 0.99),
        gui_tick_max_ms=max(tick_samples_ms),
        socket_bytes_to_write_max=socket_bytes_to_write_max,
        rtt_median_ms=statistics.median(rtt_samples_ms),
        rtt_p99_ms=_percentile(rtt_samples_ms, 0.99),
        disk_read_median_ms=statistics.median(disk_read_samples_ms),
        disk_read_p99_ms=_percentile(disk_read_samples_ms, 0.99),
        bypassed_transport_mib_s=bypassed_transport_mib_s,
        heartbeat_gaps_seconds=heartbeat_gaps,
    )


def receiver_origin_marker(receiver: FileTransferService) -> bytes:
    """Байты формата ``application/x-duo-input-origin``.

    Реальному содержимому маркера здесь всё равно кто угодно: ни один
    колбэк этого спайка его не читает. Он существует только потому, что
    publish() требует его как аргумент.
    """
    return b"duo-input-bridge-measure"


def _measure_bypassed_transport(
    app: QApplication, outgoing: PeerLink, incoming_link: PeerLink, total_bytes: int
) -> float:
    """Тот же провод, без IStream, без ChunkPipe и без FileTransferService.

    Кадры FILE_CHUNK летят подряд с надуманным transfer_id: получатель
    (FileTransferService.handle_message -> _on_chunk) не найдёт для него
    открытого pipe и молча отбросит каждый кадр ПОСЛЕ разбора - то есть
    ровно после того места, которое здесь и предстоит измерить. Слушаем не
    его, а сам PeerLink.message_received, который получает кадр сразу после
    того, как FrameAssembler его собрал.
    """
    if total_bytes <= 0:
        return 0.0
    marker = "bridge-measure-bypass"
    chunk_bytes = READ_CHUNK_BYTES
    payload = bytes((index * 131 + 7) & 0xFF for index in range(chunk_bytes))
    chunk_count = max(1, total_bytes // chunk_bytes)

    received = {"bytes": 0}

    def _observe(message: Message) -> None:
        if message.type is MessageType.FILE_CHUNK and message.header.get("transfer_id") == marker:
            received["bytes"] += len(message.blob)

    incoming_link.message_received.connect(_observe)
    try:
        started = time.perf_counter()
        for index in range(chunk_count):
            outgoing.send(
                Message(
                    MessageType.FILE_CHUNK,
                    {"transfer_id": marker, "entry_index": 0, "offset": index * chunk_bytes},
                    payload,
                )
            )
        expected = chunk_count * chunk_bytes
        if not _pump(app, lambda: received["bytes"] >= expected, BYPASS_CEILING_S):
            raise RuntimeError(
                "проход без моста не завершился за отведённое время - "
                "транспорт сам по себе завис"
            )
        elapsed = time.perf_counter() - started
        if elapsed <= 0:
            return 0.0
        return received["bytes"] / MIB / elapsed
    finally:
        incoming_link.message_received.disconnect(_observe)


# ============================================================== CLI


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--size-mib", type=int, default=2048, help="размер файла в передаче")
    parser.add_argument(
        "--cancel-after-mib",
        type=int,
        default=None,
        help="отменить сессию после стольки МиБ и измерить задержку отмены",
    )
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    cancel_after_bytes = (
        None if args.cancel_after_mib is None else args.cancel_after_mib * MIB
    )
    measurement = measure(args.size_mib * MIB, cancel_after_bytes=cancel_after_bytes)
    print(measurement.as_table())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["Measurement", "measure"]
