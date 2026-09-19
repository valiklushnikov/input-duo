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


def _manifest(transfer_id="abc123"):
    return TransferManifest(
        transfer_id=transfer_id,
        entries=(TransferEntry(path="a.txt", kind=ENTRY_FILE, size=5, mtime_ns=0),),
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


def test_arm_then_wait_sends_no_file_read(fp_backend, fake_link, fake_arm, qtbot):
    e = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(True, e)
    fp_backend.on_ack("abc123")  # simulate publish ACK
    fp_backend.on_domain_ready()  # simulate READY
    qtbot.wait(200)
    assert fake_arm.calls == [["abc123-root"]]  # armed
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
