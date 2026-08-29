"""The Step 4 acceptance configuration, checked without a board attached.

Everything the operator will be asked to observe on hardware is decided here:
which profile the device boots into, what each profile types when it is asked
to name itself, how many characters the ``/target KYPKYMA`` macro produces, and
which key runs it. If any of those drift, the operator script becomes wrong in
a way that looks like a hardware failure, so each is pinned.

The deployment path is exercised against the protocol emulator, which speaks
the same CDC contract as the firmware. That is what makes the tool something
that has been run rather than something that merely exists.
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
sys.path.insert(0, str(_TOOLS))

from step4_acceptance_config import (  # noqa: E402
    BOOT_PROFILE_ID,
    BURST_REPEATS,
    BURST_TEXT,
    MACRO_BURST,
    MACRO_IDENT,
    MACRO_REPEAT,
    MACRO_TARGET,
    REPEAT_TEXT,
    TARGET_TEXT,
    DeployError,
    backup_document,
    build_package,
    build_session,
    deploy,
    describe,
    ident_text,
    macro_plans,
    package_from_backup,
    verify_round_trip,
)

from duo_input.device.emulator import U1Emulator  # noqa: E402
from duo_input.device.qt_transport import SynchronousTransportLink  # noqa: E402
from duo_input.domain.config_binary import decode_device_config  # noqa: E402
from duo_input.generated.protocol import (  # noqa: E402
    PROFILES,
    ActionKind,
    KeyboardRoute,
    MacroStepType,
    MouseRoute,
    TriggerKind,
)
from duo_input.ui.models.binding_table import key_name  # noqa: E402


@pytest.fixture(scope="module")
def package() -> bytes:
    return build_package()


# ------------------------------------------------- what the operator will see


def test_the_project_the_gui_would_build_is_valid():
    session = build_session()

    assert session.issues == ()
    assert session.compiled_hash


def test_the_device_boots_into_a_profile_that_is_not_the_default(package: bytes):
    # A device that failed to load its configuration falls back elsewhere, so
    # booting into 3 is evidence the eight profiles came out of flash.
    assert BOOT_PROFILE_ID != 1
    assert decode_device_config(package).active_profile_id == BOOT_PROFILE_ID


def test_every_profile_types_its_own_number(package: bytes):
    config = decode_device_config(package)

    assert len(config.profiles) == PROFILES
    typed = {}
    for profile in config.profiles:
        ident = next(macro for macro in profile.macros if macro.id == MACRO_IDENT)
        typed[profile.id] = ident_text(profile.id)
        assert len(ident.steps) == 1
        assert ident.steps[0].type == MacroStepType.TEXT
        # Two modifier/usage bytes per character.
        assert len(ident.steps[0].payload) == 2 * len(typed[profile.id])
    assert len(set(typed.values())) == PROFILES
    assert typed[5] == "PROFILE-5 "


def test_the_target_macro_carries_the_text_the_brief_names(package: bytes):
    assert TARGET_TEXT == "/target KYPKYMA"
    assert len(TARGET_TEXT) == 15

    for profile in decode_device_config(package).profiles:
        target = next(macro for macro in profile.macros if macro.id == MACRO_TARGET)
        assert len(target.steps) == 1
        assert len(target.steps[0].payload) == 2 * len(TARGET_TEXT)


def test_the_repeat_macro_contains_adjacent_duplicates():
    # The defect S4b tests collapses a press into its release. Two identical
    # characters in a row are where that is visible as one character, not none.
    assert len(REPEAT_TEXT) == 10
    assert len(set(REPEAT_TEXT)) == 1


def test_the_burst_macro_types_one_target_line_per_repeat(package: bytes):
    assert BURST_TEXT.count("\n") == BURST_REPEATS
    assert BURST_TEXT.splitlines() == [TARGET_TEXT] * BURST_REPEATS

    for profile in decode_device_config(package).profiles:
        burst = next(macro for macro in profile.macros if macro.id == MACRO_BURST)
        assert len(burst.steps[0].payload) == 2 * len(BURST_TEXT)


def test_every_profile_can_reach_every_other_profile(package: bytes):
    for profile in decode_device_config(package).profiles:
        reachable = {
            binding.action.argument
            for binding in profile.bindings
            if binding.action.kind == ActionKind.SET_PROFILE
        }
        assert reachable == set(range(1, PROFILES + 1))


def test_every_profile_runs_every_macro_from_the_keys_the_script_names(package: bytes):
    expected = {
        (name, plan.macro_id) for plan in macro_plans(1) for name in plan.trigger_names
    }

    for profile in decode_device_config(package).profiles:
        bound = {
            (key_name(binding.trigger.code), binding.action.argument)
            for binding in profile.bindings
            if binding.action.kind == ActionKind.RUN_MACRO
        }
        assert bound == expected


def test_route_switching_keeps_the_keys_the_route_acceptance_already_passed_on(package: bytes):
    for profile in decode_device_config(package).profiles:
        actions = {
            (binding.trigger.kind, binding.trigger.code): (
                binding.action.kind,
                binding.action.argument,
            )
            for binding in profile.bindings
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


def test_no_profile_starts_on_the_far_computer(package: bytes):
    # Profile switching must not move the routes underneath the operator.
    for profile in decode_device_config(package).profiles:
        assert profile.keyboard_route == KeyboardRoute.PC1
        assert profile.mouse_route == MouseRoute.PC1


# ------------------------------------------------------- round-trip checking


def test_the_round_trip_compares_every_field_and_finds_no_mismatch(package: bytes):
    checks = verify_round_trip(package)

    assert checks
    assert [check.field for check in checks if not check.matches] == []
    fields = {check.field for check in checks}
    assert "active_profile_id" in fields
    assert "profile[5].macro[0].step[0].payload" in fields


def test_the_round_trip_reports_a_package_that_is_not_the_one_that_was_built():
    # A valid package that differs in one field: the comparison has to say so,
    # or "every field matches" would mean nothing.
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import SetActiveProfile

    other = build_session().apply(SetActiveProfile(1))
    assert other.project.active_profile_id != BOOT_PROFILE_ID

    mismatched = [
        check.field
        for check in verify_round_trip(compile_project_to_binary(other.project))
        if not check.matches
    ]

    assert mismatched == ["active_profile_id"]


def test_describe_names_every_profile_and_the_target_text(package: bytes):
    text = describe(package)

    for profile_id in range(1, PROFILES + 1):
        assert f"profile {profile_id} " in text
    assert TARGET_TEXT in text


# -------------------------------------------------------------- backup file


def test_a_backup_document_round_trips_to_the_bytes_it_describes(package: bytes):
    document = backup_document(
        package,
        serial_number="DIU1-TEST",
        generation=21,
        reason="Captured before a test.",
        taken_at="2026-08-29T00:00:00+03:00",
    )

    assert "DIU1-TEST" in document
    assert "Generation: 21" in document
    assert package_from_backup(document) == package


def test_the_existing_hardware_backup_still_decodes_to_a_configuration():
    # The operator's pre-route-acceptance backup is the restore path this run
    # promises; a backup that cannot be decoded is not a restore path.
    backup = (
        Path(__file__).resolve().parents[3]
        / "hardware-backups"
        / "u1-config-generation-20-before-route-acceptance.b64"
    )
    if not backup.exists():  # pragma: no cover - the directory is untracked
        pytest.skip("the hardware backup directory is not present in this checkout")

    restored = package_from_backup(backup.read_text(encoding="utf-8"))

    assert decode_device_config(restored)


# ------------------------------------------------------- the deployment path


@pytest.fixture
def emulator() -> U1Emulator:
    device = U1Emulator()
    device.install_active(build_previous_configuration())
    return device


def build_previous_configuration() -> bytes:
    """Something already on the device, so the backup has something to save."""
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import ProjectSession, RenameProfile

    return compile_project_to_binary(ProjectSession.new().apply(RenameProfile(1, "Было")).project)


def test_deploy_backs_up_writes_and_reads_back(qapp, emulator: U1Emulator, package: bytes):
    result = deploy(SynchronousTransportLink(emulator), package)

    assert result.previous_package == build_previous_configuration()
    assert result.written_package == package
    assert result.read_back_package == package
    assert result.read_back_matches
    assert verify_round_trip(result.read_back_package)
    assert [check.field for check in verify_round_trip(result.read_back_package) if not check.matches] == []


def test_deploy_refuses_to_overwrite_a_device_it_could_not_read(qapp, package: bytes):
    # No install_active: the device has no configuration to hand back.
    blank = U1Emulator()

    with pytest.raises(DeployError):
        deploy(SynchronousTransportLink(blank), package)

    assert blank.active_hash == b"\0" * 32


def test_the_written_configuration_is_still_there_after_a_power_cycle(
    qapp, emulator: U1Emulator, package: bytes
):
    # The emulator's own A/B store, not the board's - but it is the contract
    # S4a asks the flash to honour, and it is checked before anyone is asked to
    # unplug anything.
    deploy(SynchronousTransportLink(emulator), package)
    emulator.simulate_power_cycle()

    assert emulator.active_hash == hashlib.sha256(package).digest()
    assert emulator.active_profile == BOOT_PROFILE_ID
