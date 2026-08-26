"""The link boundary: the real serial port and the synchronous bridge."""

from __future__ import annotations

import pytest

from duo_input.device.qt_transport import (
    DeviceLink,
    QSerialPortTransport,
    SynchronousTransportLink,
)
from duo_input.device.transport import AbstractByteTransport


class _EchoTransport(AbstractByteTransport):
    def __init__(self) -> None:
        super().__init__()
        self.written: list[bytes] = []

    def write(self, data: bytes) -> bytes:
        if not self.is_open:
            raise RuntimeError("transport is closed")
        self.written.append(data)
        return b"reply:" + data


def test_both_links_satisfy_the_boundary_the_service_depends_on(qtbot):
    for link in (QSerialPortTransport("COM_DOES_NOT_EXIST"), SynchronousTransportLink(_EchoTransport())):
        assert isinstance(link, DeviceLink)


def test_serial_transport_reports_an_unavailable_port_without_raising(qtbot):
    transport = QSerialPortTransport("COM_DOES_NOT_EXIST")

    assert transport.port_name == "COM_DOES_NOT_EXIST"
    assert transport.open() is False
    assert transport.is_open is False


def test_serial_transport_reports_a_send_on_a_closed_port(qtbot):
    transport = QSerialPortTransport("COM_DOES_NOT_EXIST")

    with qtbot.waitSignal(transport.link_lost, timeout=1000):
        transport.send(b"\x01")


def test_synchronous_link_delivers_the_reply_through_the_event_loop(qtbot):
    transport = _EchoTransport()
    link = SynchronousTransportLink(transport)
    assert link.open() is True

    with qtbot.waitSignal(link.bytes_received, timeout=1000) as blocker:
        link.send(b"ping")
        # Nothing may be delivered synchronously.
        assert transport.written == []

    assert bytes(blocker.args[0]) == b"reply:ping"


def test_synchronous_link_splits_replies_into_separate_reads(qtbot):
    link = SynchronousTransportLink(_EchoTransport(), chunk_size=2)
    link.open()
    reads: list[bytes] = []
    link.bytes_received.connect(lambda data: reads.append(bytes(data)))

    with qtbot.waitSignal(link.bytes_received, timeout=1000):
        link.send(b"ab")

    assert b"".join(reads) == b"reply:ab"
    assert len(reads) == 4
    assert all(len(read) <= 2 for read in reads)


def test_synchronous_link_reports_a_transport_that_closed_itself(qtbot):
    transport = _EchoTransport()
    link = SynchronousTransportLink(transport)
    link.open()
    transport.close()

    with qtbot.waitSignal(link.link_lost, timeout=1000):
        link.send(b"ping")

    assert not link.is_open


def test_synchronous_link_stops_delivering_once_closed(qtbot):
    link = SynchronousTransportLink(_EchoTransport())
    link.open()
    reads: list[bytes] = []
    link.bytes_received.connect(reads.append)

    link.send(b"ping")
    link.close()
    qtbot.wait(30)

    assert reads == []


def test_synchronous_link_rejects_a_non_positive_chunk_size():
    with pytest.raises(ValueError):
        SynchronousTransportLink(_EchoTransport(), chunk_size=0)
