"""Каноническая нормализация форматов буфера, общая для всех платформ."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QMimeData, QUrl
from PySide6.QtGui import QImage

from duo_input.clipboard.formats import (
    collect_payloads,
    local_file_paths,
    normalized_payload,
    png_bytes,
    web_uri_list,
)
from duo_input.clipboard.offer import MAX_CONTENT_BYTES


def _mime_with_urls(*urls: str) -> QMimeData:
    data = QMimeData()
    data.setUrls([QUrl(url) for url in urls])
    return data


def test_web_uri_list_keeps_http_and_https():
    data = _mime_with_urls("http://example.com", "https://openai.com")

    result = web_uri_list(data)

    assert result == b"http://example.com\r\nhttps://openai.com\r\n"


def test_web_uri_list_drops_file_urls():
    data = _mime_with_urls("https://example.com", "file:///Users/me/a.txt")

    assert web_uri_list(data) == b"https://example.com\r\n"


def test_web_uri_list_of_only_files_is_absent():
    data = _mime_with_urls("file:///Users/me/a.txt", "file:///Users/me/b.txt")

    assert web_uri_list(data) is None


def test_web_uri_list_drops_non_http_schemes():
    data = _mime_with_urls("ftp://example.com", "mailto:me@example.com")

    assert web_uri_list(data) is None


def test_web_uri_list_without_urls_is_absent():
    assert web_uri_list(QMimeData()) is None


def test_png_bytes_returns_existing_png_unchanged():
    data = QMimeData()
    data.setData("image/png", b"\x89PNG\r\n\x1a\nMADE-UP")

    assert png_bytes(data) == b"\x89PNG\r\n\x1a\nMADE-UP"


def test_png_bytes_encodes_a_qimage_to_png():
    image = QImage(2, 2, QImage.Format.Format_RGB32)
    image.fill(0xFF0000)
    data = QMimeData()
    data.setImageData(image)

    result = png_bytes(data)

    assert result is not None
    assert result.startswith(b"\x89PNG\r\n\x1a\n")


def test_png_bytes_without_image_is_absent():
    assert png_bytes(QMimeData()) is None


def test_normalized_payload_dispatches_plain_text_directly():
    data = QMimeData()
    data.setData("text/plain", "привет".encode("utf-8"))

    assert normalized_payload(data, "text/plain") == "привет".encode("utf-8")


def test_collect_payloads_gathers_every_synced_format():
    data = QMimeData()
    data.setData("text/plain", b"hello")
    data.setData("text/html", b"<b>hello</b>")
    data.setUrls([QUrl("https://example.com")])

    payloads = collect_payloads(data)

    assert payloads == {
        "text/plain": b"hello",
        "text/html": b"<b>hello</b>",
        "text/uri-list": b"https://example.com\r\n",
    }


def test_collect_payloads_enforces_the_ceiling_after_normalisation():
    data = QMimeData()
    data.setData("text/plain", b"x" * (MAX_CONTENT_BYTES + 1))

    assert collect_payloads(data) == {}


def test_local_files_are_reported_separately_from_web_links(qapp, tmp_path):
    source = tmp_path / "notes.txt"
    source.write_bytes(b"x")
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(source)), QUrl("https://example.com/a")])

    assert local_file_paths(mime_data) == (str(source),)


def test_web_links_still_exclude_local_files_from_the_synced_uri_list(qapp, tmp_path):
    # formats.py:35 вырезает file:// намеренно, и должен продолжать: путь с
    # другой машины здесь был бы несуществующим.
    source = tmp_path / "notes.txt"
    source.write_bytes(b"x")
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(source)), QUrl("https://example.com/a")])

    assert collect_payloads(mime_data)["text/uri-list"] == b"https://example.com/a\r\n"


def test_a_clipboard_with_only_local_files_syncs_no_payload_at_all(qapp, tmp_path):
    source = tmp_path / "notes.txt"
    source.write_bytes(b"x")
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(source))])

    assert collect_payloads(mime_data) == {}
    assert local_file_paths(mime_data) == (str(source),)


def test_no_urls_means_no_local_files(qapp):
    mime_data = QMimeData()
    mime_data.setText("plain")

    assert local_file_paths(mime_data) == ()


def test_a_degenerate_file_url_with_no_path_contributes_nothing(qapp, tmp_path):
    # QUrl("file://") сообщает isLocalFile() == True, но toLocalFile() == "".
    # Без отдельной проверки на пустоту str(Path("")) тихо подставил бы "." -
    # рабочий каталог процесса - и он ушёл бы в сканер дерева файлов как путь
    # пользователя.
    source = tmp_path / "notes.txt"
    source.write_bytes(b"x")
    mime_data = QMimeData()
    mime_data.setUrls([QUrl("file://"), QUrl.fromLocalFile(str(source))])

    assert local_file_paths(mime_data) == (str(source),)
