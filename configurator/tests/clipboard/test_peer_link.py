"""Два узла в одном процессе: TLS поднимается, отпечаток закрепляется."""

from __future__ import annotations

import os
import logging

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtNetwork import QSslSocket

from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.listener import PeerListener
from duo_input.clipboard.peer import PeerLink, fingerprint_of_socket
from duo_input.clipboard.wire import MAX_FILE_CHUNK_BYTES, MAX_FRAME_BYTES, Message, MessageType
from duo_input.transfer.fileprovider_perf import PerfEmitter


def _perf_events(caplog) -> list[dict[str, str]]:
    events = []
    for record in caplog.records:
        message = record.getMessage()
        if "fp_perf " not in message:
            continue
        atoms = message[message.index("fp_perf ") + len("fp_perf ") :].split()
        events.append(dict(atom.split("=", 1) for atom in atoms))
    return events


@pytest.fixture
def identities(tmp_path):
    return load_or_create(tmp_path / "one"), load_or_create(tmp_path / "two")


def test_a_message_crosses_a_real_tls_connection(qtbot, identities):
    server_identity, client_identity = identities

    listener = PeerListener(server_identity)
    assert listener.listen(0) is True

    incoming: list[PeerLink] = []
    listener.link_ready.connect(incoming.append)

    client = PeerLink(client_identity)
    with qtbot.waitSignal(client.connected, timeout=5000):
        client.connect_to("127.0.0.1", listener.port, server_identity.fingerprint)

    qtbot.waitUntil(lambda: bool(incoming), timeout=5000)
    server_link = incoming[0]

    received: list[Message] = []
    server_link.message_received.connect(received.append)
    client.send(Message(MessageType.PING, {"hello": "мир"}, b""))

    qtbot.waitUntil(lambda: bool(received), timeout=5000)
    assert received[0].type is MessageType.PING
    assert received[0].header["hello"] == "мир"

    listener.stop()
    client.close()


def test_a_fresh_identity_completes_a_real_tls_handshake_on_the_active_backend(
    qtbot, tmp_path
):
    """The identity format must work with the Qt TLS backend shipped on Windows."""
    server_identity = load_or_create(tmp_path / "fresh-server")
    client_identity = load_or_create(tmp_path / "fresh-client")
    listener = PeerListener(server_identity)
    assert listener.listen(0) is True
    incoming: list[PeerLink] = []
    listener.link_ready.connect(incoming.append)

    client = PeerLink(client_identity)
    with qtbot.waitSignal(client.connected, timeout=5000):
        client.connect_to("127.0.0.1", listener.port, server_identity.fingerprint)
    qtbot.waitUntil(lambda: bool(incoming), timeout=5000)

    listener.stop()
    client.close()


def test_a_wrong_fingerprint_is_refused(qtbot, identities):
    server_identity, client_identity = identities

    listener = PeerListener(server_identity)
    assert listener.listen(0) is True

    client = PeerLink(client_identity)
    with qtbot.waitSignal(client.disconnected, timeout=5000):
        client.connect_to("127.0.0.1", listener.port, "f" * 64)

    listener.stop()
    client.close()


def test_the_peer_fingerprint_is_recorded_after_the_handshake(qtbot, identities):
    server_identity, client_identity = identities

    listener = PeerListener(server_identity)
    listener.listen(0)
    client = PeerLink(client_identity)
    with qtbot.waitSignal(client.connected, timeout=5000):
        client.connect_to("127.0.0.1", listener.port, server_identity.fingerprint)

    assert client.peer_fingerprint == server_identity.fingerprint

    listener.stop()
    client.close()


