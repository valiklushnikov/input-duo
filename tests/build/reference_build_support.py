"""Safe helpers for tests that must freshly relink the shared reference target."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
from contextlib import contextmanager, nullcontext
from pathlib import Path
from typing import Callable


REFERENCE_RELATIVE_ARTIFACTS = (
    Path("firmware/u1_reference/duo_u1_reference.elf"),
    Path("firmware/u1_reference/duo_u1_reference.uf2"),
)


@contextmanager
def firmware_artifact_lock(root: Path):
    """Serialize every destructive firmware relink sharing this repository."""

    lock_path = root / "build" / ".duo_firmware_artifact_test.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
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


def rebuild_target_artifacts(
    *,
    root: Path,
    build_dir: Path,
    backup_dir: Path,
    target: str,
    relative_artifacts: tuple[Path, ...],
    run_build: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    copy_artifact: Callable[[Path, Path], object] = shutil.copy2,
    env: dict[str, str] | None = None,
    lock_held: bool = False,
) -> tuple[Path, ...]:
    """Recreate exact artifacts from a named target without stale reads.

    Existing exact-path artifacts are copied into the caller's pytest temporary
    directory before removal. Any failed or incomplete relink removes partial
    outputs and restores those prior files in ``finally``.
    """

    artifacts = tuple(build_dir / relative for relative in relative_artifacts)
    backup_dir.mkdir(parents=True, exist_ok=True)

    lock = nullcontext() if lock_held else firmware_artifact_lock(root)
    with lock:
        backups: list[tuple[Path, Path]] = []

        # Phase 1 is deliberately non-destructive. Every existing artifact is
        # copied and byte-verified before any shared build output is removed.
        for artifact, relative in zip(artifacts, relative_artifacts):
            if artifact.is_file():
                backup = backup_dir / relative
                backup.parent.mkdir(parents=True, exist_ok=True)
                copy_artifact(artifact, backup)
                assert backup.is_file(), f"backup copy was not created: {backup}"
                assert artifact.stat().st_size == backup.stat().st_size
                assert hashlib.sha256(artifact.read_bytes()).digest() == hashlib.sha256(
                    backup.read_bytes()
                ).digest(), f"backup copy differs from {artifact}"
                backups.append((artifact, backup))

        rebuilt = False
        try:
            # Phase 2 starts only after all source artifacts have safe backups.
            for artifact, _ in backups:
                artifact.unlink()

            result = run_build(
                [
                    "cmake",
                    "--build",
                    str(build_dir),
                    "--target",
                    target,
                ],
                cwd=root,
                capture_output=True,
                text=True,
                env=env,
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


def rebuild_reference_u1_artifacts(
    *,
    root: Path,
    build_dir: Path,
    backup_dir: Path,
    run_build: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    copy_artifact: Callable[[Path, Path], object] = shutil.copy2,
) -> tuple[Path, Path]:
    """Freshly recreate the reference U1 ELF/UF2 under the shared lock."""

    artifacts = rebuild_target_artifacts(
        root=root,
        build_dir=build_dir,
        backup_dir=backup_dir,
        target="duo_u1_reference",
        relative_artifacts=REFERENCE_RELATIVE_ARTIFACTS,
        run_build=run_build,
        copy_artifact=copy_artifact,
    )
    return artifacts[0], artifacts[1]
