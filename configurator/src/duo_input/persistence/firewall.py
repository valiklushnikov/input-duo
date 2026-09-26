r"""Правила брандмауэра Windows, без которых второй компьютер не дозвонится.

Windows сама создаёт разрешающее правило для DuoInput.exe, только когда
человек ответил на её окно брандмауэра, - и только для профиля сети,
активного в ту минуту. Стоит сети стать "частной" (или папке с программой
переехать), входящие TCP 47654 (связь) и UDP 47655 (поиск) снова закрыты
действием по умолчанию, и Mac не может подключиться к Windows. Переключать
профиль сети - значит менять безопасность всей машины ради одной программы;
вместо этого программа заводит два собственных правила: только входящие,
только из локальной подсети, только для своего exe, для частных и
общедоступных сетей.

Проверка не требует прав администратора: ``Get-NetFirewallRule`` из
PowerShell читает активную политику от имени обычного пользователя. Выбран
PowerShell, а не ``netsh advfirewall firewall show rule``: подписи полей
netsh зависят от языка Windows, а его вывод - от кодовой страницы консоли
(на этой машине cp866). Здесь каждое значение печатается через ``ToString()``
перечисления - это имена из кода (``Inbound``, ``Allow``, ``Private, Public``),
а не переведённые подписи, - и отдаётся JSON-ом в явно выставленной UTF-8.

Запись - одна команда PowerShell под одним окном UAC: удалить правила с
нашими именами и создать их заново. Когда процесс уже повышен (так его
запускает установщик), та же команда идёт напрямую, без второго UAC.

Всё это имеет смысл только в собранном exe на Windows. Запуск из исходников
и macOS - пустые операции: ``missing_rules`` ничего не находит, ``apply`` и
``remove`` сразу сообщают об успехе.
"""

from __future__ import annotations

import json
import logging
import ntpath
import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Protocol

from duo_input.clipboard.coordinator import TCP_PORT
from duo_input.clipboard.discovery import BEACON_PORT

logger = logging.getLogger(__name__)

LINK_RULE_NAME = "Duo Input — связь"
DISCOVERY_RULE_NAME = "Duo Input — поиск"

#: Ключи командной строки, которыми установщик и деинсталлятор просят exe
#: поставить или убрать правила и сразу выйти, - логика правил живёт в одном
#: месте, а не копией в скрипте Inno Setup.
INSTALL_ARGUMENT = "--install-firewall-rules"
REMOVE_ARGUMENT = "--remove-firewall-rules"

#: Сколько ждать PowerShell, запущенного без повышения. Холодный старт
#: модуля NetSecurity на этой машине - около двух секунд.
RUN_TIMEOUT_SECONDS = 60


@dataclass(frozen=True)
class FirewallRule:
    """Одно входящее разрешающее правило для частных и общедоступных сетей
    из локальной подсети."""

    name: str
    protocol: str
    local_port: int
    program: str


class FirewallSystem(Protocol):
    """Всё, что выходит за пределы процесса, - чтобы тесты могли это подменить."""

    def run(self, executable: str, parameters: str) -> tuple[int | None, bytes]:
        """Запустить без повышения; ``None`` - процесс не запустился или завис."""

    def run_elevated(self, executable: str, parameters: str) -> int | None:
        """Запустить через UAC и дождаться; ``None`` - отказ в UAC или не запустился."""

    def is_elevated(self) -> bool:
        """Процесс уже работает с правами администратора."""


def required_rules(exe_path: object) -> tuple[FirewallRule, FirewallRule]:
    program = str(exe_path)
    return (
        FirewallRule(LINK_RULE_NAME, "TCP", TCP_PORT, program),
        FirewallRule(DISCOVERY_RULE_NAME, "UDP", BEACON_PORT, program),
    )


def missing_rules(exe_path: object, *, system: FirewallSystem | None = None) -> list[FirewallRule]:
    """Правила, которых нет или которые не пропустят второй компьютер.

    Правило с нашим именем, но выключенное, для другого профиля, порта или
    другого exe (папку перенесли), - всё равно что отсутствующее. Запрос,
    который не удался, не находит ничего: не видим - не просим UAC.
    """
    if not is_applicable():
        return []
    system = system or _SYSTEM
    rules = required_rules(exe_path)
    code, output = system.run(_powershell(), _parameters(_query_script(rules)))
    if code != 0:
        logger.warning("брандмауэр: запрос правил не удался (код %s)", code)
        return []
    try:
        entries = _parse_query_output(output)
    except ValueError:
        logger.warning("брандмауэр: не удалось разобрать ответ: %r", output[:200])
        return []
    missing = [
        rule for rule in rules if not any(_satisfies(entry, rule) for entry in entries)
    ]
    logger.info("брандмауэр: недостающие правила: %s", [rule.name for rule in missing])
    return missing