def test_the_listener_accepts_a_client_whose_fingerprint_it_expects(qtbot, identities):
    """Закреплённый отпечаток на стороне слушателя - тоже путь к установлению связи.

    До сих пор connected-тесты полагались на PeerListener.expect(None) (режим
    парринга). Здесь сервер уже связан с конкретным клиентом и должен
    установить соединение именно с ним, а не только принимать кого попало.
    """
    server_identity, client_identity = identities

    listener = PeerListener(server_identity)
    listener.expect(client_identity.fingerprint)
    assert listener.listen(0) is True

    incoming: list[PeerLink] = []
    listener.link_ready.connect(incoming.append)

    client = PeerLink(client_identity)
    with qtbot.waitSignal(client.connected, timeout=5000):
        client.connect_to("127.0.0.1", listener.port, server_identity.fingerprint)

    qtbot.waitUntil(lambda: bool(incoming), timeout=5000)

    listener.stop()
    client.close()


def test_inbound_tls_boundaries_keep_one_connection_generation(
    qtbot, identities, caplog
):
    server_identity, client_identity = identities
    listener = PeerListener(server_identity)
    listener.expect(client_identity.fingerprint)
    assert listener.listen(0) is True
    incoming: list[PeerLink] = []
    listener.link_ready.connect(incoming.append)
    caplog.set_level("INFO", logger="duo_input.clipboard.listener")
    caplog.set_level("INFO", logger="duo_input.clipboard.peer")

    client = PeerLink(client_identity)
    with qtbot.waitSignal(client.connected, timeout=5000):
        client.connect_to("127.0.0.1", listener.port, server_identity.fingerprint)
    qtbot.waitUntil(lambda: bool(incoming), timeout=5000)

    inbound_messages = [
        record.getMessage()
        for record in caplog.records
        if "direction=inbound" in record.getMessage()
    ]
    generations = {
        message.split("connection_generation=", 1)[1].split()[0]
        for message in inbound_messages
        if "connection_generation=" in message
    }
    assert len(generations) == 1, inbound_messages

    listener.stop()
    client.close()


def test_the_listener_forgets_a_link_once_it_disconnects(qtbot, identities):
    """M4: список принятых связей не должен расти без границы.

    С резидентностью процесс живёт днями; без удаления при разрыве каждое
    принятое соединение оставляло бы в списке запись навсегда, даже если оно
    прожило секунду.
    """
    server_identity, client_identity = identities

    listener = PeerListener(server_identity)
    assert listener.listen(0) is True

    incoming: list[PeerLink] = []
    listener.link_ready.connect(incoming.append)

    client = PeerLink(client_identity)
    with qtbot.waitSignal(client.connected, timeout=5000):
        client.connect_to("127.0.0.1", listener.port, server_identity.fingerprint)
    qtbot.waitUntil(lambda: bool(incoming), timeout=5000)
    assert len(listener._links) == 1

    client.close()
    qtbot.waitUntil(lambda: not listener._links, timeout=5000)

    listener.stop()


def test_the_listener_refuses_a_client_whose_fingerprint_it_does_not_expect(qtbot, identities):
    """Закреплённый отпечаток на стороне слушателя защищает и в обратную сторону.

    Клиент здесь настоящий и доверяет настоящему серверу - но сервер закрепил
    за собой другого пира, и этот клиент под описание не подходит. Рукопожатие
    со стороны сервера не должно завершиться вовсе: link_ready не приходит.
    """
    server_identity, client_identity = identities

    listener = PeerListener(server_identity)
    listener.expect("f" * 64)  # чужой отпечаток - не совпадает с client_identity
    assert listener.listen(0) is True

    incoming: list[PeerLink] = []
    listener.link_ready.connect(incoming.append)

    client = PeerLink(client_identity)
    client.connect_to("127.0.0.1", listener.port, server_identity.fingerprint)

    qtbot.wait(500)
    assert incoming == []

    listener.stop()
    client.close()


