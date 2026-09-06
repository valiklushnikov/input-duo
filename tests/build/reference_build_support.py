"""Safe helpers for tests that must freshly relink the shared reference target."""

from __future__ import annotations

import os
import shutil
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Callable


REFERENCE_RELATIVE_ARTIFACTS = (
    Path("firmware/u1_reference/duo_u1_reference.elf"),
    Path("firmware/u1_reference/duo_u1_reference.uf2"),
)


@contextmanager
def _exclusive_build_lock(build_dir: Path):
    """Serialize destructive relinks when multiple pytest workers share a tree."""

    build_dir.mkdir(parents=True, exist_ok=True)
    lock_path = build_dir / ".duo_u1_reference_test.lock"
    with lock_path.open("a+b") as handle:
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)

        if os.name == "nt":
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def rebuild_reference_u1_artifacts(
    *,
    root: Path,
    build_dir: Path,
    backup_dir: Path,
    run_build: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> tuple[Path, Path]:
    """Recreate the reference ELF/UF2 from its named target without stale reads.

    Existing exact-path artifacts are copied into the caller's pytest temporary
    directory before removal. Any failed or incomplete relink removes partial
    outputs and restores those prior files in ``finally``.
    """

    artifacts = tuple(build_dir / relative for relative in REFERENCE_RELATIVE_ARTIFACTS)
    backup_dir.mkdir(parents=True, exist_ok=True)

    with _exclusive_build_lock(build_dir):
        backups: list[tuple[Path, Path]] = []
        rebuilt = False
        try:
            for artifact, relative in zip(artifacts, REFERENCE_RELATIVE_ARTIFACTS):
                if artifact.is_file():
                    backup = backup_dir / relative
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(artifact, backup)
                    artifact.unlink()
                    backups.append((artifact, backup))

            result = run_build(
                [
                    "cmake",
                    "--build",
                    str(build_dir),
                    "--target",
                    "duo_u1_reference",
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            assert result.returncode == 0, result.stdout + result.stderr
            missing = [str(artifact) for artifact in artifacts if not artifact.is_file()]
            assert not missing, "named target did not recreate: " + ", ".join(missing)
            rebuilt = True
            return artifacts
        finally:
            if not rebuilt:
                for artifact in artifacts:
                    artifact.unlink(missing_ok=True)
                for artifact, backup in backups:
                    shutil.copy2(backup, artifact)
