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


def test_bind_activates_connection_without_publish(fp_fake_service):
    """RED (Gate C restart -1004 root cause): after binding the XPC connection,
    the host MUST proactively send a side-effect-free activation RPC so the
    extension's NSXPCListener fires ``shouldAcceptNewConnection`` and captures
    the connection - otherwise, after a Mac restart with NO clipboard
    publication, the extension never gets a live connection and can never call
    back for fetches (``hostProxy() == nil`` -> NotConnected -> Finder -1004).

    The activation must NOT be ``publishGeneration`` (that mutates the durable
    store); it must be a lifecycle-only, side-effect-free call.
    """
    from duo_input.transfer.fileprovider_client import FileProviderServiceClient

    client = FileProviderServiceClient()
    client.set_domain("DuoInput")
    fp_fake_service.grant_connection(client)  # binds + resumes, NO publish

    calls = [name for (name, *_) in fp_fake_service.connection.remote_calls]
    assert any(name.startswith("activate") for name in calls), (
        "host must activate the bound connection with a side-effect-free RPC "
        f"so the extension accepts it; recorded remote calls: {calls}"
    )
    assert not any("publishGeneration" in name for name in calls), (
        "activation must be side-effect-free, never publishGeneration"
    )


def test_activate_reply_block_has_outgoing_pyobjc_metadata():
    """The real NSXPC proxy must be able to encode activate's reply block.

    The fake connection accepts any Python callable, so the ordinary lifecycle
    test cannot catch a selector that was omitted from PyObjC's outgoing block
    metadata.  The production bridge registry is the real boundary here: if
    this entry disappears, ``activateWithReply_`` can fail before NSXPC sends
    the first message and the extension listener is never accepted.
    """
    import objc

    # Importing the production client performs the selector registrations.
    from duo_input.transfer import fileprovider_client as client_module

    registrations = objc._copyMetadataRegistry()[b"activateWithReply:"]
    registered_classes = {class_name for class_name, _metadata in registrations}

    assert registered_classes == {
        b"__NSXPCInterfaceProxy_DuoExtensionControl",
        b"DuoExtensionControl",
    }
    for _class_name, metadata in registrations:
        reply = metadata["arguments"][2]
        assert reply["type"] == b"@?"
        assert [argument["type"] for argument in reply["callable"]["arguments"]] == [
            b"@?",
            client_module._outgoing_objc_bool_encoding(),
            b"@",
        ]


@pytest.mark.parametrize(
    ("machine", "expected"),
    [("arm64", b"B"), ("aarch64", b"B"), ("x86_64", b"c")],
)
def test_outgoing_objc_bool_encoding_matches_architecture(machine, expected):
    from duo_input.transfer.fileprovider_client import _outgoing_objc_bool_encoding

    assert _outgoing_objc_bool_encoding(machine) == expected


def test_activation_logs_proxy_send_and_reply_boundaries(fp_fake_service, caplog):
    from duo_input.transfer.fileprovider_client import FileProviderServiceClient

    caplog.set_level("INFO", logger="duo_input.transfer.fileprovider_client")
    client = FileProviderServiceClient()
    fp_fake_service.grant_connection(client)

    messages = [record.getMessage() for record in caplog.records]
    assert any("fp_host_activate_proxy" in message and "proxy_present=True" in message for message in messages)
    assert any("fp_host_activate_send_return" in message for message in messages)
    assert any("fp_host_activate_reply" in message and "ack=True" in message for message in messages)


def test_activation_logs_missing_proxy_without_crashing(caplog):
    from duo_input.transfer.fileprovider_client import FileProviderServiceClient

    class MissingProxyConnection:
        @staticmethod
        def remoteObjectProxyWithErrorHandler_(_handler):
            return None

    caplog.set_level("INFO", logger="duo_input.transfer.fileprovider_client")
    client = FileProviderServiceClient()

    client._activate_connection(MissingProxyConnection(), generation=7)

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "fp_host_activate_proxy_unavailable" in message
        and "xpc_generation=7" in message
        for message in messages
    )


@pytest.mark.parametrize("failure_boundary", ["proxy", "send"])
def test_activation_sync_failures_are_logged_without_crashing(
    failure_boundary, caplog
):
    from duo_input.transfer.fileprovider_client import FileProviderServiceClient

    class RaisingProxy:
        @staticmethod
        def activateWithReply_(_reply):
            raise RuntimeError("send failed")

    class RaisingConnection:
        @staticmethod
        def remoteObjectProxyWithErrorHandler_(_handler):
            if failure_boundary == "proxy":
                raise RuntimeError("proxy failed")
            return RaisingProxy()

    caplog.set_level("INFO", logger="duo_input.transfer.fileprovider_client")
    client = FileProviderServiceClient()

    client._activate_connection(RaisingConnection(), generation=9)

    messages = [record.getMessage() for record in caplog.records]
    expected_event = (
        "fp_host_activate_proxy_failed"
        if failure_boundary == "proxy"
        else "fp_host_activate_send_failed"
    )
    assert any(
        expected_event in message and "xpc_generation=9" in message
        for message in messages
    )


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