def test_listener_logs_a_pinned_fingerprint_rejection_without_full_fingerprint(
    identities, caplog
):
    """An inbound identity rejection must not remain invisible or leak identity."""
    from PySide6.QtCore import QByteArray
    from PySide6.QtNetwork import QSslCertificate

    server_identity, client_identity = identities
    listener = PeerListener(server_identity)
    listener.expect("f" * 64)
    socket = _FakeSocket(QSslCertificate(QByteArray(client_identity.certificate_pem)))
    caplog.set_level("INFO", logger="duo_input.clipboard.listener")

    listener._on_server_ssl_errors(socket, [])

    messages = [record.getMessage() for record in caplog.records]
    assert any("peer_identity_rejected" in message and "reason=fingerprint_mismatch" in message for message in messages)
    assert client_identity.fingerprint not in "\n".join(messages)


def test_encrypted_link_logs_one_connection_generation(identities, caplog):
    """TLS completion and identity verification need one correlation key."""
    from PySide6.QtCore import QByteArray
    from PySide6.QtNetwork import QSslCertificate

    server_identity, client_identity = identities
    link = PeerLink(client_identity)
    socket = _FakeSocket(QSslCertificate(QByteArray(server_identity.certificate_pem)))
    link._socket = socket
    link._expected_fingerprint = server_identity.fingerprint
    caplog.set_level("INFO", logger="duo_input.clipboard.peer")

    link._on_encrypted()

    messages = [record.getMessage() for record in caplog.records]
    tls = next(message for message in messages if "peer_tls_complete" in message)
    identity = next(message for message in messages if "peer_identity_verified" in message)
    tls_generation = tls.split("connection_generation=", 1)[1].split()[0]
    identity_generation = identity.split("connection_generation=", 1)[1].split()[0]
    assert tls_generation
    assert identity_generation == tls_generation
    assert server_identity.fingerprint not in "\n".join(messages)


def test_fingerprint_of_socket_is_empty_without_a_certificate():
    """Пустой сертификат обязан давать пустую строку, а не что-то совпадающее.

    Если бы отпечаток несуществующего сертификата оказался, скажем, хэшем
    пустых байт, он мог бы случайно (или не случайно) совпасть с чьим-то
    закреплённым отпечатком. Единственно безопасное значение - пустая строка,
    которую _on_ssl_errors и _on_encrypted нигде не принимают за совпадение.
    """
    from PySide6.QtNetwork import QSslSocket

    socket = QSslSocket()  # никогда не подключался, сертификата нет и не будет
    assert fingerprint_of_socket(socket) == ""


class _FakeSocket:
    """Достаточно похож на QSslSocket, чтобы _on_ssl_errors мог с ним работать.

    Настоящий сокет без сертификата воспроизвести на реальном TLS-рукопожатии
    нельзя: peerCertificate() пуст только до того, как сертификат прислали, а
    sslErrors срабатывает уже после. Эти два теста проверяют защиту от пустого
    отпечатка напрямую, минуя сеть.
    """

    def __init__(self, certificate):
        self._certificate = certificate
        self.ignored = False
        self.aborted = False

    def peerCertificate(self):
        return self._certificate

    def ignoreSslErrors(self):
        self.ignored = True

    def setSocketOption(self, option, value):
        self.socket_options = getattr(self, "socket_options", [])
        self.socket_options.append((option, value))

    def abort(self):
        self.aborted = True


def test_a_missing_certificate_never_earns_trust_during_pairing(identities):
    """Пустой сертификат не получает доверия даже в режиме парринга.

    expected_fingerprint is None означает "закреплённого отпечатка ещё нет,
    доверие сейчас даст человек" - но человек сверяет код, а не отсутствие
    сертификата. Без проверки на непустой fingerprint это условие пропустило
    бы рукопожатие вовсе без предъявленной личности.
    """
    from PySide6.QtNetwork import QSslCertificate

    _, client_identity = identities
    client = PeerLink(client_identity)
    fake = _FakeSocket(QSslCertificate())
    client._socket = fake
    client._expected_fingerprint = None

    client._on_ssl_errors([])

    assert fake.ignored is False


