"""The link boundary: the real serial port and the synchronous bridge."""

from __future__ import annotations

import pytest
from PySide6.QtSerialPort import QSerialPort

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


# --------------------------------------------------- stale bytes on the wire
#
# A CDC-ACM device's own OS driver keeps reading and buffering whatever the
# device sends as soon as it is enumerated - whether or not this process has
# the port open, and whether or not anything is asking for a reply. On the
# reference target that includes its own plain-text trace, which the device
# has no way to know a host is about to start reading and does not stop
# sending until this transport's own first request reaches it. Left in the
# buffer, those bytes are read as the start of the first reply and the frame
# decoder calls it corrupt - measured on real hardware, not by inference.
# Independent of the trace, too: a board that rebooted mid-conversation, or a
# reply this process never finished reading last time, leaves the same kind
# of stale bytes behind.
#
# No real or virtual serial port is available in this environment, so these
# monkeypatch QSerialPort itself - verified to work at the class level in
# PySide6 - rather than skip the behaviour untested.


def test_opening_the_port_clears_whatever_it_already_received(qtbot, monkeypatch):
    """Fails if QSerialPortTransport.open() stops calling
    ``self._port.clear(QSerialPort.Direction.Input)`` after a successful
    open."""
    cleared: list[QSerialPort.Direction] = []
    # isOpen() is left real: a freshly constructed QSerialPort answers False,
    # which is what lets open()'s own early-return-if-already-open branch be
    # skipped and its real body run.
    monkeypatch.setattr(QSerialPort, "open", lambda self, mode: True)
    monkeypatch.setattr(QSerialPort, "setDataTerminalReady", lambda self, on: None)
    monkeypatch.setattr(
        QSerialPort,
        "clear",
        lambda self, direction=QSerialPort.Direction.AllDirections: (
            cleared.append(direction) or True
        ),
    )

    transport = QSerialPortTransport("COM_FAKE")

    assert transport.open() is True
    assert cleared == [QSerialPort.Direction.Input]


def test_opening_a_port_that_fails_to_open_clears_nothing(qtbot, monkeypatch):
    """A port that never opened has nothing of this process's to protect -
    and QSerialPort.clear() on an unopened port would be a call into a
    control this process does not hold."""
    cleared: list[QSerialPort.Direction] = []
    monkeypatch.setattr(QSerialPort, "open", lambda self, mode: False)
    monkeypatch.setattr(
        QSerialPort,
        "clear",
        lambda self, direction=QSerialPort.Direction.AllDirections: (
            cleared.append(direction) or True
        ),
    )

    transport = QSerialPortTransport("COM_FAKE")

    assert transport.open() is False
    assert cleared == []


def test_sending_a_request_clears_whatever_arrived_since_the_last_one(qtbot, monkeypatch):
    """Fails if QSerialPortTransport.send() stops calling
    ``self._port.clear(QSerialPort.Direction.Input)`` before writing. Covers
    the gap open()'s own clear cannot: bytes that arrive after the port
    opens but before (or, if the device is slow to react, briefly after) the
    first request reaches it."""
    cleared: list[QSerialPort.Direction] = []
    monkeypatch.setattr(QSerialPort, "isOpen", lambda self: True)
    monkeypatch.setattr(
        QSerialPort,
        "clear",
        lambda self, direction=QSerialPort.Direction.AllDirections: (
            cleared.append(direction) or True
        ),
    )
    monkeypatch.setattr(QSerialPort, "write", lambda self, data: len(data))

    transport = QSerialPortTransport("COM_FAKE")
    transport.send(b"\x01")

    assert cleared == [QSerialPort.Direction.Input]


def test_sending_on_a_closed_port_clears_nothing(qtbot, monkeypatch):
    cleared: list[QSerialPort.Direction] = []
    monkeypatch.setattr(QSerialPort, "isOpen", lambda self: False)
    monkeypatch.setattr(
        QSerialPort,
        "clear",
        lambda self, direction=QSerialPort.Direction.AllDirections: (
            cleared.append(direction) or True
        ),
    )

    transport = QSerialPortTransport("COM_FAKE")
    with qtbot.waitSignal(transport.link_lost, timeout=1000):
        transport.send(b"\x01")

    assert cleared == []


def test_each_send_clears_input_again_not_only_the_first(qtbot, monkeypatch):
    """The gap this closes reopens after every reply - a stray byte between
    request N's answer and request N+1 is exactly as capable of corrupting
    N+1 as one before request 1."""
    cleared: list[QSerialPort.Direction] = []
    monkeypatch.setattr(QSerialPort, "isOpen", lambda self: True)
    monkeypatch.setattr(
        QSerialPort,
        "clear",
        lambda self, direction=QSerialPort.Direction.AllDirections: (
            cleared.append(direction) or True
        ),
    )
    monkeypatch.setattr(QSerialPort, "write", lambda self, data: len(data))

    transport = QSerialPortTransport("COM_FAKE")
    transport.send(b"\x01")
    transport.send(b"\x02")
    transport.send(b"\x03")

    assert cleared == [QSerialPort.Direction.Input] * 3


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
