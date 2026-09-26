"""What the Inno Setup script must say about the firewall rules.

The rules themselves are described once, in ``persistence/firewall.py``; the
installer only runs the program with its switch. These tests pin the two
ends to each other, so a renamed switch cannot leave the installer calling a
flag the program no longer knows.
"""

from __future__ import annotations

import re
from pathlib import Path

from duo_input.app import firewall_check_exit_code
from duo_input.persistence import firewall

SCRIPT = Path(__file__).resolve().parents[2] / "packaging" / "duo-input.iss"


def _script() -> str:
    return SCRIPT.read_text(encoding="utf-8")


def _code_section() -> str:
    text = _script()
    return text[text.index("[Code]") :]


def _body(name: str) -> str:
    """The body of one Pascal procedure or function, up to its closing ``end;``."""
    code = _code_section()
    start = re.search(rf"^(procedure|function) {name}\b", code, re.MULTILINE).start()
    return code[start : code.index("\nend;", start)]


def test_the_installer_itself_still_asks_for_no_administrator_rights():
    assert re.search(r"^PrivilegesRequired=lowest$", _script(), re.MULTILINE)


def test_the_switches_the_installer_passes_are_the_ones_the_program_reads():
    code = _code_section()

    assert f"InstallFirewallRulesArgument = '{firewall.INSTALL_ARGUMENT}';" in code
    assert f"RemoveFirewallRulesArgument = '{firewall.REMOVE_ARGUMENT}';" in code
    assert f"CheckFirewallRulesArgument = '{firewall.CHECK_ARGUMENT}';" in code


def test_the_program_is_run_elevated_and_a_declined_prompt_fails_nothing():
    body = _body("RunProgramElevated")

    assert "ShellExec('runas', ExpandConstant('{app}\\{#AppExeName}'), Parameters" in body
    assert "ewWaitUntilTerminated" in body
    # The result only reaches the log: no Abort, no RaiseException, no MsgBox.
    assert not re.search(r"\b(Abort|RaiseException|MsgBox)\b", body)


def test_setup_installs_the_rules_after_the_files_are_in_place_only_when_needed():
    body = _body("CurStepChanged")

    assert "CurStep = ssPostInstall" in body
    assert re.search(
        r"else if FirewallRepairIsNeeded then\s+RunProgramElevated\(InstallFirewallRulesArgument\)",
        body,
    )


def test_a_silent_install_never_stops_for_a_uac_prompt():
    body = _body("CurStepChanged")

    assert re.search(r"if WizardSilent then\s+Log\(", body)
    assert body.index("if WizardSilent then") < body.index("RunProgramElevated")


def test_the_check_runs_unelevated_and_elevates_unless_fine_or_policy():
    """An upgrade over a working install must raise no UAC prompt: the
    program answers 0 (fine) or 2 (policy - elevating would not help), and
    every other code leads to the prompt. The numbers are the program's own."""
    body = _body("FirewallRepairIsNeeded")
    code = _code_section()
    repair = firewall.FirewallStatus(missing=firewall.required_rules("x"))

    assert "Exec(ExpandConstant('{app}\\{#AppExeName}'), CheckFirewallRulesArgument" in body
    assert "ShellExec" not in body and "runas" not in body
    # Only the two answers that mean "elevating would not help" skip the
    # prompt; a crashed check (0xC0000005) or any unexpected code elevates.
    assert (
        "Result := (Code <> FirewallRulesSatisfied) and (Code <> FirewallPolicyBlocked);" in body
    )
    satisfied = firewall_check_exit_code(firewall.FirewallStatus())
    policy = firewall_check_exit_code(firewall.FirewallStatus(policy_blocked=True))
    assert f"FirewallRulesSatisfied = {satisfied};" in code
    assert f"FirewallPolicyBlocked = {policy};" in code
    assert firewall_check_exit_code(repair) not in (satisfied, policy)


def test_a_check_that_cannot_run_falls_back_to_installing():
    body = _body("FirewallRepairIsNeeded")
    failed = body[body.index("  else") :]

    assert "Result := True;" in failed


def test_uninstall_removes_the_rules_while_the_program_still_exists():
    body = _body("CurUninstallStepChanged")

    assert "CurUninstallStep = usUninstall" in body
    assert "RunProgramElevated(RemoveFirewallRulesArgument)" in body
