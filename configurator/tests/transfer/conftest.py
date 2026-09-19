from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtWidgets import QApplication

sys.path.insert(0, str(Path(__file__).resolve().parent))


@pytest.fixture(scope="session")
def qapp():
    """Create the QApplication required by the responsiveness instrument."""
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    yield app


class _FakeXPCConnection:
    """Duck-typed stand-in for ``NSXPCConnection`` (Task 3).

    Implements exactly the selector-style methods
    ``FileProviderServiceClient._bind_connection`` calls on a real
    connection, storing what it is handed instead of talking XPC. No ObjC
    reply blocks are ever constructed here - see decision 4 in the task-3
    brief.
    """

    def __init__(self) -> None:
        self.exported_interface = None
        self.exported_object = None
        self.remote_interface = None
        self.invalidation_handler = None
        self.interruption_handler = None
        self.resumed = False
        self.invalidated = False
        self._remote_proxy = object()

    def setExportedInterface_(self, interface) -> None:
        self.exported_interface = interface

    def setExportedObject_(self, obj) -> None:
        self.exported_object = obj

    def setRemoteObjectInterface_(self, interface) -> None:
        self.remote_interface = interface

    def setInvalidationHandler_(self, handler) -> None:
        self.invalidation_handler = handler

    def setInterruptionHandler_(self, handler) -> None:
        self.interruption_handler = handler

    def resume(self) -> None:
        self.resumed = True

    def invalidate(self) -> None:
        self.invalidated = True

    def remoteObjectProxy(self):
        return self._remote_proxy


class _FakeFileProviderService:
    """Test double for the File Provider service discovery + connection.

    ``grant_connection`` bypasses real NSFileProviderManager discovery and
    drives the client's connection-binding path directly with a fake
    connection - exactly what a real ``connect_service()`` would hand to it
    after discovery succeeds. ``invalidate`` and ``simulate_extension_call``
    then drive the client exactly as a real XPC connection would: through
    its invalidation handler and its ``_dispatch_extension_call`` seam.
    """

    def __init__(self) -> None:
        self.connection: _FakeXPCConnection | None = None
        self._client = None

    def grant_connection(self, client) -> None:
        self.connection = _FakeXPCConnection()
        self._client = client
        client._bind_connection(self.connection)

    def invalidate(self) -> None:
        if self.connection is None:
            return
        handler = self.connection.invalidation_handler
        if handler is not None:
            handler()

    def simulate_extension_call(self, kind: str, *args) -> None:
        assert self._client is not None, "grant_connection() must run first"
        self._client._dispatch_extension_call(kind, *args)


@pytest.fixture
def fp_fake_service():
    """Fake NSXPCConnection + discovery for FileProviderServiceClient tests."""
    return _FakeFileProviderService()
