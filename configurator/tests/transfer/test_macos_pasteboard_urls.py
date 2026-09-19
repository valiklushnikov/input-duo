"""Task 8, Step 5: unit test for ``macos_pasteboard.arm_urls``.

Touches the real macOS pasteboard (host-only), same as
``test_macos_pasteboard.py`` and ``tests/clipboard/test_macos_pasteboard.py`` -
that is fine on this Mac. ``arm_urls`` must mirror ``arm`` exactly, including
whatever ``arm`` does with an empty list (ruling #4 in the task-8 brief: no
divergent empty-list guard).
"""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="pyobjc only on darwin")

pytest.importorskip("AppKit")

from AppKit import NSPasteboard  # noqa: E402
from Foundation import NSURL  # noqa: E402

from duo_input.transfer.macos_pasteboard import arm, arm_urls  # noqa: E402


def test_arm_urls_returns_an_int_change_count(tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("hello")

    count = arm_urls([target.as_uri()])

    assert isinstance(count, int)
    assert count == int(NSPasteboard.generalPasteboard().changeCount())


def test_arm_urls_accepts_a_real_nsurl(tmp_path):
    target = tmp_path / "b.txt"
    target.write_text("hello")
    url = NSURL.fileURLWithPath_(str(target))

    count = arm_urls([url])

    assert isinstance(count, int)
    assert count == int(NSPasteboard.generalPasteboard().changeCount())


def test_arm_urls_accepts_a_bare_path_string(tmp_path):
    target = tmp_path / "c.txt"
    target.write_text("hello")

    count = arm_urls([str(target)])

    assert isinstance(count, int)


def test_arm_urls_empty_list_matches_arm_empty_list_parity():
    # Whatever arm([]) does, arm_urls([]) must do the same (ruling #4) -
    # verify the actual parity rather than asserting either behaviour
    # independently.
    arm_result = arm([])
    arm_urls_result = arm_urls([])

    assert isinstance(arm_result, int)
    assert isinstance(arm_urls_result, int)
