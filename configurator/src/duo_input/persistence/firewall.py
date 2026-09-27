r"""Правила брандмауэра Windows, без которых второй компьютер не дозвонится.

Windows сама создаёт разрешающее правило для DuoInput.exe, только когда
человек ответил на её окно брандмауэра, - и только для профиля сети,
активного в ту минуту. Стоит сети стать "частной" (или папке с программой
переехать), входящие TCP 47654 (связь) и UDP 47655 (поиск) снова закрыты
действием по умолчанию, и Mac не может подключиться к Windows. Переключать
профиль сети - значит менять безопасность всей машины ради одной программы;
вместо этого программа заводит два собственных правила: только входящие,
только из локальной подсети, только для своего exe, для частных и
общедоступных сетей. У правил постоянные имена-идентификаторы
(``DuoInput-Link``, ``DuoInput-Discovery``) и группа ``Duo Input``; русские
отображаемые имена - только для человека.

Разрешающее правило не помогает, если есть запрещающее: нажми человек
когда-то "Отмена" в окне брандмауэра Windows, и она заводит для exe правила
Block, которые важнее любого Allow. Такие правила - только локальные, только
входящие, только для ровно нашего exe - удаляются в том же повышенном
скрипте. Запрет из групповой политики (правило Block из GPO или политика,
отключающая локальные правила) программа не трогает и починить не может -
о нём сообщается отдельно.

Проверка не требует прав администратора: ``Get-NetFirewallRule`` из
PowerShell читает активную политику от имени обычного пользователя. Выбран
PowerShell, а не ``netsh advfirewall firewall show rule``: подписи полей
netsh зависят от языка Windows, а его вывод - от кодовой страницы консоли
(на этой машине cp866). Здесь каждое значение печатается через ``ToString()``
перечисления - это имена из кода (``Inbound``, ``Allow``, ``Private, Public``,
``Local``), а не переведённые подписи, - и отдаётся JSON-ом в явно
выставленной UTF-8.

Запись - одна команда PowerShell под одним окном UAC. Когда процесс уже
повышен (так его запускает установщик), та же команда идёт напрямую, без
второго UAC.

Всё это имеет смысл только в собранном exe на Windows. Запуск из исходников
и macOS - пустые операции: ``check_rules`` сообщает, что всё в порядке,
``apply`` и ``remove`` сразу сообщают об успехе.
"""

from __future__ import annotations

import json
import logging
import ntpath
import re
import subprocess
import sys
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from duo_input.clipboard.coordinator import TCP_PORT
from duo_input.clipboard.discovery import BEACON_PORT

logger = logging.getLogger(__name__)

LINK_RULE_ID = "DuoInput-Link"
DISCOVERY_RULE_ID = "DuoInput-Discovery"
LINK_RULE_NAME = "Duo Input — связь"
DISCOVERY_RULE_NAME = "Duo Input — поиск"
RULE_GROUP = "Duo Input"

# Ключи командной строки, которыми установщик и деинсталлятор просят exe
# проверить, поставить или убрать правила и сразу выйти, - логика правил
# живёт в одном месте, а не копией в скрипте Inno Setup.
INSTALL_ARGUMENT = "--install-firewall-rules"
REMOVE_ARGUMENT = "--remove-firewall-rules"
CHECK_ARGUMENT = "--check-firewall-rules"

# Сколько ждать PowerShell, запущенного без повышения. Холодный старт
# модуля NetSecurity на этой машине - около двух секунд.
RUN_TIMEOUT_SECONDS = 60

# Профили, для которых ставятся наши правила: политика, отключающая
# локальные правила в одном из них, делает наши правила там мёртвыми.
_OUR_PROFILES = ("Private", "Public")


@dataclass(frozen=True)
class FirewallRule:
    """Одно входящее разрешающее правило для частных и общедоступных сетей
    из локальной подсети."""

    id: str
    name: str
    protocol: str
    local_port: int
    program: str


@dataclass(frozen=True)
class BlockRule:
    """Локальное входящее запрещающее правило для нашего exe - такие
    удаляются при починке. ``id`` - постоянное имя правила (``-Name``),
    ``name`` - отображаемое, для журнала."""

    id: str
    name: str


@dataclass(frozen=True)
class FirewallStatus:
    """Что показал запрос.

    ``known`` - запрос удался; ``missing`` - наших правил нет или они не
    годятся; ``blocks`` - локальные запреты, которые починка уберёт;
    ``policy_blocked`` - запрет из групповой политики, который программа
    убрать не может.
    """

    missing: tuple[FirewallRule, ...] = ()
    blocks: tuple[BlockRule, ...] = ()
    policy_blocked: bool = False
    known: bool = True

    @property
    def needs_repair(self) -> bool:
        return bool(self.missing or self.blocks)

    @property
    def satisfied(self) -> bool:
        return self.known and not self.needs_repair and not self.policy_blocked