def test_an_empty_fingerprint_never_matches_an_empty_expectation(identities):
    """Пустая строка не должна "совпадать" сама с собой как отпечаток.

    Если бы проверка была написана как `fingerprint == self._expected_fingerprint`
    без предварительного `fingerprint and`, пустой отпечаток отсутствующего
    сертификата совпал бы с пустой строкой expected_fingerprint, и рукопожатие
    продолжилось бы вовсе без проверки личности.
    """
    from PySide6.QtNetwork import QSslCertificate

    _, client_identity = identities
    client = PeerLink(client_identity)
    fake = _FakeSocket(QSslCertificate())
    client._socket = fake
    client._expected_fingerprint = ""

    client._on_ssl_errors([])

    assert fake.ignored is False


def test_on_encrypted_treats_an_empty_expectation_as_pinned_not_pairing(identities):
    """_on_encrypted обязана считать режимом парринга только None, как и _on_ssl_errors.

    _on_ssl_errors проверяет режим парринга строго: `self._expected_fingerprint
    is None`. Если бы _on_encrypted проверяла тот же режим через истинность
    (`if self._expected_fingerprint and ...`), пустая строка была бы неотличима
    от None: несовпадающий отпечаток пира при expected_fingerprint = "" прошёл
    бы как парринг и получил бы `connected`, а не разрыв. Контракт обеих
    проверок обязан быть одинаков: закреплённый отпечаток - это "не None",
    а не "не пусто".
    """
    from PySide6.QtCore import QByteArray
    from PySide6.QtNetwork import QSslCertificate

    server_identity, client_identity = identities
    client = PeerLink(client_identity)
    # Настоящий, но чужой сертификат - его отпечаток не совпадёт ни с чем,
    # кроме самого себя.
    fake = _FakeSocket(QSslCertificate(QByteArray(server_identity.certificate_pem)))
    client._socket = fake
    client._expected_fingerprint = ""  # закреплённый отпечаток, а не парринг

    connected: list[str] = []
    disconnected: list[str] = []
    client.connected.connect(connected.append)
    client.disconnected.connect(disconnected.append)

    client._on_encrypted()

    assert connected == []
    assert disconnected != []


from duo_input.clipboard.peer import READ_BUFFER_BYTES, WRITE_HIGH_WATER_BYTES


def test_the_read_buffer_is_bounded_so_a_flood_cannot_grow_it(qapp, tmp_path):
    # Один запрос в полёте ограничивает то, что просим МЫ. Он не ограничивает
    # то, что пришлёт сломанный или враждебный пир.
    identity = load_or_create(tmp_path)
    link = PeerLink(identity)
    socket = QSslSocket(link)

    link._wire_up(socket)

    assert socket.readBufferSize() == READ_BUFFER_BYTES
    assert socket.readBufferSize() != 0, (
        "нулевой readBufferSize означает 'без границы' - именно то, что "
        "этот тест существует чтобы запретить"
    )


def test_encrypted_enables_tcp_keepalive(qapp, tmp_path, monkeypatch):
    # Без keepalive осиротевший NAT-путь умирает тихо и разрыв всплывает лишь
    # при следующей записи - посреди передачи это роняет всё копирование.
    # Опция ставится в _on_encrypted (после connect: раньше она не применяется).
    from PySide6.QtNetwork import QAbstractSocket

    calls: list[tuple] = []
    monkeypatch.setattr(
        QSslSocket, "setSocketOption", lambda self, opt, val: calls.append((opt, val))
    )
    identity = load_or_create(tmp_path)
    link = PeerLink(identity)
    socket = QSslSocket(link)
    link._socket = socket
    link._expected_fingerprint = None  # режим парринга: _on_encrypted дойдёт до конца

    link._on_encrypted()

    assert (QAbstractSocket.SocketOption.KeepAliveOption, 1) in calls


