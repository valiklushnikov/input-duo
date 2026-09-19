"""Запись реплики поколения (generation) - общий контракт Python/Swift.

Python владеет манифестом и генерациями авторитетно; Swift-расширение хранит
их durable-реплику в своём песочном контейнере (Task 4). Обе стороны обязаны
собирать/разбирать один и тот же JSON байт-в-байт - отсюда золотой вектор в
``tests/transfer/fixtures/generation_record.json``, который читают и
питоновский, и Swift-тест.

Реплика - только метаданные: ``schema``, ``transfer_id``, ``state``,
``created_ns``, ``lease_deadline_ns`` и ``manifest`` (= ``manifest.to_dict()``).
Ни байта содержимого, ни состояния FILE_READ/FILE_CHUNK, ни peer/TLS, ни
SnapshotRegistry, ни авторизации здесь нет и не будет - это забота других
частей системы, не replica.

Модуль обязан оставаться Qt-free (см. test_boundary_shared_modules_stay_qt_free)
- как и model.py, он может понадобиться где угодно по обе стороны барьера.
"""

from __future__ import annotations

import json

from duo_input.transfer.model import TransferManifest

#: Версия схемы записи реплики. Меняется только вместе с bump на обеих
#: сторонах (Python builder + Swift ReplicaStore) - см. ADR в task-4 brief.
REPLICA_SCHEMA_VERSION = 1

STATE_ACTIVE = "active"
STATE_RETIRED = "retired"

_STATES = frozenset({STATE_ACTIVE, STATE_RETIRED})


def build_generation_record(
    manifest: TransferManifest,
    *,
    state: str,
    created_ns: int,
    lease_deadline_ns: int,
) -> bytes:
    """Канонические байты записи реплики для одного поколения.

    Канонический вид - ``sort_keys=True, ensure_ascii=False``, UTF-8: это
    ровно то, что должен произвести и Swift ReplicaStore при перезаписи
    (retire), иначе золотой вектор разойдётся между сторонами.
    """
    if state not in _STATES:
        raise ValueError(f"неизвестное состояние реплики {state!r}")
    record = {
        "schema": REPLICA_SCHEMA_VERSION,
        "transfer_id": manifest.transfer_id,
        "state": state,
        "created_ns": created_ns,
        "lease_deadline_ns": lease_deadline_ns,
        "manifest": manifest.to_dict(),
    }
    return json.dumps(record, sort_keys=True, ensure_ascii=False).encode("utf-8")


__all__ = [
    "REPLICA_SCHEMA_VERSION",
    "STATE_ACTIVE",
    "STATE_RETIRED",
    "build_generation_record",
]
