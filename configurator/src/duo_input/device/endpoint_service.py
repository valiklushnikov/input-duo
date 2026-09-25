"""PC2's conversation with U2: this computer's addresses for PC1's.

U2 answers nothing else - it holds no configuration - so this is not a
DeviceService and never appears in the main window. It opens U2 when asked,
negotiates once, and from then on each ``exchange_addresses`` is one request
and one reply. A board that does not grant ADDRESS_EXCHANGE is closed and
never asked again; one that disappears is reopened on the next call.
"""

from __future__ import annotations

import logging
import struct
from collections.abc import Callable

from PySide6.QtCore import QObject, QTimer, Signal

from duo_input.generated.protocol import PROTOCOL_VERSION_MAJOR, Capability, CdcMessageType
from duo_input.protocol.frame import CdcFrame, encode_cdc_frame

from .host_addresses import decode_host_addresses, encode_host_addresses
from .transactions import (
    ErrorCode,
    FrameAssembler,
    FrameOverflowError,
    PayloadError,
    SequenceGenerator,
    parse_device_info,
    reply_error,
)

logger = logging.getLogger("duo_input.device.endpoint")


def default_link_factory():
    from .discovery import find_u2_ports
    from .qt_transport import QSerialPortTransport

    ports = find_u2_ports()
    return QSerialPortTransport(ports[0].port_name) if ports else None


class EndpointService(QObject):
    """Opens U2 on demand and exchanges this host's addresses for its peer's."""

    peer_addresses_received = Signal(list)

    def __init__(
        self,
        link_factory: Callable[[], object | None] | None = None,
        timeout_ms: int = 2000,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._factory = link_factory or default_link_factory
        self._link = None
        self._ready = False
        self._unsupported = False
        self._pending: tuple[int, CdcMessageType] | None = None
        self._queued_local: list[str] | None = None
        self._assembler = FrameAssembler()
        self._sequence = SequenceGenerator()
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(timeout_ms)
        self._timer.timeout.connect(lambda: self._close("no answer"))

    @property
    def unsupported(self) -> bool:
        return self._unsupported

    def exchange_addresses(self, local: list[str]) -> bool:
        if self._unsupported or self._pending is not None:
            return False
        if self._link is None:
            if not self._open():
                return False
            self._queued_local = list(local)
            return True
        if not self._ready:
            return False
        self._send(CdcMessageType.EXCHANGE_ADDRESSES, encode_host_addresses(local))
        return True

    def stop(self) -> None:
        self._close("")

    # ------------------------------------------------------------------ link

    def _open(self) -> bool:
        link = self._factory()
        if link is None:
            return False
        link.bytes_received.connect(self._on_bytes)
        link.link_lost.connect(self._close)
        if not link.open():
            link.bytes_received.disconnect(self._on_bytes)
            link.link_lost.disconnect(self._close)
            return False
        self._link = link
        self._ready = False
        self._assembler.clear()
        self._sequence = SequenceGenerator()
        self._send(CdcMessageType.HELLO, struct.pack("<I", int(Capability.ADDRESS_EXCHANGE)))
        return True

    def _close(self, reason: str = "") -> None:
        self._timer.stop()
        self._pending = None
        self._ready = False
        link, self._link = self._link, None
        if link is None:
            return
        if reason:
            logger.info("u2_link_closed reason=%s", reason)
        for signal, slot in ((link.bytes_received, self._on_bytes), (link.link_lost, self._close)):
            try:
                signal.disconnect(slot)
            except (RuntimeError, TypeError):  # pragma: no cover - already detached
                pass
        link.close()

    def _send(self, request: CdcMessageType, payload: bytes) -> None:
        sequence = self._sequence.next()
        reply = CdcMessageType.DEVICE_INFO if request is CdcMessageType.HELLO else request
        self._pending = (sequence, reply)
        self._timer.start()
        self._link.send(encode_cdc_frame(CdcFrame(request, sequence, payload)))

    def _on_bytes(self, data: bytes) -> None:
        try:
            scan = self._assembler.push(bytes(data))
        except FrameOverflowError:
            self._close("frame overflow")
            return
        for frame in scan.frames:
            self._on_frame(frame)
            if self._link is None:
                return

    def _on_frame(self, frame: CdcFrame) -> None:
        if self._pending is None or frame.sequence != self._pending[0]:
            return
        if frame.type is not self._pending[1]:
            self._close(f"unexpected {frame.type.name}")
            return
        self._timer.stop()
        self._pending = None
        payload = bytes(frame.payload)
        try:
            if frame.type is CdcMessageType.DEVICE_INFO:
                self._on_hello(payload)
            else:
                self._on_exchange(payload)
        except PayloadError as error:
            self._close(str(error))

    def _on_hello(self, payload: bytes) -> None:
        info = parse_device_info(payload)
        granted = info.capabilities & int(Capability.ADDRESS_EXCHANGE)
        if info.protocol_major != PROTOCOL_VERSION_MAJOR or not granted:
            logger.warning("U2 does not support address exchange")
            self._unsupported = True
            self._close("")
            return
        self._ready = True
        local, self._queued_local = self._queued_local, None
        if local is not None:
            self._send(CdcMessageType.EXCHANGE_ADDRESSES, encode_host_addresses(local))

    def _on_exchange(self, payload: bytes) -> None:
        if reply_error(payload) is not ErrorCode.OK:
            return
        self.peer_addresses_received.emit(decode_host_addresses(payload[1:]))


__all__ = ["EndpointService", "default_link_factory"]
