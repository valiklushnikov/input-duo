"""Autosave recovery: offered only when it is genuinely newer than the file."""

from __future__ import annotations

import json

import pytest

from duo_input.domain.project_store import ProjectError
from duo_input.persistence.autosave import AutosaveService, Recovery
from duo_input.persistence.locations import autosave_directory
from duo_input.ui.models.project_session import ProjectSession, RenameProfile


@pytest.fixture
def service(tmp_path, monkeypatch) -> AutosaveService:
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    return AutosaveService()


def test_the_autosave_lives_under_the_application_directory(service, tmp_path):
    assert service.directory == autosave_directory()
    assert str(tmp_path) in str(service.directory)


def test_nothing_is_offered_before_anything_was_autosaved(service):
    assert service.recovery() is None


def test_a_dirty_session_is_autosaved(service):
    session = ProjectSession.new().apply(RenameProfile(1, "Работа"))

    service.save(session)

    recovery = service.recovery()
    assert isinstance(recovery, Recovery)
    assert recovery.project_path is None
    assert recovery.session.project.profiles[0].name == "Работа"


def test_a_clean_session_is_not_worth_autosaving(service):
    service.save(ProjectSession.new())

    assert service.recovery() is None


def test_the_autosave_records_which_file_it_belongs_to(service, tmp_path):
    saved = ProjectSession.new().save(tmp_path / "work.duoinput.json")
    edited = saved.apply(RenameProfile(1, "Работа"))

    service.save(edited)

    assert service.recovery().project_path == tmp_path / "work.duoinput.json"


def test_an_autosave_older_than_the_file_is_not_offered(service, tmp_path):
    path = tmp_path / "work.duoinput.json"
    saved = ProjectSession.new().save(path)
    service.save(saved.apply(RenameProfile(1, "старое")))
    # The operator saved the real file afterwards; the autosave is stale.
    _touch_after(path, service.state_path)

    assert service.recovery() is None


def test_an_autosave_newer_than_the_file_is_offered(service, tmp_path):
    path = tmp_path / "work.duoinput.json"
    saved = ProjectSession.new().save(path)

    service.save(saved.apply(RenameProfile(1, "новое")))
    _touch_after(service.state_path, path)

    recovery = service.recovery()
    assert recovery is not None
    assert recovery.session.project.profiles[0].name == "новое"


def test_discarding_removes_the_autosave(service):
    service.save(ProjectSession.new().apply(RenameProfile(1, "Работа")))

    service.discard()

    assert service.recovery() is None
    assert not service.state_path.exists()


def test_saving_twice_keeps_only_the_newest_autosave(service):
    service.save(ProjectSession.new().apply(RenameProfile(1, "первое")))
    service.save(ProjectSession.new().apply(RenameProfile(1, "второе")))

    assert service.recovery().session.project.profiles[0].name == "второе"
    assert len(list(service.directory.glob("*.duoinput.json"))) == 1


def test_a_corrupted_autosave_is_reported_not_crashed(service):
    service.save(ProjectSession.new().apply(RenameProfile(1, "Работа")))
    service.autosave_path.write_text("{ not json", encoding="utf-8")

    with pytest.raises(ProjectError):
        service.recovery()


def test_a_corrupted_state_file_means_no_recovery(service):
    service.save(ProjectSession.new().apply(RenameProfile(1, "Работа")))
    service.state_path.write_text("{ not json", encoding="utf-8")

    assert service.recovery() is None


def test_a_state_file_pointing_at_a_missing_autosave_offers_nothing(service):
    service.save(ProjectSession.new().apply(RenameProfile(1, "Работа")))
    service.autosave_path.unlink()

    assert service.recovery() is None


def test_recovering_yields_a_dirty_session_anchored_to_the_original_file(service, tmp_path):
    path = tmp_path / "work.duoinput.json"
    saved = ProjectSession.new().save(path)
    service.save(saved.apply(RenameProfile(1, "Работа")))

    recovered = service.recovery().session

    assert recovered.path == path
    assert recovered.dirty is True


def test_the_autosave_is_written_atomically(service):
    service.save(ProjectSession.new().apply(RenameProfile(1, "Работа")))

    assert not list(service.directory.glob("*.tmp"))


def test_the_state_file_is_plain_readable_json(service):
    service.save(ProjectSession.new().apply(RenameProfile(1, "Работа")))

    state = json.loads(service.state_path.read_text(encoding="utf-8"))

    assert set(state) >= {"autosave", "project", "saved_at"}


def _touch_after(newer, older) -> None:
    """Make ``newer`` more recent than ``older`` without waiting for the clock."""
    import os

    stamp = older.stat().st_mtime + 10
    os.utime(newer, (stamp, stamp))
