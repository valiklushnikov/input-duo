"""Task 8: the privacy regression gate.

Copying (arming the clipboard with File Provider user-visible URLs) must
issue ZERO ``FILE_READ`` - reads only happen on Finder paste (a later task).
``test_arm_then_wait_sends_no_file_read`` is verbatim from the task-8 brief
and is the actual gate: any ``FILE_READ`` after arm-and-wait blocks this task.

The fake remote here never replies synchronously (``DeferredFakeRemote``,
mirrored from ``test_fileprovider_publish.py``) - that lets the test drive
the ACK side explicitly via the plan-mandated ``on_ack(transfer_id)`` test
API instead of racing a real reply. ``fake_resolver`` and ``fake_arm`` are
wired so the single root of ``_manifest()`` resolves to the literal string
``"abc123-root"``, which is what ``fake_arm.calls == [["abc123-root"]]``
checks for.
"""

from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal

import pytest

from duo_input.transfer.fileprovider_backend import FileProviderBackend
from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest


class DeferredFakeRemote:
    """Records the publish call but never replies - the test drives the ACK
    side itself via ``on_ack()`` instead of a synchronous fake reply."""

    def __init__(self) -> None:
        self.published: list[str] = []

    def publishGeneration_reply_(self, record: bytes, reply) -> None:
        import json

        transfer_id = json.loads(record.decode("utf-8"))["transfer_id"]
        self.published.append(transfer_id)
        # Deliberately never calls reply(...) - see module docstring.


class FakeClient:
    def __init__(self, remote_obj) -> None:
        self.remote_obj = remote_obj

    def remote(self):
        return self.remote_obj


class FakeDomain(QObject):
    """Stand-in for Task 6's ``FileProviderDomainManager``: exposes the same
    ``ready`` signal shape so the backend's constructor can connect to it,
    plus ``is_ready`` for the "already ready at construction time" check.
    Neither fires on its own here - the test drives readiness explicitly via
    ``fp_backend.on_domain_ready()``, per the plan-mandated test API.
    """

    ready = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.is_ready = False


class FakeResolver:
    """root_id -> "abc123-root", regardless of which root_id is asked -
    there is only one root in ``_manifest()`` and this is the exact URL the
    privacy assertion below checks the clipboard was armed with.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, root_id, completion) -> None:
        self.calls.append(root_id)
        completion("abc123-root", None)


class ControllableResolver:
    """External async resolver fake whose completions the test controls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def __call__(self, root_id, completion) -> None:
        self.calls.append((root_id, completion))

    def complete(self, index: int, url=None, error=None) -> None:
        self.calls[index][1](url, error)


class FakeArm:
    def __init__(self) -> None:
        self.calls: list[list] = []

    def __call__(self, urls) -> None:
        self.calls.append(list(urls))


class FakeLink:
    def __init__(self) -> None:
        self.sent: list = []

    def send(self, message) -> bool:
        self.sent.append(message)
        return True


def _manifest(transfer_id="abc123", paths=("a.txt",)):
    return TransferManifest(
        transfer_id=transfer_id,
        entries=tuple(
            TransferEntry(path=path, kind=ENTRY_FILE, size=5, mtime_ns=0)
            for path in paths
        ),
        skipped=(),
        drop_effect=1,
    )


@pytest.fixture
def fake_remote():
    return DeferredFakeRemote()


@pytest.fixture
def fake_client(fake_remote):
    return FakeClient(fake_remote)


@pytest.fixture
def fake_domain():
    return FakeDomain()


@pytest.fixture
def fake_resolver():
    return FakeResolver()


@pytest.fixture
def fake_arm():
    return FakeArm()


@pytest.fixture
def fake_link():
    return FakeLink()


@pytest.fixture
def fp_backend(qapp, fake_client, fake_domain, fake_arm, fake_resolver, fake_link):
    backend = FileProviderBackend(
        fake_client, fake_domain, fake_arm, url_resolver=fake_resolver
    )
    backend.attach_link(fake_link)
    return backend


# --- Step 1: the failing privacy regression test, verbatim from the brief.


def test_arm_then_wait_sends_no_file_read(
    fp_backend, fake_link, fake_arm, qtbot, caplog
):
    caplog.set_level(logging.INFO, logger="duo_input.transfer.fileprovider_backend")
    e = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(True, e)
    fp_backend.on_ack("abc123")  # simulate publish ACK
    fp_backend.on_domain_ready()  # simulate READY
    qtbot.wait(200)
    assert fake_arm.calls == [["abc123-root"]]  # armed
    assert "fp_clipboard_armed transfer_id=abc123" in caplog.text
    assert not any(m.type.name == "FILE_READ" for m in fake_link.sent)  # NO read


