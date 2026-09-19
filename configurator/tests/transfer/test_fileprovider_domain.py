"""State-machine tests for ``FileProviderDomainManager`` (Task 6).

Pure Python/Qt logic - no PyObjC, no real ``NSFileProviderManager``, no
``time.sleep``. ``_FakeManager`` stands in for the injected ``manager``
(``add_domain``/``remove_domain``/``probe_ready``, each completed
synchronously by the test driving it) and ``_FakeSchedule`` stands in for
the injected backoff ``schedule`` callable: instead of a real ``QTimer``
wait, it records ``(delay_ms, callback)`` and lets the test fire callbacks
whenever it chooses, so the backoff delay sequence is asserted directly
rather than by waiting for it to elapse.
"""
from __future__ import annotations

from duo_input.transfer.fileprovider_domain import (
    DOMAIN_IDENTIFIER,
    ERROR_PROVIDER_NOT_FOUND,
    READINESS_DELAYS_MS,
    DomainState,
    FileProviderDomainManager,
)


class _FakeError:
    """NSError-shaped fake: ``.code()`` and ``.localizedDescription()``,
    exactly what the real adapter's completion handlers would hand the
    manager - see the module docstring's description of the injected
    ``manager`` shape.
    """

    def __init__(self, code: int, message: str = "") -> None:
        self._code = code
        self._message = message or f"error {code}"

    def code(self) -> int:
        return self._code

    def localizedDescription(self) -> str:
        return self._message


class _FakeManager:
    """Records every call the domain manager makes and lets the test
    complete them on demand.

    Probes are always driven explicitly via ``complete_next_probe``. ``add``
    and ``remove`` complete synchronously by default (matching the common
    happy-path shape); pass ``defer_add=True``/``defer_remove=True`` to queue
    their completions instead, so a test can drive the state machine into a
    later cycle and *then* fire the earlier cycle's completion late - the
    stale-completion race in finding I1.
    """

    def __init__(self, *, defer_add: bool = False, defer_remove: bool = False) -> None:
        self.add_calls: list[tuple[str, str]] = []
        self.remove_calls: list[str] = []
        self._add_result: object | None = None
        self._defer_add = defer_add
        self._defer_remove = defer_remove
        self._pending_add_completions: list = []
        self._pending_remove_completions: list = []
        self._pending_probe_completions: list = []
        self.probe_count = 0

    def set_add_result(self, error: object | None) -> None:
        self._add_result = error

    def add_domain(self, identifier: str, display_name: str, completion) -> None:
        self.add_calls.append((identifier, display_name))
        if self._defer_add:
            self._pending_add_completions.append(completion)
        else:
            completion(self._add_result)

    def remove_domain(self, identifier: str, completion) -> None:
        self.remove_calls.append(identifier)
        if self._defer_remove:
            self._pending_remove_completions.append(completion)
        else:
            completion(None)

    def probe_ready(self, identifier: str, completion) -> None:
        self.probe_count += 1
        self._pending_probe_completions.append(completion)

    def complete_next_add(self, error: object | None) -> None:
        completion = self._pending_add_completions.pop(0)
        completion(error)

    def complete_next_remove(self, error: object | None) -> None:
        completion = self._pending_remove_completions.pop(0)
        completion(error)

    def complete_next_probe(self, error: object | None) -> None:
        completion = self._pending_probe_completions.pop(0)
        completion(error)