class FirewallSystem(Protocol):
    """Всё, что выходит за пределы процесса, - чтобы тесты могли это подменить."""

    def run(self, executable: str, parameters: str) -> tuple[int | None, bytes]:
        """Запустить без повышения; ``None`` - процесс не запустился или завис."""

    def run_elevated(self, executable: str, parameters: str, hwnd: int | None) -> int | None:
        """Запустить через UAC и дождаться; ``None`` - отказ в UAC или не запустился."""

    def is_elevated(self) -> bool:
        """Процесс уже работает с правами администратора."""

    def system_directory(self) -> str:
        """Системный каталог Windows (GetSystemDirectoryW)."""

    def long_path(self, path: str) -> str:
        """Длинная форма пути существующего файла (GetLongPathNameW);
        несуществующий - как есть."""


def required_rules(exe_path: object) -> tuple[FirewallRule, FirewallRule]:
    program = str(exe_path)
    return (
        FirewallRule(LINK_RULE_ID, LINK_RULE_NAME, "TCP", TCP_PORT, program),
        FirewallRule(DISCOVERY_RULE_ID, DISCOVERY_RULE_NAME, "UDP", BEACON_PORT, program),
    )


def check_rules(exe_path: object, *, system: FirewallSystem | None = None) -> FirewallStatus:
    """Пропустит ли Windows второй компьютер к этому exe.

    Правило с нашим именем, но выключенное, для другого профиля, порта или
    другого exe (папку перенесли), - всё равно что отсутствующее. Запрос,
    который не удался, даёт ``known=False``: программа по нему UAC не
    просит, установщик - просит.
    """
    if not is_applicable():
        return FirewallStatus()
    system = system or _SYSTEM
    # Короткий путь 8.3 здесь не разворачивается: _same_program разворачивает
    # обе стороны каждого сравнения.
    program = str(exe_path)
    logger.info(
        "брандмауэр: exe %r, длинный путь %r", program, _long_exe_path(program, system)
    )
    rules = required_rules(program)
    code, output = system.run(_powershell(system), _parameters(_query_script(rules)))
    logger.info("брандмауэр: запрос правил: код %s, %d байт", code, len(output))
    if code != 0:
        logger.warning("брандмауэр: запрос правил не удался (код %s)", code)
        return FirewallStatus(known=False)
    try:
        answer = _parse_query_output(output)
    except ValueError:
        logger.warning("брандмауэр: не удалось разобрать ответ: %r", output[:200])
        return FirewallStatus(known=False)
    logger.info(
        "брандмауэр: разобрано: правил %d, запрещающих %d, профилей %d",
        len(answer["Rules"]),
        len(answer["Blocks"]),
        len(answer["Profiles"]),
    )

    missing = tuple(
        rule
        for rule in rules
        if not any(_satisfies(entry, rule, system) for entry in answer["Rules"])
    )
    blocks: list[BlockRule] = []
    policy_blocked = False
    for entry in answer["Blocks"]:
        _log_block_candidate(entry, program, system)
        if not _blocks_us(entry, program, system):
            continue
        if entry.get("Source") == "Local":
            blocks.append(BlockRule(str(entry.get("Name")), str(entry.get("DisplayName"))))
        else:
            policy_blocked = True
    for profile in answer["Profiles"]:
        if profile.get("Name") in _OUR_PROFILES and profile.get("AllowLocalFirewallRules") == "False":
            policy_blocked = True

    status = FirewallStatus(missing, tuple(blocks), policy_blocked)
    logger.info(
        "брандмауэр: недостающие правила %s, локальные запреты %s, запрет политикой: %s",
        [rule.id for rule in missing],
        [f"{block.name} ({block.id})" for block in blocks],
        policy_blocked,
    )
    return status


def apply_rules(
    exe_path: object, *, system: FirewallSystem | None = None, hwnd: int | None = None
) -> bool:
    """Одно окно UAC (если не повышены): снять локальные запреты для нашего
    exe, найденные только что, и заменить наши правила правильными.

    ``hwnd`` - окно, над которым Windows покажет UAC; без него окно UAC
    может остаться мигающей кнопкой на панели задач.
    """
    if not is_applicable():
        return True
    system = system or _SYSTEM
    program = _long_exe_path(exe_path, system)
    blocks = check_rules(program, system=system).blocks
    for block in blocks:
        logger.info("брандмауэр: будет удалено запрещающее правило %s (%s)", block.name, block.id)
    statements = _delete_ours() + _delete_blocks(blocks) + _create_statements(required_rules(program))
    return _run_privileged(statements, system, hwnd, "правила добавлены")


