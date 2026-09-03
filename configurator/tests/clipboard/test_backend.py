"""Ленивое содержимое: данные тянутся в момент запроса, а не заранее."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from duo_input.clipboard.backend import ORIGIN_MIME, RemoteMimeData
from duo_input.clipboard.offer import ClipboardOffer, describe


def _offer() -> ClipboardOffer:
    return ClipboardOffer("a" * 32, 4, describe({"text/plain": "привет".encode("utf-8")}))


def test_formats_are_announced_without_fetching_anything(qapp):
    calls: list[str] = []
    data = RemoteMimeData(_offer(), calls.append)

    formats = data.formats()

    assert "text/plain" in formats
    assert calls == []


def test_the_origin_marker_is_announced_alongside_the_real_formats(qapp):
    data = RemoteMimeData(_offer(), lambda mime: b"")

    assert ORIGIN_MIME in data.formats()
    assert bytes(data.data(ORIGIN_MIME)) == b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:4"


def test_asking_for_a_format_fetches_it_once_and_caches_it(qapp):
    calls: list[str] = []

    def fetch(mime: str) -> bytes:
        calls.append(mime)
        return "привет".encode("utf-8")

    data = RemoteMimeData(_offer(), fetch)

    first = bytes(data.data("text/plain"))
    second = bytes(data.data("text/plain"))

    assert first == second == "привет".encode("utf-8")
    assert calls == ["text/plain"]


def test_a_format_that_was_never_announced_is_not_fetched(qapp):
    calls: list[str] = []
    data = RemoteMimeData(_offer(), calls.append)

    assert bytes(data.data("image/png")) == b""
    assert calls == []


def test_a_charset_parameter_still_finds_the_announced_format(qapp):
    """Qt спрашивает text/plain;charset=utf-8, а объявляли мы text/plain.

    Точное сравнение строк здесь означало бы вставку, которая не работает
    никогда и ничего об этом не сообщает. Так было замечено в спайке Task 1.
    """
    data = RemoteMimeData(_offer(), lambda mime: "привет".encode("utf-8"))

    assert bytes(data.retrieveData("text/plain;charset=utf-8", None)) == "привет".encode("utf-8")


def test_a_failed_fetch_yields_nothing_rather_than_raising(qapp):
    def fetch(mime: str) -> bytes:
        raise TimeoutError("пир не ответил")

    data = RemoteMimeData(_offer(), fetch)

    assert bytes(data.data("text/plain")) == b""
