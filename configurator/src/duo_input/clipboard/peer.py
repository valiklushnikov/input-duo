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

import logging
import sys
import uuid
from collections import deque

from PySide6.QtCore import QByteArray, QCryptographicHash, QObject, QTimer, Signal
from PySide6.QtNetwork import (
    QAbstractSocket,
    QSsl,
    QSslCertificate,
    QSslConfiguration,
    QSslKey,
    QSslSocket,
)

from .identity import NodeIdentity
from .wire import (
    MAX_FILE_CHUNK_BYTES,
    MAX_FRAME_BYTES,
    FrameAssembler,
    Message,
    MessageType,
    WireError,
    encode,
)
from ..transfer.fileprovider_perf import PerfEmitter

logger = logging.getLogger(__name__)

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

#: Жёсткий потолок очереди записи. Водораздел выше только наблюдает, а
#: send() до этого писал всегда: каждый входящий FILE_READ отвечается
#: синхронно, до мегабайта, и поток мелких запросов успевал поставить в
#: очередь сотни мегабайт раньше, чем цикл событий сливал хоть байт.
#:
#: Честный пир сюда не доходит. Он держит один запрос в полёте на поток и
#: шлёт следующий, лишь получив ответ на предыдущий, - то есть когда тот уже
#: покинул нашу очередь. Потолок оставляет место самому крупному законному
#: кадру (CONTENT буфера обмена) поверх водораздела; кто его пробивает,
#: нарушает окно, и соединение с ним разрывается.
WRITE_LIMIT_BYTES = MAX_FRAME_BYTES + WRITE_HIGH_WATER_BYTES

