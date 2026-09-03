"""Как два компьютера становятся друг для друга своими.

Код сравнивается, а не вводится. Код, который человек вводит, надо где-то
показать, и он превращается в секрет, который надо защищать. Сравнение секретом
не является: атакующий, вклинившийся посередине, предъявит сторонам разные
сертификаты, а значит - разные коды, и человек это увидит.

Отпечатки сортируются, потому что иначе две стороны получили бы разные числа из
одной и той же пары.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .trust import TrustedPeer

#: Сколько длится открытое окно парринга.
PAIRING_WINDOW_MS = 120_000

_CODE_DIGITS = 6
_CODE_MODULUS = 10**_CODE_DIGITS


def pairing_code(one: str, two: str) -> str:
    """Шестизначный код, одинаковый на обеих машинах."""
    joined = "".join(sorted((one, two))).encode("ascii")
    digest = hashlib.sha256(joined).digest()
    value = int.from_bytes(digest[:8], "big") % _CODE_MODULUS
    return str(value).zfill(_CODE_DIGITS)


@dataclass(frozen=True)
class PairingCandidate:
    """Компьютер, который представился, но ещё не подтверждён человеком."""

    origin_id: str
    machine_name: str
    fingerprint: str
    address: str
    port: int

    def as_trusted(self) -> TrustedPeer:
        return TrustedPeer(
            origin_id=self.origin_id,
            machine_name=self.machine_name,
            fingerprint=self.fingerprint,
            last_address=self.address,
        )


__all__ = ["PAIRING_WINDOW_MS", "PairingCandidate", "pairing_code"]
