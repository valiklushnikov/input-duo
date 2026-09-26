"""Свои IPv4-адреса, которые имеет смысл сообщить второму компьютеру.

Отфильтровано всё, по чему второй компьютер заведомо не дозвонится:
loopback, link-local (169.254.x - адрес, который Windows выдаёт сама себе, когда
DHCP не ответил), неподнятые интерфейсы и виртуальные адаптеры. Qt на Windows
называет адаптеры Hyper-V/WSL/VirtualBox/VMware обычным Ethernet, поэтому их
отсекаем по имени. Порядок - проводная сеть, затем Wi-Fi, затем остальное
(VPN вроде Tailscale): второй компьютер пробует адреса именно в этом порядке.

Маячку обнаружения нужен не список адресов, а список интерфейсов: тот же
фильтр, плюс интерфейс должен уметь multicast, не быть туннелем точка-точка
и иметь хотя бы один пригодный IPv4.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Iterable
from dataclasses import dataclass

from duo_input.device.host_addresses import MAX_HOST_ADDRESSES

_VIRTUAL_NAME_PREFIXES = ("vethernet", "virtualbox", "vmware", "wsl")
_ORDER = {"ethernet": 0, "wifi": 1, "other": 2}


@dataclass(frozen=True)
class InterfaceEntry:
    name: str
    kind: str
    up: bool
    running: bool
    loopback: bool
    addresses: tuple[str, ...]
    multicast: bool = True
    point_to_point: bool = False


def _reachable(address: str) -> bool:
    try:
        parsed = ipaddress.IPv4Address(address)
    except ValueError:
        return False
    return not (parsed.is_loopback or parsed.is_link_local or parsed.is_unspecified)


def _usable(entries: Iterable[InterfaceEntry]) -> list[InterfaceEntry]:
    return [
        entry
        for entry in entries
        if entry.up
        and entry.running
        and not entry.loopback
        and entry.kind != "virtual"
        and not entry.name.lower().startswith(_VIRTUAL_NAME_PREFIXES)
    ]


def select_addresses(entries: Iterable[InterfaceEntry]) -> list[str]:
    usable = _usable(entries)
    # sorted() устойчива: внутри группы остаётся порядок перечисления.
    usable.sort(key=lambda entry: _ORDER.get(entry.kind, 2))
    result: list[str] = []
    for entry in usable:
        for address in entry.addresses:
            if _reachable(address) and address not in result:
                result.append(address)
    return result[:MAX_HOST_ADDRESSES]


def select_multicast_entries(entries: Iterable[InterfaceEntry]) -> list[InterfaceEntry]:
    """Интерфейсы, на которых маячок стоит слушать и через которые слать."""
    return [
        entry
        for entry in _usable(entries)
        if entry.multicast
        and not entry.point_to_point
        and any(_reachable(address) for address in entry.addresses)
    ]


def _interfaces_with_entries() -> list[tuple[object, InterfaceEntry]]:
    from PySide6.QtNetwork import QAbstractSocket, QNetworkInterface

    kinds = {
        QNetworkInterface.InterfaceType.Ethernet: "ethernet",
        QNetworkInterface.InterfaceType.Wifi: "wifi",
        QNetworkInterface.InterfaceType.Virtual: "virtual",
    }
    flags = QNetworkInterface.InterfaceFlag
    pairs = []
    for interface in QNetworkInterface.allInterfaces():
        state = interface.flags()
        pairs.append(
            (
                interface,
                InterfaceEntry(
                    name=interface.humanReadableName(),
                    kind=kinds.get(interface.type(), "other"),
                    up=bool(state & flags.IsUp),
                    running=bool(state & flags.IsRunning),
                    loopback=bool(state & flags.IsLoopBack),
                    addresses=tuple(
                        entry.ip().toString()
                        for entry in interface.addressEntries()
                        if entry.ip().protocol()
                        == QAbstractSocket.NetworkLayerProtocol.IPv4Protocol
                    ),
                    multicast=bool(state & flags.CanMulticast),
                    point_to_point=bool(state & flags.IsPointToPoint),
                ),
            )
        )
    return pairs


def local_ipv4_addresses() -> list[str]:
    return select_addresses([entry for _, entry in _interfaces_with_entries()])


def local_multicast_interfaces() -> list:
    """QNetworkInterface для маячка - см. select_multicast_entries."""
    pairs = _interfaces_with_entries()
    chosen = {id(entry) for entry in select_multicast_entries([entry for _, entry in pairs])}
    return [interface for interface, entry in pairs if id(entry) in chosen]


__all__ = [
    "InterfaceEntry",
    "local_ipv4_addresses",
    "local_multicast_interfaces",
    "select_addresses",
    "select_multicast_entries",
]
