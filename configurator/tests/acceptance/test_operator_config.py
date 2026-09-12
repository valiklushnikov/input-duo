"""The operator's own configuration, checked without a board attached.

The other two configurations in ``tools/step4_acceptance_config.py`` are
acceptance rigs. They bind F1-F8 to profile switching and every arrow key to a
macro, which is correct for an acceptance - the operator is asked to press
those keys and observe what comes out - and useless for a working day, because
a keyboard whose arrows do not move the cursor is not a keyboard.

This is the third: the configuration the device is meant to live in. What it
must contain is almost entirely a statement about what it must *not* contain,
so that is what most of these tests are. The route keys are the same five the
route acceptance already passed on, because an operator who has learned one
keyboard should not have to learn another.
"""

from __future__ import annotations

import hashlib
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
    OPERATOR_KEYBOARD_ROUTE,
    OPERATOR_KEYS,
    OPERATOR_MOUSE_ROUTE,
    OPERATOR_PROFILE_ID,
    OPERATOR_PROFILE_NAME,
    S4_PACKAGE_SHA256,
    build_operator_package,
    build_operator_session,
    build_package,
    build_toggle_package,
    deploy,
    describe_operator,
    verify_round_trip,
)

from duo_input.device.emulator import U1Emulator  # noqa: E402
from duo_input.device.qt_transport import SynchronousTransportLink  # noqa: E402
from duo_input.domain.config_binary import decode_device_config  # noqa: E402
from duo_input.generated.protocol import (  # noqa: E402
    PROFILES,
    SCHEMA_VERSION_MINOR,
    ActionKind,
    KeyboardRoute,
    MouseRoute,
    TriggerKind,
)
from duo_input.ui.models.binding_table import key_name  # noqa: E402
from duo_input.ui.models.project_session import ProjectSession  # noqa: E402


@pytest.fixture(scope="module")
def package() -> bytes:
    return build_operator_package()


def _operator_profile(package: bytes):
    config = decode_device_config(package)
    return next(profile for profile in config.profiles if profile.id == OPERATOR_PROFILE_ID)


def _keyboard_usages(profile) -> set[int]:
    return {
        binding.trigger.code
        for binding in profile.bindings
        if binding.trigger.kind == TriggerKind.KEYBOARD_USAGE
    }


# ------------------------------------------------------------- the project


def test_the_operator_project_the_gui_would_build_is_valid():
    session = build_operator_session()

    assert session.issues == ()
    assert session.compiled_hash


def test_the_device_boots_into_the_one_profile_the_operator_uses(package: bytes):
    # No binding selects a profile, so whatever the device boots into is the
    # only profile the operator will ever be in.
    assert decode_device_config(package).active_profile_id == OPERATOR_PROFILE_ID


def test_only_one_profile_carries_anything(package: bytes):
    # Eight profiles exist to prove profiles survive a power cycle. A person
    # needs one that works, and seven empty slots beside it.
    config = decode_device_config(package)
    assert len(config.profiles) == PROFILES

    carrying = [
        profile.id for profile in config.profiles if profile.bindings or profile.macros
    ]
    assert carrying == [OPERATOR_PROFILE_ID]


def test_the_one_profile_is_named_for_what_it_is(package: bytes):
    assert _operator_profile(package).name == OPERATOR_PROFILE_NAME


def test_the_seven_spare_profiles_are_exactly_the_pristine_ones():
    # Not renamed, not decorated, not half-filled: a slot nobody can reach
    # should not look like a slot somebody configured.
    pristine = {profile.id: profile for profile in ProjectSession.new().project.profiles}

    for profile in build_operator_session().project.profiles:
        if profile.id == OPERATOR_PROFILE_ID:
            continue
        assert profile == pristine[profile.id]


# ------------------------------------------------------ what is bound, and only that