def apply_rules(exe_path: object, *, system: FirewallSystem | None = None) -> bool:
    """Заменить наши правила правильными - одно окно UAC, если не повышены."""
    if not is_applicable():
        return True
    script = _guarded(_delete_statement() + _create_statements(required_rules(exe_path)))
    return _run_privileged(script, system or _SYSTEM, "правила добавлены")


def remove_rules(*, system: FirewallSystem | None = None) -> bool:
    """Убрать наши правила (деинсталлятор); чужие не трогаются."""
    if not is_applicable():
        return True
    return _run_privileged(_guarded(_delete_statement()), system or _SYSTEM, "правила удалены")


def is_applicable() -> bool:
    return _is_windows() and _is_frozen()


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_frozen() -> bool:
    """Собранный exe: Nuitka кладёт ``__compiled__`` в каждый модуль,
    PyInstaller и подобные ставят ``sys.frozen``."""
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


# ------------------------------------------------------------ PowerShell


def _powershell() -> str:
    """Полный путь, а не поиск по PATH: команда может выполняться повышенной."""
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return ntpath.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


def _parameters(script: str) -> str:
    """Параметры powershell.exe; скрипт - один аргумент в двойных кавычках."""
    if '"' in script:
        raise ValueError("a double quote would end the -Command argument")
    return f'-NoProfile -NonInteractive -WindowStyle Hidden -Command "{script}"'


def _quote(text: str) -> str:
    """Строка PowerShell в одинарных кавычках. Он считает кавычкой и
    типографские ‘ ’ ‚ ‛ - каждая удваивается."""
    escaped = "".join(char * 2 if char in "'‘’‚‛" else char for char in text)
    return f"'{escaped}'"


def _names(rules) -> str:
    return ",".join(_quote(rule.name) for rule in rules)


def _query_script(rules) -> str:
    """Только чтение. Каждое значение - ``ToString()`` перечисления, то есть
    имя из кода, одинаковое на любом языке Windows; массив - всегда массив,
    даже из одного элемента или пустой."""
    return (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=New-Object Text.UTF8Encoding $false; "
        "$rules=@(Get-NetFirewallRule -PolicyStore ActiveStore -DisplayName "
        f"{_names(rules)} -ErrorAction SilentlyContinue); "
        "$out=@(foreach($r in $rules){ "
        "$a=$r|Get-NetFirewallApplicationFilter; "
        "$p=$r|Get-NetFirewallPortFilter; "
        "$d=$r|Get-NetFirewallAddressFilter; "
        "[pscustomobject]@{DisplayName=$r.DisplayName; "
        "Enabled=$r.Enabled.ToString(); Direction=$r.Direction.ToString(); "
        "Action=$r.Action.ToString(); Profile=$r.Profile.ToString(); "
        "Program=[string]$a.Program; Protocol=[string]$p.Protocol; "
        "LocalPort=@($p.LocalPort) -join ','; "
        "RemoteAddress=@($d.RemoteAddress) -join ','} }); "
        "ConvertTo-Json -InputObject $out -Depth 3 -Compress"
    )


def _delete_statement() -> list[str]:
    # Через Get | Remove, а не Remove -DisplayName: отсутствие правил - не
    # ошибка, а отказ в доступе при удалении - ошибка, и скрипт её вернёт.
    rules = required_rules("")
    return [
        f"Get-NetFirewallRule -DisplayName {_names(rules)} -ErrorAction SilentlyContinue"
        " | Remove-NetFirewallRule"
    ]


def _create_statements(rules) -> list[str]:
    return [
        f"New-NetFirewallRule -DisplayName {_quote(rule.name)} -Direction Inbound"
        " -Action Allow -Profile Private,Public -RemoteAddress LocalSubnet"
        f" -Protocol {rule.protocol} -LocalPort {rule.local_port}"
        f" -Program {_quote(rule.program)} | Out-Null"
        for rule in rules
    ]


def _guarded(statements: list[str]) -> str:
    """Код возврата процесса - единственное, что видно из-за UAC."""
    body = "; ".join([*statements, "exit 0"])
    return f"$ErrorActionPreference='Stop'; try {{ {body} }} catch {{ exit 1 }}"


def _run_privileged(script: str, system: FirewallSystem, success: str) -> bool:
    executable, parameters = _powershell(), _parameters(script)
    if system.is_elevated():
        code, _output = system.run(executable, parameters)
    else:
        code = system.run_elevated(executable, parameters)
    if code == 0:
        logger.info("брандмауэр: %s", success)
        return True
    logger.warning("брандмауэр: не получилось (код %s; None - отказ в UAC)", code)
    return False