def test_encrypted_records_runtime_socket_and_tls_configuration(
    qapp, tmp_path, caplog
):
    logger = logging.getLogger("duo_input.clipboard.peer")
    ticks = iter(range(300, 10_000))
    perf = PerfEmitter(logger, "windows_python_monotonic", clock=lambda: next(ticks))
    caplog.set_level(logging.INFO, logger=logger.name)
    link = PeerLink(load_or_create(tmp_path), perf=perf)
    socket = QSslSocket(link)
    link._socket = socket
    link._expected_fingerprint = None

    link._on_encrypted()

    [configuration] = [
        event
        for event in _perf_events(caplog)
        if event["event"] == "transport_configuration"
    ]
    assert set(
        (
            "tcp_nodelay",
            "so_sndbuf",
            "so_rcvbuf",
            "so_keepalive",
            "tls_backend",
            "tls_library",
            "perf_clock",
            "perf_clock_resolution_ns",
            "perf_clock_implementation",
        )
    ).issubset(configuration)


def test_the_read_buffer_leaves_room_for_several_chunks_but_not_for_a_flood():
    assert READ_BUFFER_BYTES >= MAX_FILE_CHUNK_BYTES, (
        "буфер меньше одного чанка заставил бы Qt резать каждый кадр"
    )
    assert READ_BUFFER_BYTES < MAX_FRAME_BYTES


def test_a_link_with_no_socket_reports_no_pending_bytes(qapp, tmp_path):
    link = PeerLink(load_or_create(tmp_path))

    assert link.bytes_to_write == 0
    assert not link.write_congested


def test_congestion_is_reported_when_the_write_queue_passes_the_high_water(
    qapp, tmp_path, monkeypatch
):
    identity = load_or_create(tmp_path)
    link = PeerLink(identity)
    socket = QSslSocket(link)
    link._wire_up(socket)
    monkeypatch.setattr(
        type(socket), "bytesToWrite", lambda _self: WRITE_HIGH_WATER_BYTES + 1
    )

    assert link.write_congested


def test_the_congestion_signal_fires_only_when_the_state_actually_changes(
    qapp, tmp_path, monkeypatch
):
    identity = load_or_create(tmp_path)
    link = PeerLink(identity)
    socket = QSslSocket(link)
    link._wire_up(socket)
    seen: list[bool] = []
    link.congestion_changed.connect(seen.append)
    pending = [WRITE_HIGH_WATER_BYTES + 1]
    monkeypatch.setattr(type(socket), "bytesToWrite", lambda _self: pending[0])

    link._check_congestion()
    link._check_congestion()
    pending[0] = 0
    link._check_congestion()

    assert seen == [True, False], (
        "сигнал повторился при неизменившемся состоянии - подписчик получал бы "
        "поток одинаковых уведомлений вместо двух переходов"
    )


def test_the_bytes_written_signal_actually_drives_the_congestion_check(
    qapp, tmp_path, monkeypatch
):
    """Пин на само подключение bytesWritten, а не только на _check_congestion.

    Все тесты выше зовут _check_congestion() напрямую или читают
    write_congested - ни один не проходит через настоящий сигнал Qt.
    Удаление строки `socket.bytesWritten.connect(...)` из _wire_up не
    роняло бы ни один из них: только этот тест эмитирует bytesWritten
    по-настоящему и проверяет, что на него кто-то подписан.

    Настоящая запись через настоящий сокет здесь не годится: при потолке
    в 4 МиБ, чанках по мегабайту и одном запросе в полёте затор в
    сквозной передаче не наступает никогда, и тест, гоняющий настоящий
    трафик, прошёл бы одинаково что с подключением, что без него - это и
    есть та самая дыра, которую он должен закрывать. Синтетическая
    эмиссия доказывает, что подключение существует; момент, когда Qt сам
    решит вызвать bytesWritten, - забота Qt, а не наша.
    """
    identity = load_or_create(tmp_path)
    link = PeerLink(identity)
    socket = QSslSocket(link)
    link._wire_up(socket)
    monkeypatch.setattr(
        type(socket), "bytesToWrite", lambda _self: WRITE_HIGH_WATER_BYTES + 1
    )
    seen: list[bool] = []
    link.congestion_changed.connect(seen.append)

    socket.bytesWritten.emit(0)

    assert seen == [True]


