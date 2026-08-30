"""Movement, and the promise that it can always be skipped.

Animation here is decoration over a program that has to stay testable: every
helper must be able to reach its end state instantly, or the offscreen suite
would be waiting on timers it cannot see.
"""

from __future__ import annotations

import pytest
from PySide6.QtWidgets import QWidget

from duo_input.ui import motion


@pytest.fixture
def window(qtbot) -> QWidget:
    """A top-level widget - what both modal dialogs are."""
    item = QWidget()
    qtbot.addWidget(item)
    return item


@pytest.fixture
def widget(window) -> QWidget:
    """A page inside a window - what the stacked pages are.

    The two cases are not interchangeable: a graphics effect composites a
    child against its parent, but a *window* against its own backing store,
    which is black. Anything asserting about the effect has to say which of
    the two it means, so the child path gets its own parent here.
    """
    return QWidget(window)


def test_the_durations_are_ordered_the_way_the_design_states():
    assert motion.FAST < motion.SCRIM <= motion.LIFT


def test_animation_is_off_under_the_offscreen_platform(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")

    assert motion.animations_enabled() is False


def test_a_disabled_fade_leaves_the_widget_fully_visible(widget, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)

    assert motion.fade_in(widget) is None
    assert widget.graphicsEffect() is None


def test_a_zero_duration_fade_is_also_instant(widget, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)

    assert motion.fade_in(widget, duration=0) is None
    assert widget.graphicsEffect() is None


def test_a_second_fade_does_not_strand_the_first_effect(widget, monkeypatch):
    """A page faded twice must not keep a half-applied effect from the first."""
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    first = motion.fade_in(widget)
    assert first is not None
    first.stop()

    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    assert motion.fade_in(widget) is None
    assert widget.graphicsEffect() is None


def test_a_finished_fade_clears_its_own_effect(widget, monkeypatch, qtbot):
    """The effect a fade attaches must not outlive the animation that drove it.

    ``DeleteWhenStopped`` frees the ``QPropertyAnimation`` when it reaches its
    end, but that has never freed the ``QGraphicsOpacityEffect`` the animation
    was driving - a widget left carrying one renders through an offscreen
    buffer forever after, which is what let one page's pixels show through
    another inside a ``QStackedWidget``. This only exercises the enabled
    path, so the offscreen suite's default (disabled) path would never have
    caught it.
    """
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)

    animation = motion.fade_in(widget, duration=20)

    assert animation is not None
    with qtbot.waitSignal(animation.finished, timeout=2000):
        pass

    assert widget.graphicsEffect() is None


def test_an_enabled_fade_returns_a_running_animation(widget, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)

    animation = motion.fade_in(widget)

    assert animation is not None
    assert animation.duration() == motion.FAST
    animation.stop()


def test_a_window_fades_without_a_graphics_effect(window, monkeypatch):
    """A window must fade against the desktop, not against its own black pane.

    ``QGraphicsOpacityEffect`` on a top-level window composites the window
    over its own backing store, which is black: at opacity 0.5 a red window
    samples (127, 0, 0), and the capture dialog opened as a black rectangle
    for the length of the fade. ``setWindowOpacity`` is the only API that
    blends a window with what is actually behind it.
    """
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)

    animation = motion.fade_in(window, duration=20)

    assert animation is not None
    assert window.graphicsEffect() is None
    assert animation.propertyName() == b"windowOpacity"
    animation.stop()


def test_a_page_inside_a_window_still_fades_through_an_effect(widget, monkeypatch):
    """The child path is what the page fade uses, and it is correct as it is."""
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)

    animation = motion.fade_in(widget, duration=20)

    assert animation is not None
    assert widget.graphicsEffect() is not None
    animation.stop()


def test_a_finished_window_fade_leaves_the_window_fully_opaque(
    window, monkeypatch, qtbot
):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)

    animation = motion.fade_in(window, duration=20)

    assert animation is not None
    with qtbot.waitSignal(animation.finished, timeout=2000):
        pass

    assert window.windowOpacity() == pytest.approx(1.0)


def test_a_stopped_window_fade_does_not_leave_the_window_invisible(
    window, monkeypatch
):
    """Stopping a fade must not strand a window the operator cannot see."""
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    animation = motion.fade_in(window, duration=2000)

    assert animation is not None
    animation.stop()

    assert window.windowOpacity() == pytest.approx(1.0)


def test_a_stopped_fade_does_not_strand_its_effect(widget, monkeypatch):
    """Qt emits ``finished`` only at the end, never for a stopped animation.

    Cleanup hung on ``finished`` therefore never ran for a fade someone
    stopped mid-flight, leaving the widget at opacity 0.0 - invisible -
    for good. ``fade_in`` hands its animation back to callers, so stopping
    one is within what it offers.
    """
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    animation = motion.fade_in(widget, duration=2000)

    assert animation is not None
    animation.stop()

    assert widget.graphicsEffect() is None


def test_a_disabled_lift_leaves_the_widget_where_it_belongs(widget, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: False)
    widget.move(40, 60)

    assert motion.lift_in(widget) is None
    assert widget.pos().y() == 60


def test_an_enabled_lift_starts_below_and_ends_in_place(widget, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)
    widget.move(40, 60)

    animation = motion.lift_in(widget, distance=10)

    assert animation is not None
    assert animation.startValue().y() == 70
    assert animation.endValue().y() == 60
    animation.stop()
