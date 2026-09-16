"""Что уходит на второй компьютер при копировании файлов - и что не уходит.

Манифест описывает дерево и не содержит ни байта его содержимого: это та же
ленивая модель, что и у объявления буфера обмена. Абсолютных путей здесь нет
вовсе - получатель сам выбирает, куда писать, и отправитель не имеет права
этого диктовать.

Разделитель пути всегда "/", даже когда снимок снят на Windows. Обратный слэш
появляется ровно в одном месте - при сборке cFileName для Проводника, - и
благодаря этому манифест от платформы не зависит.

Проверка типов здесь строгая и не приводящая, ровно как в clipboard/offer.py:
bool исключён из int, потому что size=True, молча ставшее size=1, дало бы
усечённый файл без единой ошибки в журнале.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

ENTRY_FILE = "file"
ENTRY_DIRECTORY = "directory"

_KINDS = frozenset({ENTRY_FILE, ENTRY_DIRECTORY})

#: Потолок любого целого, пришедшего от пира. JSON целые не ограничивает, а
#: дальше они уходят в lseek, в FILETIME и в qlonglong - и 10**30 поднял бы
#: OverflowError там, где протокольную ошибку уже никто не ловит.
MAX_WIRE_INTEGER = (1 << 63) - 1

#: transfer_id - короткий ASCII-токен. Он становится маркером происхождения в
#: буфере обмена (ASCII) и попадает в журнал; своё мы делаем из uuid4().hex.
MAX_TRANSFER_ID_CHARS = 64
_TRANSFER_ID = re.compile(r"[0-9A-Za-z_-]{1,%d}" % MAX_TRANSFER_ID_CHARS)

#: Потолок манифеста в теле FILE_OFFER. Обычная запись - около ста байт, так
#: что 65 536 записей (paths.MAX_ENTRIES) занимают порядка шести мегабайт;
#: потолок оставляет место длинным именам и при этом кладёт границу на то,
#: что json.loads вообще согласится разбирать.
MAX_MANIFEST_BYTES = 16 * 1024 * 1024


def require_transfer_id(value) -> str:
    """``value``, если это допустимый transfer_id, иначе ``ValueError``."""
    if not isinstance(value, str) or _TRANSFER_ID.fullmatch(value) is None:
        raise ValueError("transfer_id должен быть коротким ASCII-токеном")
    return value


def _require_str(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} должна быть str")
    return value


def _require_index(raw: dict, key: str) -> int:
    value = raw.get(key)
    # bool - подтип int, а size=True не должен сойти за size=1.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} должна быть int")
    if value < 0:
        raise ValueError(f"{key} не может быть отрицательной")
    if value > MAX_WIRE_INTEGER:
        raise ValueError(f"{key} больше допустимого")
    return value


@dataclass(frozen=True)
class TransferEntry:
    """Одна запись дерева: чем является, сколько весит, когда изменена."""

    path: str
    kind: str
    size: int
    mtime_ns: int

    def to_dict(self) -> dict:
        return {"path": self.path, "kind": self.kind, "size": self.size, "mtime_ns": self.mtime_ns}

    @classmethod
    def from_dict(cls, raw: dict) -> TransferEntry:
        if not isinstance(raw, dict):
            raise ValueError("запись должна быть dict")
        kind = _require_str(raw, "kind")
        if kind not in _KINDS:
            raise ValueError(f"неизвестный вид записи {kind!r}")
        return cls(
            path=_require_str(raw, "path"),
            kind=kind,
            size=_require_index(raw, "size"),
            mtime_ns=_require_index(raw, "mtime_ns"),
        )


@dataclass(frozen=True)
class SkippedEntry:
    """То, что осознанно не передаётся - и почему.

    Существует, чтобы получатель мог СКАЗАТЬ пользователю о пропуске. Молча
    неполное дерево хуже, чем неполное дерево с объяснением.
    """

    path: str
    reason: str

    def to_dict(self) -> dict:
        return {"path": self.path, "reason": self.reason}

    @classmethod
    def from_dict(cls, raw: dict) -> SkippedEntry:
        if not isinstance(raw, dict):
            raise ValueError("пропуск должен быть dict")
        return cls(path=_require_str(raw, "path"), reason=_require_str(raw, "reason"))


@dataclass(frozen=True)
class TransferManifest:
    """Неизменяемое описание одной операции копирования."""

    transfer_id: str
    entries: tuple[TransferEntry, ...]
    skipped: tuple[SkippedEntry, ...] = field(default=())
    #: DROPEFFECT_COPY. Хранится для диагностики; получатель всегда копирует.
    drop_effect: int = 1

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries if entry.kind == ENTRY_FILE)

    def to_dict(self) -> dict:
        return {
            "transfer_id": self.transfer_id,
            "entries": [entry.to_dict() for entry in self.entries],
            "skipped": [skip.to_dict() for skip in self.skipped],
            "total_bytes": self.total_bytes,
            "drop_effect": self.drop_effect,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> TransferManifest:
        if not isinstance(raw, dict):
            raise ValueError("манифест должен быть dict")
        entries = raw.get("entries")
        if not isinstance(entries, list):
            raise ValueError("entries должна быть list")
        skipped = raw.get("skipped", [])
        if not isinstance(skipped, list):
            raise ValueError("skipped должна быть list")
        return cls(
            transfer_id=require_transfer_id(raw.get("transfer_id")),
            entries=tuple(TransferEntry.from_dict(entry) for entry in entries),
            skipped=tuple(SkippedEntry.from_dict(skip) for skip in skipped),
            drop_effect=_require_index(raw, "drop_effect") if "drop_effect" in raw else 1,
        )


def encode_manifest(manifest: TransferManifest) -> bytes:
    """Тело FILE_OFFER. Размер проверяет отправитель - см. MAX_MANIFEST_BYTES."""
    return json.dumps(manifest.to_dict(), ensure_ascii=False).encode("utf-8")


def decode_manifest(blob: bytes) -> TransferManifest:
    """Манифест из тела FILE_OFFER - или ``ValueError``, и ничего другого.

    Длина проверяется ДО разбора: границу на работу json.loads кладёт именно
    она. RecursionError - не ValueError, а "[[[[..." законно его поднимает.
    """
    if len(blob) > MAX_MANIFEST_BYTES:
        raise ValueError("манифест больше допустимого")
    try:
        raw = json.loads(blob.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise ValueError("манифест не разбирается") from error
    return TransferManifest.from_dict(raw)


__all__ = [
    "ENTRY_DIRECTORY",
    "ENTRY_FILE",
    "MAX_MANIFEST_BYTES",
    "MAX_TRANSFER_ID_CHARS",
    "MAX_WIRE_INTEGER",
    "SkippedEntry",
    "TransferEntry",
    "TransferManifest",
    "decode_manifest",
    "encode_manifest",
    "require_transfer_id",
]