def test_closing_a_congested_link_emits_the_falling_edge(qapp, tmp_path, monkeypatch):
    """close() обязано означать "больше не в заторе", а не оставить устаревший True.

    write_congested уже отвечает False сразу после close() - self._socket
    становится None, а bytes_to_write читает это первым делом. Но
    _congested хранится отдельно и без явного пересчёта остался бы
    устаревшим True: следующий _wire_up на новом сокете не заметил бы
    перехода в False, и подписчик, ждущий его, чтобы возобновить отправку,
    завис бы навсегда на пустом линке.
    """
    identity = load_or_create(tmp_path)
    link = PeerLink(identity)
    socket = QSslSocket(link)
    link._wire_up(socket)
    monkeypatch.setattr(
        type(socket), "bytesToWrite", lambda _self: WRITE_HIGH_WATER_BYTES + 1
    )
    link._check_congestion()
    assert link._congested is True  # обстановка, не сама проверка

    seen: list[bool] = []
    link.congestion_changed.connect(seen.append)

    link.close()

    assert seen == [False], (
        "close() оставил _congested устаревшим True - переход в False так и "
        "не был замечен"
    )
    assert link._congested is False


def test_rewiring_a_congested_link_with_a_fresh_socket_emits_the_falling_edge(
    qapp, tmp_path, monkeypatch
):
    """_wire_up тоже обязан сбрасывать устаревший затор, а не только close().

    Повторное использование одной и той же PeerLink с новым сокетом - без
    промежуточного close() - воспроизводит тот же дефект: свежий сокет
    ничего ещё не поставил в очередь, но _congested, унаследованный от
    прежнего сокета, остался бы True, и падающий фронт был бы потерян.
    """
    identity = load_or_create(tmp_path)
    link = PeerLink(identity)
    old_socket = QSslSocket(link)
    link._wire_up(old_socket)

    def bytes_to_write_stub(self):
        return WRITE_HIGH_WATER_BYTES + 1 if self is old_socket else 0

    monkeypatch.setattr(type(old_socket), "bytesToWrite", bytes_to_write_stub)
    link._check_congestion()
    assert link._congested is True  # обстановка, не сама проверка

    seen: list[bool] = []
    link.congestion_changed.connect(seen.append)

    fresh_socket = QSslSocket(link)
    link._wire_up(fresh_socket)

    assert seen == [False], (
        "_wire_up унаследовал устаревший True с прежнего сокета - падающий "
        "фронт так и не был замечен"
    )
    assert link._congested is False


# ------------------------------------------------------ жёсткий потолок записи


def test_the_write_limit_leaves_room_for_the_largest_legal_frame():
    from duo_input.clipboard.peer import WRITE_LIMIT_BYTES

    assert WRITE_LIMIT_BYTES >= MAX_FRAME_BYTES + WRITE_HIGH_WATER_BYTES


def test_a_send_past_the_write_limit_is_refused_and_drops_the_link(
    qapp, tmp_path, monkeypatch
):
    # Водораздел только наблюдал: send() писал всегда, и поток из мелких
    # FILE_READ ставил в очередь по мегабайту на каждый.
    from duo_input.clipboard.peer import WRITE_LIMIT_BYTES

    link = PeerLink(load_or_create(tmp_path))
    socket = QSslSocket(link)
    link._wire_up(socket)
    written = []
    monkeypatch.setattr(type(socket), "bytesToWrite", lambda _self: WRITE_LIMIT_BYTES - 10)
    monkeypatch.setattr(type(socket), "write", lambda _self, data: written.append(data) or len(data))
    lost: list[str] = []
    link.disconnected.connect(lost.append)

    accepted = link.send(Message(MessageType.FILE_CHUNK, {"read_id": 1}, b"x" * 64))

    assert accepted is False
    assert written == []
    assert len(lost) == 1


