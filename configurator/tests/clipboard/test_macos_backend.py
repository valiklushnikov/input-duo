"""State-machine подавления петель на macOS и правила приватности."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from duo_input.clipboard.backend import ORIGIN_MIME, ClipboardSnapshot
from duo_input.clipboard.macos_backend import (
    MacOSClipboardBackend,
    is_private,
    snapshot_from,
)
from duo_input.clipboard.offer import ClipboardOffer, describe


class _FakePasteboard:
    """Инъектируемая замена macos_pasteboard: без AppKit."""

    def __init__(self, count: int = 10) -> None:
        self._count = count
        self.published: list[ClipboardOffer] = []
        self._concealed = False

    def change_count(self) -> int:
        return self._count

    def set_count(self, value: int) -> None:
        self._count = value

    def is_concealed(self) -> bool:
        return self._concealed

    def set_concealed(self, value: bool) -> None:
        self._concealed = value

    def publish_with_origin(self, offer, fetcher) -> int:
        self.published.append(offer)
        self._count += 1
        return self._count


class _FakeMimeData:
    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads

    def formats(self) -> list[str]:
        return list(self._payloads)

    def hasFormat(self, mime: str) -> bool:  # noqa: N802 - Qt API
        return mime in self._payloads

    def data(self, mime: str) -> bytes:
        return self._payloads.get(mime, b"")

    def hasUrls(self) -> bool:  # noqa: N802 - Qt API
        return False

    def urls(self) -> list:
        return []

    def hasImage(self) -> bool:  # noqa: N802 - Qt API
        return False


class _FakeClipboard:
    def __init__(self, mime_data: _FakeMimeData) -> None:
        self._mime_data = mime_data

    def mimeData(self):  # noqa: N802 - Qt API
        return self._mime_data


def _backend(count=10, payloads=None) -> tuple[MacOSClipboardBackend, _FakePasteboard]:
    pasteboard = _FakePasteboard(count)
    clipboard = _FakeClipboard(_FakeMimeData(payloads or {"text/plain": b"hello"}))
    backend = MacOSClipboardBackend(clipboard, pasteboard=pasteboard)
    return backend, pasteboard


def test_a_concealed_local_change_is_never_snapshotted():
    """Qt на macOS не отдаёт org.nspasteboard.ConcealedType через formats() -
    единственный источник правды здесь нативный is_concealed() у pasteboard.

    changeCount меняется как при обычном копировании, но нативный гейт в
    _poll обязан остановить снятие снапшота ДО чтения mimeData().
    """
    backend, pasteboard = _backend(count=10)
    emitted: list = []
    backend.snapshot_taken.connect(emitted.append)
    backend.start()

    pasteboard.set_concealed(True)
    pasteboard.set_count(11)
    backend._poll()

    assert emitted == []


def test_is_private_respects_our_own_origin():
    assert is_private(["text/plain", ORIGIN_MIME]) is True


def test_an_ordinary_clipboard_is_not_private():
    assert is_private(["text/plain", "text/html"]) is False


def test_start_takes_a_baseline_and_does_not_emit():
    backend, pasteboard = _backend(count=10)
    emitted: list = []
    backend.snapshot_taken.connect(emitted.append)

    backend.start()
    backend._poll()  # первый опрос с тем же count

    assert emitted == []


def test_a_local_change_emits_a_snapshot():
    backend, pasteboard = _backend(count=10)
    emitted: list = []
    backend.snapshot_taken.connect(emitted.append)
    backend.start()

    pasteboard.set_count(11)
    backend._poll()

    assert len(emitted) == 1
    assert emitted[0].payloads["text/plain"] == b"hello"


def test_an_unchanged_count_does_nothing():
    backend, pasteboard = _backend(count=11)
    emitted: list = []
    backend.snapshot_taken.connect(emitted.append)
    backend.start()

    backend._poll()

    assert emitted == []


def test_our_own_publish_is_suppressed():
    backend, pasteboard = _backend(count=11)
    emitted: list = []
    backend.snapshot_taken.connect(emitted.append)
    backend.start()

    offer = ClipboardOffer("b" * 32, 1, describe({"text/plain": b"peer"}))
    backend.publish(offer, lambda mime: b"peer")
    backend._poll()  # видит наш собственный changeCount

    assert emitted == []


def test_a_local_copy_after_our_publish_is_not_suppressed():
    backend, pasteboard = _backend(count=11)
    emitted: list = []
    backend.snapshot_taken.connect(emitted.append)
    backend.start()

    offer = ClipboardOffer("b" * 32, 1, describe({"text/plain": b"peer"}))
    backend.publish(offer, lambda mime: b"peer")  # own -> count 12
    pasteboard.set_count(13)  # пользователь скопировал что-то своё
    backend._poll()

    assert len(emitted) == 1


def test_own_change_count_branch_suppresses_and_resets():
    """Прямое покрытие защитного пояса в _poll (current == own != last_seen).

    Даже если publish() перестанет обновлять _last_seen_change_count, опрос,
    видящий ровно наш собственный changeCount, обязан подавить эмиссию и
    сбросить _own_change_count в None - иначе своя же публикация утекла бы как
    локальное копирование.
    """
    backend, pasteboard = _backend(count=20)
    backend.start()
    backend._last_seen_change_count = 20
    backend._own_change_count = 25
    pasteboard.set_count(25)

    emitted: list = []
    backend.snapshot_taken.connect(emitted.append)
    backend._poll()

    assert emitted == []
    assert backend._own_change_count is None
    assert backend._last_seen_change_count == 25


def test_suppression_holds_without_any_origin_marker():
    """Главный инвариант: корректность держится на changeCount, не на MIME.

    FakeMimeData вообще не содержит ORIGIN_MIME, но own-publish обязан
    подавляться - потому что current == own_change_count.
    """
    backend, pasteboard = _backend(count=11)
    emitted: list = []
    backend.snapshot_taken.connect(emitted.append)
    backend.start()

    offer = ClipboardOffer("b" * 32, 1, describe({"text/plain": b"peer"}))
    backend.publish(offer, lambda mime: b"peer")
    backend._poll()

    assert emitted == []
    assert pasteboard.published == [offer]