# --- Additional coverage for the ACK-AND-READY latch and arm-at-most-once.


def test_no_arm_before_domain_ready(fp_backend, fake_arm):
    e = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(True, e)
    fp_backend.on_ack("abc123")

    assert fake_arm.calls == []  # ACK alone is not enough


def test_no_arm_before_ack(fp_backend, fake_arm):
    e = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(True, e)
    fp_backend.on_domain_ready()

    assert fake_arm.calls == []  # READY alone is not enough


def test_ready_before_ack_still_arms_once_ack_arrives(fp_backend, fake_arm):
    e = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(True, e)
    fp_backend.on_domain_ready()  # READY first
    assert fake_arm.calls == []

    fp_backend.on_ack("abc123")  # ACK completes the latch

    assert fake_arm.calls == [["abc123-root"]]


def test_arm_does_not_repeat_if_both_signals_refire(fp_backend, fake_arm):
    e = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(True, e)
    fp_backend.on_ack("abc123")
    fp_backend.on_domain_ready()
    assert fake_arm.calls == [["abc123-root"]]

    fp_backend.on_ack("abc123")  # re-fire ACK for the same generation
    fp_backend.on_domain_ready()  # re-fire READY

    assert fake_arm.calls == [["abc123-root"]]  # still only armed once


def test_stale_ack_for_a_superseded_generation_does_not_arm(fp_backend, fake_arm):
    fp_backend.handle_offer(_manifest("aaa111"))
    e_b = fp_backend.handle_offer(_manifest("bbb222"))  # supersedes A
    fp_backend.authorize(True, e_b)
    fp_backend.on_domain_ready()

    fp_backend.on_ack("aaa111")  # late ACK for the superseded offer

    assert fake_arm.calls == []  # ignored: not the currently accepted generation

    fp_backend.on_ack("bbb222")

    assert fake_arm.calls == [["abc123-root"]]


def test_accepting_new_generation_clears_old_ack_latch_before_ready(
    fp_backend, fake_arm, fake_resolver
):
    e_a = fp_backend.handle_offer(_manifest("aaa111"))
    fp_backend.authorize(True, e_a)
    fp_backend.on_ack("aaa111")

    e_b = fp_backend.handle_offer(_manifest("bbb222"))
    fp_backend.authorize(True, e_b)
    fp_backend.on_domain_ready()

    assert fake_resolver.calls == []
    assert fake_arm.calls == []

    fp_backend.on_ack("bbb222")

    assert fake_arm.calls == [["abc123-root"]]


def test_accepting_new_generation_invalidates_in_flight_resolution(
    qapp, fake_client, fake_domain, fake_link
):
    resolver = ControllableResolver()
    arm = FakeArm()
    backend = FileProviderBackend(
        fake_client, fake_domain, arm, url_resolver=resolver
    )
    backend.attach_link(fake_link)

    e_a = backend.handle_offer(_manifest("aaa111", ("a.txt",)))
    backend.authorize(True, e_a)
    backend.on_domain_ready()
    backend.on_ack("aaa111")
    # The resolver is handed real NSFileProviderItemIdentifiers
    # ("<transfer_id>:<index>"), not the clipboard-tree basenames.
    assert [root_id for root_id, _ in resolver.calls] == ["aaa111:0"]

    e_b = backend.handle_offer(_manifest("bbb222", ("b.txt",)))
    backend.authorize(True, e_b)
    resolver.complete(0, "file:///a.txt")

    assert arm.calls == []

    backend.on_ack("bbb222")
    assert [root_id for root_id, _ in resolver.calls] == ["aaa111:0", "bbb222:0"]
    resolver.complete(1, "file:///b.txt")

    assert arm.calls == [["file:///b.txt"]]


def test_resolution_failure_emits_once_arms_nothing_and_can_retry(
    qapp, fake_client, fake_domain, fake_link
):
    resolver = ControllableResolver()
    arm = FakeArm()
    backend = FileProviderBackend(
        fake_client, fake_domain, arm, url_resolver=resolver
    )
    backend.attach_link(fake_link)
    failures: list[str] = []
    backend.transfer_failed.connect(failures.append)

    epoch = backend.handle_offer(_manifest(paths=("a.txt", "b.txt")))
    backend.authorize(True, epoch)
    backend.on_domain_ready()
    backend.on_ack("abc123")
    assert [root_id for root_id, _ in resolver.calls] == ["abc123:0", "abc123:1"]

    resolver.complete(1, error=RuntimeError("temporary resolver failure"))
    resolver.complete(0, "file:///a.txt")

    assert failures == ["url_resolution_failed"]
    assert arm.calls == []

    backend.on_ack("abc123")
    assert [root_id for root_id, _ in resolver.calls] == [
        "abc123:0",
        "abc123:1",
        "abc123:0",
        "abc123:1",
    ]
    resolver.complete(2, "file:///a.txt")
    resolver.complete(3, "file:///b.txt")

    assert failures == ["url_resolution_failed"]
    assert arm.calls == [["file:///a.txt", "file:///b.txt"]]