# ------------------------------------------------------------ parsing


def _parse_query_output(output: bytes) -> list[dict]:
    """JSON-массив записей; всё прочее - ``ValueError`` (им же являются и
    ``UnicodeDecodeError``, и ``JSONDecodeError``)."""
    entries = json.loads(output.decode("utf-8"))
    if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
        raise ValueError("expected a JSON array of objects")
    return entries


def _parts(value: object) -> set[str]:
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def _same_program(stored: object, program: str) -> bool:
    def normal(path: str) -> str:
        return ntpath.normcase(ntpath.normpath(ntpath.expandvars(path)))

    return normal(str(stored or "")) == normal(program)


def _satisfies(entry: dict, rule: FirewallRule) -> bool:
    """Имя не сравнивается: запрос и так возвращает только правила с нашими
    именами, а правило, которое пропускает нужный трафик, годится под любым
    из них."""
    profiles = _parts(entry.get("Profile"))
    return (
        entry.get("Enabled") == "True"
        and entry.get("Direction") == "Inbound"
        and entry.get("Action") == "Allow"
        and ("Any" in profiles or {"Private", "Public"} <= profiles)
        and entry.get("Protocol") == rule.protocol
        and str(rule.local_port) in _parts(entry.get("LocalPort"))
        and "LocalSubnet" in _parts(entry.get("RemoteAddress"))
        and _same_program(entry.get("Program"), rule.program)
    )


# ------------------------------------------------------------ the real boundary


class _WindowsSystem:
    def run(self, executable: str, parameters: str) -> tuple[int | None, bytes]:
        try:
            completed = subprocess.run(
                f'"{executable}" {parameters}',
                stdin=subprocess.DEVNULL,
                capture_output=True,
                timeout=RUN_TIMEOUT_SECONDS,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        except (OSError, subprocess.TimeoutExpired):
            logger.exception("брандмауэр: не удалось запустить %s", executable)
            return None, b""
        return completed.returncode, completed.stdout

    def run_elevated(self, executable: str, parameters: str) -> int | None:
        return _shell_execute_and_wait("runas", executable, parameters)

    def is_elevated(self) -> bool:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())


def _shell_execute_and_wait(verb: str, executable: str, parameters: str) -> int | None:
    """ShellExecuteExW с ожиданием: код возврата, или ``None``, если процесс
    не запустился (для "runas" - в том числе человек отказал в UAC)."""
    import ctypes
    from ctypes import wintypes

    class ShellExecuteInfo(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("fMask", wintypes.ULONG),
            ("hwnd", wintypes.HWND),
            ("lpVerb", wintypes.LPCWSTR),
            ("lpFile", wintypes.LPCWSTR),
            ("lpParameters", wintypes.LPCWSTR),
            ("lpDirectory", wintypes.LPCWSTR),
            ("nShow", ctypes.c_int),
            ("hInstApp", wintypes.HINSTANCE),
            ("lpIDList", ctypes.c_void_p),
            ("lpClass", wintypes.LPCWSTR),
            ("hkeyClass", wintypes.HKEY),
            ("dwHotKey", wintypes.DWORD),
            ("hIconOrMonitor", wintypes.HANDLE),
            ("hProcess", wintypes.HANDLE),
        ]

    see_mask_nocloseprocess = 0x00000040
    see_mask_noasync = 0x00000100
    see_mask_flag_no_ui = 0x00000400
    sw_hide = 0
    infinite = 0xFFFFFFFF

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = see_mask_nocloseprocess | see_mask_noasync | see_mask_flag_no_ui
    info.lpVerb = verb
    info.lpFile = executable
    info.lpParameters = parameters
    info.nShow = sw_hide
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        logger.warning(
            "брандмауэр: %s %s не запущен (ошибка %s; 1223 - отказ в UAC)",
            verb,
            executable,
            ctypes.get_last_error(),
        )
        return None
    try:
        kernel32.WaitForSingleObject(info.hProcess, infinite)
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code)):
            return None
        return int(code.value)
    finally:
        kernel32.CloseHandle(info.hProcess)


_SYSTEM: FirewallSystem = _WindowsSystem()


__all__ = [
    "DISCOVERY_RULE_NAME",
    "INSTALL_ARGUMENT",
    "LINK_RULE_NAME",
    "REMOVE_ARGUMENT",
    "FirewallRule",
    "FirewallSystem",
    "apply_rules",
    "is_applicable",
    "missing_rules",
    "remove_rules",
    "required_rules",
]