def remove_rules(*, system: FirewallSystem | None = None) -> bool:
    """Убрать наши правила (деинсталлятор); чужие не трогаются."""
    if not is_applicable():
        return True
    system = system or _SYSTEM
    return _run_privileged(_delete_ours(), system, None, "правила удалены")


def _long_exe_path(exe_path: object, system: FirewallSystem) -> str:
    """Собранный exe, запущенный по пути 8.3, видит в sys.executable
    ``DOCUME~1\\...``, а Windows хранит в правилах длинный путь - и в
    правилах, которые мы создаём, должен стоять он же."""
    return system.long_path(str(exe_path))


def _error_file_path() -> str:
    """Свой файл на каждый запуск, во временной папке пользователя:
    повышенный процесс работает от того же пользователя."""
    return str(Path(tempfile.gettempdir()) / f"duo-input-firewall-{uuid.uuid4().hex}.txt")


def is_applicable() -> bool:
    return _is_windows() and _is_frozen()


def _is_windows() -> bool:
    return sys.platform == "win32"


def _is_frozen() -> bool:
    """Собранный exe: Nuitka кладёт ``__compiled__`` в каждый модуль,
    PyInstaller и подобные ставят ``sys.frozen``."""
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


# ------------------------------------------------------------ PowerShell


def _powershell(system: FirewallSystem) -> str:
    """Полный путь от системного каталога, а не поиск по PATH и не
    переменная окружения: команда может выполняться повышенной."""
    return ntpath.join(system.system_directory(), "WindowsPowerShell", "v1.0", "powershell.exe")


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


def _literal_name(name: str) -> str:
    """``-Name`` принимает шаблон: ``[``, ``]``, ``*``, ``?`` в имени правила
    (а имена правил Windows содержат путь к exe) экранируются обратной
    кавычкой, чтобы совпало ровно это имя, а не похожие."""
    return _quote(re.sub(r"([\[\]*?`])", r"`\1", name))


def _our_ids() -> str:
    return ",".join(_quote(rule.id) for rule in required_rules(""))


def _query_script(rules) -> str:
    """Только чтение. Наши правила - по постоянному имени; все запрещающие
    правила - целиком (их немного), разбирает их Python. Каждое значение -
    ``ToString()`` перечисления, то есть имя из кода, одинаковое на любом
    языке Windows."""
    ids = ",".join(_quote(rule.id) for rule in rules)
    return (
        "$ErrorActionPreference='Stop'; "
        "[Console]::OutputEncoding=New-Object Text.UTF8Encoding $false; "
        "function D($r){ "
        "$a=$r|Get-NetFirewallApplicationFilter; "
        "$p=$r|Get-NetFirewallPortFilter; "
        "$d=$r|Get-NetFirewallAddressFilter; "
        "[pscustomobject]@{Name=$r.Name; DisplayName=$r.DisplayName; "
        "Enabled=$r.Enabled.ToString(); Direction=$r.Direction.ToString(); "
        "Action=$r.Action.ToString(); Profile=$r.Profile.ToString(); "
        "Source=$r.PolicyStoreSourceType.ToString(); "
        "Program=[string]$a.Program; Protocol=[string]$p.Protocol; "
        "LocalPort=@($p.LocalPort) -join ','; "
        "RemoteAddress=@($d.RemoteAddress) -join ','} }; "
        f"$rules=@(Get-NetFirewallRule -PolicyStore ActiveStore -Name {ids}"
        " -ErrorAction SilentlyContinue | ForEach-Object { D $_ }); "
        "$blocks=@(Get-NetFirewallRule -PolicyStore ActiveStore -Action Block"
        " -ErrorAction SilentlyContinue | ForEach-Object { D $_ }); "
        "$profiles=@(Get-NetFirewallProfile -PolicyStore ActiveStore | ForEach-Object { "
        "[pscustomobject]@{Name=$_.Name; "
        "AllowLocalFirewallRules=$_.AllowLocalFirewallRules.ToString()} }); "
        "ConvertTo-Json -InputObject ([pscustomobject]@{Rules=$rules; Blocks=$blocks;"
        " Profiles=$profiles}) -Depth 4 -Compress"
    )