class _FakeSchedule:
    """Injected in place of ``QTimer.singleShot``: records the delay
    requested for every scheduled callback instead of actually waiting, so
    the test can assert the delay sequence and fire callbacks synchronously.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[int, object]] = []
        self.delays: list[int] = []

    def __call__(self, delay_ms: int, callback) -> None:
        self.calls.append((delay_ms, callback))
        self.delays.append(delay_ms)

    def fire_next(self) -> None:
        _, callback = self.calls.pop(0)
        callback()


def _make(manager: _FakeManager | None = None, schedule: _FakeSchedule | None = None):
    manager = manager if manager is not None else _FakeManager()
    schedule = schedule if schedule is not None else _FakeSchedule()
    domain = FileProviderDomainManager(manager=manager, schedule=schedule)
    return domain, manager, schedule


def test_domain_identifier_is_duo_input():
    domain, _, _ = _make()
    assert domain.domain_identifier == "DuoInput"
    assert DOMAIN_IDENTIFIER == "DuoInput"


def test_add_success_moves_through_registering_to_waiting_enabled():
    domain, manager, _ = _make()
    manager.set_add_result(None)
    states = []
    domain.state_changed.connect(states.append)

    domain.ensure_domain()

    assert manager.add_calls == [("DuoInput", "Duo Input")]
    # REGISTERING fires first, then the add completes synchronously and the
    # probe is issued, moving to WAITING_ENABLED - no READY yet since the
    # fake probe hasn't been completed.
    assert states == [DomainState.REGISTERING.value, DomainState.WAITING_ENABLED.value]
    assert domain.state is DomainState.WAITING_ENABLED
    assert manager.probe_count == 1
    assert not domain.is_ready


def test_probe_enabled_reaches_ready_and_emits_ready_once():
    domain, manager, _ = _make()
    manager.set_add_result(None)
    ready_events = []
    domain.ready.connect(lambda: ready_events.append(1))

    domain.ensure_domain()
    manager.complete_next_probe(None)

    assert domain.state is DomainState.READY
    assert domain.is_ready
    assert ready_events == [1]


def test_persistent_provider_not_found_backs_off_with_increasing_delays_then_ready():
    domain, manager, schedule = _make()
    manager.set_add_result(None)
    ready_events = []
    domain.ready.connect(lambda: ready_events.append(1))

    domain.ensure_domain()

    # Fail with -2011 three times; each failure must schedule exactly one
    # retry, at a strictly larger delay than the previous one, and must not
    # call time.sleep or block - firing is entirely driven by the test.
    for _ in range(3):
        manager.complete_next_probe(_FakeError(ERROR_PROVIDER_NOT_FOUND))
        assert len(schedule.calls) >= 1
        schedule.fire_next()

    assert schedule.delays == list(READINESS_DELAYS_MS[:3])
    assert schedule.delays == sorted(schedule.delays)
    assert len(set(schedule.delays)) == len(schedule.delays)  # strictly increasing

    # Fourth probe finally succeeds.
    manager.complete_next_probe(None)

    assert domain.state is DomainState.READY
    assert ready_events == [1]


def test_provider_not_found_exhausting_backoff_schedule_degrades():
    domain, manager, schedule = _make()
    manager.set_add_result(None)
    degraded_reasons = []
    domain.degraded.connect(degraded_reasons.append)

    domain.ensure_domain()

    # Fail -2011 for every rung of the schedule, then one more time past the
    # end: that last one must degrade instead of scheduling a 6th retry.
    for _ in range(len(READINESS_DELAYS_MS)):
        manager.complete_next_probe(_FakeError(ERROR_PROVIDER_NOT_FOUND))
        schedule.fire_next()
    manager.complete_next_probe(_FakeError(ERROR_PROVIDER_NOT_FOUND))

    assert domain.state is DomainState.DEGRADED
    assert len(degraded_reasons) == 1
    assert "-2011" in degraded_reasons[0] or "ProviderNotFound" in degraded_reasons[0]
    assert not schedule.calls  # no dangling retry was scheduled past the limit


def test_probe_error_other_than_provider_not_found_degrades_immediately():
    domain, manager, schedule = _make()
    manager.set_add_result(None)
    degraded_reasons = []
    domain.degraded.connect(degraded_reasons.append)

    domain.ensure_domain()
    manager.complete_next_probe(_FakeError(-1000, "something else entirely"))

    assert domain.state is DomainState.DEGRADED
    assert degraded_reasons == ["domain probe failed: something else entirely"]
    assert not schedule.calls


def test_add_failure_degrades_without_probing():
    domain, manager, schedule = _make()
    manager.set_add_result(_FakeError(-2000, "kaboom"))
    degraded_reasons = []
    domain.degraded.connect(degraded_reasons.append)

    domain.ensure_domain()

    assert domain.state is DomainState.DEGRADED
    assert degraded_reasons == ["failed to add domain: kaboom"]
    assert manager.probe_count == 0
    assert not schedule.calls


def test_ensure_domain_is_a_no_op_while_already_in_flight_or_ready():
    domain, manager, _ = _make()
    manager.set_add_result(None)

    domain.ensure_domain()
    assert manager.add_calls == [("DuoInput", "Duo Input")]

    # Still WAITING_ENABLED (probe not completed) - a second call must not
    # issue a second addDomain.
    domain.ensure_domain()
    assert manager.add_calls == [("DuoInput", "Duo Input")]

    manager.complete_next_probe(None)
    assert domain.state is DomainState.READY

    domain.ensure_domain()
    assert manager.add_calls == [("DuoInput", "Duo Input")]


def test_ensure_domain_retries_from_degraded():
    domain, manager, _ = _make()
    manager.set_add_result(_FakeError(-2000, "kaboom"))
    domain.ensure_domain()
    assert domain.state is DomainState.DEGRADED

    manager.set_add_result(None)
    domain.ensure_domain()
    assert manager.add_calls == [("DuoInput", "Duo Input"), ("DuoInput", "Duo Input")]
    assert domain.state is DomainState.WAITING_ENABLED

    manager.complete_next_probe(None)
    assert domain.state is DomainState.READY


def test_remove_domain_transitions_through_removing_to_absent():
    domain, manager, _ = _make()
    manager.set_add_result(None)
    domain.ensure_domain()
    manager.complete_next_probe(None)
    assert domain.state is DomainState.READY

    states = []
    domain.state_changed.connect(states.append)
    domain.remove_domain()

    assert manager.remove_calls == ["DuoInput"]
    assert states == [DomainState.REMOVING.value, DomainState.ABSENT.value]
    assert domain.state is DomainState.ABSENT
    assert not domain.is_ready


# --------------------------------------------------------------------- Fix round 1
# Finding I1: stale/overlapping async completions from a superseded cycle must
# not clobber the current one (generation guard + backoff-timer neutralisation).


def test_pending_backoff_timer_from_superseded_cycle_does_not_redrive_after_remove():
    # WAITING_ENABLED with a backoff-retry timer queued, then a remove
    # supersedes it: firing the stale timer must NOT issue a new probe or
    # re-drive to READY, and must NOT re-emit `ready` after removal.
    domain, manager, schedule = _make()
    manager.set_add_result(None)
    ready_events = []
    domain.ready.connect(lambda: ready_events.append(1))

    domain.ensure_domain()
    manager.complete_next_probe(_FakeError(ERROR_PROVIDER_NOT_FOUND))
    assert schedule.calls  # a backoff-retry timer is pending
    assert domain.state is DomainState.WAITING_ENABLED

    domain.remove_domain()  # supersedes the WAITING_ENABLED cycle
    assert domain.state is DomainState.ABSENT

    schedule.fire_next()  # the stale backoff timer fires late

    assert manager.probe_count == 1  # no new probe issued by the stale timer
    assert domain.state is DomainState.ABSENT
    assert ready_events == []


def test_stale_add_completion_after_remove_is_ignored():
    # remove_domain() during REGISTERING (add still in flight): when the
    # original add finally completes, its completion is stale and must be
    # ignored rather than driving into WAITING_ENABLED/probe.
    manager = _FakeManager(defer_add=True)
    domain, manager, _ = _make(manager=manager)
    ready_events = []
    domain.ready.connect(lambda: ready_events.append(1))

    domain.ensure_domain()
    assert domain.state is DomainState.REGISTERING

    domain.remove_domain()  # supersedes the in-flight add
    assert domain.state is DomainState.ABSENT

    manager.complete_next_add(None)  # the original add completes late

    assert domain.state is DomainState.ABSENT
    assert manager.probe_count == 0  # stale add did not start probing
    assert ready_events == []


def test_ensure_during_removing_is_not_clobbered_by_stale_remove_completion():
    # ensure_domain() called while a remove is still in flight starts a fresh
    # cycle; the earlier remove's late completion must NOT force ABSENT and
    # clobber the new cycle.
    manager = _FakeManager(defer_remove=True)
    domain, manager, _ = _make(manager=manager)
    manager.set_add_result(None)

    domain.ensure_domain()
    manager.complete_next_probe(None)
    assert domain.state is DomainState.READY

    domain.remove_domain()  # remove is deferred - stays REMOVING
    assert domain.state is DomainState.REMOVING

    domain.ensure_domain()  # fresh cycle supersedes the pending remove
    assert domain.state is DomainState.WAITING_ENABLED
    assert manager.add_calls == [("DuoInput", "Duo Input"), ("DuoInput", "Duo Input")]

    manager.complete_next_remove(None)  # the superseded remove completes late
    assert domain.state is DomainState.WAITING_ENABLED  # not clobbered to ABSENT

    manager.complete_next_probe(None)
    assert domain.state is DomainState.READY


# Finding I2: a synchronous raise from the injected manager must degrade, not
# propagate out of a Qt-timer-driven callback.


def test_add_raising_synchronously_degrades():
    class _RaisingManager(_FakeManager):
        def add_domain(self, identifier, display_name, completion):
            raise RuntimeError("objc bridge boom")

    domain, _, schedule = _make(manager=_RaisingManager())
    degraded_reasons = []
    domain.degraded.connect(degraded_reasons.append)

    domain.ensure_domain()  # must not raise

    assert domain.state is DomainState.DEGRADED
    assert len(degraded_reasons) == 1
    assert "objc bridge boom" in degraded_reasons[0]
    assert not schedule.calls


def test_probe_raising_synchronously_degrades():
    class _RaisingProbeManager(_FakeManager):
        def probe_ready(self, identifier, completion):
            raise RuntimeError("probe bridge boom")

    manager = _RaisingProbeManager()
    manager.set_add_result(None)
    domain, _, schedule = _make(manager=manager)
    degraded_reasons = []
    domain.degraded.connect(degraded_reasons.append)

    domain.ensure_domain()  # add ok -> WAITING_ENABLED -> probe raises, must not escape

    assert domain.state is DomainState.DEGRADED
    assert len(degraded_reasons) == 1
    assert "probe bridge boom" in degraded_reasons[0]
    assert not schedule.calls


def test_remove_raising_synchronously_degrades():
    class _RaisingRemoveManager(_FakeManager):
        def remove_domain(self, identifier, completion):
            raise RuntimeError("remove bridge boom")

    domain, _, _ = _make(manager=_RaisingRemoveManager())
    degraded_reasons = []
    domain.degraded.connect(degraded_reasons.append)

    domain.remove_domain()  # must not raise

    assert domain.state is DomainState.DEGRADED
    assert len(degraded_reasons) == 1
    assert "remove bridge boom" in degraded_reasons[0]
