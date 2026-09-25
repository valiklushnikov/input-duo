"""Task 16: per-offer выбор между File Provider и staging (``MacReceiveRouter``).

Оба реальных backend'а (``MacFileReceiver``, ``FileProviderBackend``) уже
полностью покрыты своими собственными наборами тестов
(``test_macos_receiver.py``, ``test_fileprovider_*.py``) - здесь проверяется
ТОЛЬКО логика диспетчеризации router'а: кто из двух получает offer, что
происходит при недоступности File Provider, и что выбор не меняется
посреди уже принятой генерации. Поэтому большинство тестов используют
лёгкие фейковые backend'ы (см. ``FakeStagingBackend``/``FakeFPBackend``),
которые лишь записывают, что им передали - ровно то, что предписывает
task-16 brief ("fakes ... for both backends, record which one gets the
offer"). Один сквозной тест (в конце файла) прогоняет ту же дорожку через
НАСТОЯЩИЕ ``MacFileReceiver``/``FileProviderBackend``, чтобы застраховаться
от расхождения реальных сигнатур с тем, что предполагает router.
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import QObject, Signal

from duo_input.clipboard.wire import Message, MessageType
from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.platform_files import MacReceiveRouter


def _manifest(transfer_id: str = "t1") -> TransferManifest:
    return TransferManifest(
        transfer_id=transfer_id,
        entries=(TransferEntry(path="a.txt", kind=ENTRY_FILE, size=5, mtime_ns=0),),
        skipped=(),
        drop_effect=1,
    )


class FakeStagingBackend(QObject):
    """Двойник ``MacFileReceiver`` - тот же интерфейс, только записывающий."""

    authorization_needed = Signal(object)
    transfer_started = Signal(object)
    transfer_progress = Signal("qlonglong", "qlonglong")
    transfer_completed = Signal()
    transfer_failed = Signal(str)
    transfer_cancelled = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.offers: list[TransferManifest] = []
        self.authorized: list[bool] = []
        self.messages: list[Message] = []
        self.cancelled = 0
        self.stopped = 0
        self.link = None
        self.caps = None
        self.file_reads_sent = 0
        self.armed = 0
        self._last_manifest: TransferManifest | None = None

    def handle_offer(self, manifest: TransferManifest) -> None:
        self._last_manifest = manifest
        self.offers.append(manifest)
        self.authorization_needed.emit(manifest)

    def authorize(self, accepted: bool) -> None:
        self.authorized.append(accepted)
        if accepted:
            self.transfer_started.emit(self._last_manifest)
        else:
            self.transfer_cancelled.emit()

    def handle_message(self, message: Message) -> None:
        self.messages.append(message)

    def cancel(self) -> None:
        self.cancelled += 1

    def stop(self) -> None:
        self.stopped += 1

    def attach_link(self, link) -> None:
        self.link = link

    def set_peer_capabilities(self, caps) -> None:
        self.caps = caps

    # --- test-only helpers simulating the rest of a real receive session
    def simulate_file_read(self) -> None:
        self.file_reads_sent += 1

    def simulate_arm(self) -> None:
        self.armed += 1

    def simulate_completed(self) -> None:
        self.transfer_completed.emit()

    def simulate_failed(self, reason: str) -> None:
        self.transfer_failed.emit(reason)


class FakeFPBackend(QObject):
    """Двойник ``FileProviderBackend`` - двухаргументные authorization_needed/
    authorize (эпоха) и ``generation_ready``, как у настоящего класса."""

    authorization_needed = Signal(object, int)
    transfer_started = Signal(object)
    transfer_progress = Signal("qlonglong", "qlonglong")
    transfer_completed = Signal()
    transfer_failed = Signal(str)
    transfer_cancelled = Signal()
    generation_ready = Signal(str, object)

    def __init__(self) -> None:
        super().__init__()
        self.offers: list[TransferManifest] = []
        self.authorized: list[tuple[bool, int]] = []
        self.messages: list[Message] = []
        self.cancelled = 0
        self.stopped = 0
        self.link = None
        self.caps = None
        self.file_reads_sent = 0
        self.armed = 0
        self._epoch = 0
        self._last_manifest: TransferManifest | None = None
        #: Если задано - authorize(True, ...) синхронно проваливает публикацию
        #: этой причиной ДО generation_ready (симулирует "publish fails before
        #: arm": extension not connected / publish_rejected).
        self.fail_reason_on_authorize: str | None = None

    def handle_offer(self, manifest: TransferManifest) -> int:
        self._epoch += 1
        self._last_manifest = manifest
        self.offers.append(manifest)
        self.authorization_needed.emit(manifest, self._epoch)
        return self._epoch

    def authorize(self, accepted: bool, epoch: int) -> None:
        self.authorized.append((accepted, epoch))
        if not accepted:
            self.transfer_cancelled.emit()
            return
        if self.fail_reason_on_authorize is not None:
            self.transfer_failed.emit(self.fail_reason_on_authorize)
            return
        # Публикация "успешна": generation зарегистрирована - с этого момента
        # FILE_READ через XPC уже мог бы пойти (см. докстринг router'а про
        # generation_ready как границу "точки невозврата").
        self.generation_ready.emit(self._last_manifest.transfer_id, ())

    def owns_reply(self, message: Message) -> bool:
        # The fake never issues FILE_READs itself, so no reply is "its own";
        # routing then falls back to the per-offer active backend.
        return False

    def handle_message(self, message: Message) -> None:
        self.messages.append(message)

    def cancel(self) -> None:
        self.cancelled += 1

    def stop(self) -> None:
        self.stopped += 1

    def attach_link(self, link) -> None:
        self.link = link

    def set_peer_capabilities(self, caps) -> None:
        self.caps = caps

    def simulate_file_read(self) -> None:
        self.file_reads_sent += 1

    def simulate_arm(self) -> None:
        self.armed += 1

    def simulate_completed(self) -> None:
        self.transfer_completed.emit()


class FakeDomain:
    def __init__(self, ready: bool) -> None:
        self.is_ready = ready


_UNSET = object()


class FakeFPClient:
    def __init__(self, remote_obj: object = _UNSET) -> None:
        self._remote = object() if remote_obj is _UNSET else remote_obj

    def remote(self):
        return self._remote


# --------------------------------------------------------------------- selection


def test_fp_available_routes_offer_to_fileprovider(qapp):
    staging, fp = FakeStagingBackend(), FakeFPBackend()
    router = MacReceiveRouter(
        staging, fp, domain=FakeDomain(True), client=FakeFPClient(), flag_enabled=lambda: True
    )
    prompts = []
    router.authorization_needed.connect(prompts.append)
    manifest = _manifest()

    router.handle_offer(manifest)

    assert fp.offers == [manifest]
    assert staging.offers == []
    assert prompts == [manifest]

    router.authorize(True)

    assert fp.authorized == [(True, 1)]
    assert staging.authorized == []


@pytest.mark.parametrize(
    "make_kwargs",
    [
        lambda: dict(flag_enabled=lambda: False, domain=FakeDomain(True), client=FakeFPClient()),
        lambda: dict(flag_enabled=lambda: True, domain=FakeDomain(False), client=FakeFPClient()),
        lambda: dict(
            flag_enabled=lambda: True, domain=FakeDomain(True), client=FakeFPClient(remote_obj=None)
        ),
        lambda: dict(
            flag_enabled=lambda: True,
            domain=FakeDomain(True),
            client=FakeFPClient(),
            os_supported=lambda: False,
        ),
    ],
    ids=["flag_off", "domain_not_ready", "service_unavailable", "unsupported_os"],
)
def test_each_fallback_case_routes_offer_to_staging(qapp, make_kwargs):
    staging, fp = FakeStagingBackend(), FakeFPBackend()
    router = MacReceiveRouter(staging, fp, **make_kwargs())
    manifest = _manifest()

    router.handle_offer(manifest)

    assert staging.offers == [manifest]
    assert fp.offers == []


def test_no_fileprovider_backend_configured_always_routes_to_staging(qapp):
    staging = FakeStagingBackend()
    router = MacReceiveRouter(staging, None, flag_enabled=lambda: True)
    manifest = _manifest()

    router.handle_offer(manifest)

    assert staging.offers == [manifest]


# --------------------------------------------------------------------- fallback window


def test_publish_failure_before_generation_ready_falls_back_to_staging_silently(qapp):
    staging, fp = FakeStagingBackend(), FakeFPBackend()
    fp.fail_reason_on_authorize = "extension not connected"
    router = MacReceiveRouter(
        staging, fp, domain=FakeDomain(True), client=FakeFPClient(), flag_enabled=lambda: True
    )
    prompts, failures = [], []
    router.authorization_needed.connect(prompts.append)
    router.transfer_failed.connect(failures.append)
    manifest = _manifest()

    router.handle_offer(manifest)
    router.authorize(True)

    assert fp.authorized == [(True, 1)]
    assert staging.offers == [manifest]  # тихий реплей того же manifest
    assert staging.authorized == [True]  # автоматически принят, без нового вопроса
    assert prompts == [manifest]  # РОВНО один вопрос наружу за весь offer
    assert failures == []  # никакой transfer_failed наружу не ушёл


def test_transfer_failed_after_generation_ready_is_forwarded_not_rerouted(qapp):
    staging, fp = FakeStagingBackend(), FakeFPBackend()
    router = MacReceiveRouter(
        staging, fp, domain=FakeDomain(True), client=FakeFPClient(), flag_enabled=lambda: True
    )
    failures = []
    router.transfer_failed.connect(failures.append)
    manifest = _manifest()

    router.handle_offer(manifest)
    router.authorize(True)  # ack успешен -> generation_ready -> locked
    fp.transfer_failed.emit("url_resolution_failed")  # сбой ПОСЛЕ точки невозврата

    assert failures == ["url_resolution_failed"]
    assert staging.offers == []  # отката на staging не было - backend зафиксирован


# --------------------------------------------------------------------- fixed per transfer_id


def test_selection_stays_fixed_after_lock_even_if_availability_changes(qapp):
    domain = FakeDomain(True)
    staging, fp = FakeStagingBackend(), FakeFPBackend()
    router = MacReceiveRouter(
        staging, fp, domain=domain, client=FakeFPClient(), flag_enabled=lambda: True
    )
    manifest = _manifest()
    router.handle_offer(manifest)
    router.authorize(True)  # locked

    domain.is_ready = False  # домен "деградировал" посреди уже выбранной генерации

    message = Message(MessageType.FILE_CHUNK, {"transfer_id": manifest.transfer_id}, b"x")
    router.handle_message(message)

    assert fp.messages == [message]
    assert staging.messages == []


def test_new_transfer_id_after_completion_is_selected_independently(qapp):
    flag = {"on": True}
    staging, fp = FakeStagingBackend(), FakeFPBackend()
    router = MacReceiveRouter(
        staging, fp, domain=FakeDomain(True), client=FakeFPClient(), flag_enabled=lambda: flag["on"]
    )
    manifest1 = _manifest("t1")
    router.handle_offer(manifest1)
    router.authorize(True)  # FP, locked

    flag["on"] = False
    manifest2 = _manifest("t2")
    router.handle_offer(manifest2)

    assert fp.offers == [manifest1]
    assert staging.offers == [manifest2]


# --------------------------------------------------------------------- exactly-once


def test_exactly_one_backend_ever_drives_file_read_and_arm(qapp):
    staging, fp = FakeStagingBackend(), FakeFPBackend()
    router = MacReceiveRouter(
        staging, fp, domain=FakeDomain(True), client=FakeFPClient(), flag_enabled=lambda: True
    )
    manifest = _manifest()
    router.handle_offer(manifest)
    router.authorize(True)

    # Только backend, который реально получил offer/authorize, способен
    # когда-либо послать FILE_READ или вооружить буфер - второй offer вообще
    # не видел (см. docstring MacReceiveRouter).
    fp.simulate_file_read()
    fp.simulate_arm()

    assert (fp.file_reads_sent, staging.file_reads_sent) == (1, 0)
    assert (fp.armed, staging.armed) == (1, 0)
    assert staging.offers == [] and staging.authorized == []


def test_staging_selected_end_to_end_forwards_signals_exactly_once(qapp):
    staging, fp = FakeStagingBackend(), FakeFPBackend()
    router = MacReceiveRouter(
        staging, fp, domain=FakeDomain(True), client=FakeFPClient(), flag_enabled=lambda: False
    )
    started, completed = [], []
    router.transfer_started.connect(started.append)
    router.transfer_completed.connect(lambda: completed.append(True))
    manifest = _manifest()

    router.handle_offer(manifest)
    router.authorize(True)
    staging.simulate_completed()

    assert started == [manifest]
    assert completed == [True]
    assert fp.offers == []


def test_cancel_and_stop_and_link_wiring_reach_only_expected_backends(qapp):
    staging, fp = FakeStagingBackend(), FakeFPBackend()
    router = MacReceiveRouter(
        staging, fp, domain=FakeDomain(True), client=FakeFPClient(), flag_enabled=lambda: True
    )
    link = object()
    caps = frozenset({"files"})
    router.attach_link(link)
    router.set_peer_capabilities(caps)
    assert staging.link is link and fp.link is link
    assert staging.caps is caps and fp.caps is caps

    manifest = _manifest()
    router.handle_offer(manifest)
    router.authorize(True)

    router.cancel()
    assert fp.cancelled == 1 and staging.cancelled == 0

    router.stop()
    assert fp.stopped == 1 and staging.stopped == 1


# --------------------------------------------------------------------- real backends


def test_real_backends_fp_happy_path_end_to_end(qapp, tmp_path):
    """Тот же router поверх НАСТОЯЩИХ ``MacFileReceiver``/``FileProviderBackend``
    (не фейков) - страховка от расхождения предполагаемых router'ом сигнатур
    с реальными классами."""
    from duo_input.transfer.fileprovider_backend import FileProviderBackend
    from duo_input.transfer.macos_files import MacFileReceiver
    from duo_input.transfer.staging import StagingArea

    class RealFakeRemote:
        def publishGeneration_reply_(self, record, reply):
            reply(True, None)

    class RealFakeClient:
        def remote(self):
            return RealFakeRemote()

    armed_urls = []
    staging = MacFileReceiver(StagingArea(tmp_path), pasteboard_arm=lambda paths: None)
    fp = FileProviderBackend(
        RealFakeClient(),
        object(),
        armed_urls.append,
        url_resolver=lambda root_id, reply: reply(f"file:///{root_id}", None),
    )
    fp.on_domain_ready()

    router = MacReceiveRouter(
        staging,
        fp,
        domain=FakeDomain(True),
        client=RealFakeClient(),
        flag_enabled=lambda: True,
    )

    class RealFakeLink:
        def __init__(self) -> None:
            self.sent: list[Message] = []

        def send(self, message: Message) -> bool:
            self.sent.append(message)
            return True

    link = RealFakeLink()
    router.attach_link(link)
    manifest = _manifest("real-t1")

    router.handle_offer(manifest)
    router.authorize(True)

    # root_id resolved is the FP item identifier "<transfer_id>:<index>", not
    # the basename - the clipboard is armed with the real user-visible URL.
    assert armed_urls == [["file:///real-t1:0"]]
    assert link.sent == []  # публикация/вооружение не шлют FILE_READ сами по себе