def test_route_switching_keeps_the_keys_the_route_acceptance_already_passed_on(
    package: bytes,
):
    actions = {
        (binding.trigger.kind, binding.trigger.code): (
            binding.action.kind,
            binding.action.argument,
        )
        for binding in _operator_profile(package).bindings
    }

    assert actions[(TriggerKind.KEYBOARD_USAGE, 0x42)] == (
        ActionKind.SET_KEYBOARD_ROUTE,
        KeyboardRoute.PC1,
    )
    assert actions[(TriggerKind.KEYBOARD_USAGE, 0x43)] == (
        ActionKind.SET_KEYBOARD_ROUTE,
        KeyboardRoute.PC2,
    )
    assert actions[(TriggerKind.KEYBOARD_USAGE, 0x44)] == (
        ActionKind.SET_KEYBOARD_ROUTE,
        KeyboardRoute.BOTH,
    )
    assert actions[(TriggerKind.KEYBOARD_USAGE, 0x45)] == (
        ActionKind.TOGGLE_MOUSE_ROUTE,
        0,
    )
    assert actions[(TriggerKind.MOUSE_BUTTON, 4)] == (ActionKind.TOGGLE_MOUSE_ROUTE, 0)


def test_those_five_keys_are_the_only_keys_bound_anywhere(package: bytes):
    # The whole point of this configuration. Anything else in this set is a
    # key that has stopped doing what is printed on it.
    config = decode_device_config(package)
    bound = {
        (binding.trigger.kind, binding.trigger.code)
        for profile in config.profiles
        for binding in profile.bindings
    }

    assert bound == {
        (TriggerKind.KEYBOARD_USAGE, 0x42),
        (TriggerKind.KEYBOARD_USAGE, 0x43),
        (TriggerKind.KEYBOARD_USAGE, 0x44),
        (TriggerKind.KEYBOARD_USAGE, 0x45),
        (TriggerKind.MOUSE_BUTTON, 4),
    }


def test_no_function_key_a_person_works_with_is_swallowed(package: bytes):
    # The acceptance rigs bind F1-F8 to the eight profiles. F2 renames a file
    # and F5 refreshes a browser, so on this configuration they do that.
    usages = _keyboard_usages(_operator_profile(package))

    for usage in range(0x3A, 0x42):  # F1..F8
        assert usage not in usages, f"{key_name(usage)} is bound"


def test_the_arrow_keys_move_the_cursor(package: bytes):
    # Both acceptance rigs bind all four arrows, because the bench keyboard has
    # no navigation cluster to bind instead. Here they must be left alone.
    usages = _keyboard_usages(_operator_profile(package))

    for usage in (0x4F, 0x50, 0x51, 0x52):  # Right, Left, Down, Up
        assert usage not in usages, f"{key_name(usage)} is bound"


def test_nothing_is_bound_to_a_key_this_keyboard_does_not_have(package: bytes):
    # The bench keyboard is a compact layout: no Insert, Home, PageUp, Delete
    # or End. A route key the operator cannot press is not a route key.
    usages = _keyboard_usages(_operator_profile(package))

    for usage in (0x49, 0x4A, 0x4B, 0x4C, 0x4D):
        assert usage not in usages, f"{key_name(usage)} is bound"


def test_no_binding_switches_profiles(package: bytes):
    # One profile. A key that moved the operator to an empty slot would take
    # the route keys away with it.
    for profile in decode_device_config(package).profiles:
        assert not [
            binding
            for binding in profile.bindings
            if binding.action.kind == ActionKind.SET_PROFILE
        ]


def test_the_configuration_carries_no_macros_at_all(package: bytes):
    # A macro needs a key to start it, and every key that is not a route key
    # belongs to the person typing.
    for profile in decode_device_config(package).profiles:
        assert profile.macros == ()
        assert not [
            binding
            for binding in profile.bindings
            if binding.action.kind == ActionKind.RUN_MACRO
        ]


# ---------------------------------------------------------- where it starts


def test_the_profile_starts_with_the_keyboard_and_the_mouse_together(package: bytes):
    # The routes are stored per profile and the firmware applies them when the
    # profile becomes active, so this is where the device points after a power
    # cycle. Both on PC1: the first keystroke and the first mouse movement
    # after a boot go to the same computer, and F9/F12 move from a known place.
    profile = _operator_profile(package)

    assert profile.keyboard_route == OPERATOR_KEYBOARD_ROUTE == KeyboardRoute.PC1
    assert profile.mouse_route == OPERATOR_MOUSE_ROUTE == MouseRoute.PC1