def test_a_send_within_the_write_limit_is_written(qapp, tmp_path, monkeypatch):
    link = PeerLink(load_or_create(tmp_path))
    socket = QSslSocket(link)
    link._wire_up(socket)
    written = []
    monkeypatch.setattr(type(socket), "bytesToWrite", lambda _self: 0)
    monkeypatch.setattr(type(socket), "write", lambda _self, data: written.append(data) or len(data))

    assert link.send(Message(MessageType.PING, {}, b"")) is True
    assert len(written) == 1


def test_file_chunk_send_records_encode_enqueue_and_socket_write_boundaries(
    qapp, tmp_path, monkeypatch, caplog
):
    logger = logging.getLogger("duo_input.clipboard.peer")
    ticks = iter(range(100, 10_000))
    perf = PerfEmitter(logger, "windows_python_monotonic", clock=lambda: next(ticks))
    caplog.set_level(logging.INFO, logger=logger.name)
    link = PeerLink(load_or_create(tmp_path), perf=perf)
    socket = QSslSocket(link)
    link._wire_up(socket)
    monkeypatch.setattr(type(socket), "bytesToWrite", lambda _self: 17)
    monkeypatch.setattr(type(socket), "write", lambda _self, data: len(data))

    assert link.send(
        Message(
            MessageType.FILE_CHUNK,
            {"transfer_id": "generation", "entry_index": 2, "read_id": 7, "offset": 0},
            b"payload",
        )
    ) is True

    events = _perf_events(caplog)
    assert [event["event"] for event in events] == [
        "file_chunk_encode_begin",
        "file_chunk_encode_complete",
        "outbound_enqueue",
        "socket_write_begin",
        "socket_write_complete",
    ]
    assert all(event["read_id"] == "7" for event in events)
    assert events[2]["queue_depth_bytes"] == "17"
    assert events[-1]["accepted_bytes"] != "0"


def test_file_chunk_records_fifo_writer_dequeue_and_drain(
    qapp, tmp_path, monkeypatch, caplog
):
    logger = logging.getLogger("duo_input.clipboard.peer")
    ticks = iter(range(150, 10_000))
    perf = PerfEmitter(logger, "windows_python_monotonic", clock=lambda: next(ticks))
    caplog.set_level(logging.INFO, logger=logger.name)
    link = PeerLink(load_or_create(tmp_path), perf=perf)
    socket = QSslSocket(link)
    link._wire_up(socket)
    frames: list[bytes] = []
    monkeypatch.setattr(type(socket), "bytesToWrite", lambda _self: 0)
    monkeypatch.setattr(
        type(socket), "write", lambda _self, data: frames.append(bytes(data)) or len(data)
    )

    link.send(Message(MessageType.PING, {}, b"before"))
    link.send(
        Message(
            MessageType.FILE_CHUNK,
            {
                "transfer_id": "generation",
                "entry_index": 2,
                "read_id": 7,
                "offset": 0,
            },
            b"payload",
        )
    )

    socket.bytesWritten.emit(len(frames[0]) + 1)
    assert not any(
        event["event"] == "outbound_frame_encrypted" for event in _perf_events(caplog)
    )
    socket.bytesWritten.emit(len(frames[1]) - 1)

    events = _perf_events(caplog)
    [dequeue] = [
        event for event in events if event["event"] == "outbound_plaintext_dequeue"
    ]
    [drained] = [
        event for event in events if event["event"] == "outbound_frame_encrypted"
    ]
    assert dequeue["read_id"] == "7"
    assert drained["read_id"] == "7"
    assert drained["frame_bytes"] == str(len(frames[1]))


