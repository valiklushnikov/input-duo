"""``from ..<сосед> import X``: подъём на уровень выше по пакету."""

from ..qt_sibling import QWidget

__all__ = ["QWidget"]
