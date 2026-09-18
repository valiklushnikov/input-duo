"""Три пояса подавления петель и правило свежести объявления."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from duo_input.clipboard.backend import ClipboardSnapshot
from duo_input.clipboard.offer import MAX_CONTENT_BYTES, ClipboardOffer, describe
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


def test_an_oversized_snapshot_is_logged_and_not_offered(caplog):
    """I5: то же соглашение, что у платформенной границы - журнал, а не падение.

    describe() продолжает бросать ValueError для тех, кто сам ничего не
    отфильтровал; on_local_snapshot обязан поймать её и повести себя так же,
    как и обычный отказ от объявления, а не уронить слот, подключённый к
    сигналу Qt.
    """
    service, _ = _service()
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    with caplog.at_level("WARNING", logger="duo_input.clipboard.service"):
        service.on_local_snapshot(
            ClipboardSnapshot({"image/png": b"x" * (MAX_CONTENT_BYTES + 1)})
        )

    assert sent == []
    assert service.last_sent_offer is None
    assert caplog.records


def test_an_empty_snapshot_does_not_produce_an_offer():
    service, _ = _service()
    # Сначала отправляем нормальный snapshot, чтобы был last_sent_offer
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"hello"}))
    assert len(sent) == 1
    first_offer = sent[0]
    first_seq = first_offer.seq

    # Очищаем список отправленных объявлений
    sent.clear()

    # Отправляем пустой snapshot (буфер заперт другим процессом)
    service.on_local_snapshot(ClipboardSnapshot({}))

    # Проверяем, что:
    # 1. Сигнал offer_ready не испускается
    assert sent == []
    # 2. last_sent_offer остаётся прежним
    assert service.last_sent_offer == first_offer
    # 3. Номер объявления не увеличился (не расходует последовательность)
    assert service.last_sent_offer.seq == first_seq


def test_second_belt_entire_snapshot_is_echo_only_if_all_formats_match():
    service, _ = _service()
    # Принимаем объявление с двумя форматами
    service.on_remote_offer(
        ClipboardOffer(THEIRS, 1, describe({"text/plain": b"hello", "text/html": b"<p>hello</p>"}))
    )
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    # Снимок с обоими форматами, полностью совпадающий - это эхо
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"hello", "text/html": b"<p>hello</p>"}))
    assert sent == []


def test_second_belt_snapshot_with_one_new_format_is_not_echo():
    service, _ = _service()
    # Принимаем объявление с одним форматом
    service.on_remote_offer(ClipboardOffer(THEIRS, 1, describe({"text/plain": b"hello"})))
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    # Снимок с тем же текстом, но добавлен новый формат - это НОВАЯ копия, не эхо
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"hello", "image/png": b"new image"}))

    assert len(sent) == 1
    assert sent[0].mimes() == ("image/png", "text/plain")


def test_second_belt_snapshot_with_same_format_different_content_is_not_echo():
    service, _ = _service()
    # Принимаем объявление
    service.on_remote_offer(ClipboardOffer(THEIRS, 1, describe({"text/plain": b"hello"})))
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    # Снимок с тем же форматом, но другим содержимым - это НОВАЯ копия, не эхо
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"goodbye"}))

    assert len(sent) == 1
    assert sent[0].mimes() == ("text/plain",)


def test_second_belt_snapshot_with_subset_of_formats_all_matching_is_echo():
    service, _ = _service()
    # Принимаем объявление с тремя форматами
    service.on_remote_offer(
        ClipboardOffer(
            THEIRS,
            1,
            describe({"text/plain": b"hello", "text/html": b"<p>hello</p>", "text/rtf": b"{\\rtf hello}"}),
        )
    )
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    # Снимок с подмножеством форматов (text/plain и text/html), оба совпадают.
    # Это рассматривается как эхо: приложение-получатель потеряло маркер, но отдало
    # ровно те форматы, что мы ему отправили. Любое новое содержимое пользователя
    # должно отличаться хотя бы в одном формате, чтобы быть признанным новым.
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"hello", "text/html": b"<p>hello</p>"}))

    assert sent == []


def test_second_belt_snapshot_with_format_missing_from_received_is_not_echo():
    service, _ = _service()
    # Принимаем объявление с одним форматом
    service.on_remote_offer(ClipboardOffer(THEIRS, 1, describe({"text/plain": b"hello"})))
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    # Снимок имеет ДРУГОЙ формат, который НЕ был в принятом объявлении - это НОВАЯ копия, не эхо
    service.on_local_snapshot(ClipboardSnapshot({"application/json": b'{"key": "value"}'}))

    assert len(sent) == 1
    assert sent[0].mimes() == ("application/json",)


def test_second_belt_snapshot_mixing_old_and_new_formats_is_not_echo():
    service, _ = _service()
    # Принимаем объявление с одним форматом
    service.on_remote_offer(ClipboardOffer(THEIRS, 1, describe({"text/plain": b"hello"})))
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    # Снимок имеет оба формата: один совпадает, но один новый - это НОВАЯ копия, не эхо
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"hello", "image/png": b"new image"}))

    assert len(sent) == 1
    assert sent[0].mimes() == ("image/png", "text/plain")


def test_a_link_drop_resets_so_a_fresh_low_sequence_offer_is_accepted():
    # Регресс #3: второй компьютер перезапустился, его seq снова малый; связь
    # при этом оборвалась, поэтому свежее объявление обязано приниматься, а не
    # отвергаться как "устаревшее".
    service, backend = _service()
    service.on_remote_offer(ClipboardOffer(THEIRS, 5, describe({"text/plain": b"before"})))
    assert len(backend.published) == 1

    service._on_link_lost("второй компьютер перезапустился")

    service.on_remote_offer(ClipboardOffer(THEIRS, 1, describe({"text/plain": b"after"})))
    assert len(backend.published) == 2
