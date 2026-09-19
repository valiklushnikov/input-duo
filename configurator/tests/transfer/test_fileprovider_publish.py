"""Task 7: FILE_OFFER -> authorized generation publication (first vertical slice).

privacy-critical: единственная гарантия, которую этот файл существует
проверить - что публикация generation (и, транзитивно, реплика/вооружение
буфера/FILE_READ) НИКОГДА не происходит до Accept текущей эпохи, и что
протухший/перекрытый Accept не публикует НИЧЕГО (см. task-7 brief,
"THE privacy invariant"). ``test_stale_accept_is_ignored`` - тест-ворота.

Fake client/remote here stand in for Task 3's ``FileProviderServiceClient``
and the ``DuoExtensionControl`` XPC proxy: the fake remote's
``publishGeneration_reply_`` records the transfer_id and replies
synchronously (ACK=True by default), which is enough to exercise the
epoch/ack state machine without any real NSXPCConnection.
"""

from __future__ import annotations

import json

import pytest

from duo_input.transfer.fileprovider_backend import FileProviderBackend
from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest


class FakeRemote:
    """Stand-in for the ``DuoExtensionControl`` NSXPC remote proxy.

    Replies synchronously (ack=True unless ``ack`` is set False) - real XPC
    would reply async, but the backend must not assume anything about
    timing beyond "handle the reply whenever it arrives", so a synchronous
    fake exercises the same code path.
    """

    def __init__(self, ack: bool = True) -> None:
        self.published: list[str] = []
        self.records: list[dict] = []
        self.ack = ack

    def publishGeneration_reply_(self, record: bytes, reply) -> None:
        payload = json.loads(record.decode("utf-8"))
        self.records.append(payload)
        self.published.append(payload["transfer_id"])
        reply(self.ack, None)


class FakeClient:
    """Stand-in for ``FileProviderServiceClient`` - only ``remote()`` matters here."""

    def __init__(self, remote_obj) -> None:
        self.remote_obj = remote_obj

    def remote(self):
        return self.remote_obj


class FakeArm:
    def __init__(self) -> None:
        self.calls: list[list] = []

    def __call__(self, roots) -> None:
        self.calls.append(list(roots))


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
    return FakeRemote()


@pytest.fixture
def fake_client(fake_remote):
    return FakeClient(fake_remote)


@pytest.fixture
def fake_arm():
    return FakeArm()


@pytest.fixture
def fake_link():
    return FakeLink()


@pytest.fixture
def fake_domain():
    # Task 6's FileProviderDomainManager is injected but not consulted by
    # this task (per ruling #2) - an opaque object is enough.
    return object()


@pytest.fixture
def fp_backend(qapp, fake_client, fake_domain, fake_arm, fake_link):
    backend = FileProviderBackend(fake_client, fake_domain, fake_arm)
    backend.attach_link(fake_link)
    return backend


# --- Step 1: failing publish tests, verbatim from the task-7 brief.


def test_no_publish_before_accept(fp_backend, fake_remote):
    fp_backend.handle_offer(_manifest())
    assert fake_remote.published == []  # Ask mode, no accept yet


def test_reject_publishes_nothing(fp_backend, fake_remote):
    e = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(False, e)
    assert fake_remote.published == []


def test_accept_publishes_generation_and_waits_ack(fp_backend, fake_remote):
    e = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(True, e)
    assert fake_remote.published == ["abc123"]  # publishGeneration called


def test_no_arm_before_ack(fp_backend, fake_remote, fake_arm):
    e = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(True, e)
    assert fake_arm.calls == []  # arm only after ACK (Task 8)


def test_no_file_read_on_publish(fp_backend, fake_link):
    e = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(True, e)
    assert not any(m.type.name == "FILE_READ" for m in fake_link.sent)


def test_stale_accept_is_ignored(fp_backend, fake_remote, fake_arm, fake_link):
    e_a = fp_backend.handle_offer(_manifest("aaa111"))  # offer A pending
    e_b = fp_backend.handle_offer(_manifest("bbb222"))  # offer B supersedes A
    fp_backend.authorize(True, e_a)  # late Accept of stale A
    assert fake_remote.published == []  # NO publishGeneration
    assert fake_arm.calls == []  # NO clipboard arm
    assert not any(m.type.name == "FILE_READ" for m in fake_link.sent)  # NO read
    fp_backend.authorize(True, e_b)  # Accept of current B
    assert fake_remote.published == ["bbb222"]  # only the current offer


# --- Step 5: supersede + auto-mode + idempotent re-Accept.


def test_supersede_emits_transfer_cancelled_for_the_old_offer(fp_backend, fake_remote):
    cancelled = []
    fp_backend.transfer_cancelled.connect(lambda: cancelled.append(1))

    fp_backend.handle_offer(_manifest("aaa111"))
    fp_backend.handle_offer(_manifest("bbb222"))  # supersedes A while still AWAITING_AUTH

    assert cancelled == [1]
    assert fake_remote.published == []  # superseding itself publishes nothing


def test_auto_mode_authorizes_the_current_epoch_and_publishes(fp_backend, fake_remote):
    # "Auto" mode just means the caller invokes authorize(True, epoch)
    # immediately with the epoch handle_offer just returned - no special
    # path in the backend itself.
    epoch = fp_backend.handle_offer(_manifest("ccc333"))
    fp_backend.authorize(True, epoch)
    assert fake_remote.published == ["ccc333"]


def test_reaccept_of_an_already_consumed_epoch_is_idempotent(fp_backend, fake_remote):
    epoch = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(True, epoch)
    fp_backend.authorize(True, epoch)  # re-Accept of the same, consumed epoch
    assert fake_remote.published == ["abc123"]  # not published twice


def test_reject_then_reaccept_of_the_same_epoch_is_ignored(fp_backend, fake_remote):
    epoch = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(False, epoch)
    fp_backend.authorize(True, epoch)  # late Accept after the epoch was rejected
    assert fake_remote.published == []


def test_accept_emits_generation_ready_only_after_ack(fp_backend, fake_remote):
    ready = []
    fp_backend.generation_ready.connect(lambda tid, roots: ready.append((tid, roots)))

    epoch = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(True, epoch)

    assert ready == [("abc123", ("a.txt",))]


def test_nacked_publish_emits_transfer_failed_and_no_generation_ready(fp_backend, fake_remote):
    fake_remote.ack = False
    failed = []
    ready = []
    fp_backend.transfer_failed.connect(failed.append)
    fp_backend.generation_ready.connect(lambda *a: ready.append(a))

    epoch = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(True, epoch)

    assert fake_remote.published == ["abc123"]  # publish WAS attempted...
    assert failed == ["publish_rejected"]  # ...but the extension declined it
    assert ready == []


def test_remote_unavailable_fails_without_crashing_and_publishes_nothing(fp_backend, fake_client):
    fake_client.remote_obj = None  # extension not connected (Task 3's remote() is None)
    failed = []
    fp_backend.transfer_failed.connect(failed.append)

    epoch = fp_backend.handle_offer(_manifest())
    fp_backend.authorize(True, epoch)

    assert failed == ["extension not connected"]

    # And the (now-consumed) epoch is not resurrectable by a second Accept.
    fp_backend.authorize(True, epoch)
    assert failed == ["extension not connected"]
