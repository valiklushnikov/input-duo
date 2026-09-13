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

from dataclasses import dataclass, field

ENTRY_FILE = "file"
ENTRY_DIRECTORY = "directory"

_KINDS = frozenset({ENTRY_FILE, ENTRY_DIRECTORY})


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
            transfer_id=_require_str(raw, "transfer_id"),
            entries=tuple(TransferEntry.from_dict(entry) for entry in entries),
            skipped=tuple(SkippedEntry.from_dict(skip) for skip in skipped),
            drop_effect=_require_index(raw, "drop_effect") if "drop_effect" in raw else 1,
        )


__all__ = [
    "ENTRY_DIRECTORY",
    "ENTRY_FILE",
    "SkippedEntry",
    "TransferEntry",
    "TransferManifest",
]
