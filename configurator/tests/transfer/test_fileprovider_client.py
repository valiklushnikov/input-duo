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


def test_redundant_connect_service_does_not_rebind(fp_fake_service):
    from duo_input.transfer.fileprovider_client import FileProviderServiceClient

    client = FileProviderServiceClient()
    connects = []
    client.connected.connect(lambda: connects.append(1))
    client.set_domain("DuoInput")
    fp_fake_service.grant_connection(client)     # binds once, emits connected once
    assert connects == [1]
    bound = client._connection
    # A redundant connect while already connected must be a pure no-op: it must
    # not rediscover, rebind, or re-emit `connected`.
    client.connect_service()
    assert connects == [1]
    assert client._connection is bound


def test_resolve_service_keeps_main_run_loop_alive_for_completion(monkeypatch):
    """A File Provider completion queued to main must not be starved by wait()."""
    from Foundation import NSOperationQueue

    from duo_input.transfer import fileprovider_client as module

    expected_service = object()

    class Manager:
        def getServiceWithName_itemIdentifier_completionHandler_(
            self, service_name, item_identifier, completion
        ):
            NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda: completion(expected_service, None)
            )

    monkeypatch.setattr(module, "_GET_SERVICE_TIMEOUT_S", 0.1)

    assert module.FileProviderServiceClient._resolve_service(Manager()) is expected_service


def test_resolve_connection_keeps_main_run_loop_alive_for_completion(monkeypatch):
    """The endpoint-to-connection callback has the same main-queue contract."""
    from Foundation import NSOperationQueue

    from duo_input.transfer import fileprovider_client as module

    expected_connection = object()

    class Service:
        def getFileProviderConnectionWithCompletionHandler_(self, completion):
            NSOperationQueue.mainQueue().addOperationWithBlock_(
                lambda: completion(expected_connection, None)
            )

    monkeypatch.setattr(module, "_GET_CONNECTION_TIMEOUT_S", 0.1)

    assert (
        module.FileProviderServiceClient._resolve_connection(Service())
        is expected_connection
    )
