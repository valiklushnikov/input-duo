"""Периодический обмен адресами с платой (см. спецификацию обмена адресами).

Раз в EXCHANGE_INTERVAL_MS каждому бэкенду (DeviceService на ПК1 - через U1,
EndpointService на ПК2 - через U2) отдаётся текущий список своих адресов;
ответ - адреса второго компьютера. Бэкенд, которому сейчас не до обмена
(нет платы, идёт запись конфигурации), просто пропускает такт: следующий
через пять секунд. Дальше по цепочке список уходит только при изменении.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

EXCHANGE_INTERVAL_MS = 5000


class AddressExchange(QObject):
    peer_addresses_changed = Signal(list)

    def __init__(
        self,
        backends: list,
        local_addresses: Callable[[], list[str]],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._backends = list(backends)
        self._local = local_addresses
        self._peer: list[str] = []
        for backend in self._backends:
            backend.peer_addresses_received.connect(self._on_peer)
        self._timer = QTimer(self)
        self._timer.setInterval(EXCHANGE_INTERVAL_MS)
        self._timer.timeout.connect(self.tick)

    @property
    def peer_addresses(self) -> list[str]:
        return list(self._peer)

    def start(self) -> None:
        self._timer.start()
        self.tick()

    def stop(self) -> None:
        self._timer.stop()
        for backend in self._backends:
            try:
                backend.peer_addresses_received.disconnect(self._on_peer)
            except (RuntimeError, TypeError):  # pragma: no cover - уже отключено
                pass

    def tick(self) -> None:
        local = self._local()
        for backend in self._backends:
            backend.exchange_addresses(local)  # Return value intentionally unused; backend skips if busy.

    def _on_peer(self, addresses: list) -> None:
        addresses = [str(address) for address in addresses]
        if addresses == self._peer:
            return
        self._peer = addresses
        self.peer_addresses_changed.emit(list(addresses))


__all__ = ["EXCHANGE_INTERVAL_MS", "AddressExchange"]
