# configurator/tests/transfer/test_end_to_end.py
"""Настоящий TLS на loopback, настоящее кадрирование, без COM.

Роль Проводника играет обычный рабочий поток, который дёргает ChunkPipe так
же, как это будет делать IStream: запросить, подождать, взять. Поэтому этот
файл проверяет протокол и мост, но не COM - COM приходит в фазе 3.
"""

from __future__ import annotations

import hashlib
import os
import threading

import pytest
from PySide6.QtCore import QObject, Qt, Signal

from duo_input.clipboard.wire import CAPABILITY_CLIPBOARD, CAPABILITY_FILES
from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.listener import PeerListener
from duo_input.clipboard.peer import PeerLink
from duo_input.transfer.pipe import PipeClosed
from duo_input.transfer.service import FileTransferService, TransferState

CAPS = frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES})


def deterministic_bytes(size: int, seed: int = 0) -> bytes:
    """Данные генерируются, а не хранятся: гигантских фикстур в репозитории нет."""
    return bytes((seed + index * 131) & 0xFF for index in range(size))


#: Размер одного запроса в drain() (65536). Генератор для стомегабайтного
#: теста опирается на это же число, чтобы каждый сгенерированный блок
#: совпадал ровно с одним чанком провода - иначе уникальность блоков ничего
#: не доказывала бы про перестановку чанков на проводе.
WIRE_CHUNK_BYTES = 65536


def unique_wire_chunk(seed: int, chunk_index: int, length: int = WIRE_CHUNK_BYTES) -> bytes:
    """`length` детерминированных байт, УНИКАЛЬНЫХ для каждого chunk_index.

    deterministic_bytes(n)[i] = (seed + i*131) & 0xFF имеет период 256, а
    65536 - точное кратное 256. Значит внутри одного мегабайтного блока все
    16 чанков по 64 КиБ побайтово совпадают, и то же верно между блоками,
    если блок просто повторить: перестановка или дублирование чанков на
    проводе не меняет ни побайтового сравнения, ни sha256 всего файла -
    измерено на обзоре (задача 1.13, важная находка 1): своп чанка 0 с
    чанком 500 и полный shuffle всех 1600 чанков дают тот же дигест.

    Линейная формула по модулю 256 не чинится добавкой смещения: 65536 и
    1048576 сами кратны 256, так что любая аддитивная поправка от
    chunk_index даёт не больше 256 различимых классов на 1600 чанков -
    совпадения гарантированы (голубиный принцип). Поэтому здесь не формула,
    а sha256(seed:chunk_index), растянутый на length байт: разные
    chunk_index почти наверняка дают разные 32 байта дайджеста (лавинный
    эффект хэша, а не выравнивание степеней двойки), и это единственное
    свойство, которое нужно - не криптостойкость, а взаимная
    неразличимость 1600 конкретных блоков.
    """
    digest = hashlib.sha256(f"{seed}:{chunk_index}".encode("ascii")).digest()
    repeats = -(-length // len(digest))  # ceil без импорта math
    return (digest * repeats)[:length]


@pytest.fixture
def linked_pair(qtbot, tmp_path):
    """Два сервиса на настоящем TLS-соединении через loopback."""
    sender_identity = load_or_create(tmp_path / "sender")
    receiver_identity = load_or_create(tmp_path / "receiver")

    listener = PeerListener(receiver_identity)
    listener.expect(sender_identity.fingerprint)
    assert listener.listen(0), "не удалось занять порт на loopback"

    incoming: list[PeerLink] = []
    listener.link_ready.connect(incoming.append)

    outgoing = PeerLink(sender_identity)
    outgoing.connect_to("127.0.0.1", listener.port, receiver_identity.fingerprint)
    qtbot.waitUntil(lambda: bool(incoming) and outgoing.is_open, timeout=10000)

    sender = FileTransferService()
    sender.attach_link(outgoing)
    sender.set_peer_capabilities(CAPS)
    outgoing.message_received.connect(sender.handle_message)

    receiver = FileTransferService()
    receiver.attach_link(incoming[0])
    receiver.set_peer_capabilities(CAPS)
    incoming[0].message_received.connect(receiver.handle_message)

    yield sender, receiver

    outgoing.close()
    listener.stop()


class _ReadRelay(QObject):
    """Очередь Qt из рабочего потока в поток сервиса - как у COM-шлюза."""

    read = Signal(object, "qlonglong", "qlonglong")


def drain(qtbot, service, transfer_id, entry_index, total, pipe=None) -> bytes:
    """Проводник в миниатюре: запросить, подождать, взять - до объявленного размера.

    Работает в РАБОЧЕМ потоке, как настоящий IStream, и трогает сервис только
    через сигнал с QueuedConnection. Ни одного обращения к сокету отсюда.

    Останов по объявленному размеру - ровно так, как его знает IStream из
    FILEDESCRIPTOR. Пустой FILE_CHUNK больше не конец файла: получатель
    никогда не спрашивает за пределами размера, а короткий ответ посреди
    файла закрывает поток как усечённый.

    ``pipe`` можно передать снаружи - когда тест уже открыл поток сам и
    именно за этим объектом собирается наблюдать (например, за его
    ``high_water``), а не за тем, что drain откроет заново.
    """
    if pipe is None:
        pipe = service.open_pipe(transfer_id, entry_index)
    relay = _ReadRelay()
    relay.read.connect(service.request_read, Qt.ConnectionType.QueuedConnection)
    collected = bytearray()
    failure: list[BaseException] = []

    def worker() -> None:
        try:
            while len(collected) < total:
                chunk = pipe.take(65536)
                if chunk:
                    collected.extend(chunk)
                    continue
                relay.read.emit(pipe, len(collected), 65536)
                if not pipe.wait(timeout=10.0):
                    raise TimeoutError("чанк не пришёл за 10 с")
        except BaseException as error:  # noqa: BLE001 - переносим в основной поток
            failure.append(error)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    qtbot.waitUntil(lambda: not thread.is_alive(), timeout=30000)
    thread.join(timeout=5.0)
    if failure:
        raise failure[0]
    assert len(collected) == total, (
        f"получено {len(collected)} байт, ожидалось {total}"
    )
    return bytes(collected)


@pytest.mark.parametrize("size", [0, 1, 1024, 1024 * 1024])
def test_a_file_of_each_size_arrives_byte_for_byte(qtbot, linked_pair, tmp_path, size):
    sender, receiver = linked_pair
    payload = deterministic_bytes(size)
    source = tmp_path / "payload.bin"
    source.write_bytes(payload)

    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)

    assert drain(qtbot, receiver, transfer_id, 0, size) == payload


