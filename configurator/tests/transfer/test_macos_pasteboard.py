import sys
import pytest
from pathlib import Path

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="pyobjc only on darwin")


def test_to_file_urls_builds_absolute_file_scheme():
    from duo_input.transfer.macos_pasteboard import _to_file_urls

    urls = _to_file_urls([Path("/tmp/a.txt"), "/tmp/dir"])
    assert urls == ["file:///tmp/a.txt", "file:///tmp/dir"]
