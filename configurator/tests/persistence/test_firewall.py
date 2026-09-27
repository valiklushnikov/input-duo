"""Правила брандмауэра Windows для Duo Input.

Ни один тест здесь не запускает настоящий PowerShell с записью и не вызывает
UAC: всё, что выходит за пределы процесса, идёт через подменяемую ``system``.

Фикстуры в ``fixtures/firewall`` - байты настоящего вывода запроса этого
модуля на этой машине (Windows 10, русская локаль). Каждая - объект
``{"Rules": [...], "Blocks": [...], "Profiles": [...]}``:

- ``absent.json`` - наших правил нет; в ``Blocks`` - три настоящих
  локальных исходящих запрещающих правила без программы, которые есть на
  этой машине; ``Profiles`` - настоящие три профиля;
- ``windows_prompt_rules.json`` - в ``Rules`` правила, которые сама Windows
  создала по ответу на своё окно брандмауэра (любой порт, любой адрес;
  профиль - какой был активен, когда человек ответил), запрошенные по их
  постоянным именам;
- ``cyrillic_builtin_rule.json`` - встроенное правило с русским именем:
  доказывает, что вывод приходит в UTF-8, а не в OEM-кодировке.

``present.json`` - производная: настоящих правил Duo Input на машине нет
(создавать их при разработке запрещено), поэтому ``Rules`` - записи той же
формы с нашими именами и значениями в том виде, в каком их печатает
PowerShell (``"Private, Public"``, ``"LocalSubnet"``, ``"Local"``);
``Blocks`` и ``Profiles`` взяты из ``absent.json`` как есть.

Запрещающие правила для наших тестов собираются из настоящей записи
``windows_prompt_rules.json`` - Windows создаёт свои Block-правила той же
формы, с ``Action: Block``.
"""

from __future__ import annotations

import copy
import json
import sys
import threading
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
POWERSHELL = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
#: Тот же INSTALLED в коротких именах 8.3 - так его видит собранный exe в
#: sys.executable, если его запустили по короткому пути.
SHORT = r"C:\Users\Operator\APPDAT~1\Local\PROGRA~1\DUOINP~1\DuoInput.exe"
#: Файл, куда повышенный скрипт пишет ошибку; в тестах - несуществующий.
ERROR_FILE = r"C:\DuoInputTest\duo-input-firewall-error.txt"
CATCH = (
    "catch { try { (($_ | Out-String) + $_.Exception.GetType().FullName)"
    f" | Out-File -LiteralPath '{ERROR_FILE}' -Encoding UTF8 }} catch {{}}; exit 1 }}"
)
BLOCK_ID = "TCP Query User{0A1B2C3D-0000-4000-8000-000000000001}" + INSTALLED.lower()


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


def _answer(name: str) -> dict:
    return json.loads(_fixture(name))


def _encode(answer: dict) -> bytes:
    return json.dumps(answer, ensure_ascii=False).encode("utf-8")


def _block(**changes) -> dict:
    """Запрещающее правило Windows для нашего exe - из настоящей записи."""
    entry = copy.deepcopy(_answer("windows_prompt_rules.json")["Rules"][0])
    entry.update(
        Name=BLOCK_ID, Action="Block", Program=INSTALLED.lower(), Profile="Public"
    )
    entry.update(changes)
    return entry


def _with_blocks(*blocks: dict, base: str = "present.json") -> bytes:
    answer = _answer(base)
    answer["Blocks"] = answer["Blocks"] + list(blocks)
    return _encode(answer)


