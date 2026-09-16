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
    # os.scandir() видит имя, но к моменту os.stat() файла уже нет - обычная
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
    # os.scandir() отказывает (например, ACL закрыл перечисление, хотя сам
    # каталог виден через stat). Каталог как узел всё ещё существует и
    # безопасен для передачи пустым; его содержимое - нет.
    from duo_input.transfer.scanner import REASON_UNREADABLE

    folder = tmp_path / "Photos"
    locked = folder / "locked"
    locked.mkdir(parents=True)
    (folder / "img.jpg").write_bytes(b"ok")

    real_scandir = os.scandir

    def flaky_scandir(path):
        if os.fspath(path).endswith("locked"):
            raise PermissionError("no access")
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", flaky_scandir)

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


# ------------------------------------------------------------ ранние границы
#
# Потолки paths.sanitize_manifest проверяются ПОСЛЕ обхода: без ранней
# проверки каталог на миллион файлов сначала целиком перечислялся,
# сортировался и stat-ился, и только потом получал отказ.


def _count_calls(monkeypatch, module, name):
    real = getattr(module, name)
    calls = []

    def counting(*args, **kwargs):
        calls.append(args[0] if args else None)
        return real(*args, **kwargs)

    monkeypatch.setattr(module, name, counting)
    return calls


def test_the_entry_ceiling_stops_the_walk_instead_of_finishing_it(tmp_path, monkeypatch):
    from duo_input.transfer import scanner
    from duo_input.transfer.paths import UnsafePath

    folder = tmp_path / "Many"
    folder.mkdir()
    for index in range(60):
        (folder / f"f{index:02d}.bin").write_bytes(b"x")
    monkeypatch.setattr(scanner, "MAX_ENTRIES", 5)
    stats = _count_calls(monkeypatch, os, "stat")

    with pytest.raises(UnsafePath):
        scan([folder], transfer_id="t")

    assert len(stats) <= 6, f"обход продолжился после потолка: {len(stats)} вызовов stat"


def test_the_entry_ceiling_bounds_the_enumeration_of_one_huge_directory(tmp_path, monkeypatch):
    from duo_input.transfer import scanner
    from duo_input.transfer.paths import UnsafePath

    folder = tmp_path / "Flat"
    folder.mkdir()
    for index in range(200):
        (folder / f"f{index:03d}.bin").write_bytes(b"")
    monkeypatch.setattr(scanner, "MAX_ENTRIES", 5)
    yielded = []
    real_scandir = os.scandir

    class _Counting:
        def __init__(self, path):
            self._inner = real_scandir(path)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._inner.close()

        def __iter__(self):
            for entry in self._inner:
                yielded.append(entry.name)
                yield entry

    monkeypatch.setattr(os, "scandir", _Counting)

    with pytest.raises(UnsafePath):
        scan([folder], transfer_id="t")

    assert len(yielded) <= 7, f"каталог перечислен целиком: {len(yielded)} имён"


def test_the_depth_ceiling_stops_the_descent(tmp_path, monkeypatch):
    from duo_input.transfer import scanner
    from duo_input.transfer.paths import UnsafePath

    deepest = tmp_path / "d0"
    for level in range(1, 12):
        deepest = deepest / f"d{level}"
    deepest.mkdir(parents=True)
    monkeypatch.setattr(scanner, "MAX_DEPTH", 3)
    stats = _count_calls(monkeypatch, os, "stat")

    with pytest.raises(UnsafePath):
        scan([tmp_path / "d0"], transfer_id="t")

    assert len(stats) <= 4


def test_the_total_size_ceiling_stops_the_walk(tmp_path, monkeypatch):
    from duo_input.transfer import scanner
    from duo_input.transfer.paths import UnsafePath

    folder = tmp_path / "Big"
    folder.mkdir()
    for index in range(20):
        (folder / f"f{index:02d}.bin").write_bytes(b"12345678")
    monkeypatch.setattr(scanner, "MAX_TOTAL_BYTES", 20)
    stats = _count_calls(monkeypatch, os, "stat")

    with pytest.raises(UnsafePath):
        scan([folder], transfer_id="t")

    assert len(stats) <= 4


def test_skipped_entries_count_towards_the_entry_ceiling(tmp_path, monkeypatch):
    from duo_input.transfer import scanner
    from duo_input.transfer.paths import UnsafePath

    folder = tmp_path / "Locked"
    folder.mkdir()
    for index in range(30):
        (folder / f"f{index:02d}.bin").write_bytes(b"x")
    monkeypatch.setattr(scanner, "MAX_ENTRIES", 5)
    real_stat = os.stat

    def refuse_files(path, *args, **kwargs):
        if os.fspath(path).endswith(".bin"):
            raise PermissionError("denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(os, "stat", refuse_files)

    with pytest.raises(UnsafePath):
        scan([folder], transfer_id="t")


def test_the_entry_ceiling_also_bounds_many_selected_roots(tmp_path, monkeypatch):
    # Двадцать файлов, выделенных в Проводнике, - это двадцать корней, и ни
    # один каталог при этом не перечисляется: потолок перечисления здесь не
    # срабатывает, держит только учёт записей по ходу обхода.
    from duo_input.transfer import scanner
    from duo_input.transfer.paths import UnsafePath

    roots = []
    for index in range(20):
        root = tmp_path / f"r{index:02d}.bin"
        root.write_bytes(b"x")
        roots.append(root)
    monkeypatch.setattr(scanner, "MAX_ENTRIES", 5)
    stats = _count_calls(monkeypatch, os, "stat")

    with pytest.raises(UnsafePath):
        scan(roots, transfer_id="t")

    assert len(stats) <= 5