#: Предельное время на TCP connect и TLS handshake. Системный connect timeout
#: на разных ОС может длиться минуты; после сна это навсегда оставляло
#: координатор с ``_dialing = True`` и без следующей попытки.
CONNECT_TIMEOUT_MS = 10_000


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

    def __init__(
        self,
        identity: NodeIdentity,
        parent: QObject | None = None,
        *,
        connection_generation: str | None = None,
        direction: str = "unknown",
        perf: PerfEmitter | None = None,
    ) -> None:
        super().__init__(parent)
        self._identity = identity
        self._connection_generation = connection_generation or uuid.uuid4().hex
        self._direction = direction
        self._socket: QSslSocket | None = None
        self._assembler = FrameAssembler()
        self._expected_fingerprint: str | None = None
        self._peer_fingerprint = ""
        self._congested = False
        self._pending_write_frames: deque[dict[str, object]] = deque()
        self._connect_timeout = QTimer(self)
        self._connect_timeout.setSingleShot(True)
        self._connect_timeout.setInterval(CONNECT_TIMEOUT_MS)
        self._connect_timeout.timeout.connect(
            lambda: self._fail("время подключения истекло")
        )
        clock_domain = (
            "windows_python_monotonic" if sys.platform == "win32" else "python_monotonic"
        )
        self._perf = perf or PerfEmitter(logger, clock_domain)

    @staticmethod
    def _file_perf_fields(message: Message) -> dict[str, object]:
        """Return bounded, path-free correlation safe for diagnostic logging."""
        header = message.header
        transfer_id = header.get("transfer_id")
        if (
            not isinstance(transfer_id, str)
            or not transfer_id
            or any(character.isspace() for character in transfer_id)
        ):
            transfer_id = "invalid"

        def integer(name: str) -> int:
            value = header.get(name)
            return value if isinstance(value, int) and not isinstance(value, bool) else -1

        return {
            "transfer_id": transfer_id,
            "entry_index": integer("entry_index"),
            "read_id": integer("read_id"),
            "offset": integer("offset"),
            "length": integer("length"),
        }

    @staticmethod
    def _compact_perf_text(value: object) -> str:
        rendered = str(value).strip()
        if not rendered:
            return "unknown"
        return "_".join(rendered.split())

    def _emit_transport_configuration(self, socket: QSslSocket) -> None:
        def option(name: QAbstractSocket.SocketOption) -> object:
            try:
                value = socket.socketOption(name)
                return int(value)
            except (AttributeError, TypeError, ValueError, RuntimeError):
                return "unavailable"

        try:
            tls_backend = QSslSocket.activeBackend()
        except (AttributeError, RuntimeError):
            tls_backend = "unavailable"
        try:
            tls_library = QSslSocket.sslLibraryVersionString()
        except (AttributeError, RuntimeError):
            tls_library = "unavailable"
        self._perf.emit(
            "transport_configuration",
            connection_generation=self._connection_generation,
            direction=self._direction,
            tcp_nodelay=option(QAbstractSocket.SocketOption.LowDelayOption),
            so_sndbuf=option(QAbstractSocket.SocketOption.SendBufferSizeSocketOption),
            so_rcvbuf=option(QAbstractSocket.SocketOption.ReceiveBufferSizeSocketOption),
            so_keepalive=option(QAbstractSocket.SocketOption.KeepAliveOption),
            tls_backend=self._compact_perf_text(tls_backend),
            tls_library=self._compact_perf_text(tls_library),
            perf_clock=self._perf.clock_name,
            perf_clock_resolution_ns=self._perf.clock_resolution_ns,
            perf_clock_implementation=self._compact_perf_text(
                self._perf.clock_implementation
            ),
        )

    @property
    def peer_fingerprint(self) -> str:
        return self._peer_fingerprint

    @property
    def connection_generation(self) -> str:
        return self._connection_generation

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
        self._direction = "outbound"
        self._expected_fingerprint = expected_fingerprint
        logger.info(
            "peer_socket_created connection_generation=%s direction=%s remote=%s:%d",
            self._connection_generation,
            self._direction,
            address,
            port,
        )
        socket = QSslSocket(self)
        socket.setSslConfiguration(ssl_configuration(self._identity))
        self._wire_up(socket)
        self._connect_timeout.start()
        socket.connectToHostEncrypted(address, port)

    def adopt(self, socket: QSslSocket, expected_fingerprint: str | None) -> None:
        """Принять уже зашифрованный входящий сокет."""
        self._direction = "inbound"
        self._expected_fingerprint = expected_fingerprint
        self._wire_up(socket)
        self._on_encrypted()

    def send(self, message: Message) -> bool:
        """Поставить кадр в очередь. ``False`` - кадр не отправлен.

        Отказ не бросает исключений: сюда приходят из слотов Qt, где
        исключение не доходит ни до кого. Переполнение очереди разрывает
        соединение - дальше пира с нарушенным окном обслуживать нельзя.
        """
        if self._socket is None:
            return False
        file_chunk = message.type is MessageType.FILE_CHUNK
        correlation = self._file_perf_fields(message) if file_chunk else {}
        if file_chunk:
            self._perf.emit(
                "file_chunk_encode_begin", bytes=len(message.blob), **correlation
            )
        try:
            frame = encode(message)
        except WireError as error:
            logger.error("кадр %s не отправлен: %s", message.type.name, error)
            return False
        if file_chunk:
            self._perf.emit(
                "file_chunk_encode_complete",
                bytes=len(message.blob),
                frame_bytes=len(frame),
                **correlation,
            )
        if self.bytes_to_write + len(frame) > WRITE_LIMIT_BYTES:
            logger.warning(
                "очередь записи превысила бы %d байт - соединение разорвано",
                WRITE_LIMIT_BYTES,
            )
            self._fail("очередь записи переполнена")
            return False
        if file_chunk:
            self._perf.emit(
                "outbound_enqueue",
                frame_bytes=len(frame),
                queue_depth_bytes=self.bytes_to_write,
                **correlation,
            )
            self._perf.emit(
                "socket_write_begin",
                frame_bytes=len(frame),
                queue_depth_bytes=self.bytes_to_write,
                **correlation,
            )
        accepted = self._socket.write(frame)
        if accepted > 0:
            self._pending_write_frames.append(
                {
                    "remaining": min(int(accepted), len(frame)),
                    "frame_bytes": len(frame),
                    "correlation": correlation if file_chunk else None,
                    "started": False,
                }
            )
        if file_chunk:
            self._perf.emit(
                "socket_write_complete",
                accepted_bytes=int(accepted),
                frame_bytes=len(frame),
                queue_depth_bytes=self.bytes_to_write,
                **correlation,
            )
        return True

    def close(self) -> None:
        self._stop_connect_timeout()
        if self._socket is not None:
            self._abort_socket(self._socket)
            self._socket = None
        self._pending_write_frames.clear()
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

    def _stop_connect_timeout(self) -> None:
        try:
            self._connect_timeout.stop()
        except RuntimeError:
            # При завершении QApplication Qt может уничтожить дочерний QTimer
            # раньше, чем сокет испустит свой последний disconnected.
            pass

    @staticmethod
    def _abort_socket(socket: QSslSocket) -> None:
        try:
            socket.abort()
        except RuntimeError:
            # Та же последовательность shutdown возможна для самого сокета:
            # Python wrapper ещё участвует в callback, а C++ QObject уже удалён.
            pass

    def _emit_disconnected(self, reason: str) -> None:
        try:
            self.disconnected.emit(reason)
        except RuntimeError:
            # Последний socket callback может исполняться уже после удаления
            # C++ PeerLink при завершении QApplication; слушателей тогда нет.
            pass

    def _wire_up(self, socket: QSslSocket) -> None:
        self._socket = socket
        self._pending_write_frames.clear()
        socket.setParent(self)
        socket.setReadBufferSize(READ_BUFFER_BYTES)
        socket.bytesWritten.connect(
            lambda count, bound_socket=socket: self._on_bytes_written(
                bound_socket, int(count)
            )
        )
        socket.connected.connect(self._on_tcp_connected)
        socket.errorOccurred.connect(
            lambda _error, bound_socket=socket: self._on_socket_error(bound_socket)
        )
        socket.sslErrors.connect(self._on_ssl_errors)
        socket.encrypted.connect(self._on_encrypted)
        socket.readyRead.connect(self._on_ready_read)
        socket.disconnected.connect(lambda: self._fail("соединение закрыто"))
        # Тот же сброс на входе: если эта PeerLink уже была в заторе на
        # предыдущем сокете (например, повторно связана без явного close()),
        # свежий сокет ещё ничего не поставил в очередь, и _congested не
        # должен нести старое True дальше.
        self._check_congestion()

    def _on_bytes_written(self, socket: QSslSocket, count: int) -> None:
        """Attribute FIFO drain notifications to frames already accepted by Qt.

        ``QSslSocket.write`` only means that Qt accepted plaintext into its
        internal writer. ``bytesWritten`` is the nearest public boundary for
        observing that those bytes subsequently left that queue. It does not
        claim remote receipt or expose TLS-record/socket internals.
        """
        if self._socket is not socket:
            return
        remaining_written = max(0, count)
        while remaining_written and self._pending_write_frames:
            pending = self._pending_write_frames[0]
            correlation = pending["correlation"]
            if correlation is not None and not pending["started"]:
                pending["started"] = True
                self._perf.emit(
                    "outbound_plaintext_dequeue",
                    frame_bytes=pending["frame_bytes"],
                    **correlation,
                )
            consumed = min(remaining_written, int(pending["remaining"]))
            pending["remaining"] = int(pending["remaining"]) - consumed
            remaining_written -= consumed
            if int(pending["remaining"]) != 0:
                break
            self._pending_write_frames.popleft()
            if correlation is not None:
                self._perf.emit(
                    "outbound_frame_encrypted",
                    frame_bytes=pending["frame_bytes"],
                    **correlation,
                )
        self._check_congestion()

    def _on_tcp_connected(self) -> None:
        logger.info(
            "peer_tcp_connected connection_generation=%s direction=%s",
            self._connection_generation,
            self._direction,
        )

    def _on_socket_error(self, socket: QSslSocket) -> None:
        if self._socket is socket:
            self._fail(socket.errorString() or "ошибка сетевого соединения")

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
            logger.info(
                "peer_tls_errors connection_generation=%s direction=%s decision=ignored mode=pairing error_count=%d",
                self._connection_generation,
                self._direction,
                len(errors),
            )
            socket.ignoreSslErrors()
            return
        if fingerprint and fingerprint == self._expected_fingerprint:
            logger.info(
                "peer_tls_errors connection_generation=%s direction=%s decision=ignored mode=pinned error_count=%d",
                self._connection_generation,
                self._direction,
                len(errors),
            )
            socket.ignoreSslErrors()
            return
        logger.info(
            "peer_identity_rejected connection_generation=%s direction=%s reason=fingerprint_mismatch",
            self._connection_generation,
            self._direction,
        )
        self._fail("сертификат не тот, что был закреплён")

    def _on_encrypted(self) -> None:
        socket = self._socket
        if socket is None:
            return
        self._stop_connect_timeout()
        # Включить TCP keepalive теперь, когда сокет реально подключён (нативный
        # дескриптор существует - до connect опция не применяется). Молчащий/
        # NAT-осиротевший путь иначе умирает тихо, и разрыв всплывает лишь при
        # следующей записи - посреди передачи это роняет всё копирование.
        # Keepalive держит NAT-трансляцию живой и детектирует мёртвого пира
        # быстрее прикладного heartbeat/silence. Ортогонально устойчивости fetch
        # к реконнекту (Swift FetchController): keepalive снижает частоту
        # разрывов, retry переживает те, что всё же случаются.
        socket.setSocketOption(QAbstractSocket.SocketOption.KeepAliveOption, 1)
        self._emit_transport_configuration(socket)
        logger.info(
            "peer_tls_complete connection_generation=%s direction=%s",
            self._connection_generation,
            self._direction,
        )
        self._peer_fingerprint = fingerprint_of_socket(socket)
        if self._expected_fingerprint is not None and self._peer_fingerprint != self._expected_fingerprint:
            logger.info(
                "peer_identity_rejected connection_generation=%s direction=%s reason=fingerprint_mismatch",
                self._connection_generation,
                self._direction,
            )
            self._fail("сертификат не тот, что был закреплён")
            return
        logger.info(
            "peer_identity_verified connection_generation=%s direction=%s fingerprint_short=%s",
            self._connection_generation,
            self._direction,
            self._peer_fingerprint[:12],
        )
        self.connected.emit(self._peer_fingerprint)

    def _on_ready_read(self) -> None:
        socket = self._socket
        if socket is None:
            return
        available_ns = self._perf.now()
        try:
            messages = self._assembler.feed(bytes(socket.readAll()))
        except WireError as error:
            self._fail(str(error))
            return
        frames_complete_ns = self._perf.now()
        for message in messages:
            if self._socket is not socket:
                # Обработчик предыдущего кадра разорвал соединение: кадры,
                # пришедшие тем же чтением, принадлежат уже мёртвой связи.
                return
            if message.type is MessageType.FILE_READ:
                correlation = self._file_perf_fields(message)
                self._perf.emit_at(
                    available_ns, "file_read_bytes_available", **correlation
                )
                self._perf.emit("file_read_decode_complete", **correlation)
            elif message.type is MessageType.FILE_CHUNK:
                correlation = self._file_perf_fields(message)
                self._perf.emit_at(
                    available_ns, "file_chunk_bytes_available", **correlation
                )
                self._perf.emit_at(
                    frames_complete_ns, "file_chunk_frame_complete", **correlation
                )
                self._perf.emit("file_chunk_deliver", **correlation)
            self.message_received.emit(message)

    def _fail(self, reason: str) -> None:
        socket = self._socket
        if socket is None:
            return
        # Сначала убрать ссылку: abort() может синхронно испустить disconnected,
        # и повторный вход не должен дважды сообщить координатору об одном сбое.
        self._socket = None
        self._stop_connect_timeout()
        self._abort_socket(socket)
        self._pending_write_frames.clear()
        self._emit_disconnected(reason)


__all__ = [
    "READ_BUFFER_BYTES",
    "CONNECT_TIMEOUT_MS",
    "WRITE_HIGH_WATER_BYTES",
    "WRITE_LIMIT_BYTES",
    "PeerLink",
    "fingerprint_of_socket",
    "ssl_configuration",
]
