"""Нативная обёртка pasteboard. Требует настоящей сессии macOS и pyobjc."""

from __future__ import annotations

import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "darwin", reason="нативный pasteboard есть только на macOS"
)

pytest.importorskip("AppKit")

from duo_input.clipboard.macos_pasteboard import (  # noqa: E402
    change_count,
    is_concealed,
    publish_with_origin,
)
from duo_input.clipboard.offer import ClipboardOffer, describe  # noqa: E402

from AppKit import NSPasteboard, NSPasteboardItem  # noqa: E402
from Foundation import NSData  # noqa: E402


def _write_native(uti_to_bytes: dict[str, bytes]) -> None:
    """Записать элемент напрямую через NSPasteboard, в обход macos_pasteboard.

    is_concealed() обязан видеть маркер конвенции nspasteboard.org даже когда
    его положил не наш код - Qt эти UTI на macOS вообще не отдаёт, поэтому
    единственный надёжный источник правды - сам NSPasteboard.types().
    """
    pasteboard = NSPasteboard.generalPasteboard()
    pasteboard.clearContents()
    item = NSPasteboardItem.alloc().init()
    for uti, payload in uti_to_bytes.items():
        item.setData_forType_(NSData.dataWithBytes_length_(payload, len(payload)), uti)
    assert pasteboard.writeObjects_([item])


def _offer() -> ClipboardOffer:
    return ClipboardOffer("a" * 32, 7, describe({"text/plain": b"lazy from peer"}))


def test_change_count_is_an_int():
    assert isinstance(change_count(), int)


def test_change_count_grows_after_a_native_write():
    before = change_count()
    subprocess.run(["/usr/bin/pbcopy"], input=b"nudge", check=True)

    assert change_count() != before


def test_publish_returns_the_authoritative_change_count():
    def fetch(mime: str) -> bytes:
        return b"lazy from peer" if mime == "text/plain" else b""

    count = publish_with_origin(_offer(), fetch)

    # Возвращённое значение должно совпадать с итоговым changeCount буфера,
    # иначе подавление петли примет нашу публикацию за локальное копирование.
    assert count == change_count()


def test_publish_serves_the_fetcher_lazily_to_an_external_reader():
    def fetch(mime: str) -> bytes:
        return b"lazy from peer" if mime == "text/plain" else b""

    publish_with_origin(_offer(), fetch)

    # Первый внешний читатель материализует ленивый payload. Если бы provider
    # не удерживался живым внутри macos_pasteboard, здесь была бы пустота.
    #
    # pboard-сервер доставляет provideDataForType_ обратно в этот процесс
    # через CFRunLoop. В настоящем приложении цикл событий Qt крутится
    # непрерывно и обслуживает такие запросы сам; headless pytest-процесс
    # своего run loop не крутит, поэтому здесь он покручен вручную, пока
    # внешний pbpaste ждёт ответа - иначе запрос до provider'а не дойдёт и
    # тест завис бы, а не просто увидел пустоту.
    from Foundation import NSDate, NSRunLoop

    process = subprocess.Popen(["/usr/bin/pbpaste"], stdout=subprocess.PIPE)
    deadline = time.monotonic() + 5
    while process.poll() is None and time.monotonic() < deadline:
        NSRunLoop.currentRunLoop().runUntilDate_(NSDate.dateWithTimeIntervalSinceNow_(0.05))
    pasted, _ = process.communicate(timeout=5)

    assert pasted == b"lazy from peer"


def test_is_concealed_is_true_for_a_natively_marked_concealed_item():
    _write_native(
        {
            "public.utf8-plain-text": b"secret",
            "org.nspasteboard.ConcealedType": b"",
        }
    )

    assert is_concealed() is True


def test_is_concealed_is_true_for_a_natively_marked_transient_item():
    _write_native(
        {
            "public.utf8-plain-text": b"one-shot",
            "org.nspasteboard.TransientType": b"",
        }
    )

    assert is_concealed() is True


def test_is_concealed_is_false_for_a_plain_text_only_item():
    _write_native({"public.utf8-plain-text": b"nothing to hide"})

    assert is_concealed() is False
