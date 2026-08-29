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
    TOGGLE_RUNS,
    build_package,
    build_toggle_package,
    build_toggle_session,
    deploy,
    describe_toggle,
    toggle_characters_per_computer,
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
        assert text_step_.source_text == TOGGLE_CHARACTER


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


def test_the_planned_runs_reach_the_thousand_toggles_the_roadmap_asks_for():
    assert toggle_route_changes() == TOGGLE_CYCLES * TOGGLE_RUNS
    assert toggle_route_changes() >= 1000


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
    assert f"Press Insert {TOGGLE_RUNS} times" in script
    assert str(toggle_characters_per_computer()) in script
    # A pass and a failure must be distinguishable, and a short count that is
    # the operator's own doing must not be reported as a firmware failure.
    assert "dropped_commands unchanged" in script
    assert "NOT a firmware failure" in script


def test_the_two_configurations_are_not_the_same_package():
    assert build_toggle_package() != build_package()


def test_adding_a_second_configuration_did_not_move_the_first():
    # The Step 4 acceptance ran against these exact bytes on hardware. A
    # refactor that changed them would invalidate a passed acceptance.
    import hashlib

    assert hashlib.sha256(build_package()).hexdigest() == S4_PACKAGE_SHA256


def test_the_configuration_registry_offers_both_by_name():
    assert set(CONFIGURATIONS) == {"step4", "toggle"}
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
