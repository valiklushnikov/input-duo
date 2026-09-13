"""Одно защищённое соединение со вторым компьютером.

Сертификаты здесь самоподписанные, поэтому Qt справедливо считает их ошибкой.
Ошибка снимается ровно в одном случае: предъявленный сертификат совпадает по
отпечатку с закреплённым при парринге. Всё остальное - разрыв. Это единственное
место в подсистеме, где решается, свой ли собеседник.

Во время парринга закреплённого отпечатка ещё нет, и тогда принимается любой:
доверие в этот момент даёт не сертификат, а человек, сверяющий код на двух
экранах.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QCryptographicHash, QObject, Signal
from PySide6.QtNetwork import QSsl, QSslCertificate, QSslConfiguration, QSslKey, QSslSocket

from .identity import NodeIdentity
from .wire import MAX_FILE_CHUNK_BYTES, FrameAssembler, Message, WireError, encode

#: Сколько Qt разрешено держать непрочитанным в сокете.
#:
#: Ноль (по умолчанию) означает "без границы", и до передачи файлов это было
#: безвредно: 32 МиБ потолка кадра сам по себе был границей. С файлами - нет.
#: Один запрос в полёте ограничивает то, что просим МЫ, но не то, что
#: пришлёт сломанный или враждебный пир: поток незапрошенных FILE_CHUNK
#: отбрасывается в FileTransferService._on_chunk лишь ПОСЛЕ того, как Qt его
#: сложил, а FrameAssembler собрал.
#:
#: Четыре чанка, а не один: меньше одного заставило бы Qt резать каждый кадр,
#: и сборка шла бы по кусочкам без всякой пользы.
READ_BUFFER_BYTES = MAX_FILE_CHUNK_BYTES * 4

#: За этой отметкой очередь записи считается затором.
WRITE_HIGH_WATER_BYTES = MAX_FILE_CHUNK_BYTES * 4


def ssl_configuration(identity: NodeIdentity) -> QSslConfiguration:
    """Наш сертификат и ключ, с требованием, чтобы пир тоже представился."""
    configuration = QSslConfiguration.defaultConfiguration()
    configuration.setLocalCertificate(QSslCertificate(QByteArray(identity.certificate_pem)))
    configuration.setPrivateKey(
        QSslKey(QByteArray(identity.key_pem), QSsl.KeyAlgorithm.Rsa)
    )
    configuration.setPeerVerifyMode(QSslSocket.PeerVerifyMode.VerifyPeer)
    return configuration


def fingerprint_of_socket(socket: QSslSocket) -> str:
    """Отпечаток сертификата, который предъявил собеседник на этом сокете.

    Пустой сертификат (рукопожатие ещё не началось или пир вовсе не
    представился) даёт пустую строку, а не что-то, что могло бы случайно
    совпасть с закреплённым отпечатком. Пустая строка нигде ниже не
    считается совпадением: и здесь, и в обработчике сервера совпадение
    проверяется только когда сама строка непустая.
    """
    certificate = socket.peerCertificate()
    if certificate.isNull():
        return ""
    digest = certificate.digest(QCryptographicHash.Algorithm.Sha256)
    return bytes(digest.toHex()).decode("ascii")


class PeerLink(QObject):
    """Кадры туда и обратно по одному TLS-соединению."""

    message_received = Signal(object)
    connected = Signal(str)
    disconnected = Signal(str)
    congestion_changed = Signal(bool)

    def __init__(self, identity: NodeIdentity, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._identity = identity
        self._socket: QSslSocket | None = None
        self._assembler = FrameAssembler()
        self._expected_fingerprint: str | None = None
        self._peer_fingerprint = ""
        self._congested = False

    @property
    def peer_fingerprint(self) -> str:
        return self._peer_fingerprint

    @property
    def peer_address(self) -> str:
        """IP второго конца этого соединения.

        Нужен координатору для входящих соединений: адрес, который мы сами
        набирали, и так известен (это то, что мы передали в connect_to), а
        для входящего звонка единственный способ узнать, куда звонить в
        следующий раз, - спросить у уже установленного сокета.
        """
        if self._socket is None:
            return ""
        return self._socket.peerAddress().toString()

    @property
    def is_open(self) -> bool:
        return self._socket is not None and self._socket.isEncrypted()

    def connect_to(self, address: str, port: int, expected_fingerprint: str | None) -> None:
        self._expected_fingerprint = expected_fingerprint
        socket = QSslSocket(self)
        socket.setSslConfiguration(ssl_configuration(self._identity))
        self._wire_up(socket)
        socket.connectToHostEncrypted(address, port)

    def adopt(self, socket: QSslSocket, expected_fingerprint: str | None) -> None:
        """Принять уже зашифрованный входящий сокет."""
        self._expected_fingerprint = expected_fingerprint
        self._wire_up(socket)
        self._on_encrypted()

    def send(self, message: Message) -> None:
        if self._socket is None:
            return
        self._socket.write(encode(message))

    def close(self) -> None:
        if self._socket is not None:
            self._socket.abort()
            self._socket = None
        # Закрытие обязано означать "не в заторе". Без этой проверки
        # _congested остался бы устаревшим True (write_congested уже вернул
        # бы False, потому что self._socket теперь None, но никто об этом не
        # услышал бы): при повторном _wire_up на новом сокете переход в False
        # так и не был бы замечен, и подписчик, ждущий его, чтобы возобновить
        # отправку, завис бы навсегда на пустом линке. Вызов - после сброса
        # self._socket, иначе write_congested прочитал бы ещё старый сокет.
        self._check_congestion()

    @property
    def bytes_to_write(self) -> int:
        if self._socket is None:
            return 0
        return int(self._socket.bytesToWrite())

    @property
    def write_congested(self) -> bool:
        return self.bytes_to_write > WRITE_HIGH_WATER_BYTES

    # ------------------------------------------------------------------ внутреннее

    def _wire_up(self, socket: QSslSocket) -> None:
        self._socket = socket
        socket.setParent(self)
        socket.setReadBufferSize(READ_BUFFER_BYTES)
        socket.bytesWritten.connect(lambda _count: self._check_congestion())
        socket.sslErrors.connect(self._on_ssl_errors)
        socket.encrypted.connect(self._on_encrypted)
        socket.readyRead.connect(self._on_ready_read)
        socket.disconnected.connect(lambda: self._fail("соединение закрыто"))
        # Тот же сброс на входе: если эта PeerLink уже была в заторе на
        # предыдущем сокете (например, повторно связана без явного close()),
        # свежий сокет ещё ничего не поставил в очередь, и _congested не
        # должен нести старое True дальше.
        self._check_congestion()

    def _check_congestion(self) -> None:
        """Сообщать о ПЕРЕХОДАХ, а не о состоянии на каждый записанный байт.

        bytesWritten приходит часто; сигнал на каждый его приход превратил бы
        подписчика в получателя потока одинаковых уведомлений.

        Подключён только к bytesWritten - то есть к опустошению очереди, а не
        к send()/write() - то есть к её росту. Поэтому первый восходящий
        фронт замечается лишь после того, как Qt что-то слил, а не в момент,
        когда очередь пересекла отметку: сигнал не синхронен с ростом очереди
        на стороне записи. Это осознанный выбор брифа, безвредный, пока
        сигнал никто не потребляет; менять эту синхронность - решение
        будущего автора throttling-потребителя, а не этой правки.
        """
        congested = self.write_congested
        if congested == self._congested:
            return
        self._congested = congested
        self.congestion_changed.emit(congested)

    def _on_ssl_errors(self, errors) -> None:
        socket = self._socket
        if socket is None:
            return
        fingerprint = fingerprint_of_socket(socket)
        if self._expected_fingerprint is None and fingerprint:
            # Парринг: доверие ещё не выдано, его сейчас выдаст человек.
            socket.ignoreSslErrors()
            return
        if fingerprint and fingerprint == self._expected_fingerprint:
            socket.ignoreSslErrors()
            return
        self._fail("сертификат не тот, что был закреплён")

    def _on_encrypted(self) -> None:
        socket = self._socket
        if socket is None:
            return
        self._peer_fingerprint = fingerprint_of_socket(socket)
        if self._expected_fingerprint is not None and self._peer_fingerprint != self._expected_fingerprint:
            self._fail("сертификат не тот, что был закреплён")
            return
        self.connected.emit(self._peer_fingerprint)

    def _on_ready_read(self) -> None:
        socket = self._socket
        if socket is None:
            return
        try:
            messages = self._assembler.feed(bytes(socket.readAll()))
        except WireError as error:
            self._fail(str(error))
            return
        for message in messages:
            self.message_received.emit(message)

    def _fail(self, reason: str) -> None:
        if self._socket is not None:
            self._socket.abort()
            self._socket = None
        self.disconnected.emit(reason)


__all__ = ["PeerLink", "fingerprint_of_socket", "ssl_configuration"]
