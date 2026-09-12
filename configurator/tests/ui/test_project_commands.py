"""Editing commands: every change is a pure command applied to a new session."""

from __future__ import annotations

from uuid import uuid4

import pytest

from duo_input.domain.models import Action, Binding, DeviceProject, Macro, Profile, Trigger
from duo_input.domain.validation import validate_project
from duo_input.generated.protocol import (
    ActionKind,
    BindingMode,
    KeyboardRoute,
    MouseRoute,
    TargetMode,
    TextLayout,
    TriggerKind,
)
from duo_input.ui.models.project_session import (
    AddBinding,
    ClearProfile,
    CopyProfile,
    ProjectSession,
    RemoveBinding,
    SetProfileColor,
    SetProfileRoutes,
    UpdateBinding,
    default_project,
)


def _binding(code: int = 0x04, *, modifiers: int = 0) -> Binding:
    return Binding(
        trigger=Trigger(TriggerKind.KEYBOARD_USAGE, code, modifiers),
        mode=BindingMode.REPLACE,
        action=Action(ActionKind.TOGGLE_KEYBOARD_ROUTE, 0),
    )


def _profile(session: ProjectSession, profile_id: int) -> Profile:
    return next(profile for profile in session.project.profiles if profile.id == profile_id)


# ------------------------------------------------------------------ profiles


def test_set_profile_color_changes_only_that_profile():
    session = ProjectSession.new()

    changed = session.apply(SetProfileColor(3, (1, 2, 3)))

    assert _profile(changed, 3).color_rgb == (1, 2, 3)
    assert _profile(changed, 4).color_rgb == _profile(session, 4).color_rgb
    assert validate_project(changed.project) == ()


def test_set_profile_routes_changes_where_that_profile_starts():
    # A profile stores the routes it starts in and the firmware applies them
    # when the profile becomes the active one, so this is the only way a
    # project can say which computer the device comes up pointing at.
    session = ProjectSession.new()

    changed = session.apply(SetProfileRoutes(2, KeyboardRoute.PC2, MouseRoute.PC2))

    assert _profile(changed, 2).keyboard_route == KeyboardRoute.PC2
    assert _profile(changed, 2).mouse_route == MouseRoute.PC2
    assert validate_project(changed.project) == ()


def test_set_profile_routes_leaves_every_other_profile_where_it_was():
    session = ProjectSession.new()

    changed = session.apply(SetProfileRoutes(2, KeyboardRoute.BOTH, MouseRoute.PC2))

    assert _profile(changed, 3).keyboard_route == _profile(session, 3).keyboard_route
    assert _profile(changed, 3).mouse_route == _profile(session, 3).mouse_route


def test_set_profile_routes_refuses_a_profile_that_does_not_exist():
    with pytest.raises(ValueError):
        ProjectSession.new().apply(SetProfileRoutes(9, KeyboardRoute.PC1, MouseRoute.PC1))


def test_copy_profile_duplicates_content_but_keeps_the_target_id():
    session = ProjectSession.new().apply(AddBinding(1, _binding()))
    source = _profile(session, 1)

    copied = session.apply(CopyProfile(1, 5))

    target = _profile(copied, 5)
    assert target.id == 5
    assert target.name == source.name
    assert target.color_rgb == source.color_rgb
    assert target.bindings == source.bindings
    assert validate_project(copied.project) == ()


def test_copy_profile_gives_the_copies_their_own_uuids():
    session = ProjectSession.new().apply(AddBinding(1, _binding()))

    copied = session.apply(CopyProfile(1, 5))

    original = _profile(copied, 1).bindings[0]
    duplicate = _profile(copied, 5).bindings[0]
    assert duplicate.uuid != original.uuid
    assert validate_project(copied.project) == ()


def test_copy_profile_onto_itself_is_refused():
    session = ProjectSession.new()

    with pytest.raises(ValueError):
        session.apply(CopyProfile(2, 2))


def test_clear_profile_restores_the_pristine_slot():
    session = (
        ProjectSession.new()
        .apply(AddBinding(4, _binding()))
        .apply(SetProfileColor(4, (9, 9, 9)))
    )

    cleared = session.apply(ClearProfile(4))

    assert _profile(cleared, 4) == _profile(ProjectSession.new(), 4)
    assert validate_project(cleared.project) == ()


# ------------------------------------------------------------------ bindings


def test_add_binding_appends_to_the_named_profile_only():
    session = ProjectSession.new()

    changed = session.apply(AddBinding(2, _binding(0x05)))

    assert len(_profile(changed, 2).bindings) == 1
    assert _profile(changed, 2).bindings[0].trigger.code == 0x05
    assert _profile(changed, 3).bindings == ()
    assert _profile(session, 2).bindings == ()