class FakeSystem:
    """Граница процесса: записывает всё, ничего не запускает."""

    def __init__(
        self,
        *,
        output: bytes | None = None,
        code: int | None = 0,
        elevated: bool = False,
        elevated_code: int | None = 0,
        direct_code: int | None = 0,
        error_text: str | None = None,
        long_paths: dict[str, str] | None = None,
    ) -> None:
        self.output = _fixture("present.json") if output is None else output
        #: То, что повышенный скрипт "напишет" в файл ошибки, если упадёт.
        self.error_text = error_text
        #: Короткие пути 8.3 и их длинные формы; остальное - как есть.
        self.long_paths = long_paths or {}
        self.code = code
        self.elevated = elevated
        self.elevated_code = elevated_code
        self.direct_code = direct_code
        self.queries: list[tuple[str, str]] = []
        self.runs: list[tuple[str, str]] = []
        self.elevated_runs: list[tuple[str, str, int | None]] = []

    def run(self, executable: str, parameters: str) -> tuple[int | None, bytes]:
        if "ConvertTo-Json" in parameters:
            self.queries.append((executable, parameters))
            return self.code, self.output
        self.runs.append((executable, parameters))
        return self._finish(parameters, self.direct_code), b""

    def run_elevated(self, executable: str, parameters: str, hwnd: int | None) -> int | None:
        self.elevated_runs.append((executable, parameters, hwnd))
        return self._finish(parameters, self.elevated_code)

    def _finish(self, parameters: str, code: int | None) -> int | None:
        """Как PowerShell с Out-File -Encoding UTF8: файл с BOM, если упал."""
        if code not in (0, None) and self.error_text is not None:
            path = parameters.split("Out-File -LiteralPath '")[1].split("'")[0]
            Path(path).write_bytes(b"\xef\xbb\xbf" + self.error_text.encode("utf-8"))
        return code

    def long_path(self, path: str) -> str:
        return self.long_paths.get(path, path)

    def is_elevated(self) -> bool:
        return self.elevated

    def system_directory(self) -> str:
        return r"C:\Windows\System32"


@pytest.fixture
def frozen_windows(monkeypatch):
    """Как в собранном DuoInput.exe на Windows - на любой машине с тестами.
    SystemRoot нарочно неверный: путь к PowerShell берётся из системного
    каталога, а не из окружения, которое можно подменить."""
    monkeypatch.setattr(firewall, "_is_windows", lambda: True)
    monkeypatch.setattr(firewall, "_is_frozen", lambda: True)
    monkeypatch.setenv("SystemRoot", r"D:\NotWindows")
    monkeypatch.setattr(firewall, "_error_file_path", lambda: ERROR_FILE)


def _status(output: bytes, exe: str = INSTALLED) -> firewall.FirewallStatus:
    return firewall.check_rules(exe, system=FakeSystem(output=output))


# ------------------------------------------------------------ what is required


def test_required_rules_are_one_tcp_link_and_one_udp_discovery_rule():
    link, discovery = firewall.required_rules(INSTALLED)

    assert link == firewall.FirewallRule(
        "DuoInput-Link", "Duo Input — связь", "TCP", TCP_PORT, INSTALLED
    )
    assert discovery == firewall.FirewallRule(
        "DuoInput-Discovery", "Duo Input — поиск", "UDP", BEACON_PORT, INSTALLED
    )
    # Сами порты - из модулей, которые их слушают, а не вторая копия числа.
    assert (link.local_port, discovery.local_port) == (47654, 47655)


# ------------------------------------------------------------ our allow rules


def test_both_rules_present_and_no_block_is_satisfied(frozen_windows):
    """present.json несёт и три настоящих исходящих Block-правила без
    программы - они к нам отношения не имеют."""
    status = _status(_fixture("present.json"))

    assert status == firewall.FirewallStatus()
    assert status.satisfied


def test_everything_is_missing_when_no_rule_exists(frozen_windows):
    status = _status(_fixture("absent.json"))

    assert status.missing == firewall.required_rules(INSTALLED)
    assert status.needs_repair and not status.satisfied


def test_rules_for_a_moved_program_count_as_missing(frozen_windows):
    """Папку перенесли: правила есть, но разрешают старый exe."""
    moved = r"D:\Tools\Duo Input\DuoInput.exe"

    assert _status(_fixture("present.json"), moved).missing == firewall.required_rules(moved)


def test_the_program_path_is_compared_without_regard_to_case(frozen_windows):
    """Windows хранит путь так, как его записали, - сама она пишет в нижнем
    регистре (см. windows_prompt_rules.json)."""
    assert _status(_fixture("present.json"), INSTALLED.upper()).missing == ()


def test_environment_variables_in_a_stored_program_path_are_expanded(
    frozen_windows, monkeypatch
):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Operator\AppData\Local")
    answer = _answer("present.json")
    for entry in answer["Rules"]:
        entry["Program"] = r"%LOCALAPPDATA%\Programs\Duo Input\DuoInput.exe"

    assert _status(_encode(answer)).missing == ()


def test_windows_own_prompt_rules_are_not_ours(frozen_windows):
    """Правила, которые Windows создала по своему окну: любой порт с любого
    адреса для одного exe. Наш запрос их не вернёт (другое имя), но и попади
    они в ответ - это не то, что мы ставим, и за наши сойти не должны."""
    status = _status(_fixture("windows_prompt_rules.json"), PROMPT_RULES_PROGRAM)

    assert status.missing == firewall.required_rules(PROMPT_RULES_PROGRAM)


