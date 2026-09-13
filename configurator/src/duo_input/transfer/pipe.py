"""Ограниченная очередь между COM-потоком и GUI-потоком Qt.

Это ЕДИНСТВЕННОЕ, что два потока разделяют, и поэтому здесь нет ни Qt, ни
сокетов, ни COM - только threading. Правило границы (boundary-тест) запрещает
этому модулю импортировать PySide6: иначе однажды кто-нибудь дёрнет
QSslSocket из COM-потока, потому что "так короче".

take() и wait() разделены намеренно. COM-поток обязан отправить FILE_READ
ПЕРЕД тем, как заблокироваться, а один совмещённый read() либо блокировался бы
до запроса, либо потребовал бы, чтобы очередь умела запрашивать сама - то есть
держала бы внутри себя Qt-объект, чего этот модуль существует чтобы избежать.

Потолок памяти здесь доказуем, а не обещан: push выше ёмкости отказывает, так
что очередь физически не может вырасти больше окна, сколько бы отправитель ни
присылал.
"""

from __future__ import annotations

import threading


class PipeClosed(Exception):
    """Очередь закрыта не по-хорошему: отмена, разрыв, ошибка."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PipeOverflow(Exception):
    """Попытка положить в очередь больше, чем разрешает окно."""


class ChunkPipe:
    """Байты от сети к потребителю, с окном и с мгновенным пробуждением."""

    def __init__(self, capacity_chunks: int = 1) -> None:
        if capacity_chunks < 1:
            raise ValueError("окно не может быть меньше одного чанка")
        self._capacity = capacity_chunks
        self._condition = threading.Condition()
        self._chunks: list[bytes] = []
        self._offset = 0
        self._finished = False
        self._closed_reason: str | None = None
        self._high_water = 0

    # ------------------------------------------------------------------ состояние

    @property
    def depth(self) -> int:
        with self._condition:
            return len(self._chunks)

    @property
    def high_water(self) -> int:
        with self._condition:
            return self._high_water

    @property
    def finished(self) -> bool:
        with self._condition:
            return self._finished

    @property
    def closed_reason(self) -> str | None:
        with self._condition:
            return self._closed_reason

    # ------------------------------------------------------------------ сторона сети

    def push(self, data: bytes) -> None:
        """Положить чанк. Никогда не блокирует - её зовёт GUI-поток."""
        with self._condition:
            if self._closed_reason is not None:
                raise PipeClosed(self._closed_reason)
            if self._finished:
                raise PipeClosed("поток уже завершён")
            if len(self._chunks) >= self._capacity:
                raise PipeOverflow(
                    f"в очереди уже {len(self._chunks)} чанков при окне {self._capacity}"
                )
            self._chunks.append(data)
            self._high_water = max(self._high_water, len(self._chunks))
            self._condition.notify_all()

    def finish(self) -> None:
        """Данных больше не будет, и это нормальный конец."""
        with self._condition:
            self._finished = True
            self._condition.notify_all()

    def close(self, reason: str) -> None:
        """Конец не нормальный. Первая причина побеждает.

        Второй close не перезаписывает причину: пользователь должен увидеть
        то, что произошло первым (отмену), а не то, что случилось следом как
        её последствие (разрыв).
        """
        with self._condition:
            if self._closed_reason is None:
                self._closed_reason = reason
            # notify_all, не notify: ждущих может быть несколько, и разбудить
            # одного означало бы оставить остальных висеть до таймаута.
            self._condition.notify_all()

    # ------------------------------------------------------------------ сторона потребителя

    def take(self, max_bytes: int) -> bytes:
        """До ``max_bytes`` байт. Никогда не блокирует; ``b""`` если пусто."""
        with self._condition:
            if self._closed_reason is not None:
                raise PipeClosed(self._closed_reason)
            if not self._chunks:
                return b""
            head = self._chunks[0]
            end = min(len(head), self._offset + max_bytes)
            payload = head[self._offset : end]
            if end >= len(head):
                self._chunks.pop(0)
                self._offset = 0
                self._condition.notify_all()
            else:
                self._offset = end
            return payload

    def wait(self, timeout: float) -> bool:
        """Дождаться данных, завершения или закрытия.

        ``True`` - есть что взять либо поток завершён; ``False`` - вышел срок;
        ``PipeClosed`` - закрыто.
        """
        with self._condition:
            if self._closed_reason is not None:
                raise PipeClosed(self._closed_reason)
            if self._chunks or self._finished:
                return True
            self._condition.wait(timeout)
            if self._closed_reason is not None:
                raise PipeClosed(self._closed_reason)
            return bool(self._chunks) or self._finished


__all__ = ["ChunkPipe", "PipeClosed", "PipeOverflow"]