def test_add_binding_refuses_a_trigger_the_profile_already_uses():
    session = ProjectSession.new().apply(AddBinding(2, _binding(0x06)))

    with pytest.raises(ValueError):
        session.apply(AddBinding(2, _binding(0x06)))


def test_the_same_trigger_may_be_used_in_a_different_profile():
    session = ProjectSession.new().apply(AddBinding(2, _binding(0x06)))

    changed = session.apply(AddBinding(3, _binding(0x06)))

    assert _profile(changed, 3).bindings[0].trigger.code == 0x06
    assert validate_project(changed.project) == ()


def test_a_modifier_makes_the_same_usage_a_different_trigger():
    session = ProjectSession.new().apply(AddBinding(2, _binding(0x06)))

    changed = session.apply(AddBinding(2, _binding(0x06, modifiers=0x02)))

    assert len(_profile(changed, 2).bindings) == 2
    assert validate_project(changed.project) == ()


def test_update_binding_replaces_the_binding_with_that_uuid():
    session = ProjectSession.new().apply(AddBinding(2, _binding(0x06)))
    existing = _profile(session, 2).bindings[0]

    changed = session.apply(UpdateBinding(2, existing.uuid, _binding(0x07)))

    assert len(_profile(changed, 2).bindings) == 1
    assert _profile(changed, 2).bindings[0].trigger.code == 0x07
    assert _profile(changed, 2).bindings[0].uuid == existing.uuid


def test_update_binding_may_keep_its_own_trigger():
    session = ProjectSession.new().apply(AddBinding(2, _binding(0x06)))
    existing = _profile(session, 2).bindings[0]
    edited = Binding(
        trigger=existing.trigger,
        mode=BindingMode.ADD,
        action=existing.action,
        uuid=existing.uuid,
    )

    changed = session.apply(UpdateBinding(2, existing.uuid, edited))

    assert _profile(changed, 2).bindings[0].mode is BindingMode.ADD


def test_update_binding_refuses_a_trigger_another_binding_uses():
    session = (
        ProjectSession.new()
        .apply(AddBinding(2, _binding(0x06)))
        .apply(AddBinding(2, _binding(0x07)))
    )
    first = _profile(session, 2).bindings[0]

    with pytest.raises(ValueError):
        session.apply(UpdateBinding(2, first.uuid, _binding(0x07)))


def test_update_binding_refuses_an_unknown_uuid():
    session = ProjectSession.new()

    with pytest.raises(ValueError):
        session.apply(UpdateBinding(2, uuid4(), _binding()))


def test_remove_binding_drops_exactly_one_binding():
    session = (
        ProjectSession.new()
        .apply(AddBinding(2, _binding(0x06)))
        .apply(AddBinding(2, _binding(0x07)))
    )
    first = _profile(session, 2).bindings[0]

    changed = session.apply(RemoveBinding(2, first.uuid))

    assert tuple(binding.trigger.code for binding in _profile(changed, 2).bindings) == (0x07,)


def test_binding_limit_is_enforced_by_the_command():
    session = ProjectSession.new()
    for index in range(128):
        session = session.apply(AddBinding(1, _binding(0x04, modifiers=index)))
    assert len(_profile(session, 1).bindings) == 128

    with pytest.raises(ValueError):
        session.apply(AddBinding(1, _binding(0x05)))


def test_a_run_macro_binding_survives_a_profile_copy():
    macro = Macro(id=1, name="M", target=TargetMode.INHERIT, steps=())
    binding = Binding(
        trigger=Trigger(TriggerKind.KEYBOARD_USAGE, 0x04, 0),
        mode=BindingMode.REPLACE,
        action=Action(ActionKind.RUN_MACRO, 1),
    )
    pristine = default_project()
    profiles = tuple(
        Profile(
            id=profile.id,
            name=profile.name,
            color_rgb=profile.color_rgb,
            keyboard_route=KeyboardRoute.PC1,
            mouse_route=MouseRoute.PC1,
            text_layout=TextLayout.US,
            bindings=(binding,) if profile.id == 1 else (),
            macros=(macro,) if profile.id == 1 else (),
        )
        for profile in pristine.profiles
    )
    session = ProjectSession(
        project=DeviceProject(
            schema_version=pristine.schema_version,
            active_profile_id=1,
            profiles=profiles,
        )
    )

    copied = session.apply(CopyProfile(1, 2))

    assert _profile(copied, 2).macros[0].uuid != _profile(copied, 1).macros[0].uuid
    assert _profile(copied, 2).bindings[0].action == Action(ActionKind.RUN_MACRO, 1)
    assert validate_project(copied.project) == ()


# -------------------------------------------------------------------- macros


