"""Autosave, and the narrow question of when a recovery is worth offering.

Offering to recover work that is older than the file on disk is worse than
offering nothing: it invites the operator to overwrite what they deliberately
saved. So a recovery is offered only when three things hold - an autosave
exists, it belongs to the project being opened, and it is newer than that
project's file.

The autosave itself is an ordinary ``.duoinput.json``, written the same atomic
way project files are, so a recovered file can be opened by hand if this
program ever refuses to.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QObject, QTimer

from duo_input.domain.project_store import load_project, save_project_atomic
from duo_input.persistence.locations import autosave_directory
from duo_input.ui.models.project_session import ProjectSession, default_project

AUTOSAVE_FILE_NAME = "recovery.duoinput.json"
STATE_FILE_NAME = "recovery.json"

#: How often the shell is asked to autosave while the operator is typing.
DEFAULT_INTERVAL_MS = 30_000


@dataclass(frozen=True)
class Recovery:
    """Work that was autosaved and never made it into a project file."""

    session: ProjectSession
    project_path: Path | None
    saved_at: datetime


class AutosaveService(QObject):
    """Keeps one recovery copy of the session being edited."""

    def __init__(
        self,
        directory: str | Path | None = None,
        interval_ms: int = DEFAULT_INTERVAL_MS,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._directory = Path(directory) if directory is not None else autosave_directory()
        self._timer = QTimer(self)
        self._timer.setInterval(interval_ms)

    # ------------------------------------------------------------- locations

    @property
    def directory(self) -> Path:
        return self._directory

    @property
    def timer(self) -> QTimer:
        """Fires when it is time to autosave again; the shell owns the slot."""
        return self._timer

    @property
    def autosave_path(self) -> Path:
        return self._directory / AUTOSAVE_FILE_NAME

    @property
    def state_path(self) -> Path:
        return self._directory / STATE_FILE_NAME

    # ------------------------------------------------------------------ save

    def save(self, session: ProjectSession) -> bool:
        """Autosave ``session`` if it holds unsaved work. Returns whether it did.

        A session with nothing unsaved is not written: there would be nothing
        to recover, and an autosave file with no changes in it only creates a
        recovery prompt that wastes the operator's attention.
        """
        if not session.dirty:
            return False
        self._directory.mkdir(parents=True, exist_ok=True)
        save_project_atomic(session.project, self.autosave_path)
        self._write_state(
            {
                "autosave": self.autosave_path.name,
                "project": str(session.path) if session.path else None,
                "saved_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        return True

    def discard(self) -> None:
        """Throw the recovery away; the operator chose the file on disk."""
        for path in (self.autosave_path, self.state_path):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    # -------------------------------------------------------------- recovery

    def recovery(self) -> Recovery | None:
        """The work worth offering to recover, or ``None`` when there is none.

        Raises :class:`~duo_input.domain.project_store.ProjectError` when the
        autosave itself is unreadable: that is worth telling the operator
        about, rather than silently dropping their work.
        """
        state = self._read_state()
        if state is None:
            return None
        if not self.autosave_path.is_file():
            return None

        project_path = Path(state["project"]) if state.get("project") else None
        if project_path is not None and project_path.is_file():
            if project_path.stat().st_mtime > self.state_path.stat().st_mtime:
                # The operator saved the file after this autosave was written;
                # recovering would go backwards.
                return None
            # A tie is resolved in favour of offering it. Only unsaved work is
            # ever autosaved, and a recovery the operator can decline costs
            # less than work this program throws away without asking.

        project = load_project(self.autosave_path)
        session = ProjectSession(
            project=project,
            path=project_path,
            file_hash="",
            # The baseline is whatever the recovered work departed from: the
            # file it belongs to, or the pristine project a brand-new session
            # starts on. Either way the recovery opens dirty, which is exactly
            # what it is.
            baseline=(
                load_project(project_path) if project_path is not None else default_project()
            ),
        )
        return Recovery(
            session=session,
            project_path=project_path,
            saved_at=_parse_timestamp(state.get("saved_at")),
        )

    # ----------------------------------------------------------------- state

    def _write_state(self, state: dict[str, object]) -> None:
        temporary = self.state_path.with_suffix(".json.tmp")
        try:
            with temporary.open("w", encoding="utf-8", newline="\n") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.state_path)
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise

    def _read_state(self) -> dict[str, object] | None:
        try:
            document = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            # A state file we cannot read tells us nothing about what to
            # recover. The autosave stays on disk for a human to look at.
            return None
        return document if isinstance(document, dict) else None


def _parse_timestamp(value: object) -> datetime:
    try:
        return datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return datetime.fromtimestamp(0, timezone.utc)


__all__ = [
    "AUTOSAVE_FILE_NAME",
    "DEFAULT_INTERVAL_MS",
    "STATE_FILE_NAME",
    "AutosaveService",
    "Recovery",
]
