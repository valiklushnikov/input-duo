import time
from pathlib import Path
from duo_input.transfer.model import TransferEntry, ENTRY_FILE, ENTRY_DIRECTORY
from duo_input.transfer.staging import StagingArea

def _entries():
    return [
        TransferEntry(path="dir", kind=ENTRY_DIRECTORY, size=0, mtime_ns=0),
        TransferEntry(path="dir/a.txt", kind=ENTRY_FILE, size=5, mtime_ns=0),
        TransferEntry(path="b.txt", kind=ENTRY_FILE, size=3, mtime_ns=0),
    ]

def test_finish_marks_ready_and_returns_roots(tmp_path):
    area = StagingArea(tmp_path)
    s = area.begin("t1", _entries())
    s.write(1, 0, b"hello")
    s.write(2, 0, b"abc")
    roots = s.finish()
    names = sorted(p.name for p in roots)
    assert names == ["b.txt", "dir"]                 # корневые элементы
    assert (tmp_path / "t1" / "dir" / "a.txt").read_bytes() == b"hello"
    assert not (tmp_path / "t1" / ".incomplete").exists()

def test_abort_removes_tree(tmp_path):
    area = StagingArea(tmp_path)
    s = area.begin("t1", _entries())
    s.write(1, 0, b"he")
    s.abort()
    assert not (tmp_path / "t1").exists()

def test_recover_deletes_incomplete_only(tmp_path):
    area = StagingArea(tmp_path)
    s = area.begin("t1", _entries()); s.write(1, 0, b"hello"); s.write(2, 0, b"abc"); s.finish()
    area.begin("t2", _entries())          # оставлен incomplete (упал/убит)
    area.recover()
    assert (tmp_path / "t1").exists()      # READY жив
    assert not (tmp_path / "t2").exists()  # incomplete снесён

def test_gc_deletes_ready_older_than_ttl_but_keeps_current(tmp_path):
    area = StagingArea(tmp_path, ttl_seconds=0.0)
    a = area.begin("t1", _entries()); a.write(1,0,b"hello"); a.write(2,0,b"abc"); a.finish()
    b = area.begin("t2", _entries()); b.write(1,0,b"hello"); b.write(2,0,b"abc"); b.finish()
    time.sleep(0.01)
    area.gc(keep="t2")
    assert not (tmp_path / "t1").exists()  # старше TTL → снесён
    assert (tmp_path / "t2").exists()      # keep защищён

def test_has_room_for_uses_reserve(tmp_path):
    area = StagingArea(tmp_path, reserve_bytes=0)
    assert area.has_room_for(1) is True
    huge = 2**60
    assert area.has_room_for(huge) is False
