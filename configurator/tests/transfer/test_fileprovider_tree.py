"""Task 13: multi-file and directory concurrency over a real manifest TREE.

Tasks 9-12 proved the bounded, per-fetch scheduler against FLAT manifests
(every entry a top-level file, see ``test_fileprovider_scheduler.py`` /
``test_fileprovider_streaming.py`` / ``test_fileprovider_cancel.py``). This
module exercises the exact same scheduler against a manifest with real
directory nesting (depth >= 2, siblings under a common parent, the same
basename repeated in two different directories) and asserts nothing about the
scheduler's guarantees - bounded ``MAX_ACTIVE_FETCHES``, FIFO queue fairness,
per-fetch byte isolation, and cancel/error isolation - depends on entries
being flat.

Per task-13 brief ruling #1 (VERIFICATION-FIRST): ``open_fetch`` already keys
every fetch by ``entry_index`` (a fresh ``uuid4().hex`` token per call, see
``FileProviderBackend._open_fetch``), never by filename or path, so two
entries that happen to share a basename in different directories are already
as independent as any other two entries. Nothing here re-keys anything - each
test is a verification, not a rewrite.
"""

from __future__ import annotations

import pytest

from duo_input.transfer.model import ENTRY_DIRECTORY, ENTRY_FILE, TransferEntry, TransferManifest
from test_fileprovider_scheduler import _backend, _reply


def _tree_manifest(transfer_id: str = "tree-1") -> TransferManifest:
    """A manifest with real nesting:

        dirA/
            a.bin        (3 bytes)
            b.bin        (5 bytes)
            c.bin        (7 bytes)   <- a.bin, b.bin, c.bin are SIBLINGS
            sub/
                d.bin    (4 bytes)  <- depth-2 nesting
        dirB/
            a.bin        (9 bytes)  <- SAME basename as dirA/a.bin, different dir
        root1.bin        (6 bytes)  <- two flat, top-level files
        root2.bin        (8 bytes)

    Fetchable (file) entries, in manifest order, with their index:
      1 dirA/a.bin, 2 dirA/b.bin, 3 dirA/c.bin, 5 dirA/sub/d.bin,
      7 dirB/a.bin, 8 root1.bin, 9 root2.bin  -> 7 files, i.e. > MAX_ACTIVE_FETCHES.
    """
    entries = (
        TransferEntry(path="dirA", kind=ENTRY_DIRECTORY, size=0, mtime_ns=0),  # 0
        TransferEntry(path="dirA/a.bin", kind=ENTRY_FILE, size=3, mtime_ns=0),  # 1
        TransferEntry(path="dirA/b.bin", kind=ENTRY_FILE, size=5, mtime_ns=0),  # 2
        TransferEntry(path="dirA/c.bin", kind=ENTRY_FILE, size=7, mtime_ns=0),  # 3
        TransferEntry(path="dirA/sub", kind=ENTRY_DIRECTORY, size=0, mtime_ns=0),  # 4
        TransferEntry(path="dirA/sub/d.bin", kind=ENTRY_FILE, size=4, mtime_ns=0),  # 5
        TransferEntry(path="dirB", kind=ENTRY_DIRECTORY, size=0, mtime_ns=0),  # 6
        TransferEntry(path="dirB/a.bin", kind=ENTRY_FILE, size=9, mtime_ns=0),  # 7
        TransferEntry(path="root1.bin", kind=ENTRY_FILE, size=6, mtime_ns=0),  # 8
        TransferEntry(path="root2.bin", kind=ENTRY_FILE, size=8, mtime_ns=0),  # 9
    )
    return TransferManifest(transfer_id=transfer_id, entries=entries, skipped=(), drop_effect=1)


#: Fetchable (file) indices of ``_tree_manifest()``, in manifest order.
FILE_INDICES = (1, 2, 3, 5, 7, 8, 9)
DIR_A_CHILDREN = (1, 2, 3)  # dirA/a.bin, dirA/b.bin, dirA/c.bin - literal siblings


def _open_many(backend, generation_id, indices):
    opened = [backend.open_fetch(generation_id, index) for index in indices]
    return [token for token, _size in opened]


def _snapshot(backend, token):
    fetch = backend.by_token[token]
    return (fetch.offset, fetch.read_id, fetch.expected, fetch.state)


