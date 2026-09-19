"""Python domain lifecycle manager for the Duo Input File Provider domain.

Owns exactly one state machine::

    ABSENT --ensure_domain()--> REGISTERING --add ok--> WAITING_ENABLED
      WAITING_ENABLED --probe ok--> READY
      WAITING_ENABLED --probe: -2011, backoff not exhausted--> WAITING_ENABLED (retry)
      WAITING_ENABLED --probe: -2011, backoff exhausted--> DEGRADED
      WAITING_ENABLED --probe: other error--> DEGRADED
      REGISTERING --add fails--> DEGRADED
      * --remove_domain()--> REMOVING --remove ok--> ABSENT
      * --remove_domain()--> REMOVING --remove fails--> DEGRADED

Task 16 is what actually routes offers to staging while ``DEGRADED``; this
module only detects the condition and announces it via ``degraded(reason)``.
It never tries to *fix* a stuck ``fileproviderd`` - no ``killall``, no
``launchctl kickstart``, no touching any private/system store. Persistent
``NSFileProviderErrorProviderNotFound`` (-2011) after the backoff schedule
below is exhausted is a fact to report, not a fault to repair.

Pure Qt/Python state machine - no business logic lives here beyond "is the
domain enabled yet". Everything that actually talks to the Objective-C
runtime is behind the ``_FileProviderRealAdapter`` below, gated on
``_FP_AVAILABLE`` exactly like ``fileprovider_client.py`` gates its own
PyObjC/FileProvider imports, so importing this module on a non-darwin
collection run does not hard-crash. Tests never construct the real adapter -
they inject a fake ``manager`` (duck-typed: ``add_domain``, ``remove_domain``,
``probe_ready``, each ``(identifier, *args, completion)`` where ``completion``
is called with ``None`` on success or an NSError-shaped object (``.code()``,
``.localizedDescription()``) on failure) and a fake ``schedule`` callable
(``(delay_ms, callback) -> None``) so the backoff schedule advances
synchronously, without ``time.sleep`` or a real event loop wait.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal

logger = logging.getLogger(__name__)

try:
    import objc
    from FileProvider import (
        NSFileProviderDomain,
        NSFileProviderManager,
        NSFileProviderRootContainerItemIdentifier,
    )

    _FP_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised implicitly on non-darwin
    objc = None
    NSFileProviderDomain = None
    NSFileProviderManager = None
    NSFileProviderRootContainerItemIdentifier = None
    _FP_AVAILABLE = False


#: The one domain this app ever registers. Not user-configurable: a second
#: domain would mean a second root, a second replica, a second everything -
#: out of scope for a single-user clipboard/file bridge.
DOMAIN_IDENTIFIER = "DuoInput"

#: Human-facing name Finder would show for the domain's root, if it ever
#: surfaces one (e.g. in an error dialog). Cosmetic only.
DOMAIN_DISPLAY_NAME = "Duo Input"

#: NSFileProviderErrorProviderNotFound - what a probe returns while
#: fileproviderd has registered the domain but not finished enabling it yet.
#: Transient during the enable window; only a problem if it never clears.
ERROR_PROVIDER_NOT_FOUND = -2011

#: Pause before re-probing readiness: growing, capped - same idiom as
#: clipboard/coordinator.py's RECONNECT_DELAYS_MS (see readiness_delay_ms
#: below), applied to domain-enable polling instead of TCP reconnects.
#: Endless tight polling would just be background CPU spend disguised as
#: patience; a fixed number of rungs bounds how long WAITING_ENABLED can
#: last before this reports DEGRADED instead of hanging forever.
READINESS_DELAYS_MS = (250, 500, 1000, 2000, 4000)


def readiness_delay_ms(attempt: int) -> int:
    """How long to wait before readiness-probe attempt number ``attempt``,
    counting from zero. Mirrors
    ``clipboard.coordinator.reconnect_delay_ms``: a negative attempt (guard
    against a bad call) gets the same pause as the very first attempt,
    rather than a negative delay or an exception.
    """
    if attempt < 0:
        return READINESS_DELAYS_MS[0]
    return READINESS_DELAYS_MS[min(attempt, len(READINESS_DELAYS_MS) - 1)]


class DomainState(StrEnum):
    ABSENT = "absent"
    REGISTERING = "registering"
    WAITING_ENABLED = "waiting_enabled"
    READY = "ready"
    DEGRADED = "degraded"
    REMOVING = "removing"


class _CodeError:
    """Minimal NSError-shaped error (``.code()``, ``.localizedDescription()``)
    for conditions the real adapter detects itself rather than receiving from
    a completion handler (e.g. "domain missing from
    getDomainsWithCompletionHandler_'s results"). Shaped identically to a
    real ``NSError`` so ``_error_code``/``_describe_error`` below treat both
    uniformly, and so a test fake can use the exact same shape.
    """

    def __init__(self, code: int, message: str) -> None:
        self._code = code
        self._message = message

    def code(self) -> int:
        return self._code

    def localizedDescription(self) -> str:  # noqa: N802 - mirrors NSError's selector
        return self._message


def _error_code(error: object) -> int | None:
    code_getter = getattr(error, "code", None)
    if callable(code_getter):
        return int(code_getter())
    return None


def _describe_error(error: object) -> str:
    describe = getattr(error, "localizedDescription", None)
    if callable(describe):
        return str(describe())
    return str(error)


def _qtimer_schedule(delay_ms: int, callback: Callable[[], None]) -> None:
    QTimer.singleShot(delay_ms, callback)


class _FileProviderRealAdapter:
    """The real ``manager`` - talks to ``NSFileProviderManager``. Only ever
    instantiated when ``FileProviderDomainManager()`` is built without an
    injected fake, i.e. in production, never in tests.

    ``probe_ready`` asks the domain's manager to signal its root enumerator.
    That call is a no-op if the provider is already up to date, but it fails
    with ``NSFileProviderErrorProviderNotFound`` while the domain hasn't
    finished enabling yet - exactly the readiness signal this module polls
    for. Nothing here ever shells out or touches a private store.
    """

    def add_domain(self, identifier: str, display_name: str, completion: Callable) -> None:
        if not _FP_AVAILABLE:
            completion(_CodeError(0, "PyObjC FileProvider framework unavailable"))
            return
        domain = NSFileProviderDomain.alloc().initWithIdentifier_displayName_(
            identifier, display_name
        )
        NSFileProviderManager.addDomain_completionHandler_(domain, completion)

    def remove_domain(self, identifier: str, completion: Callable) -> None:
        if not _FP_AVAILABLE:
            completion(_CodeError(0, "PyObjC FileProvider framework unavailable"))
            return

        def on_domains(domains, error):
            if error is not None:
                completion(error)
                return
            target = next(
                (d for d in (domains or []) if str(d.identifier()) == identifier), None
            )
            if target is None:
                completion(None)  # already absent - removal is idempotent
                return
            NSFileProviderManager.removeDomain_completionHandler_(target, completion)

        NSFileProviderManager.getDomainsWithCompletionHandler_(on_domains)

    def probe_ready(self, identifier: str, completion: Callable) -> None:
        if not _FP_AVAILABLE:
            completion(_CodeError(0, "PyObjC FileProvider framework unavailable"))
            return

        def on_domains(domains, error):
            if error is not None:
                completion(error)
                return
            target = next(
                (d for d in (domains or []) if str(d.identifier()) == identifier), None
            )
            if target is None:
                completion(_CodeError(ERROR_PROVIDER_NOT_FOUND, "domain not registered"))
                return
            manager = NSFileProviderManager.managerForDomain_(target)
            if manager is None:
                completion(_CodeError(ERROR_PROVIDER_NOT_FOUND, "no manager for domain"))
                return
            manager.signalEnumeratorForContainerItemIdentifier_completionHandler_(
                NSFileProviderRootContainerItemIdentifier, completion
            )

        NSFileProviderManager.getDomainsWithCompletionHandler_(on_domains)


class FileProviderDomainManager(QObject):
    """Drives the ``DuoInput`` File Provider domain to ``READY``.

    ``manager`` and ``schedule`` are injection points for tests (see module
    docstring for their shapes); production code leaves both at their
    defaults (the real ``NSFileProviderManager`` adapter, and
    ``QTimer.singleShot``).
    """

    state_changed = Signal(str)
    ready = Signal()
    degraded = Signal(str)

    def __init__(
        self,
        *,
        manager: object | None = None,
        schedule: Callable[[int, Callable[[], None]], None] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager if manager is not None else _FileProviderRealAdapter()
        self._schedule = schedule if schedule is not None else _qtimer_schedule
        self._state = DomainState.ABSENT
        self._attempt = 0
        self._ready_emitted = False
        #: Monotonic operation id. Every ``ensure_domain()``/``remove_domain()``
        #: bumps it; each async completion and each backoff-timer callback
        #: captures the value current at the moment it was scheduled and
        #: refuses to apply its transition if it no longer matches (see
        #: ``_is_current``). This is how a superseded cycle's stale ``add``/
        #: ``probe``/``remove`` completion - or a backoff-retry timer that can't
        #: be un-scheduled - is neutralised rather than allowed to clobber the
        #: current one (e.g. re-driving to READY after a remove).
        self._generation = 0

    @property
    def domain_identifier(self) -> str:
        return DOMAIN_IDENTIFIER

    @property
    def state(self) -> DomainState:
        return self._state

    @property
    def is_ready(self) -> bool:
        return self._state is DomainState.READY

    # ------------------------------------------------------------------ lifecycle

    def ensure_domain(self) -> None:
        """Add the domain if it isn't already, then poll to ``READY``.

        Idempotent while a previous call is still in flight or has already
        succeeded (``REGISTERING``/``WAITING_ENABLED``/``READY``): a second
        call is a pure no-op rather than a duplicate ``addDomain``. Callable
        again from ``ABSENT``, ``DEGRADED`` or ``REMOVING`` to (re)start the
        whole flow from scratch - a degraded domain is not a dead end, and a
        remove that is still in flight does not block a fresh cycle: bumping
        the generation below makes the superseded remove's late completion a
        no-op (see ``_is_current``).
        """
        if self._state in (
            DomainState.REGISTERING,
            DomainState.WAITING_ENABLED,
            DomainState.READY,
        ):
            return
        generation = self._bump_generation()
        self._attempt = 0
        self._ready_emitted = False
        self._set_state(DomainState.REGISTERING)
        self._guarded_manager_call(
            generation,
            lambda: self._manager.add_domain(
                DOMAIN_IDENTIFIER,
                DOMAIN_DISPLAY_NAME,
                lambda error: self._on_add_complete(generation, error),
            ),
            "failed to add domain",
        )

    def remove_domain(self) -> None:
        generation = self._bump_generation()
        self._set_state(DomainState.REMOVING)
        self._guarded_manager_call(
            generation,
            lambda: self._manager.remove_domain(
                DOMAIN_IDENTIFIER,
                lambda error: self._on_remove_complete(generation, error),
            ),
            "failed to remove domain",
        )

    # -------------------------------------------------------------- generations

    def _bump_generation(self) -> int:
        self._generation += 1
        return self._generation

    def _is_current(self, generation: int) -> bool:
        return generation == self._generation

    def _guarded_manager_call(
        self, generation: int, call: Callable[[], None], failure_reason: str
    ) -> None:
        """Invoke an injected-manager call, degrading instead of propagating.

        A synchronous raise from the real adapter (e.g. an ObjC bridging
        exception) must not escape a Qt-timer-driven callback and crash the
        app: it is routed through ``_degrade`` like any other failure. The
        generation guard means a raise from a call whose cycle has already
        been superseded is swallowed silently rather than degrading a newer,
        unrelated cycle.
        """
        try:
            call()
        except Exception as exc:  # noqa: BLE001 - must not escape into the Qt loop
            if self._is_current(generation):
                self._degrade(f"{failure_reason}: {exc!r}")
            else:
                logger.debug("ignoring raise from superseded manager call: %r", exc)

    # ---------------------------------------------------------------- completions

    def _on_add_complete(self, generation: int, error: object | None) -> None:
        if not self._is_current(generation):
            return
        if error is not None:
            self._degrade(f"failed to add domain: {_describe_error(error)}")
            return
        self._set_state(DomainState.WAITING_ENABLED)
        self._probe(generation)

    def _probe(self, generation: int) -> None:
        if not self._is_current(generation):
            return
        self._guarded_manager_call(
            generation,
            lambda: self._manager.probe_ready(
                DOMAIN_IDENTIFIER,
                lambda error: self._on_probe_complete(generation, error),
            ),
            "domain probe raised",
        )

    def _on_probe_complete(self, generation: int, error: object | None) -> None:
        if not self._is_current(generation):
            return
        if error is None:
            self._set_state(DomainState.READY)
            if not self._ready_emitted:
                self._ready_emitted = True
                self.ready.emit()
            return
        if _error_code(error) != ERROR_PROVIDER_NOT_FOUND:
            self._degrade(f"domain probe failed: {_describe_error(error)}")
            return
        if self._attempt >= len(READINESS_DELAYS_MS):
            self._degrade(
                "domain did not enable within the backoff schedule "
                f"(NSFileProviderErrorProviderNotFound persists: {_describe_error(error)})"
            )
            return
        delay_ms = readiness_delay_ms(self._attempt)
        self._attempt += 1
        self._schedule(delay_ms, lambda: self._probe(generation))

    def _on_remove_complete(self, generation: int, error: object | None) -> None:
        if not self._is_current(generation):
            return
        if error is not None:
            self._degrade(f"failed to remove domain: {_describe_error(error)}")
            return
        self._attempt = 0
        self._ready_emitted = False
        self._set_state(DomainState.ABSENT)

    def _degrade(self, reason: str) -> None:
        self._set_state(DomainState.DEGRADED)
        logger.warning("file provider domain degraded: %s", reason)
        self.degraded.emit(reason)

    def _set_state(self, state: DomainState) -> None:
        if state is self._state:
            return
        self._state = state
        self.state_changed.emit(state.value)


__all__ = [
    "DOMAIN_DISPLAY_NAME",
    "DOMAIN_IDENTIFIER",
    "ERROR_PROVIDER_NOT_FOUND",
    "READINESS_DELAYS_MS",
    "DomainState",
    "FileProviderDomainManager",
    "readiness_delay_ms",
]
