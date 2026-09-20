"""Выбор реализации границы платформы для передачи файлов.

Единственный sys.platform в подсистеме, по образцу
clipboard/platform_backend.py. Импорты ленивые и внутри ветвей: сборка под
macOS никогда не импортирует windows_files, а значит и windows_com, а значит и
ctypes-описания COM. В runtime-графе macOS Windows-кода нет вовсе.

macOS отвергается явно, а не заглушкой: аналог здесь -
NSFilePromiseProvider, его в M1 нет, и молчаливая заглушка выглядела бы как
работающая фича (спека §18).

Task 16 делает staging (``MacFileReceiver``, Tasks 1-6) и File Provider
(``FileProviderBackend``, Tasks 7-15) двумя backend'ами ОДНОГО приёмного
потока: ``MacReceiveRouter`` выбирает между ними РОВНО ОДИН РАЗ на каждое
предложение (offer) и ретранслирует сигналы выбранного backend'а наружу под
тем же интерфейсом, что раньше предоставлял голый ``MacFileReceiver`` - вызывающая
сторона (``app.py``) не должна знать, что за фасадом теперь два backend'а.
"""

from __future__ import annotations

import sys
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal


class UnsupportedPlatformError(Exception):
    """Платформа, для которой передачи файлов пока нет."""


