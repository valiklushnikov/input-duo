"""Правила брандмауэра Windows для Duo Input.

Ни один тест здесь не запускает настоящий PowerShell и не вызывает UAC: всё,
что выходит за пределы процесса, идёт через подменяемую ``system``.

Фикстуры в ``fixtures/firewall`` - байты настоящего вывода запроса этого
модуля на этой машине (Windows 10, русская локаль):

- ``absent.json`` - запрос наших двух имён, правил нет;
- ``windows_prompt_rules.json`` - запрос имени ``duoinput.exe``: правила,
  которые сама Windows создала по ответу на своё окно брандмауэра (любой
  порт, любой адрес; профиль - какой был активен, когда человек ответил);
- ``cyrillic_builtin_rule.json`` - запрос встроенного правила с русским
  именем: доказывает, что вывод приходит в UTF-8, а не в OEM-кодировке.

``present.json`` - производная: настоящих правил Duo Input на машине нет
(создавать их при разработке запрещено), поэтому это вывод той же формы с
нашими именами и значениями, в том виде, в каком их печатает PowerShell
(``"Private, Public"``, ``"LocalSubnet"``).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from duo_input.clipboard.coordinator import TCP_PORT
from duo_input.clipboard.discovery import BEACON_PORT
from duo_input.persistence import firewall

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "firewall"

#: Путь, под которым Windows создала правила из windows_prompt_rules.json -
#: именно его она и записала, в нижнем регистре.
PROMPT_RULES_PROGRAM = (
    r"C:\users\valentyn\documents\codex\2026-08-01\new-chat\work"
    r"\duo-input-mvp\configurator\dist\duoinput\duoinput.exe"
)
#: То, что present.json считает программой Duo Input.
INSTALLED = r"C:\Users\Operator\AppData\Local\Programs\Duo Input\DuoInput.exe"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class FakeSystem:
    """Граница процесса: записывает всё, ничего не запускает."""

    def __init__(
        self,
        *,
        output: bytes = b"[]\r\n",
        code: int | None = 0,
        elevated: bool = False,
        elevated_code: int | None = 0,
    ) -> None:
        self.output = output
        self.code = code
        self.elevated = elevated
        self.elevated_code = elevated_code
        self.runs: list[tuple[str, str]] = []
        self.elevated_runs: list[tuple[str, str]] = []

    def run(self, executable: str, parameters: str) -> tuple[int | None, bytes]:
        self.runs.append((executable, parameters))
        return self.code, self.output

    def run_elevated(self, executable: str, parameters: str) -> int | None:
        self.elevated_runs.append((executable, parameters))
        return self.elevated_code

    def is_elevated(self) -> bool:
        return self.elevated


@pytest.fixture
def frozen_windows(monkeypatch):
    """Как в собранном DuoInput.exe на Windows - на любой машине с тестами."""
    monkeypatch.setattr(firewall, "_is_windows", lambda: True)
    monkeypatch.setattr(firewall, "_is_frozen", lambda: True)
    monkeypatch.setenv("SystemRoot", r"C:\Windows")


# ------------------------------------------------------------ what is required


def test_required_rules_are_one_tcp_link_and_one_udp_discovery_rule():
    link, discovery = firewall.required_rules(INSTALLED)

    assert link == firewall.FirewallRule("Duo Input — связь", "TCP", TCP_PORT, INSTALLED)
    assert discovery == firewall.FirewallRule(
        "Duo Input — поиск", "UDP", BEACON_PORT, INSTALLED
    )
    # Сами порты - из модулей, которые их слушают, а не вторая копия числа.
    assert (link.local_port, discovery.local_port) == (47654, 47655)


# ------------------------------------------------------------ what is missing


def test_nothing_is_missing_when_both_rules_are_present(frozen_windows):
    system = FakeSystem(output=_fixture("present.json"))

    assert firewall.missing_rules(INSTALLED, system=system) == []


def test_everything_is_missing_when_no_rule_exists(frozen_windows):
    system = FakeSystem(output=_fixture("absent.json"))

    assert firewall.missing_rules(INSTALLED, system=system) == list(
        firewall.required_rules(INSTALLED)
    )


def test_rules_for_a_moved_program_count_as_missing(frozen_windows):
    """Папку перенесли: правила есть, но разрешают старый exe."""
    moved = r"D:\Tools\Duo Input\DuoInput.exe"
    system = FakeSystem(output=_fixture("present.json"))

    assert firewall.missing_rules(moved, system=system) == list(
        firewall.required_rules(moved)
    )


def test_the_program_path_is_compared_without_regard_to_case(frozen_windows):
    """Windows хранит путь так, как его записали, - сама она пишет в нижнем
    регистре (см. windows_prompt_rules.json)."""
    system = FakeSystem(output=_fixture("present.json"))

    assert firewall.missing_rules(INSTALLED.upper(), system=system) == []


def test_environment_variables_in_a_stored_program_path_are_expanded(
    frozen_windows, monkeypatch
):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Operator\AppData\Local")
    entries = json.loads(_fixture("present.json"))
    for entry in entries:
        entry["Program"] = r"%LOCALAPPDATA%\Programs\Duo Input\DuoInput.exe"
    system = FakeSystem(output=json.dumps(entries).encode("utf-8"))

    assert firewall.missing_rules(INSTALLED, system=system) == []


def test_windows_own_prompt_rules_are_not_ours(frozen_windows):
    """Правила, которые Windows создала по своему окну: любой порт с любого
    адреса для одного exe и профиль, какой был активен, когда человек
    ответил. Наш запрос их не вернёт (другое имя), но и попади они в ответ -
    это не то, что мы ставим, и за наши сойти не должны."""
    system = FakeSystem(output=_fixture("windows_prompt_rules.json"))

    missing = firewall.missing_rules(PROMPT_RULES_PROGRAM, system=system)

    assert missing == list(firewall.required_rules(PROMPT_RULES_PROGRAM))


#: Каждое поле, которое делает правило непригодным, - по одному. Каждый
#: случай ловит свою проверку в _satisfies: убери её, и упадёт ровно он.
UNUSABLE = {
    "disabled": ("Enabled", "False"),
    "outbound": ("Direction", "Outbound"),
    "block": ("Action", "Block"),
    "public only": ("Profile", "Public"),
    "private only": ("Profile", "Private"),
    "wrong protocol": ("Protocol", "ICMPv4"),
    "wrong port": ("LocalPort", "8080"),
    "any remote address": ("RemoteAddress", "Any"),
    "another program": ("Program", r"C:\Other\Other.exe"),
}


@pytest.mark.parametrize("field, value", UNUSABLE.values(), ids=UNUSABLE.keys())
def test_a_rule_that_would_not_let_the_peer_in_counts_as_missing(
    frozen_windows, field, value
):
    entries = json.loads(_fixture("present.json"))
    entries[0][field] = value
    system = FakeSystem(output=json.dumps(entries).encode("utf-8"))

    missing = firewall.missing_rules(INSTALLED, system=system)

    assert [rule.name for rule in missing] == [entries[0]["DisplayName"]]


def test_all_profiles_satisfy_the_profile_requirement(frozen_windows):
    """"Any" - так PowerShell печатает правило для всех профилей."""
    entries = json.loads(_fixture("present.json"))
    for entry in entries:
        entry["Profile"] = "Any"
    system = FakeSystem(output=json.dumps(entries).encode("utf-8"))

    assert firewall.missing_rules(INSTALLED, system=system) == []


def test_one_good_rule_among_duplicates_is_enough(frozen_windows):
    entries = json.loads(_fixture("present.json"))
    broken = dict(entries[0], Enabled="False")
    system = FakeSystem(output=json.dumps([broken, *entries]).encode("utf-8"))

    assert firewall.missing_rules(INSTALLED, system=system) == []


def test_the_query_output_is_read_as_utf8_not_as_the_console_code_page():
    """Настоящий вывод с русским именем правила (русская Windows, консоль в
    cp866): имя приходит целым, а значения полей - неизменными английскими
    именами перечислений, от языка системы не зависящими."""
    (entry, _domain) = firewall._parse_query_output(_fixture("cyrillic_builtin_rule.json"))

    assert entry["DisplayName"] == "Служба ловушек SNMP (UDP In)"
    assert (entry["Enabled"], entry["Direction"], entry["Profile"]) == (
        "False",
        "Inbound",
        "Private, Public",
    )
    assert entry["RemoteAddress"] == "LocalSubnet"


def test_the_query_asks_only_for_our_two_names_and_changes_nothing(frozen_windows):
    system = FakeSystem(output=_fixture("absent.json"))

    firewall.missing_rules(INSTALLED, system=system)

    [(executable, parameters)] = system.runs
    assert executable == r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
    assert "Get-NetFirewallRule -PolicyStore ActiveStore -DisplayName " \
        "'Duo Input — связь','Duo Input — поиск'" in parameters
    assert "Remove-" not in parameters and "New-" not in parameters.replace("New-Object", "")
    assert system.elevated_runs == []


@pytest.mark.parametrize(
    "code, output",
    [
        (1, b""),
        (1, _fixture("absent.json")),
        (None, b""),
        (0, b"not json"),
        (0, b'{"DisplayName": "Duo Input \xe2\x80\x94 \xd1\x81\xd0\xb2\xd1\x8f\xd0\xb7\xd1\x8c"}'),
        (0, b"\xff\xfe"),
    ],
    ids=["powershell failed", "failed after printing", "powershell did not run", "garbage", "not a list", "not utf-8"],
)
def test_a_failed_query_reports_nothing_missing(frozen_windows, code, output):
    """Не видим - не просим: запрос, который не удался, не повод показывать
    окно UAC. Правила ставит установщик; окно в программе - ремонт того, что
    мы видим сломанным."""
    system = FakeSystem(output=output, code=code)

    assert firewall.missing_rules(INSTALLED, system=system) == []


# ------------------------------------------------------------ applying


def test_apply_runs_one_elevated_command_that_replaces_both_rules(frozen_windows):
    system = FakeSystem()

    assert firewall.apply_rules(r"C:\Users\O'Brien\Duo Input\DuoInput.exe", system=system)

    program = r"'C:\Users\O''Brien\Duo Input\DuoInput.exe'"
    assert system.runs == []
    assert system.elevated_runs == [
        (
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "-NoProfile -NonInteractive -WindowStyle Hidden -Command \""
            "$ErrorActionPreference='Stop'; try { "
            "Get-NetFirewallRule -DisplayName 'Duo Input — связь','Duo Input — поиск'"
            " -ErrorAction SilentlyContinue | Remove-NetFirewallRule; "
            "New-NetFirewallRule -DisplayName 'Duo Input — связь' -Direction Inbound"
            " -Action Allow -Profile Private,Public -RemoteAddress LocalSubnet"
            f" -Protocol TCP -LocalPort 47654 -Program {program} | Out-Null; "
            "New-NetFirewallRule -DisplayName 'Duo Input — поиск' -Direction Inbound"
            " -Action Allow -Profile Private,Public -RemoteAddress LocalSubnet"
            f" -Protocol UDP -LocalPort 47655 -Program {program} | Out-Null; "
            "exit 0 } catch { exit 1 }\"",
        )
    ]


def test_every_single_quote_form_powershell_honours_is_doubled():
    """PowerShell закрывает строку в одинарных кавычках и типографскими
    ‘ ’ ‚ ‛ - путь с апострофом из раскладки не должен разорвать команду."""
    quoted = firewall._quote("a'b‘c’d‚e‛f")

    assert quoted == "'a''b‘‘c’’d‚‚e‛‛f'"


def test_a_double_quote_cannot_reach_the_command_line():
    """В пути Windows двойной кавычки не бывает, но если бы она была, она
    закрыла бы аргумент -Command посреди скрипта."""
    with pytest.raises(ValueError):
        firewall._parameters('Write-Output "x"')


def test_apply_runs_directly_when_already_elevated(frozen_windows):
    """Установщик запускает exe уже с правами администратора: второй UAC
    там не нужен и не должен появляться."""
    system = FakeSystem(elevated=True)

    assert firewall.apply_rules(INSTALLED, system=system)

    assert system.elevated_runs == []
    [(executable, parameters)] = system.runs
    assert executable.endswith("powershell.exe")
    assert "New-NetFirewallRule" in parameters


@pytest.mark.parametrize("code", [1, None], ids=["script failed", "uac declined"])
def test_apply_reports_failure(frozen_windows, code):
    assert not firewall.apply_rules(INSTALLED, system=FakeSystem(elevated_code=code))


def test_apply_reports_failure_of_a_direct_run(frozen_windows):
    assert not firewall.apply_rules(INSTALLED, system=FakeSystem(elevated=True, code=1))


def test_remove_deletes_only_our_two_rules(frozen_windows):
    system = FakeSystem()

    assert firewall.remove_rules(system=system)

    assert system.elevated_runs == [
        (
            r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
            "-NoProfile -NonInteractive -WindowStyle Hidden -Command \""
            "$ErrorActionPreference='Stop'; try { "
            "Get-NetFirewallRule -DisplayName 'Duo Input — связь','Duo Input — поиск'"
            " -ErrorAction SilentlyContinue | Remove-NetFirewallRule; "
            "exit 0 } catch { exit 1 }\"",
        )
    ]


def test_remove_runs_directly_when_already_elevated(frozen_windows):
    system = FakeSystem(elevated=True)

    assert firewall.remove_rules(system=system)

    assert system.elevated_runs == [] and len(system.runs) == 1


@pytest.mark.parametrize("code", [1, None], ids=["script failed", "uac declined"])
def test_remove_reports_failure(frozen_windows, code):
    assert not firewall.remove_rules(system=FakeSystem(elevated_code=code))


# ------------------------------------------------------------ where it applies


@pytest.mark.parametrize(
    "windows, frozen",
    [(False, True), (True, False), (False, False)],
    ids=["macos build", "windows from source", "macos from source"],
)
def test_outside_a_windows_build_everything_is_a_no_op(monkeypatch, windows, frozen):
    monkeypatch.setattr(firewall, "_is_windows", lambda: windows)
    monkeypatch.setattr(firewall, "_is_frozen", lambda: frozen)
    system = FakeSystem(output=_fixture("absent.json"))

    assert firewall.missing_rules(INSTALLED, system=system) == []
    assert firewall.apply_rules(INSTALLED, system=system) is True
    assert firewall.remove_rules(system=system) is True
    assert system.runs == [] and system.elevated_runs == []


def test_a_python_run_is_not_frozen(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert firewall._is_frozen() is False


def test_a_nuitka_build_is_frozen(monkeypatch):
    """Nuitka не ставит sys.frozen - он кладёт __compiled__ в модуль."""
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.setitem(firewall.__dict__, "__compiled__", object())

    assert firewall._is_frozen() is True


def test_a_pyinstaller_style_build_is_frozen(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert firewall._is_frozen() is True


# ------------------------------------------------------------ the real boundary


@pytest.mark.skipif(sys.platform != "win32", reason="ShellExecuteExW is Windows-only")
def test_shell_execute_waits_and_returns_the_exit_code():
    """Настоящий ShellExecuteExW - с глаголом "open", без UAC: проверяет
    структуру и ожидание кода возврата, которыми пользуется "runas"."""
    code = firewall._shell_execute_and_wait(
        "open", r"C:\Windows\System32\cmd.exe", "/d /c exit 7"
    )

    assert code == 7


@pytest.mark.skipif(sys.platform != "win32", reason="ShellExecuteExW is Windows-only")
def test_shell_execute_that_cannot_start_returns_none_and_logs_why(caplog):
    """Причина в журнале - единственный способ потом отличить отказ в UAC
    (1223) от программы, которой нет (2)."""
    with caplog.at_level("WARNING", logger=firewall.__name__):
        code = firewall._shell_execute_and_wait("open", r"C:\no\such\program.exe", "")

    assert code is None
    assert "ошибка 2;" in caplog.text


def test_run_elevated_asks_for_the_runas_verb(monkeypatch):
    calls = []
    monkeypatch.setattr(
        firewall,
        "_shell_execute_and_wait",
        lambda verb, executable, parameters: calls.append((verb, executable, parameters)) or 0,
    )

    assert firewall._WindowsSystem().run_elevated("x.exe", "-a") == 0
    assert calls == [("runas", "x.exe", "-a")]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process boundary")
def test_run_passes_the_command_line_unchanged_and_captures_output():
    code, output = firewall._WindowsSystem().run(
        r"C:\Windows\System32\cmd.exe", "/d /c echo one two& exit 3"
    )

    assert code == 3
    assert output.strip() == b"one two"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows process boundary")
def test_run_of_a_missing_program_returns_none():
    assert firewall._WindowsSystem().run(r"C:\no\such\program.exe", "") == (None, b"")
