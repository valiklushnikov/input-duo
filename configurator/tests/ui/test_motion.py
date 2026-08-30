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
def widget(qtbot) -> QWidget:
    item = QWidget()
    qtbot.addWidget(item)
    return item


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


def test_an_enabled_fade_returns_a_running_animation(widget, monkeypatch):
    monkeypatch.setattr(motion, "animations_enabled", lambda: True)

    animation = motion.fade_in(widget)

    assert animation is not None
    assert animation.duration() == motion.FAST
    animation.stop()


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
