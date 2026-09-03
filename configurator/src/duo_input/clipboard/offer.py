"""Что уходит на второй компьютер при копировании - и что не уходит.

Объявление описывает содержимое и не содержит его. Это и есть ленивая модель:
пароль, который скопировали и не вставили, машину не покидает. Отпечаток здесь
нужен дважды - чтобы получатель мог узнать уже виденное, и чтобы отправитель
мог не объявлять обратно то, что сам только что принял.

Дескрипторы всегда упорядочены по имени формата, иначе две машины получили бы
разные объявления для одного и того же буфера.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: Больше этого не объявляется вовсе: 32 МиБ.
MAX_CONTENT_BYTES = 33_554_432


@dataclass(frozen=True)
class ContentDescriptor:
    """Один формат буфера: чем он является, сколько весит, чем является точно."""

    mime: str
    size: int
    sha256: str


def describe(payloads: dict[str, bytes]) -> tuple[ContentDescriptor, ...]:
    """Описать снимок буфера, не копируя его наружу."""
    descriptors = []
    for mime in sorted(payloads):
        payload = payloads[mime]
        if len(payload) > MAX_CONTENT_BYTES:
            raise ValueError(
                f"{mime} занимает {len(payload)} байт, потолок 32 МиБ ({MAX_CONTENT_BYTES})"
            )
        descriptors.append(
            ContentDescriptor(mime=mime, size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        )
    return tuple(descriptors)


@dataclass(frozen=True)
class ClipboardOffer:
    """Объявление о том, что на этой машине что-то скопировали."""

    origin_id: str
    seq: int
    descriptors: tuple[ContentDescriptor, ...]

    def mimes(self) -> tuple[str, ...]:
        return tuple(descriptor.mime for descriptor in self.descriptors)

    def digest_of(self, mime: str) -> str | None:
        for descriptor in self.descriptors:
            if descriptor.mime == mime:
                return descriptor.sha256
        return None

    def to_dict(self) -> dict:
        return {
            "origin_id": self.origin_id,
            "seq": self.seq,
            "descriptors": [
                {"mime": d.mime, "size": d.size, "sha256": d.sha256} for d in self.descriptors
            ],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> ClipboardOffer:
        try:
            if not isinstance(raw, dict):
                raise TypeError("raw должна быть dict")
            if not isinstance(raw.get("origin_id"), str):
                raise TypeError("origin_id должна быть str")
            if not isinstance(raw.get("seq"), int):
                raise TypeError("seq должна быть int")
            if not isinstance(raw.get("descriptors"), list):
                raise TypeError("descriptors должна быть list")

            descriptors = []
            for d in raw["descriptors"]:
                if not isinstance(d, dict):
                    raise TypeError("каждый дескриптор должен быть dict")
                if not isinstance(d.get("mime"), str):
                    raise TypeError("mime должна быть str")
                if not isinstance(d.get("size"), int):
                    raise TypeError("size должна быть int")
                if not isinstance(d.get("sha256"), str):
                    raise TypeError("sha256 должна быть str")
                descriptors.append(
                    ContentDescriptor(mime=d["mime"], size=d["size"], sha256=d["sha256"])
                )

            return cls(origin_id=raw["origin_id"], seq=raw["seq"], descriptors=tuple(descriptors))
        except (KeyError, TypeError) as error:
            raise ValueError(f"объявление неполно: {error}") from error


__all__ = ["MAX_CONTENT_BYTES", "ClipboardOffer", "ContentDescriptor", "describe"]
