"""The macro step list: building steps, reading them back and reordering them."""

from __future__ import annotations

import pytest

from duo_input.domain.models import Macro, MacroStep
from duo_input.generated.protocol import (
    MACRO_STEPS_PER_MACRO,
    MAX_DELAY_MS,
    KeyboardRoute,
    MacroStepType,
    MouseRouteCommand,
    TargetMode,
    TextLayout,
)
from duo_input.ui.models.macro_steps import (
    STEP_BUILDERS,
    MacroStepListModel,
    consumer_tap_step,
    delay_step,
    key_down_step,
    key_tap_step,
    key_up_step,
    set_keyboard_route_step,
    set_mouse_route_step,
    set_profile_step,
    step_label,
    text_step,
)


def _macro(*steps: MacroStep) -> Macro:
    return Macro(id=1, name="M", target=TargetMode.INHERIT, steps=steps)


# ---------------------------------------------------------------- step builders


def test_a_key_tap_carries_its_modifier_and_usage():
    step = key_tap_step(0x02, 0x04)

    assert step.type is MacroStepType.KEY_TAP
    assert step.payload == bytes((0x02, 0x04))


def test_a_key_tap_needs_a_usage():
    with pytest.raises(ValueError):
        key_tap_step(0x02, 0)


def test_key_down_and_key_up_carry_only_a_usage():
    assert key_down_step(0x04).payload == b"\x04"
    assert key_up_step(0x04).type is MacroStepType.KEY_UP


def test_a_consumer_tap_carries_a_little_endian_usage():
    step = consumer_tap_step(0x00E9)

    assert step.type is MacroStepType.CONSUMER_TAP
    assert step.payload == b"\xe9\x00"


def test_a_fixed_delay_repeats_the_same_bound_twice():
    step = delay_step(250, 250)

    assert step.type is MacroStepType.DELAY
    assert step.payload == b"\xfa\x00\xfa\x00"


def test_a_random_delay_keeps_both_bounds():
    assert delay_step(50, 400).payload == b"\x32\x00\x90\x01"


def test_a_delay_may_not_run_backwards():
    with pytest.raises(ValueError):
        delay_step(400, 50)


def test_a_delay_may_not_exceed_the_protocol_limit():
    with pytest.raises(ValueError):
        delay_step(0, MAX_DELAY_MS + 1)


def test_a_text_step_keeps_the_source_text_it_was_written_in():
    step = text_step("/target КУРКУМА")

    assert step.type is MacroStepType.TEXT
    assert step.source_text == "/target КУРКУМА"
    # The payload is compiled per profile layout at write time, not here.
    assert step.payload == b""


def test_a_text_step_refuses_more_characters_than_the_protocol_allows():
    with pytest.raises(ValueError):
        text_step("a" * 1025)


def test_route_and_profile_steps_carry_one_byte():
    assert set_keyboard_route_step(KeyboardRoute.BOTH).payload == bytes((KeyboardRoute.BOTH,))
    assert set_mouse_route_step(MouseRouteCommand.TOGGLE).payload == (
        bytes((MouseRouteCommand.TOGGLE,))
    )
    assert set_profile_step(8).payload == b"\x08"


def test_a_profile_step_stays_inside_the_eight_slots():
    with pytest.raises(ValueError):
        set_profile_step(9)


def test_every_step_type_can_be_built_from_the_editor():
    assert set(STEP_BUILDERS) == set(MacroStepType)


# ---------------------------------------------------------------------- labels


def test_a_step_reads_as_its_protocol_type_and_payload():
    assert step_label(key_tap_step(0, 0x04)) == "KEY_TAP A"
    assert step_label(key_tap_step(0x02, 0x04)) == "KEY_TAP Shift+A"
    assert step_label(delay_step(50, 400)) == "DELAY 50-400 ms"
    assert step_label(delay_step(250, 250)) == "DELAY 250 ms"
    assert step_label(set_profile_step(3)) == "SET_PROFILE 3"


def test_a_text_step_reads_as_its_own_text():
    assert step_label(text_step("Привет")) == "TEXT Привет"


def test_a_long_text_step_is_shortened_for_the_list():
    label = step_label(text_step("x" * 200))

    assert label.startswith("TEXT ")
    assert len(label) < 100


# ----------------------------------------------------------------- list model


