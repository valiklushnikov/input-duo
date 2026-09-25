"""Какие свои адреса стоит сообщать второму компьютеру, а какие нет."""

from __future__ import annotations

from duo_input.clipboard.local_addresses import InterfaceEntry, select_addresses


def _nic(name, kind, *addresses, up=True, running=True, loopback=False):
    return InterfaceEntry(name, kind, up, running, loopback, tuple(addresses))


def test_ethernet_comes_before_wifi_and_wifi_before_the_rest():
    entries = [
        _nic("Tailscale", "other", "100.64.0.5"),
        _nic("Wi-Fi", "wifi", "192.168.1.20"),
        _nic("Ethernet", "ethernet", "192.168.1.10"),
    ]
    assert select_addresses(entries) == ["192.168.1.10", "192.168.1.20", "100.64.0.5"]


def test_loopback_link_local_and_unspecified_are_never_offered():
    entries = [
        _nic("lo", "other", "127.0.0.1", loopback=True),
        _nic("Ethernet", "ethernet", "169.254.3.4", "0.0.0.0", "127.0.0.2", "192.168.1.10"),
    ]
    assert select_addresses(entries) == ["192.168.1.10"]


def test_virtual_adapters_are_left_out_even_when_windows_calls_them_ethernet():
    entries = [
        _nic("vEthernet (WSL)", "ethernet", "172.20.0.1"),
        _nic("VirtualBox Host-Only Network", "ethernet", "192.168.56.1"),
        _nic("VMware Network Adapter VMnet8", "ethernet", "192.168.80.1"),
        _nic("WSL", "ethernet", "172.21.0.1"),
        _nic("docker0", "virtual", "172.17.0.1"),
        _nic("Ethernet", "ethernet", "192.168.1.10"),
    ]
    assert select_addresses(entries) == ["192.168.1.10"]


def test_an_interface_that_is_down_or_not_running_is_skipped():
    entries = [
        _nic("Ethernet", "ethernet", "192.168.1.10", up=False),
        _nic("Ethernet 2", "ethernet", "192.168.1.11", running=False),
        _nic("Wi-Fi", "wifi", "192.168.1.20"),
    ]
    assert select_addresses(entries) == ["192.168.1.20"]


def test_ipv6_is_ignored_and_duplicates_collapse_and_eight_is_the_limit():
    entries = [_nic("Ethernet", "ethernet", "fe80::1", "192.168.1.10", "192.168.1.10")]
    entries += [_nic(f"Wi-Fi {n}", "wifi", f"10.0.0.{n}") for n in range(1, 12)]
    result = select_addresses(entries)
    assert result[0] == "192.168.1.10"
    assert len(result) == 8
    assert len(set(result)) == 8


def test_loopback_flag_excludes_even_reachable_addresses():
    entries = [
        _nic("Loopback Pseudo-Interface 1", "other", "5.5.5.5", loopback=True),
        _nic("Ethernet", "ethernet", "192.168.1.10"),
    ]
    assert select_addresses(entries) == ["192.168.1.10"]


def test_within_kind_order_is_preserved():
    entries = [
        _nic("Ethernet 3", "ethernet", "192.168.1.13"),
        _nic("Ethernet 1", "ethernet", "192.168.1.11"),
        _nic("Wi-Fi 2", "wifi", "10.0.0.2"),
        _nic("Ethernet 2", "ethernet", "192.168.1.12"),
        _nic("Wi-Fi 1", "wifi", "10.0.0.1"),
        _nic("Other VPN", "other", "100.64.0.5"),
    ]
    result = select_addresses(entries)
    # Ethernet entries come first, in enumeration order (3, 1, 2)
    # Then Wi-Fi entries in enumeration order (2, 1)
    # Then other in enumeration order
    assert result == [
        "192.168.1.13",
        "192.168.1.11",
        "192.168.1.12",
        "10.0.0.2",
        "10.0.0.1",
        "100.64.0.5",
    ]
