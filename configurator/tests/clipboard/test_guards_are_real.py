"""Проверка того, что защитные условия действительно защищают.

Тест, который проходит и с удалённым условием, не тестирует ничего. Здесь
каждое условие подменяется на пропускающее, и соответствующая проверка обязана
упасть. Если она не падает - виновата проверка, а не этот файл.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from duo_input.clipboard.backend import ClipboardSnapshot
from duo_input.clipboard.offer import ClipboardOffer, describe
from duo_input.clipboard.service import ClipboardService

OURS = "1" * 32
THEIRS = "2" * 32


class _Backend:
    def __init__(self) -> None:
        self.published = []

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def publish(self, offer, fetcher) -> bool:
        self.published.append(offer)
        return True

    def payload(self, mime):
        return None


def test_without_the_second_belt_the_clipboard_would_echo(monkeypatch):
    service = ClipboardService(OURS)
    service.attach_backend(_Backend())
    service.on_remote_offer(ClipboardOffer(THEIRS, 1, describe({"text/plain": b"remote"})))

    monkeypatch.setattr(ClipboardService, "_echoes_what_we_received", lambda self, snapshot: False)

    sent = []
    service.offer_ready.connect(sent.append)
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"remote"}))

    assert sent, "второй пояс снят, а эха нет - значит проверка эха ничего не проверяет"


def test_without_the_third_belt_our_own_offer_would_come_back(monkeypatch):
    service = ClipboardService(OURS)
    backend = _Backend()
    service.attach_backend(backend)

    original = ClipboardService.on_remote_offer

    def without_origin_check(self, offer):
        return original(self, ClipboardOffer(THEIRS, offer.seq, offer.descriptors))

    monkeypatch.setattr(ClipboardService, "on_remote_offer", without_origin_check)
    service.on_remote_offer(ClipboardOffer(OURS, 9, describe({"text/plain": b"boomerang"})))

    assert backend.published, "третий пояс снят, а объявление не прошло - проверка мертва"


def test_the_size_ceiling_is_the_reason_a_huge_payload_is_refused():
    from duo_input.clipboard.offer import MAX_CONTENT_BYTES

    with pytest.raises(ValueError):
        describe({"image/png": b"x" * (MAX_CONTENT_BYTES + 1)})

    describe({"image/png": b"x" * MAX_CONTENT_BYTES})