def _macro(macro_id: int = 1, name: str = "M") -> Macro:
    return Macro(id=macro_id, name=name, target=TargetMode.INHERIT, steps=())


def test_add_macro_numbers_the_new_macro_itself():
    from duo_input.ui.models.project_session import AddMacro

    session = ProjectSession.new().apply(AddMacro(1, "Куркума"))

    macro = _profile(session, 1).macros[0]
    assert macro.id == 1
    assert macro.name == "Куркума"
    assert macro.target is TargetMode.INHERIT
    assert validate_project(session.project) == ()


def test_add_macro_reuses_the_lowest_free_identifier():
    from duo_input.ui.models.project_session import AddMacro, RemoveMacro

    session = (
        ProjectSession.new()
        .apply(AddMacro(1, "A"))
        .apply(AddMacro(1, "B"))
        .apply(AddMacro(1, "C"))
    )
    second = _profile(session, 1).macros[1]

    session = session.apply(RemoveMacro(1, second.uuid)).apply(AddMacro(1, "D"))

    assert [macro.id for macro in _profile(session, 1).macros] == [1, 3, 2]
    assert validate_project(session.project) == ()


def test_the_macro_limit_is_enforced_by_the_command():
    from duo_input.ui.models.project_session import AddMacro

    session = ProjectSession.new()
    for index in range(32):
        session = session.apply(AddMacro(1, f"M{index}"))

    with pytest.raises(ValueError):
        session.apply(AddMacro(1, "one too many"))


def test_rename_and_retarget_touch_only_that_macro():
    from duo_input.ui.models.project_session import AddMacro, RenameMacro, SetMacroTarget

    session = ProjectSession.new().apply(AddMacro(1, "A")).apply(AddMacro(1, "B"))
    first = _profile(session, 1).macros[0]

    session = session.apply(RenameMacro(1, first.uuid, "Куркума")).apply(
        SetMacroTarget(1, first.uuid, TargetMode.BOTH)
    )

    assert _profile(session, 1).macros[0].name == "Куркума"
    assert _profile(session, 1).macros[0].target is TargetMode.BOTH
    assert _profile(session, 1).macros[1].name == "B"
    assert validate_project(session.project) == ()


def test_setting_the_steps_replaces_them_wholesale():
    from duo_input.ui.models.macro_steps import delay_step
    from duo_input.ui.models.project_session import AddMacro, SetMacroSteps

    session = ProjectSession.new().apply(AddMacro(1, "A"))
    macro = _profile(session, 1).macros[0]

    session = session.apply(SetMacroSteps(1, macro.uuid, (delay_step(5, 5),)))

    assert len(_profile(session, 1).macros[0].steps) == 1
    assert _profile(session, 1).macros[0].uuid == macro.uuid
    assert validate_project(session.project) == ()


def test_more_steps_than_the_protocol_allows_are_refused():
    from duo_input.ui.models.macro_steps import delay_step
    from duo_input.ui.models.project_session import AddMacro, SetMacroSteps

    session = ProjectSession.new().apply(AddMacro(1, "A"))
    macro = _profile(session, 1).macros[0]

    with pytest.raises(ValueError):
        session.apply(SetMacroSteps(1, macro.uuid, (delay_step(1, 1),) * 65))


def test_removing_a_macro_a_binding_runs_is_refused():
    from duo_input.ui.models.project_session import AddMacro, RemoveMacro

    session = ProjectSession.new().apply(AddMacro(1, "A"))
    macro = _profile(session, 1).macros[0]
    session = session.apply(
        AddBinding(
            1,
            Binding(
                trigger=Trigger(TriggerKind.KEYBOARD_USAGE, 0x04, 0),
                mode=BindingMode.REPLACE,
                action=Action(ActionKind.RUN_MACRO, macro.id),
            ),
        )
    )

    with pytest.raises(ValueError):
        session.apply(RemoveMacro(1, macro.uuid))


def test_removing_an_unused_macro_is_allowed():
    from duo_input.ui.models.project_session import AddMacro, RemoveMacro

    session = ProjectSession.new().apply(AddMacro(1, "A"))
    macro = _profile(session, 1).macros[0]

    session = session.apply(RemoveMacro(1, macro.uuid))

    assert _profile(session, 1).macros == ()
    assert validate_project(session.project) == ()


def test_set_synchronised_control_changes_only_that_setting():
    from duo_input.ui.models.project_session import SetSynchronisedControl, default_project

    project = default_project()
    changed = SetSynchronisedControl(True).apply_to(project)

    assert changed.synchronised_control is True
    assert changed.profiles == project.profiles
    assert changed.active_profile_id == project.active_profile_id
    assert SetSynchronisedControl(False).apply_to(changed).synchronised_control is False