# --- two flat, top-level files -----------------------------------------------


def test_two_top_level_files_fetch_concurrently_to_correct_bytes(qapp):
    manifest = _tree_manifest()
    backend, link, _remote, manifest = _backend(qapp, manifest)
    root1, root2 = _open_many(backend, manifest.transfer_id, [8, 9])
    fetch_root1, fetch_root2 = backend.by_token[root1], backend.by_token[root2]

    replies1, replies2 = [], []
    backend.pull_chunk(root1, lambda *a: replies1.append(a))
    backend.pull_chunk(root2, lambda *a: replies2.append(a))
    [read1, read2] = link.sent
    assert read1.header["entry_index"] == 8
    assert read2.header["entry_index"] == 9
    assert read1.header["read_id"] != read2.header["read_id"]

    # Deliver in REVERSE order of the requests, with distinguishable content,
    # to catch any accidental cross-wiring between the two concurrent fetches.
    backend.handle_message(_reply(read2, b"r" * 8))
    backend.handle_message(_reply(read1, b"o" * 6))

    assert replies2 == [(b"r" * 8, True, None)]
    assert replies1 == [(b"o" * 6, True, None)]
    assert fetch_root1.state == "done"
    assert fetch_root2.state == "done"


# --- directory entries are not fetchable, and saying so disturbs nothing ----


def test_opening_a_directory_entry_raises_and_leaves_a_concurrent_file_fetch_untouched(
    qapp,
):
    manifest = _tree_manifest()
    backend, _link, _remote, manifest = _backend(qapp, manifest)
    [file_token] = _open_many(backend, manifest.transfer_id, [1])  # dirA/a.bin
    before = _snapshot(backend, file_token)

    with pytest.raises(ValueError):
        backend.open_fetch(manifest.transfer_id, 0)  # dirA itself - a directory
    with pytest.raises(ValueError):
        backend.open_fetch(manifest.transfer_id, 4)  # dirA/sub - also a directory

    assert _snapshot(backend, file_token) == before
    assert len(backend.by_token) == 1


# --- same basename, different directories: distinct fetches, distinct bytes -


def test_same_filename_in_different_directories_get_distinct_fetches_and_bytes(qapp):
    manifest = _tree_manifest()
    backend, link, _remote, manifest = _backend(qapp, manifest)
    a_in_dirA, a_in_dirB = _open_many(backend, manifest.transfer_id, [1, 7])  # both "a.bin"

    assert a_in_dirA != a_in_dirB
    fetch_a, fetch_b = backend.by_token[a_in_dirA], backend.by_token[a_in_dirB]
    assert fetch_a.entry_index == 1
    assert fetch_b.entry_index == 7
    assert fetch_a.size == 3
    assert fetch_b.size == 9

    replies_a, replies_b = [], []
    backend.pull_chunk(a_in_dirA, lambda *a: replies_a.append(a))
    backend.pull_chunk(a_in_dirB, lambda *a: replies_b.append(a))
    [read_a] = [m for m in link.sent if m.header["entry_index"] == 1]
    [read_b] = [m for m in link.sent if m.header["entry_index"] == 7]
    assert read_a.header["read_id"] != read_b.header["read_id"]

    backend.handle_message(_reply(read_a, b"AAA"))
    backend.handle_message(_reply(read_b, b"BBBBBBBBB"))

    assert replies_a == [(b"AAA", True, None)]
    assert replies_b == [(b"BBBBBBBBB", True, None)]


# --- depth >= 2 nesting -------------------------------------------------------


def test_depth_two_nested_file_fetch_reassembles_correctly(qapp):
    manifest = _tree_manifest()
    backend, link, _remote, manifest = _backend(qapp, manifest)
    [token] = _open_many(backend, manifest.transfer_id, [5])  # dirA/sub/d.bin

    replies = []
    backend.pull_chunk(token, lambda *a: replies.append(a))
    [read] = link.sent
    fetch = backend.by_token[token]
    assert read.header == {
        "transfer_id": manifest.transfer_id,
        "entry_index": 5,
        "offset": 0,
        "length": 4,
        "read_id": fetch.read_id,
    }
    backend.handle_message(_reply(read, b"deep"))

    assert replies == [(b"deep", True, None)]
    assert fetch.state == "done"


