"""FileProviderBackend - offer -> авторизация -> публикация generation ->
вооружение буфера обмена (Tasks 7-8).

Первый вертикальный срез File Provider бэкенда: принятый offer публикует
generation через XPC в реплику Swift-расширения (атомарно + ACK), чтобы
пространство File Provider стало видимым (Task 7). Как только это ACK
получено И домен (Task 6) READY, Task 8 резолвит user-visible ``file://``
URL корней generation через инъецируемый резолвер и вооружает СУЩЕСТВУЮЩИЙ
host-only буфер обмена (``pasteboard_arm`` - см. ``macos_pasteboard.arm_urls``),
а не второй подсистему. Стриминг (FILE_READ/FILE_CHUNK) приезжает в Tasks
9-10 - ``handle_message`` здесь по-прежнему заглушка.

Приватностный инвариант всего модуля - "эпоха авторизации": ``handle_offer``
возвращает эпоху текущего предложения; ``authorize`` обязан игнорировать любой
вызов, чей ``epoch`` не совпадает с текущим ожидающим (протухший/перекрытый
Accept) - публикация generation, запись в реплику и вооружение буфера обмена
происходят СТРОГО после Accept текущей эпохи и только для неё. Это
переопределяет "мягкий компромисс" из spec §22: протухшая авторизация теперь
не публикует НИЧЕГО, а не публикует "с последующим retire".

Task 8 добавляет второй, независимый latch поверх этого: вооружение буфера
обмена происходит только когда ОБА события произошли - ACK текущей generation
(``on_ack``/``_run_publish_reply``) И готовность домена (``on_domain_ready``,
подключённый к ``domain.ready``) - и не более одного раза на generation (см.
``_maybe_arm``/``_armed_transfer_id``). Если READY ещё не наступил к моменту
ACK, вооружение просто откладывается - fallback на этот случай (Task 16) здесь
сознательно не реализован.

``domain`` (Task 6) инъецируется и хранится; в этой задаче он участвует
ТОЛЬКО как источник сигнала ``ready`` для latch'а выше - per-offer выбор
File Provider vs staging при деградированном домене это Task 16 (сознательно
не делается здесь, чтобы не дублировать логику раньше времени).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from enum import Enum, auto

from PySide6.QtCore import QMetaObject, QObject, Q_ARG, Qt, Signal, Slot

from ..clipboard.wire import Message
from .fileprovider_client import FileProviderServiceClient
from .fileprovider_domain import FileProviderDomainManager
from .fileprovider_replica import STATE_ACTIVE, build_generation_record
from .model import TransferManifest

logger = logging.getLogger(__name__)

try:
    from FileProvider import NSFileProviderManager

    _FP_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised implicitly on non-darwin
    NSFileProviderManager = None
    _FP_AVAILABLE = False


def _default_url_resolver(
    domain: object,
) -> Callable[[str, Callable[[object, object], None]], None]:
    """Real resolver: ``root_id`` -> the user-visible ``file://`` URL
    ``NSFileProviderManager`` hands back for it, via
    ``getUserVisibleURLForItemIdentifier:completionHandler:``. Never
    instantiated by a test - tests inject a fake resolver instead (see the
    ``FileProviderBackend`` docstring / task-8 brief). Looks the domain up by
    identifier the same way ``fileprovider_domain._FileProviderRealAdapter``
    does, since the injected ``domain`` here is Task 6's
    ``FileProviderDomainManager`` (only exposes ``domain_identifier``, not
    the raw ``NSFileProviderDomain``).
    """

    def resolve(root_id: str, completion: Callable[[object, object], None]) -> None:
        if not _FP_AVAILABLE:
            completion(None, RuntimeError("PyObjC FileProvider framework unavailable"))
            return
        identifier = getattr(domain, "domain_identifier", None)
        if identifier is None:
            completion(None, RuntimeError("domain has no domain_identifier"))
            return

        def on_domains(domains, error):
            if error is not None:
                completion(None, error)
                return
            target = next(
                (d for d in (domains or []) if str(d.identifier()) == identifier), None
            )
            if target is None:
                completion(None, RuntimeError(f"domain {identifier!r} not registered"))
                return
            manager = NSFileProviderManager.managerForDomain_(target)
            if manager is None:
                completion(None, RuntimeError("no manager for domain"))
                return
            manager.getUserVisibleURLForItemIdentifier_completionHandler_(
                root_id, completion
            )

        NSFileProviderManager.getDomainsWithCompletionHandler_(on_domains)

    return resolve


#: Срок аренды генерации по умолчанию - совпадает с TTL staging (StagingArea,
#: ttl_seconds=86_400). Не проверяется тестами; см. ruling #1 в task-7 brief -
#: это просто "не мудрить", а не согласованная политика аренды.
LEASE_SECONDS = 86_400
LEASE_NS = LEASE_SECONDS * 1_000_000_000


class _GenerationState(Enum):
    """Состояние generation, которую этот бэкенд считает опубликованной.

    Только учёт для Task 8/16 (arm, per-offer routing) - сама реплика хранит
    свой собственный ``state`` (``STATE_ACTIVE``/``STATE_RETIRED`` из
    fileprovider_replica.py), это разные словари понятий.
    """

    ACTIVE_CLIPBOARD = auto()


def _root_ids(manifest: TransferManifest) -> tuple[str, ...]:
    """Верхнеуровневые имена записей манифеста - будущие корни в File Provider.

    Тот же приём, что и ``StagingSession.finish()``: дедуплицированный первый
    сегмент пути каждой записи, в порядке первого появления. Namespace File
    Provider показывает то же дерево, что см. буфер обмена показал бы через
    ``arm`` - один и тот же список "корней" питает оба.
    """
    ids: list[str] = []
    seen: set[str] = set()
    for entry in manifest.entries:
        first = entry.path.split("/", 1)[0]
        if first not in seen:
            seen.add(first)
            ids.append(first)
    return tuple(ids)


class FileProviderBackend(QObject):
    """Offer/authorize/publish/arm - File Provider бэкенд (Tasks 7-8)."""

    #: (manifest, epoch) - epoch должен быть передан обратно в authorize().
    authorization_needed = Signal(object, int)
    transfer_started = Signal(object)
    transfer_progress = Signal("qlonglong", "qlonglong")
    transfer_completed = Signal()
    transfer_failed = Signal(str)
    transfer_cancelled = Signal()
    #: (transfer_id, root_ids) - внутренний сигнал; питает ack-latch Task 8.
    generation_ready = Signal(str, object)

    def __init__(
        self,
        client: FileProviderServiceClient,
        domain: FileProviderDomainManager,
        pasteboard_arm,
        parent=None,
        *,
        url_resolver: Callable[[str, Callable[[object, object], None]], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        #: Хранится; в этой задаче участвует только как источник ``ready``
        #: для arm-latch'а ниже - per-offer гейтинг публикации это Task 16.
        self._domain = domain
        #: Вызывается ТОЛЬКО из ``_arm_after_ready``, когда оба latch'а держат.
        self._arm = pasteboard_arm
        #: root_id -> (url, error) резолвер. По умолчанию - реальный
        #: NSFileProviderManager (см. ``_default_url_resolver``); тесты
        #: инъецируют фейк.
        self._url_resolver = (
            url_resolver if url_resolver is not None else _default_url_resolver(domain)
        )
        self._link = None
        self._peer_caps: frozenset[str] = frozenset()
        self._offer_epoch = 0
        self._pending_epoch: int | None = None
        self._pending_manifest: TransferManifest | None = None
        #: Эпоха последней ПРИНЯТОЙ (accept) генерации - монотонно растёт.
        #: Не то же самое, что _pending_epoch (та про ещё-не-решённое
        #: предложение): _accepted_epoch фиксируется в момент authorize(True)
        #: и служит "личностью" генерации для проверки при поздней/переставшей
        #: быть актуальной ACK-ответке (см. _run_publish_reply). Ноль - "ещё
        #: ничего не принято", ни один реальный epoch (нумерация с 1) с ним не
        #: совпадёт.
        self._accepted_epoch = 0
        #: transfer_id/root_ids принятой (accept) генерации - зеркалит
        #: _accepted_epoch, но по transfer_id, а не по epoch: on_ack() (тестовый
        #: API, см. task-8 brief) не знает эпох, только transfer_id.
        self._accepted_transfer_id: str | None = None
        self._accepted_roots: tuple[str, ...] = ()
        self._active_transfer_id: str | None = None
        self._generation_state: _GenerationState | None = None
        # --- Task 8: arm-when-ready latch (ACK И domain READY, ровно раз).
        self._ack_transfer_id: str | None = None
        self._ack_roots: tuple[str, ...] = ()
        self._domain_ready = False
        self._armed_transfer_id: str | None = None
        self._arm_attempt = 0
        self._pending_resolution: (
            tuple[int, str, tuple[str, ...], set[str], dict[str, object]] | None
        ) = None
        ready_signal = getattr(domain, "ready", None)
        if ready_signal is not None:
            ready_signal.connect(self.on_domain_ready)
        if getattr(domain, "is_ready", False):
            self.on_domain_ready()

    # --- проводка (streaming ещё не подключён - Tasks 9-10)
    def attach_link(self, link) -> None:
        self._link = link

    def set_peer_capabilities(self, caps) -> None:
        self._peer_caps = frozenset(caps)

    # --- offer/авторизация
    def handle_offer(self, manifest: TransferManifest) -> int:
        """Зарегистрировать новое предложение, вернуть его эпоху.

        Если предыдущее предложение всё ещё ждёт решения (AWAITING_AUTH),
        оно перекрывается: его поздний Accept/Reject станет протухшим
        (``epoch != _pending_epoch``) и будет проигнорирован ``authorize``.
        """
        if self._pending_manifest is not None:
            self.transfer_cancelled.emit()
        self._offer_epoch += 1
        epoch = self._offer_epoch
        self._pending_epoch = epoch
        self._pending_manifest = manifest
        self.authorization_needed.emit(manifest, epoch)
        return epoch

    def authorize(self, accepted: bool, epoch: int) -> None:
        """Решение по эпохе ``epoch``.

        Протухшая/перекрытая эпоха (нет ожидающего предложения, или
        ``epoch`` не совпадает с текущим ожидающим) - ЭТО НИЧЕГО НЕ ДЕЛАЕТ:
        ни публикации, ни записи в реплику, ни вооружения буфера, ни
        FILE_READ. Это и есть приватностный инвариант этой задачи.
        """
        if self._pending_manifest is None or epoch != self._pending_epoch:
            return
        manifest = self._pending_manifest
        # Очищаем pending ДО побочных эффектов: повторный authorize(True,
        # epoch) на уже потреблённой эпохе после этой строки сам попадёт в
        # проверку выше (epoch != _pending_epoch, который теперь None) и
        # станет no-op - это и даёт идемпотентность повторного Accept без
        # отдельного учёта "уже опубликованных" эпох.
        self._pending_epoch = None
        self._pending_manifest = None
        if not accepted:
            self.transfer_cancelled.emit()
            return
        # Фиксируем принятую генерацию ДО асинхронной публикации: её epoch -
        # это "личность", по которой поздняя ACK-ответка узнает, не была ли она
        # уже перекрыта более новой принятой генерацией (см. _run_publish_reply).
        self._accepted_epoch = epoch
        # Параллельная "личность" по transfer_id для on_ack() (см. его
        # докстринг) - тот тестовый API не оперирует epoch вообще.
        self._accepted_transfer_id = manifest.transfer_id
        self._accepted_roots = _root_ids(manifest)
        # Новая принятая generation немедленно аннулирует ACK-latch и любое
        # незавершённое разрешение URL предыдущей. Иначе READY или поздний
        # completion старой generation сможет вооружить clipboard между
        # Accept(B) и ACK(B).
        self._ack_transfer_id = None
        self._ack_roots = ()
        self._arm_attempt += 1
        self._pending_resolution = None
        self._publish_generation(manifest, epoch)

    def _publish_generation(self, manifest: TransferManifest, epoch: int) -> None:
        remote = self._client.remote()
        if remote is None:
            logger.warning(
                "cannot publish generation %s: extension not connected",
                manifest.transfer_id,
            )
            self.transfer_failed.emit("extension not connected")
            return
        created_ns = time.time_ns()
        lease_deadline_ns = created_ns + LEASE_NS
        record = build_generation_record(
            manifest,
            state=STATE_ACTIVE,
            created_ns=created_ns,
            lease_deadline_ns=lease_deadline_ns,
        )

        def _on_reply(ack, error) -> None:
            # NSXPCConnection доставляет reply-блок publishGeneration:reply: на
            # приватной XPC/фоновой очереди - НЕ на Qt-потоке. Мутация полей
            # QObject и emit сигналов оттуда небезопасны, поэтому переносим
            # обработку на Qt-поток тем же приёмом, что и обратное направление
            # (FileProviderServiceClient._dispatch_extension_call): при
            # AutoConnection это прямой (синхронный) вызов, когда мы уже на
            # Qt-потоке - на этом держатся синхронные фейки в тестах - и
            # очередь, когда ответ пришёл с чужого потока.
            self._deliver_publish_reply(manifest, epoch, ack, error)

        remote.publishGeneration_reply_(record, _on_reply)

    def _deliver_publish_reply(
        self, manifest: TransferManifest, epoch: int, ack, error
    ) -> None:
        delivered = QMetaObject.invokeMethod(
            self,
            "_run_publish_reply",
            Qt.ConnectionType.AutoConnection,
            Q_ARG("QVariant", (manifest, epoch, ack, error)),
        )
        if not delivered:
            logger.error(
                "failed to marshal publish reply for %s onto the Qt thread",
                manifest.transfer_id,
            )

    @Slot("QVariant")
    def _run_publish_reply(self, payload) -> None:
        # PySide6 боксит кортеж через QVariant как list; распаковка ниже к
        # этому безразлична (list и tuple распаковываются одинаково).
        manifest, epoch, ack, error = payload
        # Поздняя/переставшая быть актуальной ACK: пока публикация A была в
        # полёте, принята более новая генерация B (_accepted_epoch = B).
        # Игнорируем ответку A целиком - ни мутации _active_transfer_id/
        # _generation_state, ни generation_ready. Иначе Task 8, повесив
        # generation_ready на pasteboard_arm, перевооружил бы буфер обмена
        # корнями старой (пусть и одобренной) генерации поверх уже активной
        # новой.
        if epoch != self._accepted_epoch:
            logger.debug(
                "ignoring publish reply for superseded generation %s "
                "(epoch %d != current %d)",
                manifest.transfer_id,
                epoch,
                self._accepted_epoch,
            )
            return
        if not ack:
            logger.warning(
                "extension declined generation %s: %r", manifest.transfer_id, error
            )
            self.transfer_failed.emit("publish_rejected")
            return
        self._active_transfer_id = manifest.transfer_id
        self._generation_state = _GenerationState.ACTIVE_CLIPBOARD
        roots = _root_ids(manifest)
        self.generation_ready.emit(manifest.transfer_id, roots)
        # Драйвим тот же arm-when-ready latch, что и тестовый on_ack() -
        # см. ruling #3 в task-8 brief: реальный ACK не дублирует логику
        # вооружения, а идёт через ту же _note_generation_acked().
        self._note_generation_acked(manifest.transfer_id, roots)

    # --- Task 8: arm-when-ready latch (ACK + domain READY, ровно раз)

    def on_ack(self, transfer_id: str) -> None:
        """Тестовый/plan-mandated API: симулировать приход publish ACK для
        ``transfer_id``, минуя реальный XPC-путь ``_run_publish_reply``.

        Игнорирует ACK для чего угодно, кроме ТЕКУЩЕЙ принятой (accept)
        генерации - тот же принцип устаревания, что ``_run_publish_reply``
        выражает через ``epoch != self._accepted_epoch``, здесь выражен через
        ``transfer_id``, поскольку у этого входа нет своей эпохи.
        """
        if transfer_id != self._accepted_transfer_id:
            logger.debug(
                "ignoring on_ack(%r): not the currently accepted generation (%r)",
                transfer_id,
                self._accepted_transfer_id,
            )
            return
        self._note_generation_acked(transfer_id, self._accepted_roots)

    def on_domain_ready(self) -> None:
        """Domain (Task 6) стал READY - вторая половина arm-latch'а."""
        self._domain_ready = True
        self._maybe_arm()

    def _note_generation_acked(self, transfer_id: str, roots: tuple[str, ...]) -> None:
        self._ack_transfer_id = transfer_id
        self._ack_roots = roots
        self._maybe_arm()

    def _maybe_arm(self) -> None:
        """Вооружить буфер обмена, когда ОБА latch'а держат - и не более
        одного раза на generation, даже если ack/ready перепрошли ещё раз
        (см. ``_armed_transfer_id``)."""
        if self._ack_transfer_id is None or not self._domain_ready:
            return
        if self._ack_transfer_id != self._accepted_transfer_id:
            return
        if self._armed_transfer_id == self._ack_transfer_id:
            return
        if self._pending_resolution is not None:
            return
        transfer_id = self._ack_transfer_id
        self._arm_after_ready(transfer_id)

    def _arm_after_ready(self, transfer_id: str) -> None:
        """Резолвить user-visible URL корней ``transfer_id`` (латчнутых в
        ``_ack_roots``) через ``self._url_resolver`` и вооружить буфер обмена
        ОДНИМ вызовом ``self._arm`` после того, как ВСЕ корни резолвнуты.
        """
        roots = self._ack_roots
        self._arm_attempt += 1
        attempt = self._arm_attempt
        if not roots:
            # Пустая generation (не должно случаться на практике) - вооружаем
            # пустым списком, той же семантикой, что arm_urls([]) у пустого
            # arm() (см. macos_pasteboard.py) - без отдельной охраны.
            self._arm_resolved_urls(transfer_id, [])
            return
        pending: set[str] = set(roots)
        resolved: dict[str, object] = {}
        self._pending_resolution = (attempt, transfer_id, roots, pending, resolved)

        def _dispatch(root_id: str, url, error) -> None:
            delivered = QMetaObject.invokeMethod(
                self,
                "_run_url_resolved",
                Qt.ConnectionType.AutoConnection,
                Q_ARG("QVariant", (attempt, root_id, url, error)),
            )
            if not delivered:
                logger.error(
                    "failed to marshal URL resolution for %s onto the Qt thread",
                    root_id,
                )

        for root_id in roots:
            def _completion(url, error, _root_id=root_id) -> None:
                _dispatch(_root_id, url, error)

            try:
                self._url_resolver(root_id, _completion)
            except Exception as error:
                _dispatch(root_id, None, error)

    @Slot("QVariant")
    def _run_url_resolved(self, payload) -> None:
        attempt, root_id, url, error = payload
        if self._pending_resolution is None or self._pending_resolution[0] != attempt:
            return  # superseded arm attempt - a newer one has already started
        _, transfer_id, roots, pending, resolved = self._pending_resolution
        if transfer_id != self._accepted_transfer_id:
            self._pending_resolution = None
            return
        if root_id not in pending:
            return
        if error is not None or url is None:
            logger.warning(
                "failed to resolve user-visible URL for %s: %r", root_id, error
            )
            self._pending_resolution = None
            self.transfer_failed.emit("url_resolution_failed")
            return
        pending.discard(root_id)
        resolved[root_id] = url
        if pending:
            return  # still waiting on other roots of this generation
        urls = [resolved[root_id] for root_id in roots]
        self._pending_resolution = None
        self._arm_resolved_urls(transfer_id, urls)

    def _arm_resolved_urls(self, transfer_id: str, urls: list[object]) -> None:
        """Publish only a still-current generation, and mark it armed only
        after the pasteboard call has returned successfully."""
        if (
            transfer_id != self._accepted_transfer_id
            or transfer_id != self._ack_transfer_id
        ):
            return
        self._arm(urls)
        self._armed_transfer_id = transfer_id

    # --- сообщения с провода (стриминг - Tasks 9-10: здесь заглушка)
    def handle_message(self, message: Message) -> None:  # noqa: ARG002 - stub
        """Ничего не делает в этой задаче.

        Ни при каких обстоятельствах не отправляет FILE_READ и не трогает
        буфер обмена - это гарантия privacy-инварианта этой задачи, а не
        забытая реализация: тело появится в Tasks 9-10.
        """
        return

    # --- завершение (стриминг ещё не подключён - Tasks 9-10: заглушки)
    def cancel(self) -> None:
        return

    def stop(self) -> None:
        return


__all__ = ["FileProviderBackend", "LEASE_NS", "LEASE_SECONDS"]
