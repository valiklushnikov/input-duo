"""PyObjC NSXPC **client** for the Duo Input File Provider transport.

The Swift extension (``fileprovider/Extension/FileProviderExtension.swift``)
owns an anonymous ``NSXPCListener`` vended through ``NSFileProviderServiceSource``
(``DuoServiceSource.swift``, service name below). This module is the *client*
side of that connection: it discovers the connection for a domain, exports the
``DuoHostCallback`` object the extension calls back into during
``fetchContents``, marshals those callbacks onto the Qt thread, and survives
invalidation/interruption with reconnect.

Transport only - by design, matching the task-3 brief:
    * no FILE_* fetch/chunk business logic,
    * no fetch scheduler, generation publication, replica, domain-readiness
      state machine, pasteboard, or staging.
Those all consume this client in later tasks; ``remote()`` is how Task 7+
gets a ``DuoExtensionControl`` proxy to call ``publishGeneration`` etc.

darwin-only. Mirrors ``fileprovider_proto.py``'s framing (compiled protocol,
no ``objc.formal_protocol`` fallback) but additionally guards its own
PyObjC/FileProvider imports so importing this module on a non-darwin
collection run does not hard-crash: everything that actually touches the
Objective-C runtime is gated on ``_XPC_AVAILABLE``.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from PySide6.QtCore import QMetaObject, QObject, Qt, Q_ARG, Signal, Slot

logger = logging.getLogger(__name__)


def _log_service_event(event: str, **fields: object) -> None:
    """Emit one timestamped service-discovery boundary without user data."""
    rendered = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info(
        "%s timestamp_ns=%d thread=%s %s",
        event,
        time.time_ns(),
        threading.current_thread().name,
        rendered,
    )

try:
    import objc
    from Foundation import NSObject
    from FileProvider import (
        NSFileProviderManager,
        NSFileProviderRootContainerItemIdentifier,
        NSFileProviderServiceName,
    )

    # fileprovider_proto.py (Task 2) is itself unconditional-import darwin-only
    # (no guard of its own - dlopen'ing the compiled protocol dylib makes an
    # unconditional import pointless there). Importing it inside this same
    # guarded block, rather than at module top, keeps that darwin-only
    # requirement from leaking into fileprovider_client's own import surface.
    from .fileprovider_proto import extension_interface, host_interface

    _XPC_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised implicitly on non-darwin
    objc = None
    NSObject = object
    NSFileProviderManager = None
    NSFileProviderRootContainerItemIdentifier = None
    extension_interface = None
    host_interface = None
    NSFileProviderServiceName = None
    _XPC_AVAILABLE = False


#: The XPC service *label* the extension vends - see
#: fileprovider/Extension/DuoServiceSource.swift (duoFileProviderServiceName).
#: An NSXPC service label, not a Mach service registration: the endpoint is
#: anonymous and delivered by the File Provider infrastructure - no App
#: Group, no named Mach service, no temporary-exception.
SERVICE_NAME = "com.duoinput.configurator.fileprovider.xpc"

#: How long connect_service() waits on each step of async discovery before
#: giving up. Discovery talks to fileproviderd; unbounded waits here would
#: turn a dead daemon into a permanently hung connect_service() call.
_GET_DOMAINS_TIMEOUT_S = 5.0
_GET_SERVICE_TIMEOUT_S = 5.0
_GET_CONNECTION_TIMEOUT_S = 5.0

#: Vocabulary for the extension->host seam (``_dispatch_extension_call``).
#: Both the real exported adapter below and the test fake
#: (tests/transfer/conftest.py, fp_fake_service) speak these three kinds;
#: they map onto the three callbacks set_callbacks() registers.
_KIND_TO_CALLBACK = {
    "open": "open_fetch",
    "pull": "pull_chunk",
    "cancel": "cancel_fetch",
}


def _xpc_error(code: int):
    """The shared DuoFPErrorDomain codes, bridged as NSError on macOS."""
    if _XPC_AVAILABLE:
        from Foundation import NSError

        return NSError.errorWithDomain_code_userInfo_(
            "com.duoinput.configurator.fileprovider.error", code, None
        )
    return RuntimeError(f"DuoFPErrorDomain:{code}")


if _XPC_AVAILABLE:
    # Clang supplies the XPC protocol's extended signatures. PyObjC additionally
    # needs the callable metadata to convert received blocks into Python
    # callables with the correct BOOL/object arguments.
    for _selector, _index, _second_type in (
        (b"openFetch:entryId:reply:", 4, b"@"),
        (b"pullChunk:reply:", 3, objc._C_NSBOOL),
    ):
        objc.registerMetaDataForSelector(
            b"_ExtensionCallbackAdapter",
            _selector,
            {
                "arguments": {
                    _index: {
                        "type": b"@?",
                        "callable_retained": True,
                        "callable": {
                            "retval": {"type": b"v"},
                            "arguments": {
                                0: {"type": b"^v"},
                                1: {"type": b"@"},
                                2: {"type": _second_type},
                                3: {"type": b"@"},
                            },
                        },
                    }
                }
            },
        )

    # The REMOTE direction needs the same treatment. When the host calls
    # publish/retire/deleteGeneration:reply: on the DuoExtensionControl proxy it
    # passes a Python callable as the reply block; PyObjC can only turn that into
    # an NSXPCConnection-acceptable block if it knows the block's argument types.
    # The clang protocol carries the method encoding but NOT the inner block
    # argument types, so without this metadata NSXPCConnection rejects the call
    # with "Block was not compiled using a compiler that inserts type information
    # about arguments". Each reply is void(^)(BOOL ack, NSError *error); the
    # block sits at method argument index 3 (self, _cmd, id/record, reply).
    # PyObjC resolves an outgoing call by the proxy's ACTUAL class. The proxy
    # NSXPCConnection.remoteObjectProxy() returns is a runtime-generated
    # __NSXPCInterfaceProxy_<ProtocolName> (verified at runtime:
    # __NSXPCInterfaceProxy_DuoExtensionControl), which does NOT formally
    # conform to the protocol - so registering against the protocol name alone
    # is never consulted. Register against that concrete proxy class name (its
    # name is deterministic from the protocol name); keep the protocol name too
    # as a harmless belt-and-suspenders.
    for _control_class in (
        b"__NSXPCInterfaceProxy_DuoExtensionControl",
        b"DuoExtensionControl",
    ):
      for _control_selector in (
        b"publishGeneration:reply:",
        b"retireGeneration:reply:",
        b"deleteGeneration:reply:",
      ):
        objc.registerMetaDataForSelector(
            _control_class,
            _control_selector,
            {
                "arguments": {
                    3: {
                        "type": b"@?",
                        "callable": {
                            "retval": {"type": b"v"},
                            "arguments": {
                                # Block self. For an OUTGOING block (one PyObjC
                                # CREATES and sends over NSXPC), the remote side
                                # verifies our block's NSMethodSignature against
                                # the clang-derived one, whose arg 0 is the real
                                # block type '@?' ({isObject,isBlock}). PyObjC's
                                # '^v' (opaque void*) works for the INCOMING
                                # fetch blocks above (received, not created) but
                                # here makes the wire sig arg0='^v' mismatch the
                                # extension's local '@?', so NSXPC rejects the
                                # message as undecodable ("incompatible reply
                                # block signature") and publishGeneration is
                                # never even invoked. Same INCOMING-vs-OUTGOING
                                # asymmetry as the BOOL 'Z' vs 'B' fix below.
                                0: {"type": b"@?"},
                                # BOOL ack. For an OUTGOING block PyObjC feeds
                                # the arg encoding to NSGetSizeAndAlignment,
                                # which rejects PyObjC's 'Z' (_C_NSBOOL) alias
                                # ("unsupported type encoding spec 'Z'"). Use the
                                # real ARM64 BOOL=_Bool encoding 'B' (_C_BOOL).
                                1: {"type": objc._C_BOOL},
                                2: {"type": b"@"},
                            },
                        },
                    }
                }
            },
        )

    class _ExtensionCallbackAdapter(NSObject):
        """Exported ``DuoHostCallback`` object - what the extension calls.

        Arguments, including retained reply blocks, cross to the Qt thread.
        The backend owns pending replies; no wire logic runs on the XPC queue.
        """

        def initWithClient_(self, client):
            self = objc.super(_ExtensionCallbackAdapter, self).init()
            if self is None:
                return None
            self._client = client
            return self

        def openFetch_entryId_reply_(self, generation_id, entry_index, reply):
            self._client._dispatch_extension_call(
                "open", generation_id, entry_index, reply
            )

        def pullChunk_reply_(self, fetch_token, reply):
            self._client._dispatch_extension_call("pull", fetch_token, reply)

        def cancelFetch_(self, fetch_token):
            self._client._dispatch_extension_call("cancel", fetch_token)

else:  # pragma: no cover - exercised implicitly on non-darwin
    _ExtensionCallbackAdapter = None


class FileProviderServiceClient(QObject):
    """NSXPC client: discovery, connection lifecycle, and Qt marshalling.

    ``connect_service()`` is idempotent while a connection is bound; after a
    ``disconnected`` signal it re-discovers on the next call. Extension callbacks
    reach registered callbacks (``set_callbacks``) via ``QMetaObject.invokeMethod``
    with ``Qt.AutoConnection`` - Direct when already on this object's thread
    (what ``fp_fake_service.simulate_extension_call`` exercises synchronously),
    Queued when the call arrives on NSXPC's private queue. This mirrors the
    COM->Qt boundary idiom in ``windows_files.py``'s ``ServiceCallbackGateway`` /
    ``post_to_service``, simplified for a single generic payload rather than
    per-slot declared C++ parameter types.
    """

    connected = Signal()
    disconnected = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._domain_identifier: str | None = None
        self._connection = None
        self._exported = None
        self._callbacks: dict[str, Callable | None] = {
            "open_fetch": None,
            "pull_chunk": None,
            "cancel_fetch": None,
        }
        # One lock guards ALL mutable connection state - _connection,
        # _exported, _connected, _disconnect_emitted, and the _connecting
        # claim below. Signal emits and ObjC/Qt call-outs
        # (connection.resume(), self.connected/disconnected.emit()) always
        # happen OUTSIDE the lock so a signal handler that re-enters
        # (e.g. a `disconnected` slot calling connect_service()) can never
        # deadlock against a critical section we still hold.
        self._lock = threading.Lock()
        self._connected = False
        self._disconnect_emitted = False
        #: True only while one connect_service() call is between claiming the
        #: slot and finishing its bind. Two concurrent connect_service() calls
        #: must not both pass the guard and double-bind (leaking the first
        #: connection and emitting `connected` twice).
        self._connecting = False

    # -------------------------------------------------------------- configuration

    def set_domain(self, domain_identifier: str) -> None:
        self._domain_identifier = domain_identifier

    def set_callbacks(
        self,
        open_fetch: Callable | None = None,
        pull_chunk: Callable | None = None,
        cancel_fetch: Callable | None = None,
    ) -> None:
        """Register the host-exported object's methods as Qt-thread callables.

        Open receives (generation, index, reply); pull receives (token, reply).
        Cancel receives the token. All run on this object's Qt thread.
        """
        self._callbacks["open_fetch"] = open_fetch
        self._callbacks["pull_chunk"] = pull_chunk
        self._callbacks["cancel_fetch"] = cancel_fetch

    # ------------------------------------------------------------------------ state

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def remote(self) -> object | None:
        """``remoteObjectProxy`` typed to ``DuoExtensionControl`` - or None."""
        with self._lock:
            connection = self._connection
        if connection is None:
            return None
        return connection.remoteObjectProxy()

    def remote_with_error_handler(self, handler) -> object | None:
        """Same proxy as ``remote()`` but with an XPC error handler, so a
        dropped/failed message (connection rejected by the appex, appex crash,
        etc.) surfaces via ``handler`` instead of the reply block silently
        never firing."""
        with self._lock:
            connection = self._connection
        if connection is None:
            return None
        return connection.remoteObjectProxyWithErrorHandler_(handler)

    # -------------------------------------------------------------------- lifecycle

    def connect_service(self) -> None:
        """Discover the domain's service connection and bind it.

        Idempotent: a call while already bound to a connection is a no-op.
        Concurrency-safe: the check-then-claim below is atomic under the lock,
        so two racing calls (e.g. a manual retry racing a reconnect fired from
        a ``disconnected`` handler) cannot both proceed to bind - the second
        sees the ``_connecting`` claim and returns. After ``disconnected``
        fires, ``_connection`` is cleared, so the next call re-discovers
        rather than reusing a dead endpoint.
        """
        if not _XPC_AVAILABLE:
            raise RuntimeError(
                "FileProviderServiceClient requires PyObjC + the FileProvider "
                "framework, available on darwin only"
            )
        if self._domain_identifier is None:
            raise RuntimeError("set_domain() must be called before connect_service()")
        with self._lock:
            if self._connection is not None or self._connecting:
                return
            self._connecting = True
        try:
            connection = self._discover_connection(self._domain_identifier)
            self._bind_connection(connection)
        finally:
            with self._lock:
                self._connecting = False

    def _bind_connection(self, connection) -> None:
        """Wire interfaces, handlers, and resume - shared by real discovery
        and by tests (fp_fake_service.grant_connection injects a fake
        connection straight into this method, bypassing discovery).

        The ObjC call-outs (setters, ``resume``) and the ``connected`` emit
        happen outside the lock; only the state publication is locked, so a
        ``connected`` handler that re-enters this object cannot deadlock.
        """
        exported = (
            _ExtensionCallbackAdapter.alloc().initWithClient_(self)
            if _XPC_AVAILABLE
            else None
        )
        connection.setExportedInterface_(host_interface())
        connection.setExportedObject_(exported)
        connection.setRemoteObjectInterface_(extension_interface())
        connection.setInvalidationHandler_(self._on_invalidated)
        connection.setInterruptionHandler_(self._on_interrupted)
        connection.resume()
        _log_service_event("fp_host_xpc_connection_resumed")
        with self._lock:
            self._disconnect_emitted = False
            self._exported = exported
            self._connection = connection
            self._connected = True
        self.connected.emit()

    def _on_invalidated(self) -> None:
        self._handle_disconnect("invalidated")

    def _on_interrupted(self) -> None:
        self._handle_disconnect("interrupted")

    def _handle_disconnect(self, reason: str) -> None:
        """Emit ``disconnected`` exactly once and drop the proxy.

        Both the invalidation and interruption handlers, and a second call
        to either (a real connection can call both, and the fake's
        ``invalidate()`` is required to be idempotent - see the task-3
        brief), funnel through this one guarded path.
        """
        with self._lock:
            if self._disconnect_emitted:
                return
            self._disconnect_emitted = True
            self._connected = False
            self._connection = None
            self._exported = None
        self.disconnected.emit(reason)

    # ------------------------------------------------------ extension -> host seam

    def _dispatch_extension_call(self, kind: str, *args) -> None:
        """Plain-Python seam driven by both the real exported adapter and
        the test fake (fp_fake_service.simulate_extension_call) - see
        decision 4 in the task-3 brief. Marshals onto this object's Qt
        thread via ``QMetaObject.invokeMethod`` with ``Qt.AutoConnection``.
        """
        delivered = QMetaObject.invokeMethod(
            self,
            "_run_dispatch",
            Qt.ConnectionType.AutoConnection,
            Q_ARG("QVariant", (kind, args)),
        )
        if not delivered:
            logger.error("failed to marshal extension call %r onto the Qt thread", kind)

    @Slot("QVariant")
    def _run_dispatch(self, payload) -> None:
        # PySide6 boxes tuples handed through QVariant as lists; unpacking
        # below is agnostic to that (list and tuple unpack identically).
        kind, args = payload
        args = list(args)
        reply = args[-1] if kind in ("open", "pull") and callable(args[-1]) else None
        settled = False

        def once(*values):
            nonlocal settled
            if not settled:
                settled = True
                reply(*values)

        if reply is not None:
            args[-1] = once
        callback = self._callbacks.get(_KIND_TO_CALLBACK.get(kind, ""))
        if callback is None:
            logger.debug("no callback registered for extension call kind=%r", kind)
            if reply is not None:
                once(None, None if kind == "open" else False, _xpc_error(8))
            return
        try:
            callback(*args)
        except Exception:  # noqa: BLE001 - must not escape a Qt slot / XPC callback
            logger.exception("extension callback %r raised", kind)
            if reply is not None:
                once(None, None if kind == "open" else False, _xpc_error(7))

    # ------------------------------------------------------------------- discovery

    def _discover_connection(self, domain_identifier: str):
        domain = self._resolve_domain(domain_identifier)
        if domain is None:
            raise RuntimeError(
                f"no NSFileProviderDomain registered for identifier {domain_identifier!r}"
            )
        manager = NSFileProviderManager.managerForDomain_(domain)
        service = self._resolve_service(manager)
        if service is None:
            raise RuntimeError(f"extension did not vend the {SERVICE_NAME!r} service")
        connection = self._resolve_connection(service)
        if connection is None:
            raise RuntimeError("extension service did not hand back an NSXPCConnection")
        return connection

    @staticmethod
    def _resolve_domain(identifier: str):
        outcome: dict[str, object] = {}
        done = threading.Event()

        def handler(domains, error):
            outcome["domains"] = domains
            outcome["error"] = error
            done.set()

        NSFileProviderManager.getDomainsWithCompletionHandler_(handler)
        if not done.wait(_GET_DOMAINS_TIMEOUT_S):
            raise TimeoutError("timed out listing NSFileProviderDomains")
        error = outcome.get("error")
        if error is not None:
            raise RuntimeError(f"getDomainsWithCompletionHandler failed: {error}")
        for domain in outcome.get("domains") or []:
            if str(domain.identifier()) == identifier:
                return domain
        return None

    @staticmethod
    def _resolve_service(manager):
        outcome: dict[str, object] = {}
        done = threading.Event()

        def handler(service, error):
            _log_service_event(
                "fp_host_get_service_completion",
                service_present=service is not None,
                error=repr(error),
            )
            outcome["service"] = service
            outcome["error"] = error
            if service is not None:
                _log_service_event("fp_host_endpoint_received")
            done.set()

        _log_service_event(
            "fp_host_get_service_begin",
            service_name=SERVICE_NAME,
            item_identifier=NSFileProviderRootContainerItemIdentifier,
        )
        manager.getServiceWithName_itemIdentifier_completionHandler_(
            NSFileProviderServiceName(SERVICE_NAME),
            NSFileProviderRootContainerItemIdentifier,
            handler,
        )
        if not done.wait(_GET_SERVICE_TIMEOUT_S):
            _log_service_event(
                "fp_host_get_service_timeout", timeout_s=_GET_SERVICE_TIMEOUT_S
            )
            raise TimeoutError("timed out requesting the File Provider service")
        error = outcome.get("error")
        if error is not None:
            raise RuntimeError(f"getServiceWithName failed: {error}")
        return outcome.get("service")

    @staticmethod
    def _resolve_connection(service):
        outcome: dict[str, object] = {}
        done = threading.Event()

        def handler(connection, error):
            outcome["connection"] = connection
            outcome["error"] = error
            done.set()

        service.getFileProviderConnectionWithCompletionHandler_(handler)
        if not done.wait(_GET_CONNECTION_TIMEOUT_S):
            raise TimeoutError("timed out obtaining the NSXPCConnection")
        error = outcome.get("error")
        if error is not None:
            raise RuntimeError(
                f"getFileProviderConnectionWithCompletionHandler failed: {error}"
            )
        return outcome.get("connection")


__all__ = ["FileProviderServiceClient", "SERVICE_NAME"]
