"""Три пояса подавления петель и правило свежести объявления."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from duo_input.clipboard.backend import ClipboardSnapshot
from duo_input.clipboard.offer import ClipboardOffer, describe
from duo_input.clipboard.service import ClipboardService

OURS = "1" * 32
THEIRS = "2" * 32


class _FakeBackend:
    """Backend, который ничего не делает и всё запоминает."""

    def __init__(self) -> None:
        self.published: list[ClipboardOffer] = []
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def publish(self, offer, fetcher) -> None:
        self.published.append(offer)

    def payload(self, mime):
        return b"hello" if mime == "text/plain" else None


def _service() -> tuple[ClipboardService, _FakeBackend]:
    service = ClipboardService(own_origin_id=OURS)
    backend = _FakeBackend()
    service.attach_backend(backend)
    return service, backend


def test_a_local_copy_produces_an_offer():
    service, _ = _service()
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"hello"}))

    assert len(sent) == 1
    assert sent[0].origin_id == OURS
    assert sent[0].mimes() == ("text/plain",)


def test_sequence_numbers_increase_with_every_offer():
    service, _ = _service()
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"one"}))
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"two"}))

    assert sent[1].seq > sent[0].seq


def test_a_remote_offer_is_published_to_the_local_clipboard():
    service, backend = _service()

    offer = ClipboardOffer(THEIRS, 1, describe({"text/plain": b"remote"}))
    service.on_remote_offer(offer)

    assert backend.published == [offer]


def test_second_belt_a_snapshot_matching_what_we_just_received_is_not_offered_back():
    service, _ = _service()
    service.on_remote_offer(ClipboardOffer(THEIRS, 1, describe({"text/plain": b"remote"})))
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"remote"}))

    assert sent == []


def test_a_genuinely_new_copy_after_a_remote_one_is_offered():
    service, _ = _service()
    service.on_remote_offer(ClipboardOffer(THEIRS, 1, describe({"text/plain": b"remote"})))
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"something else"}))

    assert len(sent) == 1


def test_third_belt_an_offer_bearing_our_own_origin_is_ignored():
    service, backend = _service()

    service.on_remote_offer(ClipboardOffer(OURS, 5, describe({"text/plain": b"boomerang"})))

    assert backend.published == []


def test_a_stale_sequence_number_is_ignored():
    service, backend = _service()
    service.on_remote_offer(ClipboardOffer(THEIRS, 5, describe({"text/plain": b"new"})))
    service.on_remote_offer(ClipboardOffer(THEIRS, 4, describe({"text/plain": b"old"})))

    assert len(backend.published) == 1


def test_content_for_answers_only_for_the_offer_we_last_sent():
    service, _ = _service()
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"hello"}))
    seq = sent[0].seq

    assert service.content_for("text/plain", seq) == b"hello"
    assert service.content_for("text/plain", seq + 1) is None
