"""Обход дерева источника. Абсолютные пути остаются здесь и на провод не идут."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from duo_input.transfer.model import ENTRY_DIRECTORY, ENTRY_FILE
from duo_input.transfer.scanner import REASON_REPARSE_POINT, scan


def test_a_single_file_becomes_one_entry_named_by_its_basename(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_bytes(b"hello")

    manifest, roots = scan([source], transfer_id="t")

    assert [(entry.path, entry.kind, entry.size) for entry in manifest.entries] == [
        ("notes.txt", ENTRY_FILE, 5)
    ]
    assert roots["notes.txt"] == source


def test_several_files_keep_only_their_basenames(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"bb")

    manifest, _roots = scan([tmp_path / "a.txt", tmp_path / "b.txt"], transfer_id="t")

    assert sorted(entry.path for entry in manifest.entries) == ["a.txt", "b.txt"]


def test_a_directory_contributes_itself_and_its_children_with_relative_paths(tmp_path):
    folder = tmp_path / "Photos"
    (folder / "raw").mkdir(parents=True)
    (folder / "img1.jpg").write_bytes(b"1234")
    (folder / "raw" / "img2.dng").write_bytes(b"56789")

    manifest, roots = scan([folder], transfer_id="t")

    assert sorted(entry.path for entry in manifest.entries) == [
        "Photos",
        "Photos/img1.jpg",
        "Photos/raw",
        "Photos/raw/img2.dng",
    ]
    assert roots["Photos/raw/img2.dng"] == folder / "raw" / "img2.dng"


def test_directories_are_marked_as_directories_and_carry_no_size(tmp_path):
    folder = tmp_path / "Empty"
    folder.mkdir()

    manifest, _roots = scan([folder], transfer_id="t")

    assert manifest.entries[0].kind == ENTRY_DIRECTORY
    assert manifest.entries[0].size == 0


def test_total_bytes_sums_only_the_files(tmp_path):
    folder = tmp_path / "Photos"
    folder.mkdir()
    (folder / "a").write_bytes(b"x" * 10)
    (folder / "b").write_bytes(b"y" * 20)

    manifest, _roots = scan([folder], transfer_id="t")

    assert manifest.total_bytes == 30


def test_a_zero_byte_file_is_offered_rather_than_skipped(tmp_path):
    empty = tmp_path / "empty.bin"
    empty.touch()

    manifest, _roots = scan([empty], transfer_id="t")

    assert manifest.entries[0].size == 0
    assert manifest.entries[0].kind == ENTRY_FILE


def test_a_unicode_name_survives_the_scan(tmp_path):
    source = tmp_path / "Отчёт.txt"
    source.write_bytes(b"x")

    manifest, roots = scan([source], transfer_id="t")

    assert manifest.entries[0].path == "Отчёт.txt"
    assert roots["Отчёт.txt"] == source


def test_the_manifest_records_the_modification_time_it_promised(tmp_path):
    source = tmp_path / "a.bin"
    source.write_bytes(b"x")

    manifest, _roots = scan([source], transfer_id="t")

    assert manifest.entries[0].mtime_ns == os.stat(source).st_mtime_ns


def test_the_scan_refuses_a_source_whose_name_windows_would_mangle(tmp_path):
    # Имя, которое sanitize_manifest отвергает, не должно пройти обход молча:
    # иначе Проводник получил бы дескриптор, который мы обещали не строить.
    from duo_input.transfer.paths import UnsafePath

    source = tmp_path / "nul.txt"
    source.write_bytes(b"x")

    with pytest.raises(UnsafePath):
        scan([source], transfer_id="t")


@pytest.mark.skipif(sys.platform != "win32", reason="junction - это Windows")
def test_a_junction_is_skipped_with_a_reason_and_not_followed(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "secret.txt").write_bytes(b"do not export")
    folder = tmp_path / "Photos"
    folder.mkdir()
    (folder / "img.jpg").write_bytes(b"ok")
    link = folder / "link"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"mklink недоступен: {created.stderr.strip()}")

    manifest, _roots = scan([folder], transfer_id="t")

    assert sorted(entry.path for entry in manifest.entries) == ["Photos", "Photos/img.jpg"]
    assert [(skip.path, skip.reason) for skip in manifest.skipped] == [
        ("Photos/link", REASON_REPARSE_POINT)
    ]
    assert not any("secret" in entry.path for entry in manifest.entries), (
        "обход пошёл по junction - папка с junction на C:\\Windows выгрузила бы ОС"
    )


@pytest.mark.skipif(sys.platform != "win32", reason="junction - это Windows")
def test_a_junction_pointing_at_an_ancestor_does_not_loop_forever(tmp_path):
    # Классический способ повесить обход дерева навечно - reparse point,
    # указывающий на собственного предка. Он ловится тем же правилом, что и
    # обычный junction: обход не идёт по reparse point вообще, независимо от
    # того, куда он указывает.
    folder = tmp_path / "Photos"
    folder.mkdir()
    (folder / "img.jpg").write_bytes(b"ok")
    loop = folder / "loop"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(loop), str(tmp_path)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"mklink недоступен: {created.stderr.strip()}")

    manifest, _roots = scan([folder], transfer_id="t")

    assert sorted(entry.path for entry in manifest.entries) == ["Photos", "Photos/img.jpg"]
    assert [(skip.path, skip.reason) for skip in manifest.skipped] == [
        ("Photos/loop", REASON_REPARSE_POINT)
    ]


def test_a_file_that_disappears_between_listing_and_stat_is_skipped(tmp_path, monkeypatch):
    # os.listdir() видит имя, но к моменту os.stat() файла уже нет - обычная
    # гонка с параллельным процессом. Скан обязан пропустить запись и
    # продолжить, а не упасть целиком.
    from duo_input.transfer.scanner import REASON_UNREADABLE

    folder = tmp_path / "Photos"
    folder.mkdir()
    (folder / "a.txt").write_bytes(b"a")
    (folder / "ghost.txt").write_bytes(b"gone")

    real_stat = os.stat

    def flaky_stat(path, *args, **kwargs):
        if os.fspath(path).endswith("ghost.txt"):
            raise FileNotFoundError("vanished between listing and stat")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", flaky_stat)

    manifest, roots = scan([folder], transfer_id="t")

    assert sorted(entry.path for entry in manifest.entries) == ["Photos", "Photos/a.txt"]
    assert [(skip.path, skip.reason) for skip in manifest.skipped] == [
        ("Photos/ghost.txt", REASON_UNREADABLE)
    ]
    assert "Photos/ghost.txt" not in roots


def test_a_directory_that_cannot_be_listed_is_skipped_not_fatal(tmp_path, monkeypatch):
    # os.listdir() отказывает (например, ACL закрыл перечисление, хотя сам
    # каталог виден через stat). Каталог как узел всё ещё существует и
    # безопасен для передачи пустым; его содержимое - нет.
    from duo_input.transfer.scanner import REASON_UNREADABLE

    folder = tmp_path / "Photos"
    locked = folder / "locked"
    locked.mkdir(parents=True)
    (folder / "img.jpg").write_bytes(b"ok")

    real_listdir = os.listdir

    def flaky_listdir(path):
        if os.fspath(path).endswith("locked"):
            raise PermissionError("no access")
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", flaky_listdir)

    manifest, _roots = scan([folder], transfer_id="t")

    assert ("Photos/locked", REASON_UNREADABLE) in [
        (skip.path, skip.reason) for skip in manifest.skipped
    ]
    assert "Photos/locked" in [
        entry.path for entry in manifest.entries if entry.kind == ENTRY_DIRECTORY
    ]
    assert sorted(entry.path for entry in manifest.entries) == [
        "Photos",
        "Photos/img.jpg",
        "Photos/locked",
    ]