class MacReceiveRouter(QObject):
    """Единая точка входа приёма файлов на macOS поверх ДВУХ backend'ов.

    Экспонирует ровно тот же интерфейс, что и ``MacFileReceiver``
    (``handle_offer``/``authorize``/``handle_message``/``cancel``/
    ``attach_link``/``set_peer_capabilities``/``stop`` + сигналы
    ``authorization_needed(object)``/``transfer_started``/``transfer_progress``/
    ``transfer_completed``/``transfer_failed``/``transfer_cancelled``) - вызывающая
    сторона (``app.py``) не отличает router от голого ``MacFileReceiver``.

    Выбор backend'а происходит РОВНО ОДИН РАЗ на ``handle_offer`` (см.
    ``_select_backend``) и фиксируется в ``self._active_backend`` на весь
    жизненный цикл этого предложения - ``authorize``/``handle_message``/
    ``cancel`` адресуются ТОЛЬКО этому backend'у, второй его offer/authorize
    вообще не видит (что и даёт "exactly one FILE_READ"/"armed exactly once":
    только выбранный backend когда-либо получает предложение, значит только
    он способен когда-либо послать FILE_READ или вооружить буфер).

    Единственное исключение - "предпубликационный" fallback (ruling #2
    Task 16): пока File Provider generation ещё НЕ зарегистрирована (до
    сигнала ``generation_ready``, самый ранний момент, когда FILE_READ через
    XPC вообще стал бы возможен), ``transfer_failed`` от File Provider
    backend'а не долетает наружу, а тихо перезапускает ТОТ ЖЕ manifest через
    staging (см. ``_fallback_to_staging``) - пользователь уже согласился на
    приём, второй раз спрашивать не нужно. После ``generation_ready``
    (``_fp_locked = True``) backend ЗАФИКСИРОВАН: последующий сбой - это уже
    настоящий отказ передачи (маппинг ошибок Task 14), а не повод для отката
    на staging.
    """

    authorization_needed = Signal(object)
    transfer_started = Signal(object)
    transfer_progress = Signal("qlonglong", "qlonglong")
    transfer_completed = Signal()
    transfer_failed = Signal(str)
    transfer_cancelled = Signal()

    def __init__(
        self,
        staging,
        fileprovider=None,
        *,
        domain=None,
        client=None,
        flag_enabled: Callable[[], bool] | None = None,
        os_supported: Callable[[], bool] | None = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._staging = staging
        self._fp = fileprovider
        self._domain = domain
        self._client = client
        self._flag_enabled = flag_enabled if flag_enabled is not None else (lambda: False)
        self._os_supported = os_supported if os_supported is not None else (lambda: True)

        #: Backend, выбранный для ТЕКУЩЕГО предложения (или ``None`` в простое).
        self._active_backend = None
        self._active_manifest = None
        self._active_transfer_id: str | None = None
        #: True после ``generation_ready`` текущей FP-generation - "точка
        #: невозврата" (см. докстринг класса): дальше сбои этой generation
        #: только отказывают передачу, но не откатывают на staging.
        self._fp_locked = False
        #: Кому адресовать следующий ``authorize()`` - backend, только что
        #: поднявший ``authorization_needed``.
        self._pending_backend = None
        self._pending_epoch: int | None = None
        #: True на время внутреннего "тихого" реплея offer'а через staging
        #: (см. ``_fallback_to_staging``) - подавляет повторный вопрос
        #: пользователю через ``authorization_needed`` наружу.
        self._suppress_staging_prompt = False
        #: Task 20: fallback registry for ``fp_backend_selected_{kind}`` when
        #: there is no FP backend instance to hold Task 17's own counters
        #: (Stage 1 default: flag off, ``fileprovider=None`` - see
        #: ``_record_selection``). Kept on the router itself rather than a
        #: module-level global so it never leaks across router instances/tests.
        self.selection_counters: dict[str, int] = {}

        self._wire(self._staging, is_fp=False)
        if self._fp is not None:
            self._wire(self._fp, is_fp=True)

    # --- проводка сигналов (один раз на весь жизненный цикл router'а)
    def _wire(self, backend, *, is_fp: bool) -> None:
        if is_fp:
            backend.authorization_needed.connect(self._on_fp_authorization_needed)
            backend.generation_ready.connect(self._on_fp_generation_ready)
            backend.transfer_failed.connect(self._on_fp_transfer_failed)
        else:
            backend.authorization_needed.connect(self._on_staging_authorization_needed)
            backend.transfer_failed.connect(self._on_staging_transfer_failed)
        backend.transfer_started.connect(self._relay(backend, self.transfer_started))
        backend.transfer_progress.connect(self._relay(backend, self.transfer_progress))
        backend.transfer_completed.connect(self._terminal(backend, self.transfer_completed))
        backend.transfer_cancelled.connect(self._terminal(backend, self.transfer_cancelled))

    def _relay(self, backend, signal: Signal) -> Callable:
        """Переслать сигнал ``backend`` наружу, только пока он ещё активен
        для текущего предложения - неактивный/уже смененный backend не
        должен ничего эмитить (структурно и не должен, но проверка дешёвая
        и защищает от сюрпризов в тестовых двойниках)."""

        def _forward(*args) -> None:
            if self._active_backend is backend:
                signal.emit(*args)

        return _forward

    def _terminal(self, backend, signal: Signal) -> Callable:
        def _forward(*args) -> None:
            if self._active_backend is not backend:
                return
            self._finish_active()
            signal.emit(*args)

        return _forward

    def _finish_active(self) -> None:
        self._active_backend = None
        self._active_manifest = None
        self._active_transfer_id = None
        self._fp_locked = False
        self._pending_backend = None
        self._pending_epoch = None

    # --- выбор backend'а (ruling #3 Task 16)
    def _select_backend(self):
        if (
            self._fp is not None
            and self._os_supported()
            and self._flag_enabled()
            and self._domain is not None
            and bool(getattr(self._domain, "is_ready", False))
            and self._client is not None
            and self._client.remote() is not None
        ):
            self._record_selection("file_provider")
            return self._fp
        self._record_selection("staging")
        return self._staging

    def _record_selection(self, kind: str) -> None:
        """Task 20: fire the Task 17 ``fp_backend_selected_{kind}`` counter
        at the real selection site, exactly once per ``handle_offer`` (the
        internal pre-publication fallback in ``_fallback_to_staging`` does
        NOT go through here again - it is not a new per-offer decision, see
        that method's docstring).

        When an FP backend instance exists (flag on, FP constructed - see
        app.py's ``_build_fileprovider_kwargs``), this reuses ITS OWN Task 17
        counters registry via the existing ``record_backend_selected`` hook,
        so a staging selection while FP is merely degraded (domain not
        ready/service unavailable) lands next to every other Task 17 counter
        on the same instance. Only when there is no FP backend instance at
        all (Stage 1 default: flag off - the common case) does the router
        fall back to its own tiny ``selection_counters`` dict under the
        identical key - no new metrics framework, just the smallest registry
        that still counts a staging-only run (ruling #2, Task 20)."""
        record = getattr(self._fp, "record_backend_selected", None)
        if record is not None:
            record(kind)
            return
        key = f"fp_backend_selected_{kind}"
        self.selection_counters[key] = self.selection_counters.get(key, 0) + 1

    # --- offer/авторизация
    def handle_offer(self, manifest) -> None:
        if self._active_backend is not None:
            # Предыдущее предложение ещё не завершилось (нет completed/
            # cancelled/failed между offer'ами) - гасим его локально. Это
            # МОЖЕТ быть другой backend, чем тот, что выберется ниже, поэтому
            # чистим явно, а не полагаемся на то, что backend сам заметит
            # повторный handle_offer (он видит только свои собственные
            # повторы, не чужие).
            self._active_backend.stop()
        self._active_manifest = manifest
        self._active_transfer_id = manifest.transfer_id
        self._fp_locked = False
        self._pending_backend = None
        self._pending_epoch = None
        backend = self._select_backend()
        self._active_backend = backend
        backend.handle_offer(manifest)

    def authorize(self, accepted: bool) -> None:
        backend = self._pending_backend
        if backend is None:
            return
        epoch = self._pending_epoch
        self._pending_backend = None
        self._pending_epoch = None
        if backend is self._fp:
            backend.authorize(accepted, epoch)
        else:
            backend.authorize(accepted)

    def _on_staging_authorization_needed(self, manifest) -> None:
        if self._suppress_staging_prompt:
            # Тихий реплей после pre-arm fallback (см. _fallback_to_staging) -
            # пользователь уже сказал "да" один раз для этого transfer_id.
            self._suppress_staging_prompt = False
            self._staging.authorize(True)
            return
        self._pending_backend = self._staging
        self._pending_epoch = None
        self.authorization_needed.emit(manifest)

    def _on_fp_authorization_needed(self, manifest, epoch: int) -> None:
        self._pending_backend = self._fp
        self._pending_epoch = epoch
        self.authorization_needed.emit(manifest)

    def _on_fp_generation_ready(self, transfer_id: str, _roots: object) -> None:
        if self._active_backend is self._fp and transfer_id == self._active_transfer_id:
            self._fp_locked = True

    def _on_staging_transfer_failed(self, reason: str) -> None:
        if self._active_backend is not self._staging:
            return
        self._finish_active()
        self.transfer_failed.emit(reason)

    def _on_fp_transfer_failed(self, reason: str) -> None:
        if self._active_backend is not self._fp:
            return
        if not self._fp_locked:
            self._fallback_to_staging()
            return
        self._finish_active()
        self.transfer_failed.emit(reason)

    def _fallback_to_staging(self) -> None:
        """Selection-time откат (ruling #2 Task 16): публикация FP ещё не
        зарегистрировала generation (``generation_ready`` не приходил), а
        значит ни один FILE_READ по ней в принципе не мог уйти и буфер обмена
        не мог быть вооружён - чисто, ничего не потеряно. Тот же manifest
        отправляется через staging тем же путём, каким пришёл бы обычный
        offer, автоматически принятый (``_suppress_staging_prompt``), без
        второго вопроса пользователю.
        """
        manifest = self._active_manifest
        self._active_backend = self._staging
        self._fp_locked = False
        self._suppress_staging_prompt = True
        self._staging.handle_offer(manifest)

    # --- остальной интерфейс MacFileReceiver
    def handle_message(self, message) -> None:
        backend = self._active_backend
        if backend is not None:
            backend.handle_message(message)

    def cancel(self) -> None:
        backend = self._active_backend
        if backend is not None:
            backend.cancel()

    def stop(self) -> None:
        self._staging.stop()
        if self._fp is not None:
            self._fp.stop()
        self._finish_active()

    def attach_link(self, link) -> None:
        self._staging.attach_link(link)
        if self._fp is not None:
            self._fp.attach_link(link)

    def set_peer_capabilities(self, caps) -> None:
        self._staging.set_peer_capabilities(caps)
        if self._fp is not None:
            self._fp.set_peer_capabilities(caps)


def create_file_backend(
    parent=None,
    *,
    fileprovider_backend=None,
    fileprovider_domain=None,
    fileprovider_client=None,
    fileprovider_flag_enabled: Callable[[], bool] | None = None,
    fileprovider_os_supported: Callable[[], bool] | None = None,
):
    """Собрать backend приёма файлов для текущей платформы.

    На darwin всегда возвращает ``MacReceiveRouter`` (даже когда File
    Provider недоступен/выключен) - единообразный тип для вызывающей
    стороны; без ``fileprovider_backend`` router просто всегда выбирает
    staging, byte-for-byte то же поведение, что было у голого
    ``MacFileReceiver`` раньше (Task 16 ruling #4: флаг по умолчанию
    выключен, регресс staging недопустим).

    Опциональные ``fileprovider_*``-параметры собирает и передаёт
    ``app.py`` (владелец жизненного цикла ``FileProviderDomainManager``/
    ``FileProviderServiceClient``/``FileProviderBackend`` и флага
    ``clipboard/fileprovider_enabled``) - эта фабрика сама ничего File
    Provider-специфичного не создаёт, только собирает router.
    """
    if sys.platform == "win32":
        from .windows_files import WindowsFileClipboardBackend

        return WindowsFileClipboardBackend(parent)
    if sys.platform == "darwin":
        from pathlib import Path

        from .macos_files import MacFileReceiver
        from .macos_pasteboard import arm
        from .staging import StagingArea

        root = Path.home() / "Library" / "Caches" / "duo-input" / "incoming"
        staging = StagingArea(root)
        # Startup invariant (спека): incomplete-каталоги от прошлого падения
        # снесены, а READY почищен по TTL/бюджету ДО того, как приёмник
        # начнёт что-либо принимать. Оба метода best-effort и сами глотают
        # ошибки файловой системы.
        staging.recover()
        staging.gc()
        staging_receiver = MacFileReceiver(staging, pasteboard_arm=arm, parent=parent)
        return MacReceiveRouter(
            staging_receiver,
            fileprovider_backend,
            domain=fileprovider_domain,
            client=fileprovider_client,
            flag_enabled=fileprovider_flag_enabled,
            os_supported=fileprovider_os_supported,
            parent=parent,
        )
    raise UnsupportedPlatformError(sys.platform)


__all__ = ["MacReceiveRouter", "UnsupportedPlatformError", "create_file_backend"]
