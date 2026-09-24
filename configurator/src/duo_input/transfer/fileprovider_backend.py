"""FileProviderBackend - offer -> авторизация -> публикация generation ->
вооружение буфера обмена (Tasks 7-8).

Первый вертикальный срез File Provider бэкенда: принятый offer публикует
generation через XPC в реплику Swift-расширения (атомарно + ACK), чтобы
пространство File Provider стало видимым (Task 7). Как только это ACK
получено И домен (Task 6) READY, Task 8 резолвит user-visible ``file://``
URL корней generation через инъецируемый резолвер и вооружает СУЩЕСТВУЮЩИЙ
host-only буфер обмена (``pasteboard_arm`` - см. ``macos_pasteboard.arm_urls``),
а не второй подсистему. Task 9 добавляет ограниченный FIFO-планировщик
независимых fetch'ей и сопоставление ``FILE_CHUNK`` по ``read_id``; Task 10
доставляет ответы через Swift IPC, сохраняя один pending reply на fetch.

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

import itertools
import logging
import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, StrEnum, auto
from pathlib import PurePosixPath

from PySide6.QtCore import QMetaObject, QObject, QTimer, Q_ARG, Qt, Signal, Slot

from ..clipboard.wire import MAX_FILE_CHUNK_BYTES, Message, MessageType
from .fileprovider_client import FileProviderServiceClient, _xpc_error
from .fileprovider_domain import FileProviderDomainManager
from .fileprovider_generation_store import (
    GenerationRegistryStore,
    PersistedGeneration,
)
from .fileprovider_replica import STATE_ACTIVE, STATE_RETIRED, build_generation_record
from .model import ENTRY_FILE, TransferManifest

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
MAX_ACTIVE_FETCHES = 4
#: Per-file read window (spec §3). Up to this many ``FILE_READ`` ranges are kept
#: in flight/buffered per active fetch so the host↔Windows link stays full even
#: though the XPC ``pullChunk`` contract (and the Swift consumer) remain strictly
#: sequential - the host prefetches ahead of the consumer cursor and serves each
#: sequential pull from a bounded in-order reorder buffer. WINDOW=4 is the
#: measured configuration; this is NOT an adaptive window (spec §20/§32).
PER_FILE_READ_WINDOW = 4
#: Global read-ahead budget: file-level pipeline (MAX_ACTIVE_FETCHES=4) times the
#: per-file window (4) times one 1-MiB chunk = 16 MiB conceptual maximum payload
#: outstanding (spec §8). Bytes are only briefly held in the per-fetch reorder
#: buffer (received-but-not-yet-consumed); ``_outstanding_bytes`` sums the length
#: of every live range and the window planner parks against this bound (§9).
MAX_TOTAL_BUFFERED_BYTES = PER_FILE_READ_WINDOW * MAX_ACTIVE_FETCHES * MAX_FILE_CHUNK_BYTES
#: TTL после которого протухшая (retired) generation без in-use ref подлежит
#: удалению - зеркалит StagingArea(ttl_seconds=86_400)/LEASE_NS.
GENERATION_TTL_NS = LEASE_NS
#: Сколько retired generation держим одновременно: за этим порогом GC вытесняет
#: самую старую quiesced (ref==0) generation - count-budget, аналог disk-budget
#: у StagingArea.gc. Только метаданные реплики, не байты.
MAX_GENERATIONS = 8

#: Task 17 (ruling 3, carried from Task 14): deadline for a single outstanding
#: FILE_READ before the per-fetch watchdog fails it with Timeout. No exact
#: value is spec-mandated (spec §16 names "session timeout (watchdog)" without
#: a number) - chosen comfortably above a realistic single-chunk round trip
#: (MAX_FILE_CHUNK_BYTES fits in memory; no disk-bound wait expected on either
#: side) while still catching a genuinely hung host. Injectable via
#: ``timer_factory``/``read_timeout_ms`` so tests never wait it out.
FETCH_READ_TIMEOUT_MS = 30_000

#: NSFileProviderErrorDomainDisabled. There is a transient ~1-minute window
#: right after ``addDomain`` where the domain is still DISABLED:
#: ``getUserVisibleURLForItemIdentifier`` fails with this code and the mount
#: ``ls`` times out, until fileproviderd itself flips the domain to
#: ``state:enabled``. Observed and documented in the 2026-09-17 lazy-file-
#: provider spike ("код интеграции обязан учитывать: ретраи/ожидание enabled").
#: So an -2011 on the arm path is NOT a real failure - it means "not enabled
#: yet"; we retry the resolve rather than dropping the FP arm into staging.
FP_DOMAIN_DISABLED_CODE = -2011
#: Backoff between arm resolve retries while the domain is still disabled, and
#: a finite cap: chosen comfortably above the observed ~1-min window so the
#: first-registration transient is absorbed, but bounded so a genuinely stuck
#: (not merely transient) DISABLED state still eventually falls back honestly.
#: Both injectable-adjacent via ``timer_factory`` (tests drive the fake timer).
ARM_RETRY_INTERVAL_MS = 2_500
ARM_DISABLED_MAX_RETRIES = 48


def _entry_name(manifest: TransferManifest, entry_index: int) -> str:
    """Basename-only display name for one manifest entry - mirrors
    ``source.py:_name()``. Used ONLY in log lines (never in wire messages),
    and only the basename, never the (possibly nested) manifest path, per the
    module's privacy invariant (task-17 brief ruling #1)."""
    try:
        return PurePosixPath(manifest.entries[entry_index].path).name
    except (IndexError, AttributeError):
        return ""


def _log_event(event: str, **fields: object) -> None:
    """One structured log line per observable transition: ``event`` plus
    ``key=value`` correlation ids (``transfer_id``/``entry_index``/
    ``fetch_token``/``read_id``/basename-only ``name``) - see the counters
    section of the module docstring and task-17 brief ruling #1. NEVER pass a
    full manifest path or blob/content bytes here - only ids, basenames,
    counts and enum-ish labels.
    """
    rendered = " ".join(f"{key}={value}" for key, value in fields.items())
    logger.info("%s %s", event, rendered)


class FetchState(StrEnum):
    QUEUED = auto()
    #: Admitted (holds a slot in ``_active``); its read window is managed by
    #: ``_fill_window``. Replaces the old REQUESTING/RECEIVING split - with a
    #: window >1 a fetch can simultaneously have reads in flight AND buffered
    #: chunks, so the per-fetch distinction is no longer meaningful.
    REQUESTING = auto()
    DONE = auto()
    CANCELLED = auto()
    FAILED = auto()


class RangeState(StrEnum):
    #: ``FILE_READ`` sent, awaiting its ``FILE_CHUNK``.
    IN_FLIGHT = auto()
    #: ``FILE_CHUNK`` arrived and is buffered, awaiting in-order consume.
    RECEIVED = auto()


@dataclass
class _Range:
    """One windowed ``[offset, length]`` read of a fetch (spec §4/§7). Live from
    the moment its ``FILE_READ`` is issued (IN_FLIGHT) until the consumer pull
    delivers it (deleted). ``read_id`` is the wire correlation id."""

    read_id: int
    offset: int
    length: int
    state: RangeState
    blob: bytes | None = None


@dataclass
class Fetch:
    fetch_token: str
    generation_id: str
    entry_index: int
    size: int
    state: FetchState
    #: Next byte to hand to the (sequential) consumer - the in-order delivery
    #: cursor. A range is deliverable only when ``ranges[consume_offset]`` is
    #: RECEIVED (spec §6).
    consume_offset: int = 0
    #: Next byte to REQUEST - the window planner cursor (spec §4). Advances as
    #: ``FILE_READ``s are issued, independently of ``consume_offset``.
    plan_offset: int = 0
    #: Live ranges keyed by their byte offset; at most ``PER_FILE_READ_WINDOW``
    #: (invariant enforced in ``_fill_window``/``_issue_read``).
    ranges: dict[int, _Range] = field(default_factory=dict)
    #: Parked consumer pull reply - at most one (the Swift consumer pulls
    #: strictly serially, one ``pullChunk`` outstanding per fetch).
    reply: Callable | None = None
    #: True пока этот fetch держит in-use ref своей generation. Ставится один
    #: раз в _open_fetch, снимается ровно один раз при первом settle (см.
    #: _finish_fetch/_decrement_in_use) - гарантия "decrement exactly once"
    #: даже если cancel гонится с завершением (settle-once).
    in_use_counted: bool = False

    def in_flight(self) -> int:
        return sum(1 for r in self.ranges.values() if r.state is RangeState.IN_FLIGHT)