# --- bounded concurrency across a tree ---------------------------------------


def test_opening_more_than_max_active_fetches_in_a_tree_respects_the_bound(qapp):
    manifest = _tree_manifest()
    backend, link, _remote, manifest = _backend(qapp, manifest)

    tokens = _open_many(backend, manifest.transfer_id, FILE_INDICES)

    assert len(tokens) == 7  # > MAX_ACTIVE_FETCHES (4), spread across the tree
    assert [backend.by_token[t].state for t in tokens] == [
        "requesting",
        "requesting",
        "requesting",
        "requesting",
        "queued",
        "queued",
        "queued",
    ]
    assert backend._active == set(tokens[:4])
    assert list(backend._queue) == tokens[4:]
    assert link.sent == []  # admission alone never triggers a FILE_READ


def test_queue_admits_fifo_successor_across_tree_branches(qapp):
    manifest = _tree_manifest()
    backend, link, _remote, manifest = _backend(qapp, manifest)
    tokens = _open_many(backend, manifest.transfer_id, FILE_INDICES)
    # Active: dirA/a.bin, dirA/b.bin, dirA/c.bin, dirA/sub/d.bin (indices 0-3).
    # Queued: dirB/a.bin, root1.bin, root2.bin (indices 4-6), in that order.
    fetch0 = backend.by_token[tokens[0]]
    backend.pull_chunk(tokens[0])
    [read] = link.sent

    backend.handle_message(_reply(read, b"AAA"))  # completes dirA/a.bin (3 bytes)

    assert fetch0.state == "done"
    assert backend.by_token[tokens[4]].state == "requesting"  # dirB/a.bin - FIFO head
    assert tokens[4] in backend._active
    assert list(backend._queue) == list(tokens[5:])  # root1.bin, root2.bin - order preserved

    # Drain one more slot: the FIFO successor must again be the head of what's
    # left, never a fetch from a different point in the tree jumping the line.
    backend.pull_chunk(tokens[1])
    [read2] = [m for m in link.sent if m.header["entry_index"] == 2]
    backend.handle_message(_reply(read2, b"BBBBB"))

    assert backend.by_token[tokens[5]].state == "requesting"  # root1.bin
    assert list(backend._queue) == [tokens[6]]


# --- sibling isolation on cancel: the mandated failure/rollback test --------


def test_cancelling_one_sibling_does_not_disturb_other_siblings_state_or_bytes(qapp):
    """The brief's failure/rollback condition: "sibling interference on
    cancel/error -> block". ``dirA/a.bin``, ``dirA/b.bin``, ``dirA/c.bin`` are
    literal siblings (same parent directory, all three fit inside
    ``MAX_ACTIVE_FETCHES`` so none of this is confused with queue admission).
    Cancelling the MIDDLE sibling while all three have a read in flight must
    leave the other two byte-for-byte and state-for-state untouched, and they
    must still reassemble their OWN, correct bytes afterwards.
    """
    manifest = _tree_manifest()
    backend, link, _remote, manifest = _backend(qapp, manifest)
    a_token, b_token, c_token = _open_many(backend, manifest.transfer_id, DIR_A_CHILDREN)
    assert backend._active == {a_token, b_token, c_token}

    replies_a, replies_b, replies_c = [], [], []
    backend.pull_chunk(a_token, lambda *a: replies_a.append(a))
    backend.pull_chunk(b_token, lambda *a: replies_b.append(a))
    backend.pull_chunk(c_token, lambda *a: replies_c.append(a))
    [read_a] = [m for m in link.sent if m.header["entry_index"] == 1]
    [read_b] = [m for m in link.sent if m.header["entry_index"] == 2]
    [read_c] = [m for m in link.sent if m.header["entry_index"] == 3]
    snapshot_a_before = _snapshot(backend, a_token)
    snapshot_c_before = _snapshot(backend, c_token)

    backend.cancel_fetch(b_token)  # cancel the MIDDLE sibling, mid-flight

    # The cancelled sibling settled its own pull with an error, exactly once,
    # and is fully forgotten.
    assert len(replies_b) == 1
    assert replies_b[0][0] is None and replies_b[0][1] is False
    assert b_token not in backend.by_token

    # The OTHER two siblings: completely untouched by the cancel - same
    # offset, same read_id, same expected, same state, no premature reply.
    assert _snapshot(backend, a_token) == snapshot_a_before
    assert _snapshot(backend, c_token) == snapshot_c_before
    assert replies_a == []
    assert replies_c == []
    fetch_a = backend.by_token[a_token]
    fetch_c = backend.by_token[c_token]
    assert backend.by_read_id[read_a.header["read_id"]] is fetch_a
    assert backend.by_read_id[read_c.header["read_id"]] is fetch_c

    # Both siblings still complete correctly, with THEIR OWN bytes, afterward.
    backend.handle_message(_reply(read_a, b"AAA"))
    backend.handle_message(_reply(read_c, b"CCCCCCC"))

    assert replies_a == [(b"AAA", True, None)]
    assert replies_c == [(b"CCCCCCC", True, None)]
    assert fetch_a.state == "done"
    assert fetch_c.state == "done"
    # The cancelled sibling's read_id is gone; the survivors' were never
    # confused with it or with each other.
    assert read_b.header["read_id"] not in backend.by_read_id


