"""Asynchronous link implementations behind :class:`DeviceService`.

The service never talks to a serial port directly. It talks to a *link*: a
QObject that emits ``bytes_received`` whenever the device pushes bytes and
``link_lost`` when the connection dies. :class:`QSerialPortTransport` is the
production implementation; :class:`SynchronousTransportLink` bridges any
synchronous :class:`~duo_input.device.transport.AbstractByteTransport` (such as
the deterministic ``U1Emulator``) into the same asynchronous shape by handing
its reply back through the Qt event loop.
"""

from __future__ import annotations

from functools import partial
from typing import Protocol, runtime_checkable

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtSerialPort import QSerialPort

from .transport import AbstractByteTransport

DEFAULT_BAUD_RATE = 115200


@runtime_checkable
class DeviceLink(Protocol):
    """The only transport surface :class:`DeviceService` depends on."""

    bytes_received: Signal
    link_lost: Signal

    @property
    def is_open(self) -> bool: ...

    def open(self) -> bool: ...

    def close(self) -> None: ...

    def send(self, data: bytes) -> None: ...


class QSerialPortTransport(QObject):
    """A real CDC-ACM serial port, read incrementally and never blocking."""

    bytes_received = Signal(bytes)
    link_lost = Signal(str)

    def __init__(self, port_name: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._port_name = port_name
        self._port = QSerialPort(self)
        self._port.setPortName(port_name)
        self._port.setBaudRate(DEFAULT_BAUD_RATE)
        self._port.setDataBits(QSerialPort.DataBits.Data8)
        self._port.setParity(QSerialPort.Parity.NoParity)
        self._port.setStopBits(QSerialPort.StopBits.OneStop)
        self._port.setFlowControl(QSerialPort.FlowControl.NoFlowControl)
        self._port.readyRead.connect(self._on_ready_read)
        self._port.errorOccurred.connect(self._on_error_occurred)

    @property
    def port_name(self) -> str:
        return self._port_name

    @property
    def is_open(self) -> bool:
        return self._port.isOpen()

    def open(self) -> bool:
        if self._port.isOpen():
            return True
        if not self._port.open(QSerialPort.OpenModeFlag.ReadWrite):
            return False

        # DTR tells a CDC device that a program is listening, and QSerialPort
        # does not raise it on open. The U1 checks it before replying, so
        # without this the device receives every request and answers none -
        # which looks exactly like a device that is not there.
        #
        # The emulator has no control lines, so only a real port shows this.
        self._port.setDataTerminalReady(True)

        # A port can hold bytes that arrived before this process ever opened
        # it - the OS driver keeps reading and buffering a CDC-ACM device's
        # output as soon as it is enumerated, whether or not anything has
        # the port open. On the reference target that includes its own
        # plain-text trace: the device has no way to know a host is about to
        # start reading, and does not stop sending it until this transport's
        # own first request has reached it. Left in the buffer, those bytes
        # are read as the start of the first reply, and the frame decoder
        # calls it corrupt - measured on real hardware, not by inference.
        # Correct independent of the trace, too: a board that rebooted mid
        # conversation, or a reply this process never finished reading last
        # time, leaves exactly the same kind of stale bytes behind.
        self._port.clear(QSerialPort.Direction.Input)
        return True

    def close(self) -> None:
        if self._port.isOpen():
            self._port.close()

    def send(self, data: bytes) -> None:
        if not self._port.isOpen():
            self.link_lost.emit("serial port is not open")
            return
        # Deliberately no clear() here, unlike open(). This link is not
        # request/response only: CAPTURE_EVENT is device-initiated (see
        # docs/protocol/compatibility.md and ConfigService::emit_capture_event),
        # so the input buffer at this moment can hold a key the operator has
        # already pressed. The device has flipped its capture state off by
        # then and will never send it again, so a clear here loses that answer
        # for good. Bytes that are not frames are the stream reassembler's
        # problem, and it drops them without taking a frame with them.
        if self._port.write(bytes(data)) < 0:
            self.link_lost.emit(self._port.errorString())

    def _on_ready_read(self) -> None:
        data = bytes(self._port.readAll().data())
        if data:
            # Partial frames are retained by the service's FrameAssembler.
            self.bytes_received.emit(data)

    def _on_error_occurred(self, error: QSerialPort.SerialPortError) -> None:
        if error is QSerialPort.SerialPortError.NoError:
            return
        detail = self._port.errorString()
        self.close()
        self.link_lost.emit(detail)


class SynchronousTransportLink(QObject):
    """Adapts a synchronous byte transport to the asynchronous link boundary.

    Replies are delivered through ``QTimer.singleShot(0, ...)`` so the caller
    returns to the event loop between request and reply, exactly as a real
    serial port behaves. ``chunk_size`` splits each reply into several
    deliveries, which reproduces a frame arriving across multiple reads.
    """

    bytes_received = Signal(bytes)
    link_lost = Signal(str)

    def __init__(
        self,
        transport: AbstractByteTransport,
        chunk_size: int | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if chunk_size is not None and chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        self._transport = transport
        self._chunk_size = chunk_size
        self._closed = False

    @property
    def transport(self) -> AbstractByteTransport:
        return self._transport

    @property
    def is_open(self) -> bool:
        return self._transport.is_open and not self._closed

    def open(self) -> bool:
        self._closed = False
        self._transport.open()
        return self._transport.is_open

    def close(self) -> None:
        self._closed = True
        self._transport.close()

    def send(self, data: bytes) -> None:
        QTimer.singleShot(0, partial(self._deliver, bytes(data)))

    def poll(self) -> None:
        """Ask the transport for bytes the device produced on its own."""
        QTimer.singleShot(0, partial(self._deliver, b""))

    def _deliver(self, data: bytes) -> None:
        if self._closed:
            return
        try:
            response = self._transport.write(data)
        except (RuntimeError, OSError) as error:
            self._closed = True
            self.link_lost.emit(str(error))
            return
        if not self._transport.is_open:
            self._closed = True
            self.link_lost.emit("transport closed by the device")
            return
        for piece in self._split(response):
            if self._closed:
                return
            self.bytes_received.emit(piece)

    def _split(self, response: bytes) -> list[bytes]:
        if not response:
            return []
        if self._chunk_size is None:
            return [response]
        return [
            response[index : index + self._chunk_size]
            for index in range(0, len(response), self._chunk_size)
        ]


__all__ = [
    "DEFAULT_BAUD_RATE",
    "DeviceLink",
    "QSerialPortTransport",
    "SynchronousTransportLink",
]
