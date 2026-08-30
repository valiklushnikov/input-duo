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

from PySide6.QtCore import QAbstractAnimation, QEasingCurve, QPoint, QPropertyAnimation
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

    How that is done depends on what ``widget`` is, and the two cases are not
    interchangeable. A ``QGraphicsOpacityEffect`` composites a child against
    its parent, which is right for a page inside a window. On a *top-level*
    window it composites against that window's own backing store, which is
    black - the capture dialog opened as a black rectangle and lightened into
    the page for the length of the fade. Only ``setWindowOpacity`` asks the
    compositor to blend a window with what is really behind it.

    A widget with no effect is a widget at full opacity, so the child's
    instant path clears any effect a previous fade left behind rather than
    setting one to 1.0.
    """
    if not animations_enabled() or duration <= 0:
        if widget.isWindow():
            widget.setWindowOpacity(1.0)
        else:
            widget.setGraphicsEffect(None)
        return None

    if widget.isWindow():
        return _fade_window_in(widget, duration)
    return _fade_child_in(widget, duration)


def _fade_window_in(widget: QWidget, duration: int) -> QPropertyAnimation:
    """Fade a top-level window against the desktop behind it."""
    widget.setWindowOpacity(0.0)
    animation = QPropertyAnimation(widget, b"windowOpacity", widget)
    animation.setDuration(duration)
    animation.setEasingCurve(_CURVE)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)

    def _restore(state: QAbstractAnimation.State, _previous) -> None:
        # A fade that is stopped part-way would otherwise leave a window the
        # operator cannot see and cannot dismiss.
        if state is not QAbstractAnimation.State.Running:
            widget.setWindowOpacity(1.0)

    animation.stateChanged.connect(_restore)
    animation.start(QPropertyAnimation.DeletionPolicy.DeleteWhenStopped)
    return animation


def _fade_child_in(widget: QWidget, duration: int) -> QPropertyAnimation:
    """Fade a widget against its parent, which is what a page needs."""
    effect = QGraphicsOpacityEffect(widget)
    widget.setGraphicsEffect(effect)
    animation = QPropertyAnimation(effect, b"opacity", widget)
    animation.setDuration(duration)
    animation.setEasingCurve(_CURVE)
    animation.setStartValue(0.0)
    animation.setEndValue(1.0)

    def _clear_effect(state: QAbstractAnimation.State, _previous) -> None:
        # DeleteWhenStopped frees the animation, not the effect it drove, so
        # a widget that was ever faded in would otherwise carry a graphics
        # effect forever - and a widget with one renders through an offscreen
        # buffer, which is exactly what leaked one page's pixels over another
        # inside a QStackedWidget. This hangs on ``stateChanged`` rather than
        # ``finished`` because Qt emits ``finished`` only for an animation
        # that reached its end: one stopped part-way would leave the widget
        # at opacity 0.0 for good. Guard against clearing a *newer* fade on
        # the same widget: if this fired late, the widget's current effect is
        # already someone else's.
        if state is QAbstractAnimation.State.Running:
            return
        if widget.graphicsEffect() is effect:
            widget.setGraphicsEffect(None)

    animation.stateChanged.connect(_clear_effect)
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