def test_a_hundred_megabyte_file_arrives_with_the_digest_it_promised(
    qtbot, linked_pair, tmp_path
):
    sender, receiver = linked_pair
    size = 100 * 1024 * 1024
    chunk_count = size // WIRE_CHUNK_BYTES
    source = tmp_path / "big.bin"
    hasher = hashlib.sha256()
    with open(source, "wb") as handle:
        for chunk_index in range(chunk_count):
            block = unique_wire_chunk(0, chunk_index)
            handle.write(block)
            hasher.update(block)
    expected = hasher.hexdigest()

    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)

    received = drain(qtbot, receiver, transfer_id, 0, size)

    assert hashlib.sha256(received).hexdigest() == expected


def test_a_nested_directory_arrives_with_its_structure(qtbot, linked_pair, tmp_path):
    sender, receiver = linked_pair
    folder = tmp_path / "Photos"
    (folder / "raw").mkdir(parents=True)
    (folder / "img1.jpg").write_bytes(b"first")
    (folder / "raw" / "img2.dng").write_bytes(b"second")

    sender.offer_local_files([folder])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)

    manifest = receiver.offered_manifest
    assert sorted(entry.path for entry in manifest.entries) == [
        "Photos",
        "Photos/img1.jpg",
        "Photos/raw",
        "Photos/raw/img2.dng",
    ]


def test_a_unicode_filename_survives_the_wire(qtbot, linked_pair, tmp_path):
    sender, receiver = linked_pair
    source = tmp_path / "Отчёт за квартал.txt"
    source.write_bytes(b"payload")

    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)

    assert receiver.offered_manifest.entries[0].path == "Отчёт за квартал.txt"
    assert drain(qtbot, receiver, transfer_id, 0, 7) == b"payload"


def test_nothing_is_transferred_until_a_pipe_is_opened(qtbot, linked_pair, tmp_path):
    sender, receiver = linked_pair
    source = tmp_path / "unread.bin"
    source.write_bytes(deterministic_bytes(4096))

    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)
    qtbot.wait(300)

    assert sender.snapshots.serving == frozenset(), (
        "отправитель начал обслуживать снимок до того, как получатель начал вставку"
    )
    assert transfer_id in sender.snapshots.transfer_ids


