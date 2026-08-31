"""The three-state project model: dirty, file hash, compiled hash, device hash."""

from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest

from duo_input.domain.models import DeviceProject
from duo_input.domain.project_store import PROJECT_SCHEMA_VERSION
from duo_input.domain.text_compiler import compile_project_to_binary
from duo_input.domain.validation import validate_project
from duo_input.generated.protocol import PROFILES
from duo_input.ui.models.project_session import (
    ProjectSession,
    RenameProfile,
    SetActiveProfile,
    default_project,
)


def _hex(project: DeviceProject) -> str:
    return hashlib.sha256(compile_project_to_binary(project)).hexdigest()


def test_default_project_is_valid_and_has_eight_profiles():
    project = default_project()

    assert isinstance(project, DeviceProject)
    assert project.schema_version == PROJECT_SCHEMA_VERSION
    assert len(project.profiles) == PROFILES
    assert tuple(profile.id for profile in project.profiles) == tuple(range(1, PROFILES + 1))
    assert validate_project(project) == ()


def test_new_session_is_clean_and_has_no_file_or_device_hash():
    session = ProjectSession.new()

    assert session.path is None
    assert session.dirty is False
    assert session.file_hash == ""
    assert session.device_hash == ""
    assert session.compiled_hash == _hex(session.project)
    assert session.compiled_size == len(compile_project_to_binary(session.project))
    assert session.issues == ()
    assert session.can_write is False  # nothing is connected


def test_apply_returns_a_new_session_and_never_mutates_the_old_one():
    session = ProjectSession.new()

    edited = session.apply(SetActiveProfile(3))

    assert edited is not session
    assert session.project.active_profile_id == 1
    assert edited.project.active_profile_id == 3
    assert session.dirty is False
    assert edited.dirty is True
    assert edited.compiled_hash != session.compiled_hash


def test_rename_profile_only_touches_the_named_profile():
    session = ProjectSession.new()

    edited = session.apply(RenameProfile(2, "Работа"))

    assert edited.project.profiles[1].name == "Работа"
    assert edited.project.profiles[0] == session.project.profiles[0]
    assert edited.dirty is True


def test_reverting_an_edit_clears_dirty_again():
    session = ProjectSession.new()
    original_name = session.project.profiles[1].name

    edited = session.apply(RenameProfile(2, "Работа"))
    reverted = edited.apply(RenameProfile(2, original_name))

    assert reverted.dirty is False


def test_save_clears_dirty_and_records_the_file_hash(tmp_path):
    path = tmp_path / "profile.duoinput.json"
    session = ProjectSession.new().apply(SetActiveProfile(4))

    saved = session.save(path)

    assert saved.dirty is False
    assert saved.path == path
    assert saved.file_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert saved.project == session.project


def test_save_does_not_change_the_device_hash_or_hide_a_mismatch(tmp_path):
    session = (
        ProjectSession.new().with_device_hash(b"\x11" * 32).apply(SetActiveProfile(2))
    )
    assert session.dirty is True
    assert session.device_matches is False

    saved = session.save(tmp_path / "profile.duoinput.json")

    assert saved.dirty is False
    assert saved.device_hash == "11" * 32
    assert saved.device_matches is False
    assert saved.file_hash != saved.device_hash
    assert saved.file_hash != saved.compiled_hash


def test_load_restores_a_clean_session(tmp_path):
    path = tmp_path / "profile.duoinput.json"
    ProjectSession.new().apply(RenameProfile(1, "Игры")).save(path)

    loaded = ProjectSession.load(path)

    assert loaded.dirty is False
    assert loaded.path == path
    assert loaded.file_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    assert loaded.project.profiles[0].name == "Игры"
    assert loaded.device_hash == ""


def test_device_matches_only_when_the_device_holds_the_compiled_package():
    session = ProjectSession.new()

    aligned = session.with_device_hash(bytes.fromhex(session.compiled_hash))
    stale = aligned.apply(SetActiveProfile(5))

    assert aligned.device_matches is True
    assert stale.device_matches is False


def test_disconnecting_clears_the_device_hash():
    session = ProjectSession.new().with_device_hash(b"\x22" * 32).with_connection(True)

    assert session.device_hash == "22" * 32

    disconnected = session.with_connection(False).with_device_hash(b"")

    assert disconnected.device_hash == ""
    assert disconnected.device_matches is False
    assert disconnected.can_write is False


def test_can_write_requires_a_connection_and_a_valid_project():
    session = ProjectSession.new()

    assert session.with_connection(True).can_write is True

    broken = replace(session.project, active_profile_id=99)
    invalid = ProjectSession(project=broken).with_connection(True)

    assert invalid.issues != ()
    assert invalid.can_write is False
    assert invalid.compiled_hash == ""
    assert invalid.compiled_size is None


def test_save_refuses_a_session_without_a_path():
    with pytest.raises(ValueError):
        ProjectSession.new().save()


def test_agreeing_with_the_device_clears_the_change_marker():
    """After a read or a write, the project and the board are the same thing."""
    session = ProjectSession.new().apply(RenameProfile(1, "Edited"))
    assert session.dirty is True

    agreed = session.agreeing_with_device()

    assert agreed.dirty is False
    assert agreed.project == session.project


def test_an_edit_after_agreeing_reads_as_changed_again():
    session = ProjectSession.new().agreeing_with_device()

    edited = session.apply(RenameProfile(1, "Since"))

    assert edited.dirty is True


def test_saving_a_copy_does_not_clear_the_change_marker(tmp_path):
    """A file is a copy, not the truth. Writing one changes nothing about
    whether the board is up to date."""
    session = ProjectSession.new().apply(RenameProfile(1, "Edited"))

    saved = session.save(tmp_path / "copy.duoinput.json")

    assert saved.dirty is True
