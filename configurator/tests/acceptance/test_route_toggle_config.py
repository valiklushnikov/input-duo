"""The route-toggle configuration, checked without a board attached.

The roadmap asks for a thousand physical route toggles. Asking a person to
press a key a thousand times is not a test, it is a punishment, and a person
who has pressed a key nine hundred times is not observing anything. So the
toggling is done by the device: one macro performs as many route changes as a
macro is allowed steps for, and the operator presses its trigger a number of
times small enough to stay attentive for.

That arithmetic is the whole design, so it is pinned here. If the step budget,
the cycle count or the number of runs drifts, the operator script's "press it
this many times" becomes wrong in a way that reads as a hardware failure.

The counting evidence is pinned too: every character the macro types goes to
both computers, so each computer must end with exactly as many as the
arithmetic says. A shortfall is the only outward sign that a macro start was
refused or a command was dropped.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# Must be set before pytest-qt instantiates the QApplication.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

_TOOLS = Path(__file__).resolve().parents[3] / "tools"
if str(_TOOLS) not in sys.path:
    sys.path.insert(0, str(_TOOLS))

from step4_acceptance_config import (  # noqa: E402
    CONFIGURATIONS,
    MACRO_HOME,
    MACRO_MARK,
    MACRO_TOGGLE,
    S4_PACKAGE_SHA256,
    TOGGLE_CHARACTER,
    TOGGLE_CYCLES,
    TOGGLE_LINE_END,
    TOGGLE_RUNS,
    build_package,
    build_toggle_package,
    build_toggle_session,
    deploy,
    describe_toggle,
    toggle_characters_per_computer,
    toggle_lines,
    toggle_operator_script,
    toggle_route_changes,
    verify_round_trip,
)

from duo_input.device.emulator import U1Emulator  # noqa: E402
from duo_input.device.qt_transport import SynchronousTransportLink  # noqa: E402
from duo_input.domain.config_binary import decode_device_config  # noqa: E402
from duo_input.generated.protocol import (  # noqa: E402
    MACRO_STEPS_PER_MACRO,
    PROFILES,
    ActionKind,
    KeyboardRoute,
    MacroStepType,
    TargetMode,
    TriggerKind,
)
from duo_input.ui.models.binding_table import key_name  # noqa: E402


@pytest.fixture(scope="module")
def package() -> bytes:
    return build_toggle_package()


# --------------------------------------------------------------- the project


def test_the_toggle_project_the_gui_would_build_is_valid():
    assert build_toggle_session().issues == ()


def test_the_toggle_macro_spends_the_whole_step_budget():
    # Every step not spent on a route change is a toggle the operator has to
    # make up for with another press, so the budget is spent to the last step.
    macro = build_toggle_session().project.profiles[0].macros[MACRO_TOGGLE - 1]
    assert len(macro.steps) == MACRO_STEPS_PER_MACRO
    assert len(macro.steps) == TOGGLE_CYCLES * 2


def test_every_route_step_is_followed_by_exactly_one_character():
    # "Toggle the route and type one character" - the character is what paces
    # the output queue and what the operator counts afterwards.
    macro = build_toggle_session().project.profiles[0].macros[MACRO_TOGGLE - 1]
    for index in range(0, len(macro.steps), 2):
        route_step, text_step_ = macro.steps[index], macro.steps[index + 1]
        assert route_step.type == MacroStepType.SET_KEYBOARD_ROUTE
        assert text_step_.type == MacroStepType.TEXT
        assert text_step_.source_text.startswith(TOGGLE_CHARACTER)


def test_one_run_writes_exactly_one_line_and_ends_it():
    # The operator counts lines, not characters. That only works if a run
    # produces one line and closes it: a run that left its line open would
    # merge with the next one, and two presses would look like one.
    macro = build_toggle_session().project.profiles[0].macros[MACRO_TOGGLE - 1]
    typed = "".join(
        step.source_text for step in macro.steps if step.type == MacroStepType.TEXT
    )
    assert typed == TOGGLE_CHARACTER * TOGGLE_CYCLES + TOGGLE_LINE_END
    assert typed.count("\n") == 1
    assert typed.endswith("\n")
    # Every line is the same width, so a short one is visible at a glance.
    assert len(typed.splitlines()[0]) == TOGGLE_CYCLES


def test_consecutive_route_steps_alternate_between_the_two_computers():
    # A route "change" that set the route it was already on would change
    # nothing and release nothing, and the count would be a fiction.
    macro = build_toggle_session().project.profiles[0].macros[MACRO_TOGGLE - 1]
    routes = [step.payload[0] for step in macro.steps if step.type == MacroStepType.SET_KEYBOARD_ROUTE]
    assert len(routes) == TOGGLE_CYCLES
    for previous, current in zip(routes, routes[1:]):
        assert previous != current
    assert KeyboardRoute(routes[0]) == KeyboardRoute.PC2
    # Ends on PC1, so the device is left reachable from the near computer and
    # the next run starts from the same place this one did.
    assert KeyboardRoute(routes[-1]) == KeyboardRoute.PC1


def test_the_toggle_macro_types_to_both_computers():
    # A macro's route is fixed when it is enqueued: its SET_KEYBOARD_ROUTE
    # steps move the operator's keys, not the rest of its own text. Targeting
    # BOTH is what makes the character count the same on each computer and so
    # countable at all.
    macro = build_toggle_session().project.profiles[0].macros[MACRO_TOGGLE - 1]
    assert TargetMode(macro.target) == TargetMode.BOTH


def test_the_delivered_toggle_count_is_the_one_the_ledger_records():
    # The roadmap asks for 1000. This procedure performs 320, and that
    # shortfall is a recorded ruling rather than a silent substitution: ten
    # lines fit on one screen and thirty-two do not, and an operator who
    # miscounts is a worse instrument than a smaller number honestly
    # reported. If either constant moves, the ledger entry is wrong and this
    # test is what says so.
    assert TOGGLE_RUNS == 10
    assert toggle_route_changes() == TOGGLE_CYCLES * TOGGLE_RUNS == 320
    assert toggle_lines() == TOGGLE_RUNS
    assert toggle_characters_per_computer() == 320


def test_describe_admits_the_procedure_is_short_of_the_thousand(package: bytes):
    text = describe_toggle(package)
    assert "1000" in text
    assert str(toggle_route_changes()) in text


def test_every_profile_carries_the_toggle_macro_on_the_keys_the_script_names(package: bytes):
    config = decode_device_config(package)
    assert len(config.profiles) == PROFILES
    for profile in config.profiles:
        triggers = {
            key_name(binding.trigger.code)
            for binding in profile.bindings
            if binding.trigger.kind == TriggerKind.KEYBOARD_USAGE
            and binding.action.kind == ActionKind.RUN_MACRO
            and binding.action.argument == MACRO_TOGGLE
        }
        assert triggers == {"Insert", "Up"}


def test_every_key_the_script_names_is_an_arrow_key(package: bytes):
    # The operator's keyboard is a compact layout: no Insert, no Home, no
    # PageUp, and an F-row only behind an Fn layer. Everything the script
    # asks for has to be reachable from the four arrows the board has.
    config = decode_device_config(package)
    arrows = {"Up", "Down", "Left", "Right"}
    for profile in config.profiles:
        bound = {
            key_name(binding.trigger.code)
            for binding in profile.bindings
            if binding.trigger.kind == TriggerKind.KEYBOARD_USAGE
        }
        assert arrows <= bound


def test_the_other_computer_is_reachable_from_an_arrow_key(package: bytes):
    # The run ends on PC1 and the stranded-modifier check has to be made on
    # both computers, so the operator needs a way to move the keyboard that
    # is not F10.
    config = decode_device_config(package)
    for profile in config.profiles:
        down = next(
            binding
            for binding in profile.bindings
            if binding.trigger.kind == TriggerKind.KEYBOARD_USAGE
            and key_name(binding.trigger.code) == "Down"
        )
        assert down.action.kind == ActionKind.SET_KEYBOARD_ROUTE
        assert KeyboardRoute(down.action.argument) == KeyboardRoute.PC2


def test_every_profile_can_mark_a_run_and_come_home(package: bytes):
    config = decode_device_config(package)
    for profile in config.profiles:
        macro_ids = {macro.id for macro in profile.macros}
        assert {MACRO_TOGGLE, MACRO_MARK, MACRO_HOME} <= macro_ids
        run = {
            binding.action.argument
            for binding in profile.bindings
            if binding.action.kind == ActionKind.RUN_MACRO
        }
        assert {MACRO_TOGGLE, MACRO_MARK, MACRO_HOME} <= run


def test_coming_home_puts_the_keyboard_back_on_the_near_computer(package: bytes):
    config = decode_device_config(package)
    home = next(macro for macro in config.profiles[0].macros if macro.id == MACRO_HOME)
    first = home.steps[0]
    assert MacroStepType(first.type) == MacroStepType.SET_KEYBOARD_ROUTE
    assert KeyboardRoute(first.payload[0]) == KeyboardRoute.PC1


# ------------------------------------------------- what a write would produce


def test_the_round_trip_compares_every_field_and_finds_no_mismatch(package: bytes):
    checks = verify_round_trip(package, expected=build_toggle_session().project)
    assert checks
    assert [check.field for check in checks if not check.matches] == []


def test_describe_states_the_arithmetic_the_operator_will_follow(package: bytes):
    text = describe_toggle(package)
    assert str(TOGGLE_CYCLES) in text
    assert str(TOGGLE_RUNS) in text
    assert str(toggle_route_changes()) in text


def test_the_operator_script_asks_for_the_number_of_presses_the_plan_needs():
    # The script is rendered from the same constants as the arithmetic, so it
    # cannot come to disagree with the configuration it describes.
    script = toggle_operator_script()
    assert f"Press Up {TOGGLE_RUNS} times" in script
    assert str(toggle_characters_per_computer()) in script
    # A pass and a failure must be distinguishable, and a short count that is
    # the operator's own doing must not be reported as a firmware failure.
    assert "dropped_commands unchanged" in script
    assert "NOT a firmware failure" in script


def test_the_operator_script_never_names_a_key_the_keyboard_does_not_have():
    # This is the defect being fixed: the script asked for Insert and Home on
    # a keyboard that has neither, so it could not be run at all.
    script = toggle_operator_script()
    for absent in ("Insert", "Home", "PageUp", "Delete", "End"):
        assert absent not in script


def test_the_operator_script_holds_the_modifier_instead_of_re_gripping_it():
    # Re-gripping Left Shift between presses was the fiddliest part of the
    # procedure and it bought little: S4c already accepted the stranded
    # modifier on hardware, across an unplug and a reconnect. Here the
    # modifier only has to prove it is not stranded at the end.
    script = toggle_operator_script()
    assert "re-grip" not in script.lower()
    assert "keep holding" in script.lower()


def test_the_operator_script_counts_lines_rather_than_characters():
    # Counting 1024 characters by eye is a chore, not a check. One press is
    # one full line, so the operator counts ten of them and reads the total
    # off the editor's own status bar.
    script = toggle_operator_script()
    assert f"{toggle_lines()} lines" in script
    assert f"Ln {toggle_lines() + 1}" in script


def test_the_operator_script_fits_on_one_page():
    # A procedure that has to be scrolled back through is one that gets done
    # out of order.
    assert len(toggle_operator_script().splitlines()) <= 40


def test_the_two_configurations_are_not_the_same_package():
    assert build_toggle_package() != build_package()


def test_adding_a_second_configuration_did_not_move_the_first():
    # The Step 4 acceptance ran against schema-minor 0. Normalize the current
    # minor and its CRC to prove no other byte moved from that hardware run.
    import hashlib
    import zlib

    package = bytearray(build_package())
    # Literal, not the generated constant: config_binary.py packs byte 5 from
    # that same constant, so comparing against it here would only prove the
    # packer agrees with itself. Pin the number instead.
    assert package[5] == 2
    package[5] = 0
    package[12:16] = b"\0" * 4
    package[12:16] = zlib.crc32(package).to_bytes(4, "little")
    assert hashlib.sha256(package).hexdigest() == S4_PACKAGE_SHA256


def test_the_configuration_registry_offers_both_acceptance_rigs_by_name():
    # The registry has grown a third entry since - the operator's own
    # configuration - which is checked where it is defined. What matters here
    # is that both rigs are still reachable and still build their own bytes.
    assert {"step4", "toggle"} <= set(CONFIGURATIONS)
    assert CONFIGURATIONS["toggle"].build_package() == build_toggle_package()
    assert CONFIGURATIONS["step4"].build_package() == build_package()


# ------------------------------------------------------- the deployment path


def build_previous_configuration() -> bytes:
    """Something already on the device, so the backup has something to save."""
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import ProjectSession, RenameProfile

    return compile_project_to_binary(ProjectSession.new().apply(RenameProfile(1, "Было")).project)


@pytest.fixture
def emulator() -> U1Emulator:
    device = U1Emulator()
    device.install_active(build_previous_configuration())
    return device


def test_deploy_backs_up_writes_and_reads_back(qapp, emulator: U1Emulator, package: bytes):
    result = deploy(SynchronousTransportLink(emulator), package)

    assert result.previous_package == build_previous_configuration()
    assert result.written_package == package
    assert result.read_back_matches
    checks = verify_round_trip(result.read_back_package, expected=build_toggle_session().project)
    assert [check.field for check in checks if not check.matches] == []
