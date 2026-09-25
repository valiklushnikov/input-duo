"""Обмен адресами по таймеру: свои - плате, чужие - дальше, только при изменении."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from duo_input.clipboard.address_exchange import EXCHANGE_INTERVAL_MS, AddressExchange


class _Backend(QObject):
    peer_addresses_received = Signal(list)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[list[str]] = []

    def exchange_addresses(self, local: list[str]) -> bool:
        self.sent.append(list(local))
        return True


def test_every_tick_hands_every_backend_the_current_local_list(qapp):
    first, second = _Backend(), _Backend()
    local = [["192.168.1.10"]]
    exchange = AddressExchange([first, second], lambda: local[0])

    exchange.tick()
    local[0] = ["192.168.1.11"]
    exchange.tick()

    assert first.sent == [["192.168.1.10"], ["192.168.1.11"]]
    assert second.sent == first.sent


def test_the_peer_list_is_announced_only_when_it_changes(qapp):
    backend = _Backend()
    exchange = AddressExchange([backend], lambda: [])
    seen = []
    exchange.peer_addresses_changed.connect(seen.append)

    backend.peer_addresses_received.emit(["10.0.0.2"])
    backend.peer_addresses_received.emit(["10.0.0.2"])
    backend.peer_addresses_received.emit(["10.0.0.3"])

    assert seen == [["10.0.0.2"], ["10.0.0.3"]]
    assert exchange.peer_addresses == ["10.0.0.3"]


def test_start_exchanges_at_once_and_then_on_the_interval(qtbot):
    backend = _Backend()
    exchange = AddressExchange([backend], lambda: ["192.168.1.10"])
    exchange.start()
    try:
        assert len(backend.sent) == 1
        assert exchange._timer.interval() == EXCHANGE_INTERVAL_MS
        assert exchange._timer.isActive()
    finally:
        exchange.stop()
    assert not exchange._timer.isActive()


def test_stop_disconnects_backends_so_later_peer_addresses_received_emits_nothing(qapp):
    backend = _Backend()
    exchange = AddressExchange([backend], lambda: [])
    seen = []
    exchange.peer_addresses_changed.connect(seen.append)

    exchange.stop()
    backend.peer_addresses_received.emit(["10.0.0.2"])

    assert seen == []
    assert exchange.peer_addresses == []
