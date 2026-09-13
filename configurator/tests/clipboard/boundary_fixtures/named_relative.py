"""``from .<сосед> import X``: старый разборщик записывал голое "qt_sibling"."""

from .qt_sibling import QWidget

__all__ = ["QWidget"]