def test_file_read_receive_records_bytes_available_and_decode_complete(
    qapp, tmp_path, monkeypatch, caplog
):
    from duo_input.clipboard.wire import encode

    logger = logging.getLogger("duo_input.clipboard.peer")
    ticks = iter(range(200, 10_000))
    perf = PerfEmitter(logger, "windows_python_monotonic", clock=lambda: next(ticks))
    caplog.set_level(logging.INFO, logger=logger.name)
    link = PeerLink(load_or_create(tmp_path), perf=perf)
    socket = QSslSocket(link)
    link._wire_up(socket)
    frame = encode(
        Message(
            MessageType.FILE_READ,
            {
                "transfer_id": "generation",
                "entry_index": 2,
                "read_id": 7,
                "offset": 0,
                "length": 1024,
            },
            b"",
        )
    )
    monkeypatch.setattr(type(socket), "readAll", lambda _self: frame)
    delivered = []
    link.message_received.connect(delivered.append)

    link._on_ready_read()

    events = _perf_events(caplog)
    assert [event["event"] for event in events] == [
        "file_read_bytes_available",
        "file_read_decode_complete",
    ]
    assert all(event["read_id"] == "7" for event in events)
    assert len(delivered) == 1


def test_file_chunk_receive_records_socket_frame_and_delivery_boundaries_without_mutation(
    qapp, tmp_path, monkeypatch, caplog
):
    from duo_input.clipboard.wire import encode

    logger = logging.getLogger("duo_input.clipboard.peer")
    ticks = iter(range(500, 10_000))
    perf = PerfEmitter(logger, "python_monotonic", clock=lambda: next(ticks))
    caplog.set_level(logging.INFO, logger=logger.name)
    link = PeerLink(load_or_create(tmp_path), perf=perf)
    socket = QSslSocket(link)
    link._wire_up(socket)
    original = Message(
        MessageType.FILE_CHUNK,
        {
            "transfer_id": "generation",
            "entry_index": 2,
            "read_id": 7,
            "offset": 4096,
        },
        b"payload",
    )
    monkeypatch.setattr(type(socket), "readAll", lambda _self: encode(original))
    delivered = []
    link.message_received.connect(delivered.append)

    link._on_ready_read()

    events = _perf_events(caplog)
    assert [event["event"] for event in events] == [
        "file_chunk_bytes_available",
        "file_chunk_frame_complete",
        "file_chunk_deliver",
    ]
    assert all(event["read_id"] == "7" for event in events)
    assert delivered == [original]


def test_a_message_whose_header_cannot_be_framed_is_refused_without_raising(
    qapp, tmp_path, monkeypatch
):
    from duo_input.clipboard.wire import MAX_HEADER_BYTES

    link = PeerLink(load_or_create(tmp_path))
    socket = QSslSocket(link)
    link._wire_up(socket)
    written = []
    monkeypatch.setattr(type(socket), "write", lambda _self, data: written.append(data) or len(data))

    accepted = link.send(Message(MessageType.PING, {"pad": "x" * MAX_HEADER_BYTES}, b""))

    assert accepted is False
    assert written == []


def test_messages_after_a_failure_in_the_same_read_are_not_delivered(
    qapp, tmp_path, monkeypatch
):
    from duo_input.clipboard.wire import encode

    link = PeerLink(load_or_create(tmp_path))
    socket = QSslSocket(link)
    link._wire_up(socket)
    frames = encode(Message(MessageType.PING, {"n": 1}, b"")) + encode(
        Message(MessageType.PING, {"n": 2}, b"")
    )
    monkeypatch.setattr(type(socket), "readAll", lambda _self: frames)
    delivered = []

    def on_message(message):
        delivered.append(message.header["n"])
        link._fail("flood")

    link.message_received.connect(on_message)

    link._on_ready_read()

    assert delivered == [1]
