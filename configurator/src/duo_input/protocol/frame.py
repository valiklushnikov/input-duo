"""Strict CDC and SPI v1 frame codecs."""

from dataclasses import dataclass

from duo_input.generated.protocol import (
    CDC_MAX_PAYLOAD,
    PROTOCOL_VERSION_MAJOR,
    PROTOCOL_VERSION_MINOR,
    SPI_FRAME_SIZE,
    CdcMessageType,
    SpiMessageType,
)

from .cobs import cobs_decode, cobs_encode
from .crc import crc16_ccitt, crc32_ieee


_CDC_HEADER_SIZE = 10
_CDC_CRC_SIZE = 4
_SPI_HEADER_SIZE = 10
_SPI_CRC_OFFSET = SPI_FRAME_SIZE - 2
_SPI_PAYLOAD_MAX = _SPI_CRC_OFFSET - _SPI_HEADER_SIZE


class FrameError(ValueError):
    """Raised when a frame violates the frozen v1 wire format."""


def _immutable_payload(payload: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(payload, (bytes, bytearray, memoryview)):
        raise TypeError("payload must be bytes-like")
    return bytes(payload)


@dataclass(frozen=True)
class CdcFrame:
    type: CdcMessageType
    sequence: int
    payload: bytes | bytearray | memoryview
    minor: int = PROTOCOL_VERSION_MINOR
    flags: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _immutable_payload(self.payload))


@dataclass(frozen=True)
class SpiFrame:
    type: SpiMessageType
    sequence: int
    payload: bytes | bytearray | memoryview
    minor: int = PROTOCOL_VERSION_MINOR
    flags: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "payload", _immutable_payload(self.payload))


def is_minor_compatible(
    minor: int, required_capabilities: int = 0, supported_capabilities: int = 0
) -> bool:
    """Return whether a frame minor can run with the negotiated capabilities."""
    return minor == PROTOCOL_VERSION_MINOR or (
        required_capabilities & ~supported_capabilities
    ) == 0


def _require_u8(value: int, name: str) -> None:
    if not isinstance(value, int) or not 0 <= value <= 0xFF:
        raise FrameError(f"invalid {name}")


def _require_u16(value: int, name: str) -> None:
    if not isinstance(value, int) or not 0 <= value <= 0xFFFF:
        raise FrameError(f"invalid {name}")


def _validate_cdc_frame(frame: CdcFrame) -> None:
    if not isinstance(frame, CdcFrame):
        raise TypeError("frame must be CdcFrame")
    _require_u8(frame.minor, "minor")
    _require_u16(frame.sequence, "sequence")
    if frame.flags != 0:
        raise FrameError("invalid flags")
    if not isinstance(frame.type, CdcMessageType):
        raise FrameError("invalid type")
    if len(frame.payload) > CDC_MAX_PAYLOAD:
        raise FrameError("invalid payload length")


def _validate_spi_frame(frame: SpiFrame) -> None:
    if not isinstance(frame, SpiFrame):
        raise TypeError("frame must be SpiFrame")
    _require_u8(frame.minor, "minor")
    _require_u16(frame.sequence, "sequence")
    if frame.flags != 0:
        raise FrameError("invalid flags")
    if not isinstance(frame.type, SpiMessageType):
        raise FrameError("invalid type")
    if len(frame.payload) > _SPI_PAYLOAD_MAX:
        raise FrameError("invalid payload length")


def encode_cdc_frame(frame: CdcFrame) -> bytes:
    _validate_cdc_frame(frame)
    raw = bytearray((ord("D"), ord("I"), PROTOCOL_VERSION_MAJOR, frame.minor, int(frame.type), frame.flags))
    raw.extend(frame.sequence.to_bytes(2, "little"))
    raw.extend(len(frame.payload).to_bytes(2, "little"))
    raw.extend(frame.payload)
    raw.extend(crc32_ieee(raw).to_bytes(_CDC_CRC_SIZE, "little"))
    return cobs_encode(raw) + b"\0"