def test_a_disconnect_midway_wakes_the_blocked_reader(qtbot, linked_pair, tmp_path):
    sender, receiver = linked_pair
    source = tmp_path / "big.bin"
    source.write_bytes(deterministic_bytes(8 * 1024 * 1024))

    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)
    pipe = receiver.open_pipe(transfer_id, 0)
    receiver.request_read(pipe, 0, 65536)
    qtbot.waitUntil(lambda: pipe.depth > 0, timeout=5000)

    # Настоящий разрыв, а не вызов приватного обработчика: закрываем сам
    # сокет получателя. attach_link уже подключил его disconnected к
    # _on_link_lost, поэтому это проходит по-настоящему боевой путь.
    # ._link - приватное поле FileTransferService, и обращение к нему здесь
    # намеренное: публичного доступа к присоединённой связи сервис не даёт,
    # а под проверкой находится именно настоящий путь разрыва, а не то, что
    # у сервиса случайно нашёлся способ до него дотянуться.
    receiver._link.close()
    qtbot.waitUntil(lambda: receiver.state is TransferState.DISCONNECTED, timeout=5000)

    assert receiver.state is TransferState.DISCONNECTED
    with pytest.raises(PipeClosed):
        pipe.take(1024)


def test_the_peak_queue_depth_never_exceeds_one_chunk(qtbot, linked_pair, tmp_path):
    # Потолок памяти при одном запросе в полёте - размер ОДНОГО чанка.
    sender, receiver = linked_pair
    size = 8 * 1024 * 1024
    source = tmp_path / "big.bin"
    source.write_bytes(deterministic_bytes(size))

    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)
    pipe = receiver.open_pipe(transfer_id, 0)
    # drain должен получить именно ЭТОТ pipe: open_pipe заново открыл бы
    # другой поток, и high_water читался бы с потока, через который не
    # прошло ни байта.
    drain_thread_result = drain(qtbot, receiver, transfer_id, 0, size, pipe=pipe)

    assert len(drain_thread_result) == size
    assert pipe.high_water <= 1, (
        f"глубина очереди доходила до {pipe.high_water} при окне 1"
    )


def test_a_source_deleted_before_the_paste_fails_the_session(qtbot, linked_pair, tmp_path):
    sender, receiver = linked_pair
    source = tmp_path / "vanishing.bin"
    source.write_bytes(deterministic_bytes(1024))
    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)
    os.remove(source)

    failures: list[str] = []
    receiver.transfer_failed.connect(failures.append)
    pipe = receiver.open_pipe(transfer_id, 0)
    receiver.request_read(pipe, 0, 1024)
    qtbot.waitUntil(lambda: bool(failures), timeout=5000)

    assert failures == ["source_missing"]
    assert receiver.state is TransferState.FAILED


def test_pasting_twice_transfers_the_same_snapshot_again(qtbot, linked_pair, tmp_path):
    sender, receiver = linked_pair
    payload = deterministic_bytes(4096)
    source = tmp_path / "twice.bin"
    source.write_bytes(payload)
    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)

    first = drain(qtbot, receiver, transfer_id, 0, 4096)
    receiver.finish_session("completed")
    qtbot.wait(100)
    # Измерено (задача 1.13, правило 4): открытие потока снова само уводит
    # состояние из COMPLETED в TRANSFERRING - _offered переживает
    # finish_session, а open_pipe выбирает именно его вне _ACTIVE. Второй
    # paste не нуждается ни в чём, кроме второго open_pipe.
    second = drain(qtbot, receiver, transfer_id, 0, 4096)

    assert first == second == payload


def test_a_flood_of_injected_reads_cannot_grow_the_senders_write_queue(
    qtbot, linked_pair, tmp_path
):
    # Каждый FILE_READ до мегабайта отвечается синхронно: без жёсткого
    # потолка двести крошечных запросов ставили бы в очередь сокета двести
    # мегабайт, прежде чем цикл событий успеет слить хоть байт.
    from duo_input.clipboard.peer import WRITE_LIMIT_BYTES
    from duo_input.clipboard.wire import MAX_FILE_CHUNK_BYTES, Message, MessageType

    sender, _receiver = linked_pair
    source = tmp_path / "big.bin"
    source.write_bytes(deterministic_bytes(8 * MAX_FILE_CHUNK_BYTES))
    transfer_id = sender.offer_local_files([source])
    link = sender._link
    lost: list[str] = []
    link.disconnected.connect(lost.append)
    peak = 0

    for read_id in range(1, 201):
        sender.handle_message(
            Message(
                MessageType.FILE_READ,
                {
                    "transfer_id": transfer_id,
                    "entry_index": 0,
                    "offset": (read_id % 8) * MAX_FILE_CHUNK_BYTES,
                    "length": MAX_FILE_CHUNK_BYTES,
                    "read_id": read_id,
                },
                b"",
            )
        )
        peak = max(peak, link.bytes_to_write)

    assert peak <= WRITE_LIMIT_BYTES
    assert lost, "нарушитель окна не был отключён"
    assert sender.snapshots.transfer_ids == ()
