"""What ``--check`` is actually asking.

``tools/generate_protocol.py --check`` is a release gate: ``build_release.ps1``
refuses to build when it fails. The question it is meant to answer is whether
the checked-in generated files still describe ``protocol/schema.json``. It used
to answer a different question - whether their bytes matched the generator's
output on this filesystem - and those two questions disagree on any fresh
checkout, because git hands Windows CRLF and the generator writes LF. A clean
clone therefore could not build a release, and said nothing about why.

These tests pin the intended question and the diagnostics a gate owes its
operator.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
GENERATOR_PATH = REPOSITORY_ROOT / "tools" / "generate_protocol.py"


def _load_generator():
    spec = importlib.util.spec_from_file_location("duo_generate_protocol", GENERATOR_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generate_protocol = _load_generator()


def test_check_accepts_generated_files_stored_with_crlf(tmp_path: Path) -> None:
    """A checkout normalised to CRLF is up to date, not stale."""
    target = tmp_path / "generated.hpp"
    content = "// generated; do not edit\n#pragma once\n"
    target.write_bytes(content.replace("\n", "\r\n").encode("utf-8"))

    assert generate_protocol.stale_report(target, content) is None


def test_check_leaves_a_crlf_checkout_untouched(tmp_path: Path) -> None:
    """Being up to date means not being rewritten - line endings included."""
    target = tmp_path / "generated.hpp"
    crlf = "// generated; do not edit\r\n#pragma once\r\n".encode("utf-8")
    target.write_bytes(crlf)

    assert generate_protocol.write_if_changed(target, "// generated; do not edit\n#pragma once\n") is False
    assert target.read_bytes() == crlf


def test_check_names_the_file_and_the_difference(tmp_path: Path) -> None:
    """A silent exit 1 in a release gate is its own defect."""
    target = tmp_path / "protocol.py"
    target.write_bytes(b"CDC_VERSION = 1\n")

    report = generate_protocol.stale_report(target, "CDC_VERSION = 2\n")

    assert report is not None
    assert str(target) in report
    assert "-CDC_VERSION = 1" in report
    assert "+CDC_VERSION = 2" in report


def test_missing_generated_file_is_stale_and_says_so(tmp_path: Path) -> None:
    target = tmp_path / "never-generated.hpp"

    report = generate_protocol.stale_report(target, "anything\n")

    assert report is not None
    assert "missing" in report


def test_check_reports_every_stale_file_and_exits_nonzero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(generate_protocol, "CPP_PATH", tmp_path / "generated.hpp")
    monkeypatch.setattr(generate_protocol, "PYTHON_PATH", tmp_path / "protocol.py")
    monkeypatch.setattr(generate_protocol, "PYTHON_INIT_PATH", tmp_path / "__init__.py")

    assert generate_protocol.main(["--check"]) == 1
    captured = capsys.readouterr()
    assert "generated.hpp" in captured.out + captured.err
    assert "protocol.py" in captured.out + captured.err


def test_repository_generated_files_match_the_schema() -> None:
    """The gate itself, run against the real tree, whatever its line endings."""
    assert generate_protocol.main(["--check"]) == 0