class _DomainDisabledError:
    """Minimal NSError stand-in for NSFileProviderErrorDomainDisabled (-2011),
    the transient "domain not enabled yet" window right after addDomain."""

    def code(self) -> int:
        return -2011

    def domain(self) -> str:
        return "NSFileProviderErrorDomain"

    def __repr__(self) -> str:
        return "NSError(NSFileProviderErrorDomain:-2011 disabled)"


class _RetryFakeTimer:
    """Single-shot QTimer stand-in; the test fires expiry via ``.fire()``."""

    def __init__(self) -> None:
        self._callback = None
        self.running = False

    def setSingleShot(self, _single: bool) -> None:  # noqa: N802 - QTimer selector
        pass

    @property
    def timeout(self) -> "_RetryFakeTimer":
        return self

    def connect(self, callback) -> None:
        self._callback = callback

    def start(self, _ms: int) -> None:
        self.running = True

    def stop(self) -> None:
        self.running = False

    def fire(self) -> None:
        if self.running and self._callback is not None:
            self.running = False
            self._callback()


def test_arm_retries_while_domain_transiently_disabled(
    qapp, fake_client, fake_domain, fake_link
):
    """-2011 (NSFileProviderErrorDomainDisabled) on the arm path is the
    transient "domain not enabled yet" window right after addDomain, not a real
    failure: the backend retries the resolve (never emitting transfer_failed /
    dropping into staging) until the domain enables and the URL resolves, then
    arms exactly once."""
    timers: list[_RetryFakeTimer] = []

    def timer_factory() -> _RetryFakeTimer:
        timer = _RetryFakeTimer()
        timers.append(timer)
        return timer

    resolver = ControllableResolver()
    arm = FakeArm()
    failures: list[str] = []
    backend = FileProviderBackend(
        fake_client,
        fake_domain,
        arm,
        url_resolver=resolver,
        timer_factory=timer_factory,
    )
    backend.attach_link(fake_link)
    backend.transfer_failed.connect(failures.append)

    epoch = backend.handle_offer(_manifest())
    backend.authorize(True, epoch)
    backend.on_domain_ready()
    backend.on_ack("abc123")

    # First resolve attempt lands in the transient disabled window.
    assert [rid for rid, _ in resolver.calls] == ["abc123:0"]
    resolver.complete(0, error=_DomainDisabledError())
    # No failure, nothing armed, a retry timer scheduled instead.
    assert failures == []
    assert arm.calls == []
    assert len(timers) == 1 and timers[0].running

    # Retry fires; domain still disabled once more.
    timers[0].fire()
    assert [rid for rid, _ in resolver.calls] == ["abc123:0", "abc123:0"]
    resolver.complete(1, error=_DomainDisabledError())
    assert failures == []
    assert arm.calls == []
    assert len(timers) == 2 and timers[1].running

    # Domain finally enabled: resolve succeeds -> armed exactly once.
    timers[1].fire()
    assert [rid for rid, _ in resolver.calls] == ["abc123:0", "abc123:0", "abc123:0"]
    resolver.complete(2, "file:///abc123:0")
    assert failures == []
    assert arm.calls == [["file:///abc123:0"]]


def test_non_disabled_resolution_error_fails_without_retry(
    qapp, fake_client, fake_domain, fake_link
):
    """A non -2011 resolver error is a genuine failure: emit transfer_failed
    once, arm nothing, and schedule NO retry (contrast the disabled window)."""
    timers: list[_RetryFakeTimer] = []

    def timer_factory() -> _RetryFakeTimer:
        timer = _RetryFakeTimer()
        timers.append(timer)
        return timer

    resolver = ControllableResolver()
    arm = FakeArm()
    failures: list[str] = []
    backend = FileProviderBackend(
        fake_client,
        fake_domain,
        arm,
        url_resolver=resolver,
        timer_factory=timer_factory,
    )
    backend.attach_link(fake_link)
    backend.transfer_failed.connect(failures.append)

    epoch = backend.handle_offer(_manifest())
    backend.authorize(True, epoch)
    backend.on_domain_ready()
    backend.on_ack("abc123")
    resolver.complete(0, error=RuntimeError("real failure"))

    assert failures == ["url_resolution_failed"]
    assert arm.calls == []
    assert timers == []  # no retry scheduled for a genuine (non -2011) error
