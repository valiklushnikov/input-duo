"""Кто нам свой. Ровно один компьютер, закреплённый отпечатком.

Файл повреждён - значит доверенного пира нет. Это единственный безопасный
ответ: догадываться о содержимом хранилища доверия означало бы принять
соединение, которое пользователь никогда не подтверждал.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class TrustedPeer:
    """Второй компьютер, подтверждённый пользователем на обеих машинах."""

    origin_id: str
    machine_name: str
    fingerprint: str
    last_address: str


class TrustStore:
    """Единственный доверенный пир, хранимый рядом с журналами."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def peer(self) -> TrustedPeer | None:
        try:
            raw = json.loads(self._path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        try:
            origin_id = raw["origin_id"]
            machine_name = raw["machine_name"]
            fingerprint = raw["fingerprint"]
            last_address = raw["last_address"]
        except KeyError:
            return None

        # Все поля должны быть непустыми строками, не другими типами
        if not isinstance(origin_id, str) or not origin_id:
            return None
        if not isinstance(machine_name, str) or not machine_name:
            return None
        if not isinstance(fingerprint, str) or not fingerprint:
            return None
        if not isinstance(last_address, str) or not last_address:
            return None

        return TrustedPeer(
            origin_id=origin_id,
            machine_name=machine_name,
            fingerprint=fingerprint,
            last_address=last_address,
        )

    def remember(self, peer: TrustedPeer) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(asdict(peer), indent=2), "utf-8")

    def forget(self) -> None:
        self._path.unlink(missing_ok=True)

    def update_address(self, address: str) -> None:
        current = self.peer()
        if current is None:
            return
        self.remember(replace(current, last_address=address))


__all__ = ["TrustStore", "TrustedPeer"]
