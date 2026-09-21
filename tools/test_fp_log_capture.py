#!/usr/bin/env python3
"""Deterministic tests for the bounded/reopen-safe capture writer.

These exercise the two defects the tool exists to prevent, without depending on
`log stream` timing:

* rotation/bounding at the size ceiling, and
* reopening the output path after it is unlinked, so a deleted file never keeps
  growing (the 113 MB unlinked-inode failure this tool replaces).

Run: python3 tools/test_fp_log_capture.py
"""
import os
import tempfile
import unittest

from fp_log_capture import BoundedReopeningWriter


class BoundedReopeningWriterTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="fpcap-test-")
        self.path = os.path.join(self.dir, "capture.log")

    def _write_lines(self, w, n, prefix="x"):
        for i in range(n):
            w.write(f"{prefix}{i}-" + ("p" * 64) + "\n")

    def test_path_exists_on_open(self):
        w = BoundedReopeningWriter(self.path, max_bytes=1 << 20, backups=2, check_every=5)
        self.addCleanup(w.close)
        self.assertTrue(os.path.exists(self.path), "capture file must exist immediately")

    def test_rotation_bounds_active_file_and_keeps_backups(self):
        # ~70 bytes/line; rotate at 700 bytes -> ~10 lines per generation.
        w = BoundedReopeningWriter(self.path, max_bytes=700, backups=2, check_every=5)
        self.addCleanup(w.close)
        self._write_lines(w, 300)
        # Active file is bounded (never the full 300-line volume).
        self.assertLess(os.path.getsize(self.path), 700 + 4096,
                        "active file must stay near the rotation ceiling")
        # At most `backups` rotated generations survive; no unbounded pile-up.
        rotated = [f for f in os.listdir(self.dir)
                   if f.startswith("capture.log.")]
        self.assertLessEqual(len(rotated), 2, f"too many backups kept: {rotated}")
        self.assertGreaterEqual(len(rotated), 1, "rotation should have produced a backup")

    def test_reopen_after_unlink_stops_unlinked_growth(self):
        w = BoundedReopeningWriter(self.path, max_bytes=1 << 20, backups=2, check_every=5)
        self.addCleanup(w.close)
        self._write_lines(w, 10, prefix="a")
        first_ino = os.stat(self.path).st_ino

        # Simulate the failure that produced the 113 MB ghost log: delete the
        # file out from under the running writer.
        os.remove(self.path)
        # Keep writing past the check interval; the writer must notice its fd is
        # unlinked and reopen the path.
        self._write_lines(w, 40, prefix="b")

        self.assertTrue(os.path.exists(self.path), "writer must recreate the deleted path")
        second_ino = os.stat(self.path).st_ino
        self.assertNotEqual(first_ino, second_ino,
                            "writer must be on a fresh inode, not the unlinked one")
        # The live descriptor points at the path's current inode (not the ghost).
        open_ino = os.fstat(w._fh.fileno()).st_ino
        self.assertEqual(open_ino, second_ino,
                         "descriptor must track the live file, never the unlinked inode")
        # Post-reopen writes actually land in the visible file.
        with open(self.path) as fh:
            self.assertIn("b39-", fh.read(), "later writes must be visible in the reopened file")


if __name__ == "__main__":
    unittest.main(verbosity=2)
