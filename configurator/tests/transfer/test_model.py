"""Манифест: что мы говорим о файлах, не отдавая ни одного байта.

Абсолютных путей здесь нет намеренно - они не уходят на провод вовсе. Путь в
записи всегда относительный и всегда с разделителем "/", чтобы манифест не
зависел от платформы, снявшей его.
"""

from __future__ import annotations

import pytest

from duo_input.transfer.model import (
    ENTRY_DIRECTORY,
    ENTRY_FILE,
    SkippedEntry,
    TransferEntry,
    TransferManifest,
)


def _manifest() -> TransferManifest:
    return TransferManifest(
        transfer_id="t-1",
        entries=(
            TransferEntry(path="Photos", kind=ENTRY_DIRECTORY, size=0, mtime_ns=1),
            TransferEntry(path="Photos/img1.jpg", kind=ENTRY_FILE, size=1024, mtime_ns=2),
            TransferEntry(path="notes.txt", kind=ENTRY_FILE, size=7, mtime_ns=3),
        ),
        skipped=(SkippedEntry(path="link", reason="reparse_point"),),
        drop_effect=1,
    )


def test_total_bytes_counts_files_and_ignores_directories():
    assert _manifest().total_bytes == 1031


def test_a_manifest_survives_a_round_trip_through_a_dictionary():
    manifest = _manifest()

    assert TransferManifest.from_dict(manifest.to_dict()) == manifest


def test_a_size_of_true_is_refused_rather_than_silently_becoming_one():
    raw = _manifest().to_dict()
    raw["entries"][1]["size"] = True

    with pytest.raises(ValueError):
        TransferManifest.from_dict(raw)


def test_an_mtime_of_true_is_refused_for_the_same_reason():
    raw = _manifest().to_dict()
    raw["entries"][1]["mtime_ns"] = True

    with pytest.raises(ValueError):
        TransferManifest.from_dict(raw)


def test_an_unknown_entry_kind_is_refused():
    raw = _manifest().to_dict()
    raw["entries"][0]["kind"] = "socket"

    with pytest.raises(ValueError):
        TransferManifest.from_dict(raw)


def test_a_missing_transfer_id_is_refused():
    raw = _manifest().to_dict()
    del raw["transfer_id"]

    with pytest.raises(ValueError):
        TransferManifest.from_dict(raw)


def test_entries_that_are_not_a_list_are_refused():
    # Значение здесь должно быть кортежем настоящих записей, а не dict и не
    # строкой: и по dict, и по строке итерация отдаёт не то, что ожидает
    # TransferEntry.from_dict (ключи и символы соответственно), так что они
    # проваливаются на ЕГО собственной проверке "raw должен быть dict" - тест
    # прошёл бы, даже если бы проверки "entries должна быть list" не было
    # вовсе (Task 1.4 review, fix round 1). Кортеж из настоящих записей
    # проходит их проверки без изменений и падает только на типе контейнера.
    raw = _manifest().to_dict()
    raw["entries"] = tuple(raw["entries"])

    with pytest.raises(ValueError):
        TransferManifest.from_dict(raw)


def test_a_negative_size_is_refused():
    raw = _manifest().to_dict()
    raw["entries"][1]["size"] = -1

    with pytest.raises(ValueError):
        TransferManifest.from_dict(raw)


def test_a_manifest_with_no_skipped_entries_still_round_trips():
    manifest = TransferManifest(
        transfer_id="t-2",
        entries=(TransferEntry(path="a.bin", kind=ENTRY_FILE, size=1, mtime_ns=1),),
    )

    assert TransferManifest.from_dict(manifest.to_dict()) == manifest