def test_the_starting_routes_are_chosen_rather_than_inherited(monkeypatch):
    # Both are PC1, which is also what a pristine profile happens to be. That
    # coincidence is why this test exists: the project has to *say* PC1, so
    # that a change to the pristine default cannot move the device underneath
    # the operator without anybody choosing it.
    import step4_acceptance_config as tool

    monkeypatch.setattr(tool, "OPERATOR_KEYBOARD_ROUTE", KeyboardRoute.PC2)
    monkeypatch.setattr(tool, "OPERATOR_MOUSE_ROUTE", MouseRoute.PC2)
    profile = tool.build_operator_session().project.profiles[OPERATOR_PROFILE_ID - 1]

    assert profile.keyboard_route == KeyboardRoute.PC2
    assert profile.mouse_route == MouseRoute.PC2


# ------------------------------------------------------------- what it says


def test_describe_lists_every_bound_key_and_what_it_does(package: bytes):
    text = describe_operator(package)

    for key in OPERATOR_KEYS:
        assert key.label in text
        assert key.meaning in text


def test_describe_says_that_everything_else_is_left_alone(package: bytes):
    # The reader has to be able to tell this configuration from an acceptance
    # rig without decoding it themselves.
    text = describe_operator(package)

    assert "F1-F8" in text
    assert "arrow" in text.lower()


# ------------------------------------------------------- round-trip checking


def test_the_round_trip_compares_every_field_and_finds_no_mismatch(package: bytes):
    checks = verify_round_trip(package, expected=build_operator_session().project)

    assert checks
    assert [check.field for check in checks if not check.matches] == []
    assert "active_profile_id" in {check.field for check in checks}


def test_the_round_trip_reports_a_package_that_is_not_the_one_that_was_built():
    # "Every field matches" means nothing unless a difference is found when
    # there is one.
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import RenameProfile

    other = build_operator_session().apply(RenameProfile(OPERATOR_PROFILE_ID, "Другой"))

    mismatched = [
        check.field
        for check in verify_round_trip(
            compile_project_to_binary(other.project),
            expected=build_operator_session().project,
        )
        if not check.matches
    ]

    assert mismatched == [f"profile[{OPERATOR_PROFILE_ID}].name"]


# ------------------------------------------------------------ the registry


def test_the_registry_offers_all_three_configurations_by_name():
    assert set(CONFIGURATIONS) == {"step4", "toggle", "operator"}
    assert CONFIGURATIONS["operator"].build_package() == build_operator_package()


def test_the_operator_configuration_is_neither_acceptance_rig():
    assert build_operator_package() != build_package()
    assert build_operator_package() != build_toggle_package()


def test_adding_a_third_configuration_did_not_move_the_first():
    # The Step 4 acceptance ran against schema-minor 0. Normalize the current
    # minor and its CRC to prove no other byte moved from that hardware run.
    import zlib

    package = bytearray(build_package())
    assert package[5] == SCHEMA_VERSION_MINOR
    package[5] = 0
    package[12:16] = b"\0" * 4
    package[12:16] = zlib.crc32(package).to_bytes(4, "little")
    assert hashlib.sha256(package).hexdigest() == S4_PACKAGE_SHA256


# ------------------------------------------------------- the deployment path


def build_previous_configuration() -> bytes:
    """Something already on the device, so the backup has something to save."""
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import RenameProfile

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
    checks = verify_round_trip(
        result.read_back_package, expected=build_operator_session().project
    )
    assert [check.field for check in checks if not check.matches] == []


def test_the_written_configuration_is_still_there_after_a_power_cycle(
    qapp, emulator: U1Emulator, package: bytes
):
    deploy(SynchronousTransportLink(emulator), package)
    emulator.simulate_power_cycle()

    assert emulator.active_hash == hashlib.sha256(package).digest()
    assert emulator.active_profile == OPERATOR_PROFILE_ID
