"""Два узла в одном процессе: TLS поднимается, отпечаток закрепляется."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.listener import PeerListener
from duo_input.clipboard.peer import PeerLink, fingerprint_of_socket
from duo_input.clipboard.wire import Message, MessageType


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