def test_erroring_one_sibling_does_not_disturb_other_siblings_state_or_bytes(qapp):
    """Same matrix as above, but the failure mode is a protocol ERROR (a
    malformed chunk for one sibling), not a Finder-initiated cancel - the
    brief's failure/rollback condition names both "cancel/error"."""
    manifest = _tree_manifest()
    backend, link, _remote, manifest = _backend(qapp, manifest)
    a_token, b_token, c_token = _open_many(backend, manifest.transfer_id, DIR_A_CHILDREN)

    replies_a, replies_b, replies_c = [], [], []
    backend.pull_chunk(a_token, lambda *a: replies_a.append(a))
    backend.pull_chunk(b_token, lambda *a: replies_b.append(a))
    backend.pull_chunk(c_token, lambda *a: replies_c.append(a))
    [read_a] = [m for m in link.sent if m.header["entry_index"] == 1]
    [read_b] = [m for m in link.sent if m.header["entry_index"] == 2]
    [read_c] = [m for m in link.sent if m.header["entry_index"] == 3]
    snapshot_a_before = _snapshot(backend, a_token)
    snapshot_c_before = _snapshot(backend, c_token)

    # Malformed reply for b_token (wrong length: b.bin expects 5 bytes).
    fetch_b = backend.by_token[b_token]
    backend.handle_message(_reply(read_b, b"toolong!!"))

    assert fetch_b.state == "failed"
    assert len(replies_b) == 1
    assert replies_b[0][0] is None and replies_b[0][1] is False

    assert _snapshot(backend, a_token) == snapshot_a_before
    assert _snapshot(backend, c_token) == snapshot_c_before
    assert replies_a == []
    assert replies_c == []

    fetch_a = backend.by_token[a_token]
    fetch_c = backend.by_token[c_token]
    backend.handle_message(_reply(read_a, b"AAA"))
    backend.handle_message(_reply(read_c, b"CCCCCCC"))

    assert replies_a == [(b"AAA", True, None)]
    assert replies_c == [(b"CCCCCCC", True, None)]
    assert fetch_a.state == "done"
    assert fetch_c.state == "done"


# --- fetch identity is by entry_index, never by filename/path ---------------


def test_fetch_tokens_and_read_ids_are_unique_across_the_whole_tree(qapp):
    """Every ``open_fetch``/``pull_chunk`` across the tree gets its own,
    globally unique token and read_id - never derived from (and therefore
    never colliding on) the entry's path or basename."""
    manifest = _tree_manifest()
    backend, link, _remote, manifest = _backend(qapp, manifest)
    tokens = _open_many(backend, manifest.transfer_id, FILE_INDICES)

    assert len(set(tokens)) == len(tokens)  # every token distinct
    for token in tokens:
        backend.pull_chunk(token)
    # Only the 4 admitted-active fetches actually issued a FILE_READ; each
    # got its own read_id even though several sizes coincide with entries
    # under different directories.
    read_ids = [m.header["read_id"] for m in link.sent]
    assert len(set(read_ids)) == len(read_ids)
