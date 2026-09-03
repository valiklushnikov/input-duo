"""Приём входящего соединения от второго компьютера.

У QSslServer своя, отдельная от QSslSocket точка принятия решения: пока
сервер сам не проигнорирует ошибки самоподписанного сертификата, рукопожатие
с этой стороны не завершится и pendingConnectionAvailable не придёт вовсе -
клиент просто увидит закрытое соединение. Поэтому то же правило доверия, что
у PeerLink, здесь продублировано на уровне сервера: без этого дубликата
подключение не поднимается ни в режиме парринга, ни при верном отпечатке.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QHostAddress, QSslServer, QSslSocket

from .identity import NodeIdentity
from .peer import PeerLink, fingerprint_of_socket, ssl_configuration


class PeerListener(QObject):
    """Слушает порт и выдаёт готовую связь на каждое принятое соединение."""

    link_ready = Signal(object)

    def __init__(self, identity: NodeIdentity, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._identity = identity
        self._expected_fingerprint: str | None = None
        self._server = QSslServer(self)
        self._server.setSslConfiguration(ssl_configuration(identity))
        self._server.sslErrors.connect(self._on_server_ssl_errors)
        self._server.pendingConnectionAvailable.connect(self._on_pending)
        self._links: list[PeerLink] = []

    @property
    def port(self) -> int:
        return int(self._server.serverPort())

    @property
    def is_listening(self) -> bool:
        return self._server.isListening()

    def expect(self, fingerprint: str | None) -> None:
        """Чей отпечаток считать своим. None означает режим парринга."""
        self._expected_fingerprint = fingerprint

    def listen(self, port: int) -> bool:
        return self._server.listen(QHostAddress.SpecialAddress.Any, port)

    def stop(self) -> None:
        for link in self._links:
            link.close()
        self._links.clear()
        self._server.close()

    def _on_server_ssl_errors(self, socket: QSslSocket, errors) -> None:
        """То же правило, что в PeerLink._on_ssl_errors, но для входящей стороны.

        Не проигнорировать здесь - значит и не узнать, кто стучится: сокет
        так и не станет доступен через nextPendingConnection.
        """
        fingerprint = fingerprint_of_socket(socket)
        if self._expected_fingerprint is None and fingerprint:
            # Парринг: доверие ещё не выдано, его сейчас выдаст человек.
            socket.ignoreSslErrors()
            return
        if fingerprint and fingerprint == self._expected_fingerprint:
            socket.ignoreSslErrors()
            return
        # Ничего не делаем: Qt сам оборвёт рукопожатие с чужим сертификатом.

    def _on_pending(self) -> None:
        while True:
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            link = PeerLink(self._identity, self)
            link.adopt(socket, self._expected_fingerprint)
            self._links.append(link)
            # Без этого список растёт без границы: с резидентностью процесс
            # живёт днями, а каждое принятое соединение оставляло бы в нём
            # запись навсегда, даже разорванную секунду спустя.
            link.disconnected.connect(lambda _reason, link=link: self._forget(link))
            self.link_ready.emit(link)

    def _forget(self, link: PeerLink) -> None:
        if link in self._links:
            self._links.remove(link)


__all__ = ["PeerListener"]
