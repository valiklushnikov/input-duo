"""Lifecycle + marshalling tests for the PyObjC NSXPC service client (Task 3).

Transport only: these tests exercise connection lifecycle (invalidation ->
``disconnected``, idempotent) and extension->host callback marshalling onto
the Qt thread, using the ``fp_fake_service`` fixture (conftest.py) instead of
a real ``NSXPCConnection``. No FILE_* business logic is exercised here - see
the task-3 brief.

darwin-only: exercises PyObjC + FileProvider + the compiled protocol dylib
(fileprovider_proto.py, Task 2).
"""
from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="darwin only")


def test_invalidation_emits_disconnected_once(fp_fake_service):
    from duo_input.transfer.fileprovider_client import FileProviderServiceClient

    client = FileProviderServiceClient()
    seen = []
    client.disconnected.connect(seen.append)
    client.set_domain("DuoInput")
    fp_fake_service.grant_connection(client)     # inject a fake NSXPCConnection
    fp_fake_service.invalidate()
    fp_fake_service.invalidate()                 # second invalidation is a no-op
    assert len(seen) == 1


def test_extension_callback_marshals_to_qt_thread(fp_fake_service):
    from duo_input.transfer.fileprovider_client import FileProviderServiceClient

    client = FileProviderServiceClient()
    calls = []
    client.set_callbacks(open_fetch=lambda *a: calls.append(("open", *a)),
                         pull_chunk=lambda *a: calls.append(("pull", *a)),
                         cancel_fetch=lambda *a: calls.append(("cancel", *a)))
    fp_fake_service.grant_connection(client)
    fp_fake_service.simulate_extension_call("open", "abc123", 0)
    assert calls == [("open", "abc123", 0)]
