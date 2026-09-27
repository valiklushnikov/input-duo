"""Snapshot lifetime across a peer-link reconnect (spec 2026-09-12 §9,
scenario 2: "повторный Ctrl+V после восстановления связи просто работает").

Losing the link ends the SESSION (descriptors closed -> the files are no longer
locked on Windows) but not the PUBLISHED snapshot: a reconnect of the SAME
pinned peer can keep serving FILE_READs of the transfer that was in progress.
A different/unknown peer, or stopping the subsystem, still releases everything.
"""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QObject, Signal

from duo_input.clipboard.wire import CAPABILITY_CLIPBOARD, CAPABILITY_FILES, Message, MessageType
from duo_input.transfer.service import FileTransferService

PEER = "a" * 64
OTHER_PEER = "b" * 64


class _Link(QObject):
    message_received = Signal(object)
    disconnected = Signal(str)

    def __init__(self, fingerprint: str = PEER) -> None:
        super().__init__()
        self.sent: list[Message] = []
        self.peer_fingerprint = fingerprint

    def send(self, message: Message) -> bool:
        self.sent.append(message)
        return True

    def close(self) -> None: ...


def _read(transfer_id, offset=0, length=4, read_id=7):
    return Message(MessageType.FILE_READ, {"transfer_id": transfer_id, "entry_index": 0,
                                           "offset": offset, "length": length, "read_id": read_id}, b"")


def _of(link, kind):
    return [m for m in link.sent if m.type is kind]


@pytest.fixture
def served(qapp, tmp_path):
    """A transfer that was being served when the link dropped."""
    service = FileTransferService()
    link = _Link()
    service.attach_link(link)
    service.set_peer_capabilities(frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES}))
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])
    service.handle_message(_read(transfer_id, length=2))
    assert _of(link, MessageType.FILE_CHUNK)
    return service, link, source, transfer_id


def _reconnect(service, fingerprint=PEER):
    new = _Link(fingerprint)
    service.attach_link(new)
    service.set_peer_capabilities(frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES}))
    return new


def test_link_loss_keeps_snapshot_but_closes_descriptors(served):
    service, link, source, transfer_id = served
    link.disconnected.emit("connection closed")
    assert transfer_id in service.snapshots.transfer_ids
    assert transfer_id not in service.snapshots.serving   # session over, fds closed
    os.remove(source)                                      # not locked any more


def test_same_peer_reconnect_serves_the_in_progress_transfer(served):
    service, link, _source, transfer_id = served
    link.disconnected.emit("connection closed")
    new = _reconnect(service)
    service.handle_message(_read(transfer_id, offset=2, length=3, read_id=8))
    (chunk,) = _of(new, MessageType.FILE_CHUNK)
    assert chunk.blob == b"234"
    assert chunk.header["read_id"] == 8
    assert not _of(new, MessageType.FILE_ERROR)


def test_same_peer_direct_reattach_without_loss_signal_also_keeps_snapshot(served):
    service, _link, _source, transfer_id = served
    new = _reconnect(service)                              # new link, old never signalled
    service.handle_message(_read(transfer_id, offset=0, length=2, read_id=9))
    assert _of(new, MessageType.FILE_CHUNK)


def test_source_changed_while_disconnected_is_still_detected(served):
    service, link, source, transfer_id = served
    link.disconnected.emit("connection closed")
    source.write_bytes(b"CHANGED-and-longer")
    new = _reconnect(service)
    service.handle_message(_read(transfer_id, offset=0, length=2, read_id=10))
    (error,) = _of(new, MessageType.FILE_ERROR)
    assert error.header["reason"] == "source_changed"


def test_different_peer_releases_every_snapshot(served):
    service, link, _source, transfer_id = served
    link.disconnected.emit("connection closed")
    new = _reconnect(service, OTHER_PEER)
    assert service.snapshots.transfer_ids == ()
    service.handle_message(_read(transfer_id, read_id=11))
    (error,) = _of(new, MessageType.FILE_ERROR)
    assert error.header["reason"] == "source_missing"


def test_unknown_peer_identity_releases_every_snapshot(served):
    service, link, _source, _transfer_id = served
    link.disconnected.emit("connection closed")
    _reconnect(service, "")
    assert service.snapshots.transfer_ids == ()


def test_stopping_the_subsystem_releases_every_snapshot(served):
    service, _link, source, _transfer_id = served
    service.detach_link()
    assert service.snapshots.transfer_ids == ()
    os.remove(source)


def test_capabilities_are_renegotiated_on_reconnect(served):
    service, link, source, _transfer_id = served
    link.disconnected.emit("connection closed")
    new = _Link()
    service.attach_link(new)
    # until the new peer announces files/2 nothing file-related is sent
    assert service.offer_local_files([source]) is None
    assert new.sent == []