def test_an_empty_model_has_no_rows(qtbot):
    model = MacroStepListModel()

    assert model.rowCount() == 0
    assert model.steps() == ()


def test_the_model_lists_the_steps_of_one_macro(qtbot):
    model = MacroStepListModel()

    model.set_macro(_macro(key_tap_step(0, 0x04), delay_step(10, 10)))

    assert model.rowCount() == 2
    assert model.index(0, 0).data() == "KEY_TAP A"
    assert model.index(1, 0).data() == "DELAY 10 ms"


def test_inserting_a_step_puts_it_where_it_was_asked_for(qtbot):
    model = MacroStepListModel()
    model.set_macro(_macro(key_tap_step(0, 0x04), key_tap_step(0, 0x05)))

    model.insert_step(1, delay_step(5, 5))

    assert [step.type for step in model.steps()] == [
        MacroStepType.KEY_TAP,
        MacroStepType.DELAY,
        MacroStepType.KEY_TAP,
    ]


def test_appending_past_the_step_limit_is_refused(qtbot):
    model = MacroStepListModel()
    model.set_macro(_macro(*[delay_step(1, 1)] * MACRO_STEPS_PER_MACRO))

    with pytest.raises(ValueError):
        model.insert_step(MACRO_STEPS_PER_MACRO, delay_step(1, 1))


def test_removing_a_step_drops_exactly_that_row(qtbot):
    model = MacroStepListModel()
    model.set_macro(_macro(key_tap_step(0, 0x04), delay_step(5, 5), key_tap_step(0, 0x06)))

    model.remove_step(1)

    assert [step.type for step in model.steps()] == [
        MacroStepType.KEY_TAP,
        MacroStepType.KEY_TAP,
    ]


def test_replacing_a_step_keeps_its_place_and_its_identity(qtbot):
    model = MacroStepListModel()
    model.set_macro(_macro(key_tap_step(0, 0x04), delay_step(5, 5)))
    key = model.key_of(1)

    model.set_step(1, delay_step(9, 9))

    assert model.step_at(1).payload == b"\x09\x00\x09\x00"
    assert model.key_of(1) == key


def test_moving_a_step_carries_its_identity_with_it(qtbot):
    model = MacroStepListModel()
    model.set_macro(_macro(key_tap_step(0, 0x04), delay_step(5, 5), key_tap_step(0, 0x06)))
    key = model.key_of(0)

    assert model.move_step(0, 2) is True

    assert [step.payload for step in model.steps()] == [
        delay_step(5, 5).payload,
        key_tap_step(0, 0x06).payload,
        key_tap_step(0, 0x04).payload,
    ]
    assert model.key_of(2) == key


def test_every_row_keeps_its_own_identity(qtbot):
    model = MacroStepListModel()
    model.set_macro(_macro(delay_step(1, 1), delay_step(1, 1), delay_step(1, 1)))

    keys = [model.key_of(row) for row in range(3)]

    assert len(set(keys)) == 3


def test_moving_a_step_onto_itself_changes_nothing(qtbot):
    model = MacroStepListModel()
    model.set_macro(_macro(delay_step(1, 1), delay_step(2, 2)))

    assert model.move_step(1, 1) is False
    assert model.step_at(1).payload == b"\x02\x00\x02\x00"


def test_a_move_outside_the_list_is_refused(qtbot):
    model = MacroStepListModel()
    model.set_macro(_macro(delay_step(1, 1)))

    with pytest.raises(ValueError):
        model.move_step(0, 5)


def test_every_change_announces_the_new_steps(qtbot):
    model = MacroStepListModel()
    model.set_macro(_macro(delay_step(1, 1)))
    seen: list[tuple[MacroStep, ...]] = []
    model.steps_changed.connect(seen.append)

    model.insert_step(1, delay_step(2, 2))
    model.remove_step(0)

    assert len(seen) == 2
    assert seen[-1] == (model.step_at(0),)


def test_the_model_never_edits_the_macro_it_was_given(qtbot):
    macro = _macro(delay_step(1, 1))
    model = MacroStepListModel()
    model.set_macro(macro)

    model.insert_step(1, delay_step(2, 2))

    assert macro.steps == (macro.steps[0],)


def test_a_text_step_compiles_against_the_profile_layout(qtbot):
    from duo_input.domain.text_compiler import compile_text_payload

    step = text_step("привет")

    assert compile_text_payload(step.source_text, TextLayout.RU)