def _delete_ours() -> list[str]:
    # Через Get | Remove, а не Remove -Name: отсутствие правил - не ошибка,
    # а отказ в доступе при удалении - ошибка, и скрипт её вернёт.
    return [
        f"Get-NetFirewallRule -Name {_our_ids()} -ErrorAction SilentlyContinue"
        " | Remove-NetFirewallRule"
    ]


def _delete_blocks(blocks) -> list[str]:
    # Get без -PolicyStore читает только локальное хранилище (PersistentStore):
    # правило из групповой политики этой командой не найти и не удалить.
    # Where-Object - на случай, если между чтением и UAC правило с этим именем
    # успели превратить в разрешающее или исходящее: удаляем только то, что
    # по-прежнему запрещает входящие.
    return [
        f"Get-NetFirewallRule -Name {_literal_name(block.id)} -ErrorAction SilentlyContinue"
        " | Where-Object { $_.Action -eq 'Block' -and $_.Direction -eq 'Inbound' }"
        " | Remove-NetFirewallRule"
        for block in blocks
    ]


def _create_statements(rules) -> list[str]:
    return [
        f"New-NetFirewallRule -Name {_quote(rule.id)} -DisplayName {_quote(rule.name)}"
        f" -Group {_quote(RULE_GROUP)} -Direction Inbound"
        " -Action Allow -Profile Private,Public -RemoteAddress LocalSubnet"
        f" -Protocol {rule.protocol} -LocalPort {rule.local_port}"
        f" -Program {_quote(rule.program)} | Out-Null"
        for rule in rules
    ]


def _guarded(statements: list[str], error_file: str) -> str:
    """Из-за UAC виден только код возврата процесса - поэтому текст и тип
    исключения скрипт кладёт в ``error_file``. Если не вышло и это, код 1
    всё равно остаётся."""
    body = "; ".join([*statements, "exit 0"])
    report = (
        "(($_ | Out-String) + $_.Exception.GetType().FullName)"
        f" | Out-File -LiteralPath {_quote(error_file)} -Encoding UTF8"
    )
    return (
        f"$ErrorActionPreference='Stop'; try {{ {body} }}"
        f" catch {{ try {{ {report} }} catch {{}}; exit 1 }}"
    )


def _run_privileged(
    statements: list[str], system: FirewallSystem, hwnd: int | None, success: str
) -> bool:
    error_file = Path(_error_file_path())
    executable, parameters = _powershell(system), _parameters(_guarded(statements, str(error_file)))
    try:
        if system.is_elevated():
            code, _output = system.run(executable, parameters)
        else:
            code = system.run_elevated(executable, parameters, hwnd)
        if code == 0:
            logger.info("брандмауэр: %s", success)
            return True
        logger.warning(
            "брандмауэр: не получилось (код %s; None - отказ в UAC): %s",
            code,
            _read_error(error_file),
        )
        return False
    finally:
        try:
            error_file.unlink(missing_ok=True)
        except OSError:
            logger.warning("брандмауэр: не удалось удалить %s", error_file)


def _read_error(error_file: Path) -> str:
    # Out-File -Encoding UTF8 в Windows PowerShell пишет BOM.
    try:
        return error_file.read_text(encoding="utf-8-sig", errors="replace").strip()
    except OSError:
        return "подробностей нет"


# ------------------------------------------------------------ parsing


def _parse_query_output(output: bytes) -> dict[str, list[dict]]:
    """Объект с тремя массивами записей; всё прочее - ``ValueError`` (им же
    являются и ``UnicodeDecodeError``, и ``JSONDecodeError``)."""
    answer = json.loads(output.decode("utf-8"))
    if not isinstance(answer, dict):
        raise ValueError("expected a JSON object")
    for key in ("Rules", "Blocks", "Profiles"):
        entries = answer.get(key)
        if not isinstance(entries, list) or not all(isinstance(e, dict) for e in entries):
            raise ValueError(f"expected {key} to be a JSON array of objects")
    return answer


def _parts(value: object) -> set[str]:
    return {part.strip() for part in str(value or "").split(",") if part.strip()}


def _normal_program(path: object, system: FirewallSystem) -> str:
    """Переменные окружения, короткие имена 8.3 (если файл есть) и регистр -
    не повод считать программы разными."""
    expanded = ntpath.normpath(ntpath.expandvars(str(path or "")))
    return ntpath.normcase(ntpath.normpath(system.long_path(expanded)))


def _same_program(stored: object, program: str, system: FirewallSystem) -> bool:
    return _normal_program(stored, system) == _normal_program(program, system)