#: Каждое поле, которое делает правило непригодным, - по одному.
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
    answer = _answer("present.json")
    answer["Rules"][0][field] = value

    assert [rule.id for rule in _status(_encode(answer)).missing] == ["DuoInput-Link"]


def test_all_profiles_satisfy_the_profile_requirement(frozen_windows):
    """"Any" - так PowerShell печатает правило для всех профилей."""
    answer = _answer("present.json")
    for entry in answer["Rules"]:
        entry["Profile"] = "Any"

    assert _status(_encode(answer)).missing == ()


def test_one_good_rule_among_duplicates_is_enough(frozen_windows):
    answer = _answer("present.json")
    answer["Rules"].insert(0, dict(answer["Rules"][0], Enabled="False"))

    assert _status(_encode(answer)).missing == ()


# ------------------------------------------------------------ block rules


def test_a_local_block_rule_for_our_exe_needs_repair_even_with_our_rules(frozen_windows):
    """Block важнее любого Allow: наши правила на месте, а связи нет."""
    status = _status(_with_blocks(_block()))

    assert status.missing == ()
    assert status.blocks == (firewall.BlockRule(BLOCK_ID, "duoinput.exe"),)
    assert status.needs_repair and not status.policy_blocked
    assert not status.satisfied


def test_a_block_rule_for_our_exe_is_matched_like_the_allow_rules(frozen_windows, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\Operator\AppData\Local")
    stored = r"%LOCALAPPDATA%\PROGRAMS\Duo Input\DuoInput.exe"

    assert _status(_with_blocks(_block(Program=stored))).blocks != ()


IGNORED_BLOCKS = {
    "another program": {"Program": r"C:\Other\Other.exe"},
    "no program": {"Program": "Any"},
    "disabled": {"Enabled": "False"},
    "outbound": {"Direction": "Outbound"},
}


@pytest.mark.parametrize("changes", IGNORED_BLOCKS.values(), ids=IGNORED_BLOCKS.keys())
def test_block_rules_that_are_not_about_our_inbound_traffic_are_ignored(
    frozen_windows, changes
):
    status = _status(_with_blocks(_block(**changes)))

    assert status == firewall.FirewallStatus()


def test_a_group_policy_block_for_our_exe_is_a_distinct_status(frozen_windows):
    """Программа его убрать не может - и не должна пытаться."""
    status = _status(_with_blocks(_block(Source="GroupPolicy")))

    assert status.policy_blocked
    assert status.blocks == ()
    assert not status.satisfied


def test_a_policy_that_ignores_local_rules_on_our_profile_is_a_policy_block(frozen_windows):
    answer = _answer("present.json")
    [public] = [profile for profile in answer["Profiles"] if profile["Name"] == "Public"]
    public["AllowLocalFirewallRules"] = "False"

    assert _status(_encode(answer)).policy_blocked


def test_the_domain_profile_is_not_ours(frozen_windows):
    """Наши правила - для частных и общедоступных сетей; что делает
    политика доменного профиля, к ним не относится."""
    answer = _answer("present.json")
    [domain] = [profile for profile in answer["Profiles"] if profile["Name"] == "Domain"]
    domain["AllowLocalFirewallRules"] = "False"

    assert not _status(_encode(answer)).policy_blocked


# ------------------------------------------------------------ the query


def test_the_query_output_is_read_as_utf8_not_as_the_console_code_page():
    """Настоящий вывод с русским именем правила (русская Windows, консоль в
    cp866): имя приходит целым, а значения полей - неизменными английскими
    именами перечислений, от языка системы не зависящими."""
    answer = firewall._parse_query_output(_fixture("cyrillic_builtin_rule.json"))
    entry = answer["Rules"][0]

    assert entry["DisplayName"] == "Служба ловушек SNMP (UDP In)"
    assert (entry["Enabled"], entry["Direction"], entry["Profile"], entry["Source"]) == (
        "False",
        "Inbound",
        "Private, Public",
        "Local",
    )
    assert entry["RemoteAddress"] == "LocalSubnet"


def test_the_query_reads_our_rules_by_id_every_block_rule_and_the_profiles(frozen_windows):
    system = FakeSystem()

    firewall.check_rules(INSTALLED, system=system)

    [(executable, parameters)] = system.queries
    assert executable == POWERSHELL
    assert (
        "Get-NetFirewallRule -PolicyStore ActiveStore -Name 'DuoInput-Link','DuoInput-Discovery'"
        in parameters
    )
    assert "Get-NetFirewallRule -PolicyStore ActiveStore -Action Block" in parameters
    assert "Get-NetFirewallProfile -PolicyStore ActiveStore" in parameters
    assert "Remove-" not in parameters and "New-NetFirewallRule" not in parameters
    assert system.runs == [] and system.elevated_runs == []


@pytest.mark.parametrize(
    "code, output",
    [
        (1, b""),
        (1, _fixture("present.json")),
        (None, b""),
        (0, b"not json"),
        (0, b"[]"),
        (0, b'{"Rules": [], "Blocks": []}'),
        (0, b'{"Rules": [], "Blocks": [], "Profiles": {"Name": "Public"}}'),
        (0, b'{"Rules": [1], "Blocks": [], "Profiles": []}'),
        (0, b"\xff\xfe"),
    ],
    ids=[
        "powershell failed",
        "failed after printing",
        "powershell did not run",
        "garbage",
        "not an object",
        "a key missing",
        "not a list",
        "not objects",
        "not utf-8",
    ],
)
def test_a_failed_query_is_unknown_not_missing(frozen_windows, code, output):
    """Не видим - так и говорим: программа по неизвестному статусу UAC не
    просит, а установщик - просит (см. --check-firewall-rules)."""
    status = firewall.check_rules(INSTALLED, system=FakeSystem(output=output, code=code))

    assert status == firewall.FirewallStatus(known=False)
    assert not status.needs_repair and not status.satisfied


# ------------------------------------------------------------ applying


def _script_of(parameters: str) -> str:
    prefix = '-NoProfile -NonInteractive -WindowStyle Hidden -Command "'
    assert parameters.startswith(prefix) and parameters.endswith('"')
    return parameters[len(prefix) : -1]


def _statements(parameters: str) -> list[str]:
    script = _script_of(parameters)
    head, _, rest = script.partition("try { ")
    assert head == "$ErrorActionPreference='Stop'; "
    body, _, tail = rest.rpartition(" } " + CATCH)
    assert tail == ""
    return body.split("; ")


def test_apply_runs_one_elevated_command_that_replaces_both_rules(frozen_windows):
    exe = r"C:\Users\O'Brien\Duo Input\DuoInput.exe"
    system = FakeSystem()

    assert firewall.apply_rules(exe, system=system, hwnd=0x1234)

    program = r"'C:\Users\O''Brien\Duo Input\DuoInput.exe'"
    assert system.runs == []
    assert system.elevated_runs == [
        (
            POWERSHELL,
            "-NoProfile -NonInteractive -WindowStyle Hidden -Command \""
            "$ErrorActionPreference='Stop'; try { "
            "Get-NetFirewallRule -Name 'DuoInput-Link','DuoInput-Discovery'"
            " -ErrorAction SilentlyContinue | Remove-NetFirewallRule; "
            "New-NetFirewallRule -Name 'DuoInput-Link' -DisplayName 'Duo Input — связь'"
            " -Group 'Duo Input' -Direction Inbound"
            " -Action Allow -Profile Private,Public -RemoteAddress LocalSubnet"
            f" -Protocol TCP -LocalPort 47654 -Program {program} | Out-Null; "
            "New-NetFirewallRule -Name 'DuoInput-Discovery' -DisplayName 'Duo Input — поиск'"
            " -Group 'Duo Input' -Direction Inbound"
            " -Action Allow -Profile Private,Public -RemoteAddress LocalSubnet"
            f" -Protocol UDP -LocalPort 47655 -Program {program} | Out-Null; "
            f"exit 0 }} {CATCH}\"",
            0x1234,
        )
    ]


def test_apply_removes_exactly_the_local_block_rule_found_by_its_id(frozen_windows):
    """Одно окно UAC: сперва наши правила, потом ровно этот запрет - по
    постоянному имени, а не по отображаемому (у Windows их много с одним
    и тем же "duoinput.exe") - и только потом новые правила."""
    other = _block(Name="Other{1}", Program=r"C:\Other\Other.exe")
    policy = _block(Name="GPO{2}", Source="GroupPolicy")
    system = FakeSystem(output=_with_blocks(_block(), other, policy))

    assert firewall.apply_rules(INSTALLED, system=system)

    [(_executable, parameters, _hwnd)] = system.elevated_runs
    statements = _statements(parameters)
    assert statements[:2] == [
        "Get-NetFirewallRule -Name 'DuoInput-Link','DuoInput-Discovery'"
        " -ErrorAction SilentlyContinue | Remove-NetFirewallRule",
        f"Get-NetFirewallRule -Name '{BLOCK_ID}' -ErrorAction SilentlyContinue"
        " | Where-Object { $_.Action -eq 'Block' -and $_.Direction -eq 'Inbound' }"
        " | Remove-NetFirewallRule",
    ]
    assert [s.split(" -DisplayName")[0] for s in statements[2:4]] == [
        "New-NetFirewallRule -Name 'DuoInput-Link'",
        "New-NetFirewallRule -Name 'DuoInput-Discovery'",
    ]
    assert statements[4:] == ["exit 0"]
    assert "Other{1}" not in parameters and "GPO{2}" not in parameters


def test_apply_logs_each_block_rule_it_is_about_to_remove(frozen_windows, caplog):
    system = FakeSystem(output=_with_blocks(_block()))

    with caplog.at_level("INFO", logger=firewall.__name__):
        firewall.apply_rules(INSTALLED, system=system)

    assert f"будет удалено запрещающее правило duoinput.exe ({BLOCK_ID})" in caplog.text


def test_a_block_rule_id_is_matched_literally_not_as_a_wildcard(frozen_windows):
    """-Name принимает шаблон, а в имени правила Windows - путь к exe."""
    tricky = r"UDP Query User{X}C:\apps[1]\what?\*\duoinput.exe"
    system = FakeSystem(output=_with_blocks(_block(Name=tricky)))

    firewall.apply_rules(INSTALLED, system=system)

    [(_executable, parameters, _hwnd)] = system.elevated_runs
    assert r"-Name 'UDP Query User{X}C:\apps`[1`]\what`?\`*\duoinput.exe'" in parameters


def test_every_single_quote_form_powershell_honours_is_doubled():
    """PowerShell закрывает строку в одинарных кавычках и типографскими
    ‘ ’ ‚ ‛ - путь с апострофом из раскладки не должен разорвать команду."""
    assert firewall._quote("a'b‘c’d‚e‛f") == "'a''b‘‘c’’d‚‚e‛‛f'"


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
    assert executable == POWERSHELL
    assert "New-NetFirewallRule" in parameters


@pytest.mark.parametrize("code", [1, None], ids=["script failed", "uac declined"])
def test_apply_reports_failure(frozen_windows, code):
    assert not firewall.apply_rules(INSTALLED, system=FakeSystem(elevated_code=code))


def test_apply_reports_failure_of_a_direct_run(frozen_windows):
    assert not firewall.apply_rules(INSTALLED, system=FakeSystem(elevated=True, direct_code=1))


def test_remove_deletes_only_our_two_rules(frozen_windows):
    """Деинсталлятор убирает своё и только своё - и запреты Windows тоже
    оставляет в покое."""
    system = FakeSystem(output=_with_blocks(_block()))

    assert firewall.remove_rules(system=system)

    assert system.queries == []
    assert system.elevated_runs == [
        (
            POWERSHELL,
            "-NoProfile -NonInteractive -WindowStyle Hidden -Command \""
            "$ErrorActionPreference='Stop'; try { "
            "Get-NetFirewallRule -Name 'DuoInput-Link','DuoInput-Discovery'"
            " -ErrorAction SilentlyContinue | Remove-NetFirewallRule; "
            f"exit 0 }} {CATCH}\"",
            None,
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

    assert firewall.check_rules(INSTALLED, system=system).satisfied
    assert firewall.apply_rules(INSTALLED, system=system) is True
    assert firewall.remove_rules(system=system) is True
    assert system.queries == [] and system.runs == [] and system.elevated_runs == []


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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows system directory")
def test_the_real_system_directory_holds_powershell():
    system = firewall._WindowsSystem()

    assert Path(firewall._powershell(system)).is_file()


@pytest.mark.skipif(sys.platform != "win32", reason="ShellExecuteExW is Windows-only")
def test_shell_execute_waits_and_returns_the_exit_code_from_a_worker_thread():
    """Настоящий ShellExecuteExW - с глаголом "open", без UAC, из рабочего
    потока, как в программе: проверяет структуру, COM на потоке и ожидание
    кода возврата, которыми пользуется "runas"."""
    result: list[int | None] = []
    worker = threading.Thread(
        target=lambda: result.append(
            firewall._shell_execute_and_wait(
                "open", r"C:\Windows\System32\cmd.exe", "/d /c exit 7", None
            )
        )
    )
    worker.start()
    worker.join(30)

    assert result == [7]


@pytest.mark.skipif(sys.platform != "win32", reason="ShellExecuteExW is Windows-only")
def test_shell_execute_that_cannot_start_returns_none_and_logs_why(caplog):
    """Причина в журнале - единственный способ потом отличить отказ в UAC
    (1223) от программы, которой нет (2)."""
    with caplog.at_level("WARNING", logger=firewall.__name__):
        code = firewall._shell_execute_and_wait("open", r"C:\no\such\program.exe", "")

    assert code is None
    assert "ошибка 2;" in caplog.text


def test_run_elevated_asks_for_the_runas_verb_over_the_given_window(monkeypatch):
    calls = []
    monkeypatch.setattr(
        firewall,
        "_shell_execute_and_wait",
        lambda *arguments: calls.append(arguments) or 0,
    )

    assert firewall._WindowsSystem().run_elevated("x.exe", "-a", 0x42) == 0
    assert calls == [("runas", "x.exe", "-a", 0x42)]


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


@pytest.mark.skipif(sys.platform != "win32", reason="Windows system directory")
def test_a_failed_system_directory_lookup_is_an_error_not_a_relative_path(monkeypatch):
    """Пустой ответ GetSystemDirectoryW дал бы относительный путь
    "WindowsPowerShell\v1.0\powershell.exe" - его нашли бы в текущем
    каталоге, а команда может выполняться повышенной."""
    import ctypes

    monkeypatch.setattr(ctypes.windll.kernel32, "GetSystemDirectoryW", lambda buffer, size: 0)

    with pytest.raises(OSError):
        firewall._WindowsSystem().system_directory()


# ------------------------------------------------------------ 8.3 short paths


def test_a_short_exe_path_finds_the_rules_stored_with_the_long_path(frozen_windows):
    """Собранный exe, запущенный по пути 8.3, видит в sys.executable
    DOCUME~1\\...; Windows хранит в правилах длинный путь."""
    system = FakeSystem(output=_with_blocks(_block()), long_paths={SHORT: INSTALLED})

    status = firewall.check_rules(SHORT, system=system)

    assert status.missing == ()
    assert [block.id for block in status.blocks] == [BLOCK_ID]


def test_rules_are_created_with_the_long_path_of_a_short_exe_path(frozen_windows):
    system = FakeSystem(long_paths={SHORT: INSTALLED})

    assert firewall.apply_rules(SHORT, system=system)

    [(_executable, parameters, _hwnd)] = system.elevated_runs
    assert f"-Program '{INSTALLED}'" in parameters
    assert "~1" not in parameters


def test_a_short_path_stored_by_someone_else_still_matches(frozen_windows):
    answer = _answer("present.json")
    for entry in answer["Rules"]:
        entry["Program"] = SHORT
    system = FakeSystem(
        output=_with_blocks(_block(Program=SHORT)),
        long_paths={SHORT: INSTALLED},
    )
    rules_system = FakeSystem(output=_encode(answer), long_paths={SHORT: INSTALLED})

    assert firewall.check_rules(INSTALLED, system=rules_system).missing == ()
    assert [block.id for block in firewall.check_rules(INSTALLED, system=system).blocks] == [BLOCK_ID]


def test_env_variables_are_expanded_before_the_long_path_lookup(frozen_windows, monkeypatch):
    monkeypatch.setenv("DUOTEST", r"C:\Users\Operator\APPDAT~1")
    stored = r"%DUOTEST%\Local\PROGRA~1\DUOINP~1\DuoInput.exe"
    system = FakeSystem(output=_with_blocks(_block(Program=stored)), long_paths={SHORT: INSTALLED})

    assert [block.id for block in firewall.check_rules(INSTALLED, system=system).blocks] == [BLOCK_ID]


@pytest.mark.skipif(sys.platform != "win32", reason="GetLongPathNameW is Windows-only")
def test_the_real_long_path_expands_8_3_names_of_an_existing_file(tmp_path):
    import ctypes

    target = tmp_path / "a rather long directory name" / "DuoInput.exe"
    target.parent.mkdir()
    target.write_bytes(b"")
    buffer = ctypes.create_unicode_buffer(1024)
    ctypes.windll.kernel32.GetShortPathNameW(str(target), buffer, len(buffer))
    short = buffer.value
    if short.lower() == str(target).lower():
        pytest.skip("8.3 names are disabled on this volume")

    assert firewall._WindowsSystem().long_path(short).lower() == str(target).lower()


@pytest.mark.skipif(sys.platform != "win32", reason="GetLongPathNameW is Windows-only")
def test_the_real_long_path_leaves_a_missing_file_unchanged():
    missing = r"C:\NO~1\SUCH~1\program.exe"

    assert firewall._WindowsSystem().long_path(missing) == missing


# ------------------------------------------------------------ why the elevated script failed


def test_the_elevated_scripts_error_is_logged_and_its_file_removed(
    frozen_windows, monkeypatch, tmp_path, caplog
):
    """Из-за UAC виден только код возврата; сам текст ошибки повышенный
    скрипт кладёт в файл во временной папке пользователя, программа его
    читает, пишет в журнал и удаляет."""
    error_file = tmp_path / "duo-input-firewall-x.txt"
    monkeypatch.setattr(firewall, "_error_file_path", lambda: str(error_file))
    system = FakeSystem(
        elevated_code=1,
        error_text="Отказано в доступе.\r\nSystem.UnauthorizedAccessException",
    )

    with caplog.at_level("WARNING", logger=firewall.__name__):
        assert not firewall.apply_rules(INSTALLED, system=system)

    assert "Отказано в доступе." in caplog.text
    assert "System.UnauthorizedAccessException" in caplog.text
    assert "\ufeff" not in caplog.text
    assert not error_file.exists()


def test_a_direct_run_logs_its_error_the_same_way(frozen_windows, monkeypatch, tmp_path, caplog):
    error_file = tmp_path / "duo-input-firewall-y.txt"
    monkeypatch.setattr(firewall, "_error_file_path", lambda: str(error_file))
    system = FakeSystem(elevated=True, direct_code=1, error_text="boom")

    with caplog.at_level("WARNING", logger=firewall.__name__):
        assert not firewall.remove_rules(system=system)

    assert "boom" in caplog.text
    assert not error_file.exists()


def test_a_failure_without_an_error_file_says_so(frozen_windows, monkeypatch, tmp_path, caplog):
    """Отказ в UAC - скрипт не запускался, файла нет."""
    monkeypatch.setattr(firewall, "_error_file_path", lambda: str(tmp_path / "none.txt"))

    with caplog.at_level("WARNING", logger=firewall.__name__):
        assert not firewall.apply_rules(INSTALLED, system=FakeSystem(elevated_code=None))

    assert "подробностей нет" in caplog.text


def test_each_run_gets_its_own_error_file_in_the_temp_directory(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall.tempfile, "gettempdir", lambda: str(tmp_path))

    first, second = firewall._error_file_path(), firewall._error_file_path()

    assert first != second
    assert Path(first).parent == tmp_path and Path(second).parent == tmp_path


@pytest.mark.skipif(sys.platform != "win32", reason="real PowerShell, nothing elevated or changed")
def test_the_real_catch_clause_writes_the_error_text(tmp_path):
    """Настоящий PowerShell без повышения и без правил: скрипт падает на
    throw, и catch должен оставить текст и тип исключения в файле."""
    error_file = tmp_path / "it's here.txt"
    system = firewall._WindowsSystem()
    script = firewall._guarded(["throw 'boom-for-the-test'"], str(error_file))

    code, _output = system.run(firewall._powershell(system), firewall._parameters(script))

    assert code == 1
    text = error_file.read_text(encoding="utf-8-sig")
    assert "boom-for-the-test" in text
    assert "System.Management.Automation.RuntimeException" in text


# ------------------------------------------------------------ diagnostics


def test_the_check_leaves_its_diagnostics_in_the_log(frozen_windows, caplog):
    """Эти строки читают на стенде, когда собранный exe видит не то, что
    запуск из исходников: что за exe, во что развернулся, что ответил
    запрос и как сравнивались запреты с тем же именем файла."""
    unrelated = _block(Name="Other{9}", Program=r"C:\Other\Other.exe")
    ours_short = _block(Program=SHORT)
    system = FakeSystem(output=_with_blocks(ours_short, unrelated), long_paths={SHORT: INSTALLED})

    with caplog.at_level("INFO", logger=firewall.__name__):
        firewall.check_rules(SHORT, system=system)

    lines = [record.getMessage() for record in caplog.records]
    [exe_line] = [line for line in lines if line.startswith("брандмауэр: exe ")]
    assert repr(SHORT) in exe_line and repr(INSTALLED) in exe_line
    [query_line] = [line for line in lines if line.startswith("брандмауэр: запрос правил: код")]
    assert f"код 0, {len(system.output)} байт" in query_line
    assert "брандмауэр: разобрано: правил 2, запрещающих 5, профилей 3" in lines
    [block_line] = [line for line in lines if line.startswith("брандмауэр: запрет ")]
    assert BLOCK_ID in block_line
    assert repr(SHORT) in block_line
    assert repr(INSTALLED.lower()) in block_line
    assert "Enabled True, Direction Inbound, Source Local; наш: True" in block_line
    # Три настоящих исходящих запрета без программы и чужой exe - не в журнале.
    assert "Other{9}" not in caplog.text


# ------------------------------------------------------------ the running program


FROZEN_BINARY = r"C:\Users\Operator\AppData\Local\Programs\Duo Input\DuoInput.exe"
#: Так Nuitka standalone заполняет sys.executable: python.exe рядом с
#: бинарником, которого на диске нет (стенд, 2026-09-27).
NUITKA_EXECUTABLE = r"C:\Users\Operator\AppData\Local\Programs\Duo Input\python.exe"


def test_the_running_program_is_the_loaded_image_not_sys_executable(monkeypatch):
    monkeypatch.setattr(firewall, "_is_windows", lambda: True)
    monkeypatch.setattr(firewall.sys, "executable", NUITKA_EXECUTABLE)
    monkeypatch.setattr(firewall, "_module_file_name", lambda: FROZEN_BINARY)

    assert firewall.running_program() == Path(FROZEN_BINARY)


def test_without_a_module_name_argv0_is_used_if_it_is_a_file(monkeypatch, tmp_path):
    binary = tmp_path / "DuoInput.exe"
    binary.write_bytes(b"")
    monkeypatch.setattr(firewall, "_is_windows", lambda: True)
    monkeypatch.setattr(firewall.sys, "executable", NUITKA_EXECUTABLE)
    monkeypatch.setattr(firewall, "_module_file_name", lambda: None)
    monkeypatch.setattr(firewall.sys, "argv", [str(binary), "--hidden"])

    assert firewall.running_program() == binary.resolve()


def test_sys_executable_is_the_last_resort(monkeypatch, tmp_path):
    monkeypatch.setattr(firewall, "_is_windows", lambda: True)
    monkeypatch.setattr(firewall.sys, "executable", NUITKA_EXECUTABLE)
    monkeypatch.setattr(firewall, "_module_file_name", lambda: None)
    monkeypatch.setattr(firewall.sys, "argv", [str(tmp_path / "missing.exe")])

    assert firewall.running_program() == Path(NUITKA_EXECUTABLE)


def test_outside_windows_the_running_program_is_sys_executable(monkeypatch):
    monkeypatch.setattr(firewall, "_is_windows", lambda: False)
    monkeypatch.setattr(firewall.sys, "executable", "/Applications/Duo Input.app/Contents/MacOS/DuoInput")
    monkeypatch.setattr(firewall, "_module_file_name", lambda: FROZEN_BINARY)

    assert firewall.running_program() == Path("/Applications/Duo Input.app/Contents/MacOS/DuoInput")


@pytest.mark.skipif(sys.platform != "win32", reason="GetModuleFileNameW is Windows-only")
def test_the_real_module_file_name_is_the_image_that_is_running():
    """Под venv sys.executable - Scripts\\python.exe, а загружен базовый
    интерпретатор: то же расхождение, что у собранного exe, в миниатюре."""
    name = firewall._module_file_name()

    assert name is not None
    assert Path(name).samefile(getattr(sys, "_base_executable", sys.executable))


@pytest.mark.skipif(sys.platform != "win32", reason="GetModuleFileNameW is Windows-only")
def test_the_real_module_file_name_grows_its_buffer(monkeypatch):
    """Путь длиннее начального буфера не должен обрезаться."""
    full = firewall._module_file_name()
    monkeypatch.setattr(firewall, "_MODULE_NAME_START", 8)

    assert firewall._module_file_name() == full
