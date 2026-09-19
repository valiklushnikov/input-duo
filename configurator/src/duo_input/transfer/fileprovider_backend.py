"""FileProviderBackend - offer -> авторизация -> публикация generation (Task 7).

Первый вертикальный срез File Provider бэкенда: принятый offer публикует
generation через XPC в реплику Swift-расширения (атомарно + ACK), чтобы
пространство File Provider стало видимым. Стриминг (FILE_READ/FILE_CHUNK)
приезжает в Tasks 9-10, вооружение буфера обмена (``pasteboard_arm``) - в
Task 8. Здесь оба явно НЕ реализованы: ``handle_message`` - заглушка, ``_arm``
хранится, но не вызывается нигде в этом модуле.

Приватностный инвариант всего модуля - "эпоха авторизации": ``handle_offer``
возвращает эпоху текущего предложения; ``authorize`` обязан игнорировать любой
вызов, чей ``epoch`` не совпадает с текущим ожидающим (протухший/перекрытый
Accept) - публикация generation, запись в реплику и (в Task 8) вооружение
буфера обмена происходят СТРОГО после Accept текущей эпохи и только для неё.
Это переопределяет "мягкий компромисс" из spec §22: протухшая авторизация
теперь не публикует НИЧЕГО, а не публикует "с последующим retire".

``domain`` (Task 6) инъецируется и хранится, но не участвует в гейтинге
публикации в этой задаче - per-offer выбор File Provider vs staging и
готовность домена это Task 16 (сознательно не делается здесь, чтобы не
дублировать логику раньше времени).
"""

from __future__ import annotations

import logging
import time
from enum import Enum, auto

from PySide6.QtCore import QMetaObject, QObject, Q_ARG, Qt, Signal, Slot

from ..clipboard.wire import Message
from .fileprovider_client import FileProviderServiceClient
from .fileprovider_domain import FileProviderDomainManager
from .fileprovider_replica import STATE_ACTIVE, build_generation_record
from .model import TransferManifest

logger = logging.getLogger(__name__)

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
    """Offer/authorize/publish половина File Provider бэкенда (Task 7)."""

    #: (manifest, epoch) - epoch должен быть передан обратно в authorize().
    authorization_needed = Signal(object, int)
    transfer_started = Signal(object)
    transfer_progress = Signal("qlonglong", "qlonglong")
    transfer_completed = Signal()
    transfer_failed = Signal(str)
    transfer_cancelled = Signal()
    #: (transfer_id, root_ids) - внутренний сигнал, потребляет Task 8 (arm).
    generation_ready = Signal(str, object)

    def __init__(
        self,
        client: FileProviderServiceClient,
        domain: FileProviderDomainManager,
        pasteboard_arm,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._client = client
        #: Хранится, но не гейтит публикацию в этой задаче - см. Task 16.
        self._domain = domain
        #: НЕ вызывается нигде в этом модуле - вооружение это Task 8.
        self._arm = pasteboard_arm
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
        self._active_transfer_id: str | None = None
        self._generation_state: _GenerationState | None = None

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
        self.generation_ready.emit(manifest.transfer_id, _root_ids(manifest))

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
