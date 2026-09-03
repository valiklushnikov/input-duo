"""Доверенный пир: ровно один, переживает перезапуск, забывается начисто."""

from __future__ import annotations

from duo_input.clipboard.trust import TrustedPeer, TrustStore

PEER = TrustedPeer(
    origin_id="a" * 32,
    machine_name="LAPTOP-ONE",
    fingerprint="b" * 64,
    last_address="192.168.1.10",
)


def test_empty_store_has_no_peer(tmp_path):
    assert TrustStore(tmp_path / "peers.json").peer() is None


def test_remembered_peer_survives_a_restart(tmp_path):
    path = tmp_path / "peers.json"
    TrustStore(path).remember(PEER)

    assert TrustStore(path).peer() == PEER


def test_remembering_a_second_peer_replaces_the_first(tmp_path):
    path = tmp_path / "peers.json"
    store = TrustStore(path)
    store.remember(PEER)
    other = TrustedPeer("c" * 32, "LAPTOP-TWO", "d" * 64, "192.168.1.11")
    store.remember(other)

    assert store.peer() == other


def test_forgetting_leaves_nothing_behind(tmp_path):
    path = tmp_path / "peers.json"
    store = TrustStore(path)
    store.remember(PEER)
    store.forget()

    assert store.peer() is None
    assert TrustStore(path).peer() is None


def test_address_is_updated_without_touching_the_fingerprint(tmp_path):
    path = tmp_path / "peers.json"
    store = TrustStore(path)
    store.remember(PEER)
    store.update_address("192.168.1.99")

    remembered = TrustStore(path).peer()
    assert remembered.last_address == "192.168.1.99"
    assert remembered.fingerprint == PEER.fingerprint


def test_unreadable_file_is_treated_as_no_peer(tmp_path):
    path = tmp_path / "peers.json"
    path.write_text("{ это не json", "utf-8")

    assert TrustStore(path).peer() is None