class _GenerationState(Enum):
    """Durable-состояние generation в учёте этого бэкенда.

    ``ACTIVE_CLIPBOARD`` - текущая вооружённая generation; ``RETIRED`` -
    перекрытая новым буфером обмена, но с СОХРАНЁННОЙ записью реплики (Task 15).
    Это зеркалит два durable-состояния самой реплики
    (``STATE_ACTIVE``/``STATE_RETIRED`` из fileprovider_replica.py).

    ``IN_USE`` и ``GC_ELIGIBLE`` из брифа - НЕ отдельные enum-состояния, а
    производные условия поверх ``RETIRED``: retired с in-use ref (>0) - это
    IN_USE (namespace deletion заблокирован), retired без in-use ref и за
    TTL/бюджетом - это GC_ELIGIBLE, т.е. можно НАЧАТЬ namespace deletion
    (``deleteGeneration`` -> extension тombstones; durable метаданные реплики
    сохраняются, PHYSICAL_REPLICA_DELETE = DISABLED). См. ``_gc``.
    """

    ACTIVE_CLIPBOARD = auto()
    RETIRED = auto()


@dataclass
class _Generation:
    """Учётная запись одной опубликованной generation - только метаданные
    жизненного цикла (не байты, не FILE_READ-состояние). ``manifest`` держим,
    чтобы retired-но-ещё-референсимая generation оставалась servable
    (``_open_fetch`` смотрит сюда, а не только на ``_active_manifest``)."""

    transfer_id: str
    manifest: TransferManifest
    state: _GenerationState
    #: Момент активации (ACK), наносекунды инъецируемых часов - точка отсчёта
    #: TTL и порядок вытеснения по count-budget (старые первыми).
    created_ns: int
    #: True после отправки TRANSFER_END при переходе в quiesced (retired &
    #: ref==0) - чтобы сигнал ушёл РОВНО один раз на generation.
    quiesced: bool = False


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
    """Offer/publish/arm plus independent, bounded File Provider fetches."""

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
        url_resolver: Callable[[str, Callable[[object, object], None]], None]
        | None = None,
        clock: Callable[[], int] | None = None,
        generation_ttl_ns: int = GENERATION_TTL_NS,
        max_generations: int = MAX_GENERATIONS,
        timer_factory: Callable[[], object] | None = None,
        read_timeout_ms: int = FETCH_READ_TIMEOUT_MS,
        generation_store: GenerationRegistryStore | None = None,
    ) -> None:
        super().__init__(parent)
        # --- Task 17: observability - a plain dict, not a metrics framework
        # (ruling #2). Counters are incremented in place; gauges (e.g.
        # fp_active_fetches) are overwritten wherever the quantity they track
        # changes. Readable directly by tests via ``backend.counters``.
        self.counters: dict[str, int | str] = {}
        # --- Task 17 (ruling #3, carried from Task 14): per-outstanding-read
        # watchdog. ``timer_factory`` defaults to a real QTimer bound to this
        # backend; tests inject a fake factory so expiry never waits on a real
        # clock (see task-17 report). Keyed by read_id - the same key
        # by_read_id uses - so arm/disarm always targets exactly one read.
        self._timer_factory = (
            timer_factory if timer_factory is not None else lambda: QTimer(self)
        )
        self._read_timeout_ms = read_timeout_ms
        self._read_timers: dict[int, object] = {}
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
        self._accepted_manifest: TransferManifest | None = None
        self._active_transfer_id: str | None = None
        self._active_manifest: TransferManifest | None = None
        self._generation_state: _GenerationState | None = None
        # --- Task 15: generation lifecycle (retire / TTL+budget GC).
        #: Инъецируемые часы (наносекунды) для TTL - тесты передают ручной
        #: clock, продакшн - time.time_ns. Не sleep'аем нигде.
        self._clock = clock if clock is not None else time.time_ns
        self._generation_ttl_ns = generation_ttl_ns
        self._max_generations = max_generations
        #: transfer_id -> учётная запись жизненного цикла (active/retired).
        #: Retired-запись КЕПТ здесь (реплика тоже кепт), пока GC её не удалит.
        self._generations: dict[str, _Generation] = {}
        #: Host-owned durable mirror of _generations (metadata only). Written on
        #: every lifecycle transition and rehydrated at construction so an old
        #: RETIRED generation stays fetchable across a host restart with no new
        #: publish. None disables persistence (test fakes / non-FP hosts).
        self._generation_store = generation_store
        #: transfer_id -> число ACTIVE fetch'ей, привязанных к generation
        #: (in-use ref). ЯВНЫЙ счётчик, а не скан by_token: даже после Task 19
        #: (settled-записи больше не задерживаются в by_token - см.
        #: _finish_fetch) скан пересчитывал бы только ещё-не-удалённые записи
        #: в момент вызова, а не устойчивый учёт поверх времени. Инкремент в
        #: _open_fetch, декремент РОВНО раз при settle (см.
        #: Fetch.in_use_counted).
        self._gen_in_use: dict[str, int] = {}
        # --- Task 9: per-fetch scheduler. There is deliberately no shared
        # cursor/offset/read id: every open owns all three through Fetch.
        self.by_token: dict[str, Fetch] = {}
        self.by_read_id: dict[int, Fetch] = {}
        self._active: set[str] = set()
        self._queue: deque[str] = deque()
        # --- Task 11 / windowing: second, finer admission gate on top of the
        # slot gate above - a fetch whose next window read would push outstanding
        # bytes over MAX_TOTAL_BUFFERED_BYTES parks here (FIFO) until a range is
        # consumed and frees budget (_resume_windows). With the shipped config
        # (MAX_TOTAL_BUFFERED_BYTES == MAX_ACTIVE_FETCHES * window * chunk) this
        # is a defense-in-depth net that does not fire - the per-fetch window cap
        # already bounds outstanding reads; it only engages if a future
        # WINDOW/MAX_ACTIVE change would otherwise exceed the global budget.
        self._pull_queue: deque[str] = deque()
        self._read_ids = itertools.count(1)
        # --- Task 14: link.disconnected -> local fail-all (see attach_link).
        self._link_lost_slot: tuple[object, Callable] | None = None
        # --- Task 8: arm-when-ready latch (ACK И domain READY, ровно раз).
        self._ack_transfer_id: str | None = None
        self._ack_roots: tuple[str, ...] = ()
        self._domain_ready = False
        self._armed_transfer_id: str | None = None
        self._arm_attempt = 0
        self._pending_resolution: (
            tuple[int, str, tuple[str, ...], set[str], dict[str, object]] | None
        ) = None
        #: How many times the current arm has retried because the domain was
        #: still transiently DISABLED (-2011); reset on a new accepted
        #: generation and on a successful/terminal resolve. See
        #: FP_DOMAIN_DISABLED_CODE / _schedule_arm_retry.
        self._arm_retries = 0
        self._arm_retry_timer: object | None = None
        ready_signal = getattr(domain, "ready", None)
        if ready_signal is not None:
            ready_signal.connect(self.on_domain_ready)
        # Task 17: fp_domain_state/fp_domain_not_ready - observability only,
        # optional (test fakes such as plain ``object()`` or FakeDomain
        # without this signal keep working exactly as before Task 8/16 did).
        state_changed_signal = getattr(domain, "state_changed", None)
        if state_changed_signal is not None:
            state_changed_signal.connect(self._on_domain_state_changed)
        if getattr(domain, "is_ready", False):
            self.on_domain_ready()
        # Rehydrate durable generations BEFORE the backend is exposed for XPC
        # open_fetch (set_callbacks below): there must be no window where a fetch
        # can arrive while _generations is still empty after a restart.
        self._rehydrate_generations()
        if hasattr(client, "set_callbacks"):
            client.set_callbacks(self.open_fetch, self.pull_chunk, self.cancel_fetch)

    def _rehydrate_generations(self) -> None:
        """Reconstruct runtime ``_generations`` from the durable host store.

        Only metadata is restored; runtime-only state (``_gen_in_use``, fetch
        counters, pending replies) starts fresh at zero. A single corrupt record
        is skipped and logged, never aborting the whole rehydrate. A restored
        ACTIVE generation re-becomes the tracked active clipboard for serving and
        for the retire-on-next-publish handoff, but is NOT re-armed (arming needs
        a fresh ack+domain-ready latch, which only a new publish drives)."""
        store = self._generation_store
        if store is None:
            return
        loaded, skipped = store.load_all()
        for record in loaded:
            state = (
                _GenerationState.RETIRED
                if record.state == STATE_RETIRED
                else _GenerationState.ACTIVE_CLIPBOARD
            )
            self._generations[record.transfer_id] = _Generation(
                transfer_id=record.transfer_id,
                manifest=record.manifest,
                state=state,
                created_ns=record.created_ns,
                quiesced=record.quiesced,
            )
            self._gen_in_use.setdefault(record.transfer_id, 0)
            if state is _GenerationState.ACTIVE_CLIPBOARD:
                self._active_transfer_id = record.transfer_id
                self._active_manifest = record.manifest
                self._generation_state = _GenerationState.ACTIVE_CLIPBOARD
            _log_event(
                "fp_generation_rehydrate_loaded",
                transfer_id=record.transfer_id,
                state=record.state,
            )
        for identifier, reason in skipped:
            self._bump("fp_generation_rehydrate_skipped")
            _log_event(
                "fp_generation_rehydrate_skipped",
                transfer_id=identifier,
                reason=reason,
            )
        self._sync_generation_gauge()
        _log_event(
            "fp_generation_rehydrate_complete",
            loaded=len(loaded),
            skipped=len(skipped),
        )

    def _persist_generation(self, generation: _Generation) -> None:
        """Atomically write ``generation``'s durable record. Best-effort: a disk
        error degrades to "not durable across restart" (the pre-store behaviour),
        never breaks live serving. Callers invoke this BEFORE exposing the
        generation as serviceable / acknowledging a state change."""
        store = self._generation_store
        if store is None:
            return
        state = (
            STATE_RETIRED
            if generation.state is _GenerationState.RETIRED
            else STATE_ACTIVE
        )
        try:
            store.save(
                PersistedGeneration(
                    transfer_id=generation.transfer_id,
                    manifest=generation.manifest,
                    state=state,
                    created_ns=generation.created_ns,
                    quiesced=generation.quiesced,
                )
            )
        except OSError:
            logger.exception(
                "could not persist generation %s durably", generation.transfer_id
            )

    # --- проводка
    def attach_link(self, link) -> None:
        """Attach the peer link and, if it exposes a ``disconnected`` signal
        (the real ``PeerLink`` does - see ``clipboard/peer.py``; the plain
        ``FakeLink`` fixture in test_fileprovider_scheduler.py deliberately
        does not, and must keep working unchanged), subscribe to it so a real
        disconnect fails every in-flight fetch locally (Task 14 - see
        ``_on_link_lost``/``_fail_all_active``). Mirrors the existing
        ``TransferService.attach_link``/``_release_link_lost_slot`` idiom
        (``service.py``) rather than inventing a new one.
        """
        self._release_link_lost_slot()
        self._link = link
        self._bump("fp_ipc_connect")
        _log_event(
            "fp_ipc_connect",
            connection_generation=getattr(link, "connection_generation", "unknown"),
        )
        disconnected = getattr(link, "disconnected", None)
        if disconnected is None:
            return

        def _on_disconnected(reason, attached_link=link) -> None:
            self._on_link_lost(attached_link, reason)

        disconnected.connect(_on_disconnected)
        self._link_lost_slot = (link, _on_disconnected)

    def _release_link_lost_slot(self) -> None:
        slot, self._link_lost_slot = self._link_lost_slot, None
        if slot is None:
            return
        link, handler = slot
        try:
            link.disconnected.disconnect(handler)
        except (RuntimeError, TypeError):
            pass

    def _on_link_lost(self, link, reason: str) -> None:
        """Real disconnect (invalidated/interrupted/socket-closed): local-only
        fail-all, no wire message (there is nothing left to send it to, and
        ``FILE_ERROR`` already only flows host->us, never us->host) - see
        task-14 brief ruling #4. ``self._link`` is cleared so any fetch
        opened/pulled AFTER this point sees "not connected" (code 8,
        ``serverUnreachable`` on the Swift side) rather than trying to use a
        dead link.
        """
        if link is not self._link:
            return  # stale slot from an already-superseded link - ignore
        logger.info("file provider peer link lost (%s)", reason)
        self._bump("fp_ipc_disconnect")
        _log_event("fp_ipc_disconnect", reason=reason)
        self._release_link_lost_slot()
        self._link = None
        self._fail_all_active(_xpc_error(3))  # DuoFPErrorPeerLost

    def on_session_timeout(self) -> None:
        """Hook for a session watchdog (owned elsewhere) to call when the
        session is judged dead: every fetch still in flight fails locally
        with Timeout, the same local-only fail-all as a link disconnect."""
        self._fail_all_active(_xpc_error(5))  # DuoFPErrorTimeout

    def _fail_all_active(self, error) -> None:
        """Settle every fetch not already in a terminal state with ``error``,
        one at a time through the existing ``_finish_fetch`` (so each one's
        pending XPC reply is settled, its byte-budget freed, and both queues
        re-admitted exactly like any other single-fetch failure) - see
        ``_on_link_lost``/``on_session_timeout``. Snapshotting the token list
        first is required: ``_finish_fetch`` re-admits ``_queue``, which can
        promote a QUEUED fetch to REQUESTING (still non-terminal, still
        correctly failed on the next iteration) - iterating ``by_token``
        itself while it mutates would be unsafe.
        """
        tokens = [
            token
            for token, fetch in self.by_token.items()
            if fetch.state
            not in (FetchState.DONE, FetchState.FAILED, FetchState.CANCELLED)
        ]
        for token in tokens:
            fetch = self.by_token.get(token)
            if fetch is None or fetch.state in (
                FetchState.DONE,
                FetchState.FAILED,
                FetchState.CANCELLED,
            ):
                continue
            self._finish_fetch(fetch, FetchState.FAILED, error)

    def set_peer_capabilities(self, caps) -> None:
        self._peer_caps = frozenset(caps)

    # --- Task 17: counters (a plain dict - see __init__ - not a framework)
    def _bump(self, name: str, by: int = 1) -> None:
        self.counters[name] = self.counters.get(name, 0) + by

    def _sync_active_gauges(self) -> None:
        self.counters["fp_active_fetches"] = len(self._active)
        self.counters["fp_queued_fetches"] = len(self._queue)

    def _sync_generation_gauge(self) -> None:
        active = sum(
            1
            for generation in self._generations.values()
            if generation.state is _GenerationState.ACTIVE_CLIPBOARD
        )
        self.counters["fp_generation_active"] = active

    def record_backend_selected(self, kind: str) -> None:
        """Hook for ``MacReceiveRouter._select_backend`` (Task 16,
        ``platform_files.py``) to call at the real selection site, so
        ``fp_backend_selected{file_provider|staging}`` increments where the
        choice is actually made. ``platform_files.py`` is NOT in this task's
        file list (see task-17 report "fp_backend_selected" section) - this
        method is exposed for that router to call, but the call site itself
        is intentionally NOT wired here.
        """
        if kind not in ("file_provider", "staging"):
            raise ValueError(f"unknown backend kind: {kind!r}")
        self._bump(f"fp_backend_selected_{kind}")
        _log_event("fp_backend_selected", backend=kind)

    def _on_domain_state_changed(self, state: str) -> None:
        self.counters["fp_domain_state"] = state
        _log_event("fp_domain_state", state=state)
        if state != "ready":
            self._bump("fp_domain_not_ready")
            _log_event("fp_domain_not_ready", state=state)

    # --- Task 17 (ruling #3): per-outstanding-read watchdog
    def _arm_watchdog(self, read_id: int) -> None:
        timer = self._timer_factory()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._on_watchdog_expired(read_id))
        self._read_timers[read_id] = timer
        timer.start(self._read_timeout_ms)

    def _disarm_watchdog(self, read_id: int) -> None:
        timer = self._read_timers.pop(read_id, None)
        if timer is not None:
            timer.stop()

    def _on_watchdog_expired(self, read_id: int) -> None:
        """A single outstanding read's deadline passed with no chunk/error.
        Fails EXACTLY that fetch with Timeout (the same mapping
        ``on_session_timeout`` uses) - every other in-flight fetch is
        untouched. Guards against a stale/racing timer firing after the read
        already settled (disarmed) or was reassigned - by the time a real
        settle happens ``_clear_all_ranges`` (or ``_on_chunk`` for that one
        range) has already popped this read_id out of both ``_read_timers`` and
        ``by_read_id``. Any single windowed read that hangs fails the whole
        fetch with Timeout (its other in-flight ranges are cleared with it).
        """
        self._read_timers.pop(read_id, None)
        fetch = self.by_read_id.get(read_id)
        if fetch is None:
            return  # already settled/received - stale timer, no-op
        self._bump("fp_fetch_timeout")
        _log_event(
            "fp_fetch_timeout",
            transfer_id=fetch.generation_id,
            entry_index=fetch.entry_index,
            fetch_token=fetch.fetch_token,
            read_id=read_id,
        )
        self._finish_fetch(fetch, FetchState.FAILED, _xpc_error(5))  # DuoFPErrorTimeout

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
        # Kept so _fp_item_identifier can map roots→FP identifiers on the arm
        # path even when arm is driven by the test-only on_ack() seam (which,
        # unlike the real _run_publish_reply, never sets _active_manifest).
        self._accepted_manifest = manifest
        # Новая принятая generation немедленно аннулирует ACK-latch и любое
        # незавершённое разрешение URL предыдущей. Иначе READY или поздний
        # completion старой generation сможет вооружить clipboard между
        # Accept(B) и ACK(B).
        self._ack_transfer_id = None
        self._ack_roots = ()
        self._arm_attempt += 1
        self._pending_resolution = None
        # A new accepted generation supersedes any in-flight domain-disabled
        # arm retry from a previous one (its retry timer must not fire and
        # re-arm the old roots).
        self._arm_retries = 0
        self._cancel_arm_retry()
        self._publish_generation(manifest, epoch)

    def _publish_generation(self, manifest: TransferManifest, epoch: int) -> None:
        _log_event(
            "fp_host_control_rpc_begin",
            operation="publishGeneration",
            transfer_id=manifest.transfer_id,
            timestamp_ns=time.time_ns(),
        )
        # Use an XPC error handler so a dropped/failed publish (connection
        # rejected by the appex, appex crash, etc.) surfaces as a logged error
        # instead of the reply block silently never firing - which is exactly
        # the failure mode that masked an earlier reply-block bug.
        def _xpc_error_handler(error) -> None:
            logger.error(
                "publishGeneration XPC connection error for %s: %r",
                manifest.transfer_id,
                error,
            )

        # Prefer the error-handler proxy (production client); fall back to the
        # plain proxy for test fakes / any client that predates it.
        get_remote = getattr(self._client, "remote_with_error_handler", None)
        remote = (
            get_remote(_xpc_error_handler)
            if get_remote is not None
            else self._client.remote()
        )
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
            _log_event(
                "fp_host_control_rpc_reply",
                operation="publishGeneration",
                transfer_id=manifest.transfer_id,
                ack=bool(ack),
                error_present=error is not None,
                timestamp_ns=time.time_ns(),
            )
            # NSXPCConnection доставляет reply-блок publishGeneration:reply: на
            # приватной XPC/фоновой очереди - НЕ на Qt-потоке. Мутация полей
            # QObject и emit сигналов оттуда небезопасны, поэтому переносим
            # обработку на Qt-поток тем же приёмом, что и обратное направление
            # (FileProviderServiceClient._dispatch_extension_call): при
            # AutoConnection это прямой (синхронный) вызов, когда мы уже на
            # Qt-потоке - на этом держатся синхронные фейки в тестах - и
            # очередь, когда ответ пришёл с чужого потока.
            self._deliver_publish_reply(manifest, epoch, ack, error)

        try:
            _log_event(
                "fp_host_control_rpc_sent",
                operation="publishGeneration",
                transfer_id=manifest.transfer_id,
                timestamp_ns=time.time_ns(),
            )
            remote.publishGeneration_reply_(record, _on_reply)
        except Exception:  # noqa: BLE001 - a swallowed XPC/block error here is invisible otherwise
            logger.exception(
                "publishGeneration raised over XPC for %s", manifest.transfer_id
            )
            raise

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
        _log_event(
            "fp_host_control_rpc_completion",
            operation="publishGeneration",
            transfer_id=manifest.transfer_id,
            ack=bool(ack),
            error_present=error is not None,
            timestamp_ns=time.time_ns(),
        )
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
        # Task 15: перехват предыдущей активной generation ДО перезаписи
        # _active_transfer_id - её нужно RETIRE (state-only, запись кепт), а не
        # бросить: она может быть ещё в работе (in-use ref) и её нельзя терять
        # посреди fetch'а. deleteGeneration - позже, из GC, и только без ref.
        prev_id = self._active_transfer_id
        self._register_active_generation(manifest)
        self._active_transfer_id = manifest.transfer_id
        self._active_manifest = manifest
        self._generation_state = _GenerationState.ACTIVE_CLIPBOARD
        if prev_id is not None and prev_id != manifest.transfer_id:
            self._retire_generation(prev_id)
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

    def _fp_item_identifier(self, transfer_id: str, root: str) -> str:
        """Map a top-level manifest root (first path segment / basename, as
        ``_root_ids()`` produces) to the ``NSFileProviderItemIdentifier`` the
        Swift domain vends for it: the entry's ``"<transfer_id>:<index>"`` when
        the root is itself a manifest entry (a top-level file or directory
        entry - see ``ItemModel.swift``), else the bare ``"<transfer_id>"``
        generation container as a safe fallback (an implicit top-level dir with
        no directory entry of its own)."""
        manifest = self._accepted_manifest or self._active_manifest
        if manifest is not None:
            for index, entry in enumerate(manifest.entries):
                if entry.path == root:
                    return f"{transfer_id}:{index}"
        return transfer_id

    def _arm_after_ready(self, transfer_id: str) -> None:
        """Резолвить user-visible URL корней ``transfer_id`` (латчнутых в
        ``_ack_roots``) через ``self._url_resolver`` и вооружить буфер обмена
        ОДНИМ вызовом ``self._arm`` после того, как ВСЕ корни резолвнуты.
        """
        # BUGFIX (production E2E, bug #2): getUserVisibleURLForItemIdentifier
        # needs a real NSFileProviderItemIdentifier - the Swift domain vends
        # "<transfer_id>:<index>" for entries (and "<transfer_id>" for the
        # generation container), NOT the top-level path segment (basename) that
        # _root_ids()/_ack_roots carries for the clipboard tree. Passing the
        # basename ("filetest1.txt") handed fileproviderd an unknown identifier
        # -> NSFileProviderErrorDomain -2011 "Sync is not enabled for (null)".
        # Unit tests inject a fake resolver that accepts any id, so this only
        # surfaced on the real File Provider path.
        basenames = self._ack_roots
        roots = tuple(
            self._fp_item_identifier(transfer_id, name) for name in basenames
        )
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
            # Transient DISABLED window right after addDomain (-2011): the
            # domain isn't enabled yet, not a real failure. Retry the whole arm
            # after a short delay instead of dropping the FP arm into staging;
            # give up (honest failure) only past the retry cap. See
            # FP_DOMAIN_DISABLED_CODE.
            if (
                self._is_domain_disabled(error)
                and self._arm_retries < ARM_DISABLED_MAX_RETRIES
            ):
                self._arm_retries += 1
                logger.info(
                    "fp arm: domain disabled (-2011), not enabled yet; "
                    "retry %d/%d for %s in %dms",
                    self._arm_retries,
                    ARM_DISABLED_MAX_RETRIES,
                    transfer_id,
                    ARM_RETRY_INTERVAL_MS,
                )
                self._pending_resolution = None
                self._schedule_arm_retry(transfer_id)
                return
            logger.warning(
                "failed to resolve user-visible URL for %s: %r", root_id, error
            )
            self._pending_resolution = None
            self._arm_retries = 0
            self.transfer_failed.emit("url_resolution_failed")
            return
        pending.discard(root_id)
        resolved[root_id] = url
        if pending:
            return  # still waiting on other roots of this generation
        urls = [resolved[root_id] for root_id in roots]
        self._pending_resolution = None
        self._arm_retries = 0
        self._arm_resolved_urls(transfer_id, urls)

    @staticmethod
    def _is_domain_disabled(error) -> bool:
        """True iff ``error`` is NSFileProviderErrorDomainDisabled (-2011) - the
        transient "domain not enabled yet" state just after ``addDomain`` (see
        FP_DOMAIN_DISABLED_CODE). Tolerates both a real ``NSError`` (``code``/
        ``domain`` are zero-arg methods) and a plain test double (attributes)."""
        if error is None:
            return False
        code = getattr(error, "code", None)
        code = code() if callable(code) else code
        domain = getattr(error, "domain", None)
        domain = domain() if callable(domain) else domain
        return code == FP_DOMAIN_DISABLED_CODE and (
            domain is None or "NSFileProviderErrorDomain" in str(domain)
        )

    def _schedule_arm_retry(self, transfer_id: str) -> None:
        """Re-run ``_arm_after_ready`` after ``ARM_RETRY_INTERVAL_MS`` - the
        domain is still in its transient disabled window. Cancels any prior
        retry timer; the fire is gated on the generation still being the
        current accepted+acked one and not already armed, so a superseding
        generation's retry can never re-arm stale roots."""
        self._cancel_arm_retry()
        timer = self._timer_factory()
        timer.setSingleShot(True)

        def _fire() -> None:
            self._arm_retry_timer = None
            if (
                transfer_id == self._accepted_transfer_id
                and transfer_id == self._ack_transfer_id
                and self._armed_transfer_id != transfer_id
            ):
                self._arm_after_ready(transfer_id)

        timer.timeout.connect(_fire)
        self._arm_retry_timer = timer
        timer.start(ARM_RETRY_INTERVAL_MS)

    def _cancel_arm_retry(self) -> None:
        timer = self._arm_retry_timer
        self._arm_retry_timer = None
        if timer is not None:
            timer.stop()

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
        _log_event(
            "fp_clipboard_armed",
            transfer_id=transfer_id,
            url_count=len(urls),
            timestamp_ns=time.time_ns(),
        )

    # --- Task 9: bounded per-fetch scheduler
    def open_fetch(self, generation_id: str, entry_index: int, reply=None):
        # Task 14: host-down (no peer link at all, e.g. never attached or
        # already disconnected - see _on_link_lost) must reply NotConnected,
        # not silently admit a fetch that can never actually read a byte
        # (_issue_read's own self._link is None check only fires once the window
        # starts issuing - by then the item already looks "in progress" to Finder
        # for no reason).
        if self._link is None:
            if reply is None:
                raise RuntimeError("file provider peer not connected")
            reply(None, None, _xpc_error(8))
            return None
        try:
            token, size = self._open_fetch(generation_id, entry_index)
        except ValueError:
            if reply is None:
                raise
            reply(None, None, _xpc_error(4))
            return None
        if reply is not None:
            reply(token, size, None)
        return token, size

    def _open_fetch(self, generation_id: str, entry_index: int) -> tuple[str, int]:
        """Open one regular file from a tracked generation.

        Task 15: serve ANY tracked generation, not just the active one - a
        RETIRED generation keeps its replica record and stays servable while
        referenced, and opening a fetch on it takes an in-use ref that blocks
        its GC deletion (never its retire). An unknown/already-GC'd id raises,
        exactly as a non-active id did before (the acked-generation gate in
        test_fileprovider_scheduler still holds: nothing is tracked before ACK).
        """
        generation = self._generations.get(generation_id)
        if generation is None:
            raise ValueError(f"generation {generation_id!r} is not published")
        manifest = generation.manifest
        if (
            not isinstance(entry_index, int)
            or isinstance(entry_index, bool)
            or not 0 <= entry_index < len(manifest.entries)
        ):
            raise ValueError(f"entry {entry_index!r} is not in the active generation")
        entry = manifest.entries[entry_index]
        if entry.kind != ENTRY_FILE:
            raise ValueError("directories have no fetchable contents")

        fetch_token = uuid.uuid4().hex
        state = (
            FetchState.REQUESTING
            if len(self._active) < MAX_ACTIVE_FETCHES
            else FetchState.QUEUED
        )
        fetch = Fetch(
            fetch_token=fetch_token,
            generation_id=generation_id,
            entry_index=entry_index,
            size=entry.size,
            state=state,
            in_use_counted=True,
        )
        self._gen_in_use[generation_id] = self._gen_in_use.get(generation_id, 0) + 1
        self.by_token[fetch_token] = fetch
        if state is FetchState.REQUESTING:
            self._active.add(fetch_token)
        else:
            self._queue.append(fetch_token)
        self._sync_active_gauges()
        self._bump("fp_fetch_started")
        _log_event(
            "fp_fetch_started",
            transfer_id=generation_id,
            entry_index=entry_index,
            fetch_token=fetch_token,
            name=_entry_name(manifest, entry_index),
        )
        return fetch_token, entry.size

    def pull_chunk(self, fetch_token: str, reply=None) -> None:
        """Serve the next in-order chunk to the (sequential) consumer and keep
        this fetch's read window full (spec §4/§6).

        The Swift consumer pulls strictly one chunk at a time, but the host
        keeps up to ``PER_FILE_READ_WINDOW`` ``FILE_READ`` ranges in flight per
        fetch (``_fill_window``) so the host<->Windows link never idles between
        chunks. If the next in-order range is already buffered it is delivered
        at once; otherwise this reply is parked until its range arrives
        (``_try_deliver`` from ``_on_chunk``). Window refill is bounded by the
        global byte budget; a fetch that cannot fill because the budget is full
        parks in ``_pull_queue`` (FIFO) and resumes as ranges are consumed
        (``_resume_windows``).
        """
        fetch = self.by_token.get(fetch_token)
        if fetch is None or fetch.state in (
            FetchState.DONE,
            FetchState.FAILED,
            FetchState.CANCELLED,
        ):
            if reply is not None:
                reply(None, False, _xpc_error(7))
            return
        if fetch.reply is not None:
            # A consumer pull is already parked - the Swift side pulls serially
            # (one pullChunk outstanding per fetch); a second concurrent pull is
            # a protocol error.
            if reply is not None:
                reply(None, False, _xpc_error(7))
            return
        fetch.reply = reply
        self._fill_window(fetch)
        self._try_deliver(fetch)

    def _outstanding_bytes(self) -> int:
        """Sum of the length of every live range across all fetches - the
        quantity ``MAX_TOTAL_BUFFERED_BYTES`` bounds (spec §8/§9). Counts both
        IN_FLIGHT ranges (requested, not yet received) and RECEIVED ranges
        (buffered in the per-fetch reorder store, not yet consumed): both hold
        payload the host is on the hook for. Unlike the old WINDOW=1 backend,
        RECEIVED ranges really are held in memory (``_Range.blob``) until the
        sequential consumer catches up, bounded to <= 4 MiB per stream."""
        return sum(
            r.length for fetch in self.by_token.values() for r in fetch.ranges.values()
        )

    def _fill_window(self, fetch: Fetch) -> None:
        """Issue ``FILE_READ``s until the window is full, the file is fully
        planned, or the global byte budget is reached (then park in
        ``_pull_queue``). Idempotent; safe to call on every pull/consume."""
        while (
            fetch.state is FetchState.REQUESTING
            and fetch.fetch_token in self._active
            and len(fetch.ranges) < PER_FILE_READ_WINDOW
            and fetch.plan_offset < fetch.size
        ):
            length = min(MAX_FILE_CHUNK_BYTES, fetch.size - fetch.plan_offset)
            if self._outstanding_bytes() + length > MAX_TOTAL_BUFFERED_BYTES:
                if fetch.fetch_token not in self._pull_queue:
                    self._pull_queue.append(fetch.fetch_token)
                return
            offset = fetch.plan_offset
            fetch.plan_offset += length
            if not self._issue_read(fetch, offset, length):
                return  # genuine send failure -> fetch already settled FAILED

    def _issue_read(self, fetch: Fetch, offset: int, length: int) -> bool:
        """Send one ``FILE_READ`` for ``[offset, length]`` and register its live
        range. Returns False only on a genuine send failure (the fetch is then
        already settled FAILED); a fake link that completes synchronously inside
        ``send()`` returns True (the range was consumed, not failed)."""
        if self._link is None:
            self._finish_fetch(fetch, FetchState.FAILED, _xpc_error(8))
            return False
        read_id = next(self._read_ids)
        rng = _Range(
            read_id=read_id, offset=offset, length=length, state=RangeState.IN_FLIGHT
        )
        fetch.ranges[offset] = rng
        self.by_read_id[read_id] = fetch
        self._observe_outstanding(fetch)
        self._bump("fp_range_issued")
        _log_event(
            "fp_range_issued",
            transfer_id=fetch.generation_id,
            entry_index=fetch.entry_index,
            fetch_token=fetch.fetch_token,
            read_id=read_id,
            offset=offset,
            length=length,
        )
        # Arm the per-read watchdog BEFORE send() - a fake link may complete
        # synchronously inside send() (SynchronouslyCompletingLink), which
        # disarms this same timer via the chunk path before send() returns.
        self._arm_watchdog(read_id)
        message = Message(
            MessageType.FILE_READ,
            {
                "transfer_id": fetch.generation_id,
                "entry_index": fetch.entry_index,
                "offset": offset,
                "length": length,
                "read_id": read_id,
            },
            b"",
        )
        try:
            sent = self._link.send(message)
        except Exception as error:  # noqa: BLE001 - external transport boundary
            logger.warning(
                "FILE_READ send failed for fetch %s: %s",
                fetch.fetch_token,
                type(error).__name__,
            )
            sent = False
        if sent is False:
            # Only a genuine failure if this exact range is still live and
            # in-flight; a synchronous completion inside send() already retired
            # it (or the whole fetch), in which case there is nothing to fail.
            if (
                self.by_token.get(fetch.fetch_token) is fetch
                and fetch.state is FetchState.REQUESTING
                and fetch.ranges.get(offset) is rng
                and rng.state is RangeState.IN_FLIGHT
            ):
                self._finish_fetch(fetch, FetchState.FAILED, _xpc_error(3))
                return False
        return True

    def _try_deliver(self, fetch: Fetch) -> None:
        """Hand the next in-order range to a parked consumer reply, if present
        and buffered (spec §6). Delivers at most one chunk (the consumer pulls
        serially), advances the consume cursor, finishes on EOF, and refills the
        freed window slot. Out-of-order arrivals wait here until the range at
        ``consume_offset`` is RECEIVED."""
        if fetch.reply is None:
            return
        # Only an admitted (slot-holding) fetch may settle here: a fetch still in
        # the MAX_ACTIVE_FETCHES slot queue keeps its reply parked until
        # _admit_from_queue promotes it. Without this guard a zero-byte QUEUED
        # fetch would complete out of turn (bypassing the slot gate and leaving a
        # stale token in _queue).
        if (
            fetch.state is not FetchState.REQUESTING
            or fetch.fetch_token not in self._active
        ):
            return
        # Zero-byte, or the whole file already consumed: the fetch is complete.
        if fetch.consume_offset >= fetch.size:
            reply, fetch.reply = fetch.reply, None
            self._finish_fetch(fetch, FetchState.DONE)
            reply(b"", True, None)
            return
        rng = fetch.ranges.get(fetch.consume_offset)
        if rng is None or rng.state is not RangeState.RECEIVED:
            return  # not arrived yet - the reply stays parked
        reply, fetch.reply = fetch.reply, None
        blob = rng.blob if rng.blob is not None else b""
        del fetch.ranges[fetch.consume_offset]
        fetch.consume_offset += rng.length
        self._bump("fp_range_consumed")
        _log_event(
            "fp_range_consumed",
            transfer_id=fetch.generation_id,
            entry_index=fetch.entry_index,
            fetch_token=fetch.fetch_token,
            read_id=rng.read_id,
            offset=rng.offset,
            length=rng.length,
        )
        if fetch.consume_offset >= fetch.size:
            self._finish_fetch(fetch, FetchState.DONE)
            reply(blob, True, None)
            return
        reply(blob, False, None)
        # A window slot just freed: refill this stream, then let any
        # budget-parked stream proceed on the freed global budget.
        self._fill_window(fetch)
        self._resume_windows()

    def _resume_windows(self) -> None:
        """Re-fill budget-parked fetches (FIFO) after a range was consumed and
        freed global budget. Each ``_fill_window`` re-parks itself if it is
        still capped, so one drain pass per consume is sufficient."""
        pending = list(self._pull_queue)
        self._pull_queue.clear()
        for token in pending:
            fetch = self.by_token.get(token)
            if (
                fetch is None
                or fetch.state is not FetchState.REQUESTING
                or token not in self._active
            ):
                continue
            self._fill_window(fetch)

    def _observe_outstanding(self, fetch: Fetch) -> None:
        """Update the bound gauges (spec §21) and flag any window violation.
        Called right after a range is registered, so the maxima reflect the
        peak the process actually reached."""
        counters = self.counters
        in_flight = fetch.in_flight()
        per_bytes = sum(r.length for r in fetch.ranges.values())
        counters["fp_max_outstanding_reads_per_stream"] = max(
            int(counters.get("fp_max_outstanding_reads_per_stream", 0)), in_flight
        )
        counters["fp_max_global_outstanding_reads"] = max(
            int(counters.get("fp_max_global_outstanding_reads", 0)), len(self.by_read_id)
        )
        counters["fp_max_outstanding_bytes_per_stream"] = max(
            int(counters.get("fp_max_outstanding_bytes_per_stream", 0)), per_bytes
        )
        counters["fp_max_global_outstanding_bytes"] = max(
            int(counters.get("fp_max_global_outstanding_bytes", 0)),
            self._outstanding_bytes(),
        )
        # NOTE: window OCCUPANCY over time (and TIME_WINDOW_FULL_PERCENT, spec
        # §27) is derived from the fp_range_issued/received/consumed event stream
        # with timestamps, NOT a running mean here: an issue-time-only sample
        # would systematically understate the steady-state occupancy (it never
        # samples the window while it sits full between issues). The peak gauges
        # above are exact; the event log carries the rest.
        if in_flight > PER_FILE_READ_WINDOW or len(fetch.ranges) > PER_FILE_READ_WINDOW:
            self._bump("fp_window_bound_violation")
            _log_event(
                "fp_window_bound_violation",
                fetch_token=fetch.fetch_token,
                in_flight=in_flight,
                live=len(fetch.ranges),
            )

    def _admit_from_queue(self) -> None:
        while self._queue and len(self._active) < MAX_ACTIVE_FETCHES:
            fetch_token = self._queue.popleft()
            fetch = self.by_token.get(fetch_token)
            if fetch is None or fetch.state is not FetchState.QUEUED:
                continue
            fetch.state = FetchState.REQUESTING
            self._active.add(fetch_token)
            if fetch.reply is not None:
                # A pull arrived while this fetch was slot-queued: now that it is
                # admitted, start its window and serve any buffered range.
                self._fill_window(fetch)
                self._try_deliver(fetch)
        self._sync_active_gauges()

    def _correlate(self, message: Message) -> tuple[_Range | None, Fetch | None]:
        """Map a ``FILE_CHUNK``/``FILE_ERROR`` to its live IN_FLIGHT range via
        the wire ``read_id`` + ``offset`` (spec §5 - reuses existing identity).
        Returns ``(None, None)`` for a stale/late/unexpected message: unknown
        ``read_id``, terminal/reassigned range, or a header that does not match
        the range this fetch is expecting."""
        header = message.header
        read_id = header.get("read_id")
        if not isinstance(read_id, int) or isinstance(read_id, bool):
            return None, None
        entry_index = header.get("entry_index")
        offset = header.get("offset")
        if any(
            not isinstance(value, int) or isinstance(value, bool) or value < 0
            for value in (entry_index, offset)
        ):
            return None, None
        fetch = self.by_read_id.get(read_id)
        if fetch is None:
            return None, None
        rng = fetch.ranges.get(offset)
        if (
            rng is None
            or rng.read_id != read_id
            or rng.state is not RangeState.IN_FLIGHT
            or header.get("transfer_id") != fetch.generation_id
            or entry_index != fetch.entry_index
        ):
            return None, None
        return rng, fetch

    def _clear_all_ranges(self, fetch: Fetch) -> None:
        """Drop every live range of ``fetch``: pop each ``read_id`` from
        ``by_read_id`` and disarm its watchdog (so a late chunk for any of them
        is dropped as unknown), and release the reorder buffer. Used by every
        terminal settle (cancel/fail/timeout/disconnect)."""
        for rng in fetch.ranges.values():
            self.by_read_id.pop(rng.read_id, None)
            self._disarm_watchdog(rng.read_id)
        fetch.ranges.clear()

    def _rearm_inflight_watchdogs(self, fetch: Fetch) -> None:
        """Restart the deadline of every still-IN_FLIGHT read of ``fetch`` - used
        when a chunk arrives, so a windowed read's timeout measures time since the
        fetch last made progress, not time since it was issued (see ``_on_chunk``
        for why serial sender servicing makes this necessary)."""
        for rng in fetch.ranges.values():
            if rng.state is RangeState.IN_FLIGHT:
                self._disarm_watchdog(rng.read_id)
                self._arm_watchdog(rng.read_id)

    #: FetchState (terminal) -> its counter name, for _finish_fetch below.
    _TERMINAL_COUNTERS = {
        FetchState.DONE: "fp_fetch_completed",
        FetchState.CANCELLED: "fp_fetch_cancelled",
        FetchState.FAILED: "fp_fetch_failed",
    }

    def _finish_fetch(self, fetch: Fetch, state: FetchState, error=None) -> None:
        reply, fetch.reply = fetch.reply, None
        live_reads = len(fetch.ranges)
        self._clear_all_ranges(fetch)
        fetch.state = state
        self._active.discard(fetch.fetch_token)
        try:
            self._pull_queue.remove(fetch.fetch_token)
        except ValueError:
            pass
        self._sync_active_gauges()
        counter_name = self._TERMINAL_COUNTERS.get(state)
        if counter_name is not None:
            self._bump(counter_name)
            _log_event(
                counter_name,
                transfer_id=fetch.generation_id,
                entry_index=fetch.entry_index,
                fetch_token=fetch.fetch_token,
                live_reads=live_reads,
            )
        if reply is not None:
            reply(None, False, error or _xpc_error(7))
        self._admit_from_queue()
        self._resume_windows()
        # Task 15: settle-once in-use decrement. Every terminal path
        # (DONE/FAILED/CANCELLED) funnels through here, and in_use_counted
        # guards against a double-decrement when e.g. a cancel races an
        # already-settled completion. When the generation's ref hits 0 it may
        # now be quiesced (if retired) and GC-eligible.
        if fetch.in_use_counted:
            fetch.in_use_counted = False
            self._decrement_in_use(fetch.generation_id)
        # Task 19: every terminal settle (DONE/FAILED/CANCELLED) drops the
        # token from by_token here, unified - previously only cancel_fetch did
        # this explicitly, so a long-lived extension leaked one dict entry per
        # completed/failed fetch forever. All production readers of by_token
        # already use .get() and handle a None/missing entry the same way they
        # handle "unknown token" (see pull_chunk/_resume_windows/
        # _admit_from_queue/cancel_fetch) - verified during task-19 review, so
        # dropping it here the instant a fetch goes terminal is safe.
        self.by_token.pop(fetch.fetch_token, None)

    def cancel_fetch(self, fetch_token: str) -> None:
        """Task 12: Finder cancel, purely local - NO wire message (there is
        no ``FILE_CANCEL`` in ``MessageType`` and none must ever be added;
        see task-12 brief ruling #1). Drops ``fetch_token`` from ``by_token``
        AND every one of its ranges from ``by_read_id`` (via ``_finish_fetch``/
        ``_clear_all_ranges``), stops issuing any further ``FILE_READ`` for it,
        frees its window budget and re-admits both queues
        (``_admit_from_queue``/``_resume_windows`` inside ``_finish_fetch``).

        Idempotent no-op for an unknown token or one already settled
        (``DONE``/``FAILED``/already ``CANCELLED``) - ruling #2: cancel vs.
        completion is settle-once, and the loser here is always this no-op
        branch, never a crash. Any of the (up to ``PER_FILE_READ_WINDOW``)
        ``FILE_CHUNK``s that later arrive for the reads this fetch held find
        nothing in ``by_read_id`` and are dropped silently by ``_correlate``
        (unknown ``read_id``) - they cannot resurrect the fetch, write, refill,
        or complete it (spec §10).
        """
        fetch = self.by_token.get(fetch_token)
        if fetch is None or fetch.state in (
            FetchState.DONE,
            FetchState.FAILED,
            FetchState.CANCELLED,
        ):
            return
        outstanding = len(fetch.ranges)
        if outstanding:
            self._bump("fp_cancel_outstanding", outstanding)
            _log_event(
                "fp_cancel_outstanding",
                fetch_token=fetch_token,
                outstanding=outstanding,
            )
        self._finish_fetch(fetch, FetchState.CANCELLED, _xpc_error(3))

    def _on_chunk(self, message: Message) -> None:
        rng, fetch = self._correlate(message)
        if fetch is None or rng is None:
            # No live in-flight range matches this message - it settled/moved on
            # already (cancelled, timed out, a duplicate, or an unexpected/stale
            # range) by the time this chunk arrived (spec §7/§10).
            self._bump("fp_late_chunk")
            _log_event("fp_late_chunk", read_id=message.header.get("read_id"))
            return
        chunk_size = len(message.blob)
        if chunk_size != rng.length or rng.offset + chunk_size > fetch.size:
            if chunk_size > rng.length or rng.offset + chunk_size > fetch.size:
                event = "fp_oversized_chunk"
            else:
                event = "fp_truncated"
            self._bump(event)
            _log_event(
                event,
                transfer_id=fetch.generation_id,
                entry_index=fetch.entry_index,
                fetch_token=fetch.fetch_token,
                read_id=rng.read_id,
            )
            self._finish_fetch(fetch, FetchState.FAILED, _xpc_error(7))
            return
        # Range received: retire its read_id + watchdog and buffer the payload in
        # the bounded reorder store until the sequential consumer reaches it.
        self.by_read_id.pop(rng.read_id, None)
        self._disarm_watchdog(rng.read_id)
        rng.state = RangeState.RECEIVED
        rng.blob = message.blob
        # Forward progress on this fetch means the sender is alive, so restart the
        # deadline of every OTHER read still in flight for it. The window issues
        # up to PER_FILE_READ_WINDOW reads at once but the sender answers them
        # serially (one event-loop thread, see TransferService._answer_read), so
        # a later read's clock would otherwise be consumed by head-of-line
        # queueing behind earlier ones and fail a steadily-progressing transfer.
        # This keeps the watchdog meaning "no chunk for 30s" (a genuinely hung
        # host), exactly as it did under WINDOW=1, not "issued 30s ago".
        self._rearm_inflight_watchdogs(fetch)
        self._bump("fp_bytes_received", chunk_size)
        self._bump("fp_range_received")
        _log_event(
            "fp_bytes_received",
            transfer_id=fetch.generation_id,
            entry_index=fetch.entry_index,
            fetch_token=fetch.fetch_token,
            read_id=rng.read_id,
            bytes=chunk_size,
        )
        # Deliver in order if this filled the gap at the consume cursor; an
        # out-of-order arrival simply waits here (range stays RECEIVED). No
        # refill is due until a range is actually consumed (the window is still
        # full), which _try_deliver handles.
        self._try_deliver(fetch)

    #: Wire ``FILE_ERROR`` ``reason`` -> ``DuoFPErrorDomain`` code (spec §16).
    #: ``"cancelled"``/``"not connected"`` are deliberately absent - those are
    #: purely local (Finder cancel, no peer link at all), never a reason the
    #: OTHER side puts on the wire.
    _FILE_ERROR_REASON_CODES = {
        "source_missing": 1,  # DuoFPErrorSourceMissing
        "source_changed": 2,  # DuoFPErrorSourceChanged
        "peer_lost": 3,  # DuoFPErrorPeerLost
        "unauthorized": 4,  # DuoFPErrorUnauthorized
        "timeout": 5,  # DuoFPErrorTimeout
        "disk_full": 6,  # DuoFPErrorDiskFull
        "protocol": 7,  # DuoFPErrorProtocol
    }

    def _on_file_error(self, message: Message) -> None:
        _rng, fetch = self._correlate(message)
        if fetch is None:
            # Stale/late error for a range that already settled - drop it, the
            # same way _on_chunk drops a late chunk. A live fetch is failed once
            # (below); any other in-flight range's later chunk/error is then a
            # late no-op (spec §14).
            return
        reason = message.header.get("reason")
        code = (
            self._FILE_ERROR_REASON_CODES.get(reason, 7)
            if isinstance(reason, str)
            else 7
        )
        self._finish_fetch(fetch, FetchState.FAILED, _xpc_error(code))

    # --- Task 15: generation lifecycle (retire / quiesce / TTL+budget GC)
    def _register_active_generation(self, manifest: TransferManifest) -> None:
        """Record a freshly-acked generation as the active clipboard. Its
        ``created_ns`` (from the injectable clock) is the TTL/eviction anchor."""
        transfer_id = manifest.transfer_id
        generation = _Generation(
            transfer_id=transfer_id,
            manifest=manifest,
            state=_GenerationState.ACTIVE_CLIPBOARD,
            created_ns=self._clock(),
        )
        # Durable-first: commit the record before it becomes serviceable in
        # runtime state, so a crash can never surface an in-memory generation
        # whose metadata was never written.
        self._persist_generation(generation)
        self._generations[transfer_id] = generation
        self._gen_in_use.setdefault(transfer_id, 0)
        self._sync_generation_gauge()

    def _retire_generation(self, generation_id: str) -> None:
        """Persist ``state="retired"`` over XPC and mark the local record
        RETIRED - the record is KEPT (still enumerable/servable), never deleted
        here. Retire is state-only and is NEVER blocked by an in-use ref. If
        the generation is already quiesced (no in-use ref) it also emits the
        quiesce ``TRANSFER_END`` now; otherwise that waits until the last
        in-use ref drops (see ``_decrement_in_use``)."""
        generation = self._generations.get(generation_id)
        if generation is None or generation.state is _GenerationState.RETIRED:
            return
        generation.state = _GenerationState.RETIRED
        # Durable-first: commit RETIRED locally before acknowledging it over XPC,
        # so the state that survives a crash is never "more active" than the one
        # the extension was told about.
        self._persist_generation(generation)
        self._sync_generation_gauge()
        remote = self._client.remote()
        if remote is not None:
            remote.retireGeneration_reply_(
                generation_id, self._control_reply_cb("retireGeneration", generation_id)
            )
        else:
            logger.warning(
                "cannot retire generation %s: extension not connected", generation_id
            )
        if self._gen_in_use.get(generation_id, 0) == 0:
            self._quiesce_generation(generation_id)
        self._gc()

    def _decrement_in_use(self, generation_id: str) -> None:
        """Drop one in-use ref. At zero, a RETIRED generation quiesces (frees
        the sender's fds via ``TRANSFER_END``) and becomes GC-eligible."""
        remaining = self._gen_in_use.get(generation_id, 0) - 1
        if remaining <= 0:
            self._gen_in_use[generation_id] = 0
            self._quiesce_generation(generation_id)
            self._gc()
        else:
            self._gen_in_use[generation_id] = remaining

    def _quiesce_generation(self, generation_id: str) -> None:
        """A generation has quiesced when it is RETIRED and holds no in-use
        ref: signal the (unchanged) sender to ``close_descriptors`` via the
        EXISTING ``TRANSFER_END`` wire message, exactly once per generation."""
        generation = self._generations.get(generation_id)
        if generation is None or generation.state is not _GenerationState.RETIRED:
            return
        if generation.quiesced or self._gen_in_use.get(generation_id, 0) != 0:
            return
        generation.quiesced = True
        self._persist_generation(generation)
        self._send_transfer_end(generation_id)

    def _send_transfer_end(self, generation_id: str) -> None:
        """Send ``TRANSFER_END`` for a quiesced generation - the sender frees
        the held file descriptors it kept for this generation's reads
        (``close_descriptors``). Uses the existing closed-set wire message; no
        new ``MessageType`` is introduced. Best-effort: a missing/failed link
        just means there is nothing left to tell."""
        if self._link is None:
            return
        message = Message(
            MessageType.TRANSFER_END,
            {
                "transfer_id": generation_id,
                "session_id": generation_id,
                "status": "completed",
            },
            b"",
        )
        try:
            self._link.send(message)
        except Exception as error:  # noqa: BLE001 - external transport boundary
            logger.warning(
                "TRANSFER_END send failed for %s: %s",
                generation_id,
                type(error).__name__,
            )

    def _gc(self) -> None:
        """BEGIN NAMESPACE DELETION for quiesced, unreferenced RETIRED
        generations past TTL or over the count budget - mirrors
        ``StagingArea.gc`` (TTL sweep, then oldest-first budget eviction). The
        ``ref==0 AND (past TTL OR over budget)`` condition is unchanged; what
        changed is its consequence. ``deleteGeneration`` no longer means
        "physically remove the replica record" - the extension now TOMBSTONES
        the generation (durable delete journal + working-set signal) and KEEPS
        the record (PHYSICAL_REPLICA_DELETE = DISABLED this phase; metadata is
        tiny and retained indefinitely). The active generation and any in-use
        generation are never touched."""
        now = self._clock()
        retired = sorted(
            (
                g
                for g in self._generations.values()
                if g.state is _GenerationState.RETIRED
            ),
            key=lambda g: g.created_ns,  # oldest first
        )
        # 1) TTL: delete quiesced (ref==0) generations past their lease.
        survivors: list[_Generation] = []
        for generation in retired:
            in_use = self._gen_in_use.get(generation.transfer_id, 0)
            if in_use == 0 and now - generation.created_ns > self._generation_ttl_ns:
                self._delete_generation(generation.transfer_id)
            else:
                survivors.append(generation)
        # 2) Count budget: evict oldest quiesced generations beyond the budget.
        # An in-use generation is skipped (never dropped mid-fetch) and does not
        # itself count against the deletable budget below.
        over_budget = len(survivors) - self._max_generations
        for generation in survivors:
            if over_budget <= 0:
                break
            if self._gen_in_use.get(generation.transfer_id, 0) != 0:
                continue
            self._delete_generation(generation.transfer_id)
            over_budget -= 1

    def _delete_generation(self, generation_id: str) -> None:
        """Begin namespace deletion for a generation: drop this host's in-memory
        tracking (it is no longer a GC candidate) and ask the extension to
        TOMBSTONE it via ``deleteGeneration`` - a durable delete journal entry
        plus a working-set signal so File Provider drops the items from the
        namespace. The extension KEEPS the backing replica record
        (PHYSICAL_REPLICA_DELETE = DISABLED this phase); dropping the host's
        volatile bookkeeping is not a physical metadata delete. Best-effort like
        ``StagingArea.gc``: a failed XPC reply is only logged."""
        generation = self._generations.pop(generation_id, None)
        self._gen_in_use.pop(generation_id, None)
        # The generation is no longer serviceable by the host (its runtime
        # bookkeeping is gone), so drop the durable record too - it must not
        # rehydrate on the next restart. The extension keeps its own replica
        # record (PHYSICAL_REPLICA_DELETE = DISABLED); this is host bookkeeping.
        if self._generation_store is not None:
            self._generation_store.delete(generation_id)
        if generation is None:
            return
        self._sync_generation_gauge()
        self._bump("fp_gc_generation")
        _log_event("fp_gc_generation", transfer_id=generation_id)
        remote = self._client.remote()
        if remote is not None:
            remote.deleteGeneration_reply_(
                generation_id, self._control_reply_cb("deleteGeneration", generation_id)
            )
        else:
            logger.warning(
                "cannot delete generation %s: extension not connected", generation_id
            )

    def purge_stale_generations(self, persisted_ids) -> list[str]:
        """Startup recovery: any persisted replica record with NO live snapshot
        in this process (not in ``_generations``) is orphaned - a leftover from
        a previous run - and is tombstoned via ``deleteGeneration`` (durable
        delete journal + working-set signal; the extension KEEPS the record,
        PHYSICAL_REPLICA_DELETE = DISABLED). Returns the ids acted on.

        NOTE: intentionally NOT wired into app start this phase - after a host
        restart ``_generations`` is empty, so every persisted record would look
        "stale" and get tombstoned while File Provider may still hold its item
        identities. Deciding a safe condition is deferred (Phase 2,
        FILE_PROVIDER_TOMBSTONE_COMPACTION). Kept + unit-tested directly."""
        purged: list[str] = []
        remote = self._client.remote()
        for generation_id in persisted_ids:
            if generation_id in self._generations:
                continue
            purged.append(generation_id)
            if remote is not None:
                remote.deleteGeneration_reply_(
                    generation_id,
                    self._control_reply_cb("deleteGeneration", generation_id),
                )
            else:
                logger.warning(
                    "cannot purge stale generation %s: extension not connected",
                    generation_id,
                )
        return purged

    def _control_reply_cb(self, op: str, generation_id: str) -> Callable:
        """Reply block for retire/delete XPC calls. These do NOT mutate QObject
        state (local bookkeeping already happened before the call), so unlike
        publish they need no Qt-thread marshalling - a rejected reply is only
        logged; GC/startup-purge is best-effort and self-healing."""

        def _cb(ack, error=None) -> None:
            if not ack:
                logger.warning("%s for %s rejected: %r", op, generation_id, error)

        return _cb

    # --- messages from the unchanged files/2 wire
    def handle_message(self, message: Message) -> None:
        if message.type is MessageType.FILE_CHUNK:
            self._on_chunk(message)
        elif message.type is MessageType.FILE_ERROR:
            self._on_file_error(message)

    # --- session-wide lifecycle is implemented by the later lifecycle tasks
    def cancel(self) -> None:
        return

    def stop(self) -> None:
        return


__all__ = [
    "Fetch",
    "FETCH_READ_TIMEOUT_MS",
    "FetchState",
    "FileProviderBackend",
    "GENERATION_TTL_NS",
    "LEASE_NS",
    "LEASE_SECONDS",
    "MAX_ACTIVE_FETCHES",
    "MAX_GENERATIONS",
    "MAX_TOTAL_BUFFERED_BYTES",
]