def decode_cdc_frame(
    transport: bytes | bytearray | memoryview,
    required_capabilities: int = 0,
    supported_capabilities: int = 0,
) -> CdcFrame:
    transport = _immutable_payload(transport)
    if not transport or transport[-1] != 0 or 0 in transport[:-1]:
        raise FrameError("invalid CDC delimiter")
    try:
        raw = cobs_decode(transport[:-1])
    except ValueError as error:
        raise FrameError("malformed COBS frame") from error
    if len(raw) < _CDC_HEADER_SIZE + _CDC_CRC_SIZE:
        raise FrameError("invalid CDC length")
    if raw[:2] != b"DI":
        raise FrameError("invalid CDC magic")
    if raw[2] != PROTOCOL_VERSION_MAJOR:
        raise FrameError("incompatible CDC major")
    if not is_minor_compatible(raw[3], required_capabilities, supported_capabilities):
        raise FrameError("incompatible CDC minor")
    try:
        frame_type = CdcMessageType(raw[4])
    except ValueError as error:
        raise FrameError("invalid CDC type") from error
    if raw[5] != 0:
        raise FrameError("invalid CDC flags")
    payload_size = int.from_bytes(raw[8:10], "little")
    if payload_size > CDC_MAX_PAYLOAD or len(raw) != _CDC_HEADER_SIZE + payload_size + _CDC_CRC_SIZE:
        raise FrameError("invalid CDC length")
    if int.from_bytes(raw[-_CDC_CRC_SIZE:], "little") != crc32_ieee(raw[:-_CDC_CRC_SIZE]):
        raise FrameError("invalid CDC CRC")
    return CdcFrame(frame_type, int.from_bytes(raw[6:8], "little"), raw[10:-4], raw[3], raw[5])


def encode_spi_frame(frame: SpiFrame) -> bytes:
    _validate_spi_frame(frame)
    encoded = bytearray(SPI_FRAME_SIZE)
    encoded[:6] = bytes((ord("D"), ord("S"), PROTOCOL_VERSION_MAJOR, frame.minor, int(frame.type), frame.flags))
    encoded[6:8] = frame.sequence.to_bytes(2, "little")
    encoded[8:10] = len(frame.payload).to_bytes(2, "little")
    encoded[_SPI_HEADER_SIZE : _SPI_HEADER_SIZE + len(frame.payload)] = frame.payload
    encoded[_SPI_CRC_OFFSET:] = crc16_ccitt(encoded[:_SPI_CRC_OFFSET]).to_bytes(2, "little")
    return bytes(encoded)


def decode_spi_frame(
    transport: bytes | bytearray | memoryview,
    required_capabilities: int = 0,
    supported_capabilities: int = 0,
) -> SpiFrame:
    transport = _immutable_payload(transport)
    if len(transport) != SPI_FRAME_SIZE:
        raise FrameError("invalid SPI size")
    if transport[:2] != b"DS":
        raise FrameError("invalid SPI magic")
    if transport[2] != PROTOCOL_VERSION_MAJOR:
        raise FrameError("incompatible SPI major")
    if not is_minor_compatible(transport[3], required_capabilities, supported_capabilities):
        raise FrameError("incompatible SPI minor")
    try:
        frame_type = SpiMessageType(transport[4])
    except ValueError as error:
        raise FrameError("invalid SPI type") from error
    if transport[5] != 0:
        raise FrameError("invalid SPI flags")
    payload_size = int.from_bytes(transport[8:10], "little")
    if payload_size > _SPI_PAYLOAD_MAX:
        raise FrameError("invalid SPI length")
    if any(transport[_SPI_HEADER_SIZE + payload_size : _SPI_CRC_OFFSET]):
        raise FrameError("invalid SPI padding")
    if int.from_bytes(transport[_SPI_CRC_OFFSET:], "little") != crc16_ccitt(transport[:_SPI_CRC_OFFSET]):
        raise FrameError("invalid SPI CRC")
    return SpiFrame(frame_type, int.from_bytes(transport[6:8], "little"), transport[10 : 10 + payload_size], transport[3], transport[5])
