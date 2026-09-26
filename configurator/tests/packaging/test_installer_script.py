"""What the Inno Setup script must say about the firewall rules.

The rules themselves are described once, in ``persistence/firewall.py``; the
installer only runs the program with its switch. These tests pin the two
ends to each other, so a renamed switch cannot leave the installer calling a
flag the program no longer knows.
"""

from __future__ import annotations

import re
from pathlib import Path

from duo_input.persistence import firewall

SCRIPT = Path(__file__).resolve().parents[2] / "packaging" / "duo-input.iss"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _code_section() -> str:
    text = _script()
    return text[text.index("[Code]") :]


def _body(name: str) -> str:
    """The body of one Pascal procedure, up to its closing ``end;``."""
    code = _code_section()
    start = code.index(f"procedure {name}(")
    return code[start : code.index("\nend;", start)]


def test_the_installer_itself_still_asks_for_no_administrator_rights():
    assert re.search(r"^PrivilegesRequired=lowest$", _script(), re.MULTILINE)


def test_the_switches_the_installer_passes_are_the_ones_the_program_reads():
    code = _code_section()

    assert f"InstallFirewallRulesArgument = '{firewall.INSTALL_ARGUMENT}';" in code
    assert f"RemoveFirewallRulesArgument = '{firewall.REMOVE_ARGUMENT}';" in code


def test_the_program_is_run_elevated_and_a_declined_prompt_fails_nothing():
    body = _body("RunProgramElevated")

    assert "ShellExec('runas', ExpandConstant('{app}\\{#AppExeName}'), Parameters" in body
    assert "ewWaitUntilTerminated" in body
    # The result only reaches the log: no Abort, no RaiseException, no MsgBox.
    assert not re.search(r"\b(Abort|RaiseException|MsgBox)\b", body)


def test_setup_installs_the_rules_after_the_files_are_in_place():
    body = _body("CurStepChanged")

    assert "CurStep = ssPostInstall" in body
    assert "RunProgramElevated(InstallFirewallRulesArgument)" in body


def test_uninstall_removes_the_rules_while_the_program_still_exists():
    body = _body("CurUninstallStepChanged")

    assert "CurUninstallStep = usUninstall" in body
    assert "RunProgramElevated(RemoveFirewallRulesArgument)" in body
