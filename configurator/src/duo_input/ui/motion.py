"""How things move, and how to make them stop.

Qt style sheets have no ``transition``, so every animation here is an explicit
``QPropertyAnimation``. That cost is why so little moves: a modal appearing
and a page changing, and nothing else. Hover and press states change at once,
as they always have - animating them would mean an animation object bound to
every button in the program, which is a great deal of machinery for an effect
nobody is waiting to see.

Like :mod:`duo_input.ui.theme`, this module is presentation and nothing else.
It has never heard of a profile, a binding or a device.
"""

from __future__ import annotations

import os

from PySide6.QtCore import QEasingCurve, QPoint, QPropertyAnimation
from PySide6.QtWidgets import QGraphicsOpacityEffect, QWidget

#: A page changing under the operator: quick enough not to be a wait.
FAST = 160
#: The wash that dims the window behind a modal.
SCRIM = 200
#: A dialog rising into place.
LIFT = 220

_CURVE = QEasingCurve.Type.OutCubic


def animations_enabled() -> bool:
    """False where movement cannot be seen or must not be waited on.

    The offscreen platform renders for the screenshot tests, which compare
    finished frames; an animation there is a timer the suite would have to
    sleep through for no gain.
    """
    return os.environ.get("QT_QPA_PLATFORM") != "offscreen"


def fade_in(widget: QWidget, duration: int = FAST) -> QPropertyAnimation | None:
    """Bring ``widget`` up from transparent. ``None`` when it happened at once.

    A widget with no effect is a widget at full opacity, so the instant path
    clears any effect a previous fade left behind rather than setting one to
    1.0 - ``setWindowOpacity`` would be meaningless here, since the things
    faded are pages inside a window, not windows.
    """
    if not animations_enabled() or duration <= 0:
        widget.setGraphicsEffect(None)
        return None

    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setEasingCurve(_CURVE)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


def lift_in(
    widget: QWidget, distance: int = 10, duration: int = LIFT
) -> QPropertyAnimation | None:
    """Raise ``widget`` into its own position from ``distance`` below it."""
    if not animations_enabled() or duration <= 0:
        return None

    end = widget.pos()
    start = QPoint(end.x(), end.y() + distance)
    animation = QPropertyAnimation(widget, b"pos", widget)
    animation.setDuration(duration)
    animation.setEasingCurve(_CURVE)
    animation.setStartValue(start)
    animation.setEndValue(end)
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


__all__ = ["FAST", "LIFT", "SCRIM", "animations_enabled", "fade_in", "lift_in"]
