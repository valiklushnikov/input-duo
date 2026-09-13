"""Что течёт по TLS-соединению между двумя копиями Duo Input.

Все кадры устроены одинаково - длина, тип, заголовок в JSON, сырые байты, - и
разбор не зависит от типа кадра. У всех сообщений, кроме CONTENT, сырая часть
пуста; у CONTENT она и есть содержимое буфера.

Контрольной суммы здесь нет намеренно: целостность даёт TLS, а вторая проверка
поверх неё создала бы впечатление, что канал без TLS тоже допустим.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum

from .offer import MAX_CONTENT_BYTES

PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 0

#: Потолок содержимого плюс место под заголовок.
MAX_FRAME_BYTES = MAX_CONTENT_BYTES + 65_536

#: Потолок одного FILE_CHUNK: 1 МиБ.
#:
#: Отдельная величина от MAX_FRAME_BYTES намеренно. Потолок кадра существует,
#: чтобы испорченное поле длины не заставило нас выделить гигабайт; потолок
#: чанка существует, чтобы ограничить память под передачу. Одно число вместо
#: двух означало бы, что один FILE_CHUNK вправе нести 32 МиБ.
MAX_FILE_CHUNK_BYTES = 1_048_576

_LENGTH_BYTES = 4
_TYPE_BYTES = 1
_HEADER_LENGTH_BYTES = 2


class WireError(Exception):
    """Кадр, которого не могло прислать исправное второе устройство."""


class MessageType(IntEnum):
    HELLO = 1
    OFFER = 2
    FETCH = 3
    CONTENT = 4
    CONTENT_ERROR = 5
    PING = 6
    PONG = 7
    PAIR_REQUEST = 8
    PAIR_CONFIRM = 9
    # Передача файлов. Отдельная логическая подсистема поверх того же кадра -
    # framing к типу сообщения безразличен, о чём сказано в docstring модуля.
    #
    # Старый пир, получив любой из этих типов, бросит WireError и оборвёт
    # соединение целиком, вместе с буфером обмена. Поэтому они не отправляются
    # никому, кто не объявил files/1 в HELLO - см. coordinator.CAPABILITIES.
    FILE_OFFER = 10
    TRANSFER_BEGIN = 11
    FILE_READ = 12
    FILE_CHUNK = 13
    FILE_ERROR = 14
    TRANSFER_END = 15


@dataclass(frozen=True)
class Message:
    type: MessageType
    header: dict = field(default_factory=dict)
    blob: bytes = b""


def encode(message: Message) -> bytes:
    header = json.dumps(message.header, ensure_ascii=False).encode("utf-8")
    body = len(header).to_bytes(_HEADER_LENGTH_BYTES, "big") + header + message.blob
    payload = bytes([int(message.type)]) + body
    return len(payload).to_bytes(_LENGTH_BYTES, "big") + payload


class FrameAssembler:
    """Собирает кадры из потока байтов, как они приходят из сокета."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[Message]:
        self._buffer.extend(chunk)
        messages: list[Message] = []
        while True:
            if len(self._buffer) < _LENGTH_BYTES:
                return messages
            length = int.from_bytes(self._buffer[:_LENGTH_BYTES], "big")
            if length > MAX_FRAME_BYTES:
                raise WireError(f"объявленная длина кадра {length} больше допустимой")
            if len(self._buffer) < _LENGTH_BYTES + length:
                return messages
            payload = bytes(self._buffer[_LENGTH_BYTES : _LENGTH_BYTES + length])
            del self._buffer[: _LENGTH_BYTES + length]
            messages.append(_decode_payload(payload))


def _decode_payload(payload: bytes) -> Message:
    if len(payload) < _TYPE_BYTES + _HEADER_LENGTH_BYTES:
        raise WireError("кадр короче собственного заголовка")
    try:
        message_type = MessageType(payload[0])
    except ValueError as error:
        raise WireError(f"неизвестный тип сообщения {payload[0]}") from error

    start = _TYPE_BYTES
    header_length = int.from_bytes(payload[start : start + _HEADER_LENGTH_BYTES], "big")
    header_start = start + _HEADER_LENGTH_BYTES
    header_end = header_start + header_length
    if header_end > len(payload):
        raise WireError("заголовок не помещается в кадр")

    try:
        header = json.loads(payload[header_start:header_end].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise WireError(f"заголовок не разбирается: {error}") from error
    if not isinstance(header, dict):
        raise WireError("заголовок не является объектом")

    return Message(message_type, header, payload[header_end:])


__all__ = [
    "MAX_FILE_CHUNK_BYTES",
    "MAX_FRAME_BYTES",
    "PROTOCOL_MAJOR",
    "PROTOCOL_MINOR",
    "FrameAssembler",
    "Message",
    "MessageType",
    "WireError",
    "encode",
]
