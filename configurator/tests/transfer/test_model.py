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


# ------------------------------------------------------- манифест на проводе


def _typical_manifest(count: int) -> TransferManifest:
    return TransferManifest(
        transfer_id="0123456789abcdef0123456789abcdef",
        entries=tuple(
            TransferEntry(
                path=f"Photos/2026/IMG_{index:06d}.jpg",
                kind=ENTRY_FILE,
                size=4_200_000,
                mtime_ns=1_726_000_000_000_000_000,
            )
            for index in range(count)
        ),
    )


def test_a_manifest_survives_its_wire_encoding():
    from duo_input.transfer.model import decode_manifest, encode_manifest

    manifest = TransferManifest(
        transfer_id="t-1",
        entries=(TransferEntry(path="Отчёт.txt", kind=ENTRY_FILE, size=3, mtime_ns=7),),
        skipped=(SkippedEntry(path="link", reason="reparse_point"),),
    )

    assert decode_manifest(encode_manifest(manifest)) == manifest


def test_the_documented_entry_ceiling_fits_in_one_manifest_with_ordinary_names():
    # Прежний двухбайтовый заголовок вмещал около семисот таких записей, а
    # спецификация обещала 65 536. Поэтому манифест едет в теле кадра.
    from duo_input.transfer.model import MAX_MANIFEST_BYTES, encode_manifest
    from duo_input.transfer.paths import MAX_ENTRIES

    encoded = encode_manifest(_typical_manifest(MAX_ENTRIES))

    assert len(encoded) <= MAX_MANIFEST_BYTES


def test_the_manifest_ceiling_leaves_the_frame_room_for_its_header():
    from duo_input.clipboard.wire import MAX_FRAME_BYTES, MAX_HEADER_BYTES
    from duo_input.transfer.model import MAX_MANIFEST_BYTES

    assert MAX_MANIFEST_BYTES + MAX_HEADER_BYTES + 3 <= MAX_FRAME_BYTES


def test_a_manifest_at_the_byte_ceiling_crosses_the_real_framing():
    from duo_input.clipboard.wire import FrameAssembler, Message, MessageType, encode
    from duo_input.transfer.model import MAX_MANIFEST_BYTES, decode_manifest, encode_manifest

    manifest = _typical_manifest(3)
    encoded = encode_manifest(manifest)
    padded = encoded + b" " * (MAX_MANIFEST_BYTES - len(encoded))

    [message] = FrameAssembler().feed(encode(Message(MessageType.FILE_OFFER, {}, padded)))

    assert decode_manifest(message.blob) == manifest


@pytest.mark.parametrize(
    "blob",
    [b"", b"{", b"[]", b"null", b"\xff", b"[" * 200_000, b'{"transfer_id": "t"} x'],
    ids=["empty", "open", "list", "null", "not-utf8", "deep-nesting", "trailing"],
)
def test_a_malformed_manifest_is_a_value_error(blob):
    from duo_input.transfer.model import decode_manifest

    with pytest.raises(ValueError):
        decode_manifest(blob)


def test_a_manifest_past_the_byte_ceiling_is_refused_before_parsing(monkeypatch):
    from duo_input.transfer import model

    parsed = []
    monkeypatch.setattr(model.json, "loads", lambda *args, **kwargs: parsed.append(1))

    with pytest.raises(ValueError):
        model.decode_manifest(b" " * (model.MAX_MANIFEST_BYTES + 1))
    assert parsed == []


@pytest.mark.parametrize("transfer_id", ["", "тест", "a b", "a/b", "x" * 65, "t\x00"])
def test_a_transfer_id_must_be_a_short_ascii_token(transfer_id):
    raw = _typical_manifest(1).to_dict()
    raw["transfer_id"] = transfer_id

    with pytest.raises(ValueError):
        TransferManifest.from_dict(raw)


@pytest.mark.parametrize("field", ["size", "mtime_ns"])
def test_an_integer_beyond_sixty_three_bits_is_refused(field):
    raw = _typical_manifest(1).to_dict()
    raw["entries"][0][field] = 2**63

    with pytest.raises(ValueError):
        TransferManifest.from_dict(raw)