def _log_block_candidate(entry: dict, program: str, system: FirewallSystem) -> None:
    """Одна строка на каждое запрещающее правило с тем же ИМЕНЕМ файла, что у
    нашего exe: что хранится, что с чем сравнили и чем кончилось. Остальные
    запреты (их на машине бывают сотни) в журнал не попадают."""
    stored = str(entry.get("Program") or "")
    if ntpath.basename(stored).lower() != ntpath.basename(program).lower():
        return
    logger.info(
        "брандмауэр: запрет %s: Program %r; сравнение %r с %r; "
        "Enabled %s, Direction %s, Source %s; наш: %s",
        entry.get("Name"),
        stored,
        _normal_program(stored, system),
        _normal_program(program, system),
        entry.get("Enabled"),
        entry.get("Direction"),
        entry.get("Source"),
        _blocks_us(entry, program, system),
    )


def _satisfies(entry: dict, rule: FirewallRule, system: FirewallSystem) -> bool:
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
        and _same_program(entry.get("Program"), rule.program, system)
    )


def _blocks_us(entry: dict, program: str, system: FirewallSystem) -> bool:
    """Включённое входящее запрещающее правило ровно для нашего exe. Правило
    без программы (``Any``) - чужая забота: его не трогаем и не считаем.
    Action не сравнивается: запрос возвращает только правила Block."""
    return (
        entry.get("Enabled") == "True"
        and entry.get("Direction") == "Inbound"
        and _same_program(entry.get("Program"), program, system)
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

    def run_elevated(self, executable: str, parameters: str, hwnd: int | None) -> int | None:
        return _shell_execute_and_wait("runas", executable, parameters, hwnd)

    def is_elevated(self) -> bool:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())

    def system_directory(self) -> str:
        import ctypes

        buffer = ctypes.create_unicode_buffer(260)
        length = ctypes.windll.kernel32.GetSystemDirectoryW(buffer, len(buffer))
        if not 0 < length < len(buffer):
            raise OSError("GetSystemDirectoryW failed")
        return buffer.value

    def long_path(self, path: str) -> str:
        import ctypes
        from ctypes import wintypes

        function = ctypes.windll.kernel32.GetLongPathNameW
        function.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        function.restype = wintypes.DWORD
        buffer = ctypes.create_unicode_buffer(32768)
        length = function(path, buffer, len(buffer))
        # 0 - файла нет (или путь не разобрать): сравниваем как есть.
        return buffer.value if 0 < length < len(buffer) else path


def _shell_execute_and_wait(
    verb: str, executable: str, parameters: str, hwnd: int | None = None
) -> int | None:
    """ShellExecuteExW с ожиданием: код возврата, или ``None``, если процесс
    не запустился (для "runas" - в том числе человек отказал в UAC).

    Вызывается из рабочего потока, поэтому COM на нём инициализируется
    здесь же: ShellExecuteEx этого требует."""
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
    coinit_apartmentthreaded = 0x2
    coinit_disable_ole1dde = 0x4
    sw_hide = 0
    infinite = 0xFFFFFFFF

    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    ole32 = ctypes.WinDLL("ole32")
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    ole32.CoInitializeEx.argtypes = [ctypes.c_void_p, wintypes.DWORD]
    ole32.CoInitializeEx.restype = ctypes.c_long
    ole32.CoUninitialize.argtypes = []
    ole32.CoUninitialize.restype = None

    info = ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = see_mask_nocloseprocess | see_mask_noasync | see_mask_flag_no_ui
    info.hwnd = hwnd
    info.lpVerb = verb
    info.lpFile = executable
    info.lpParameters = parameters
    info.nShow = sw_hide

    # S_OK (0) и S_FALSE (1) - COM на потоке наш, и его надо отпустить;
    # RPC_E_CHANGED_MODE - поток уже в другом режиме, отпускать не нам.
    initialized = ole32.CoInitializeEx(None, coinit_apartmentthreaded | coinit_disable_ole1dde) in (0, 1)
    try:
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
    finally:
        if initialized:
            ole32.CoUninitialize()


_SYSTEM: FirewallSystem = _WindowsSystem()


__all__ = [
    "CHECK_ARGUMENT",
    "DISCOVERY_RULE_ID",
    "DISCOVERY_RULE_NAME",
    "INSTALL_ARGUMENT",
    "LINK_RULE_ID",
    "LINK_RULE_NAME",
    "REMOVE_ARGUMENT",
    "RULE_GROUP",
    "BlockRule",
    "FirewallRule",
    "FirewallStatus",
    "FirewallSystem",
    "apply_rules",
    "check_rules",
    "is_applicable",
    "remove_rules",
    "required_rules",
]
