"""Transport boundary used by device services without a GUI dependency."""

from abc import ABC, abstractmethod


class AbstractByteTransport(ABC):
    """Minimal synchronous byte transport lifecycle."""

    def __init__(self) -> None:
        self._is_open = False

    @property
    def is_open(self) -> bool:
        return self._is_open

    def open(self) -> None:
        self._is_open = True

    def close(self) -> None:
        self._is_open = False

    @abstractmethod
    def write(self, data: bytes) -> bytes:
        """Write bytes and return synchronously available response bytes."""
