"""The application entry point and the window factory behind it."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from duo_input.app import ENTRY_POINT, build_main_window, main
from duo_input.device.service import DeviceService
from duo_input.ui.main_window import MainWindow

PYPROJECT = Path(__file__).resolve().parents[2] / "pyproject.toml"
#: То, что GetModuleFileNameW сообщает о собранной программе.
BINARY = r"C:\Program Files\Duo Input\DuoInput.exe"


def test_pyproject_declares_the_console_entry_point():
    document = tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))

    scripts = document["project"]["scripts"]

    assert scripts["duo-input-configurator"] == ENTRY_POINT


def test_entry_point_target_is_the_main_callable():
    module_name, _, attribute = ENTRY_POINT.partition(":")

    assert module_name == "duo_input.app"
    assert attribute == "main"
    assert callable(main)


def test_file_self_check_invokes_the_com_vtable_before_starting_qt(capsys, monkeypatch):
    from duo_input import app

    def qt_must_not_start(*_args, **_kwargs):
        raise AssertionError("the file self-check must remain windowless")

    with monkeypatch.context() as context:
        context.setattr(app.QApplication, "instance", qt_must_not_start)
        result = main(["DuoInput.exe", "--self-check-files"])

    output = capsys.readouterr().out.lower()
    assert result == 0
    assert "files: ok" in output
    assert "callback: addref 2, release 1" in output
    assert "descriptor: 592" in output


def test_build_main_window_produces_a_wired_shell(qtbot):
    service = DeviceService(timeout_ms=5000)

    window = build_main_window(service, transport_factory=lambda: None)
    qtbot.addWidget(window)

    assert isinstance(window, MainWindow)
    assert window.service is service
    assert window.session.dirty is False


def test_starting_a_window_still_adopts_a_device_that_answers(qtbot, tmp_path):
    """The startup sequence is a function so it can be tested at all.

    There is no file to reopen any more, so the only thing ``start_window``
    still does beyond showing the window is give the autoconnect its first
    synchronous chance to find a board - covered end to end here through the
    real entry point rather than through ``MainWindow`` directly.
    """
    from PySide6.QtCore import QSettings

    from duo_input.app import build_main_window, start_window
    from duo_input.device.emulator import U1Emulator
    from duo_input.device.service import DeviceService
    from duo_input.domain.text_compiler import compile_project_to_binary
    from duo_input.ui.models.project_session import ProjectSession, RenameProfile

    store = QSettings(str(tmp_path / "duo-input.ini"), QSettings.Format.IniFormat)
    store.clear()

    emulator = U1Emulator()
    on_board = ProjectSession.new().apply(RenameProfile(1, "On the board")).project
    emulator.install_active(compile_project_to_binary(on_board))

    window = build_main_window(
        DeviceService(timeout_ms=5000),
        transport_factory=lambda: emulator,
        settings=store,
    )
    qtbot.addWidget(window)
    start_window(window)

    qtbot.waitUntil(
        lambda: window.session.active_profile.name == "On the board", timeout=5000
    )


def test_starting_the_application_configures_the_log(tmp_path, monkeypatch):
    from duo_input.app import configure_application
    from duo_input.persistence.locations import log_directory

    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))

    path = configure_application()

    assert path.parent == log_directory()
    assert path.parent.is_dir()


def test_the_program_carries_an_icon(qapp):
    from duo_input.app import icon_path

    assert icon_path().is_file()
    assert icon_path().suffix == ".ico"


FIREWALL_SWITCHES = {
    "install": ("--install-firewall-rules", "apply_rules"),
    "remove": ("--remove-firewall-rules", "remove_rules"),
}


@pytest.mark.parametrize("succeeded, exit_code", [(True, 0), (False, 1)], ids=["ok", "failed"])
@pytest.mark.parametrize("argument, action", FIREWALL_SWITCHES.values(), ids=FIREWALL_SWITCHES.keys())
def test_firewall_switch_acts_and_exits_without_a_window(
    monkeypatch, argument, action, succeeded, exit_code
):
    """Установщик вызывает exe с ключом и ждёт код возврата: ни окна, ни
    трея, ни замка единственного экземпляра - работающая копия программы не
    должна ничего заметить."""
    import sys

    from duo_input import app
    from duo_input.persistence import firewall

    calls: list[tuple] = []

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("a firewall switch must not start the application")

    monkeypatch.setattr(app, "single_instance_lock", must_not_run)
    monkeypatch.setattr(app, "configure_application", lambda: calls.append(("log",)))
    # В собранном exe sys.executable - несуществующий python.exe (Nuitka).
    monkeypatch.setattr(app, "running_program", lambda: Path(BINARY))
    monkeypatch.setattr(
        firewall, "apply_rules", lambda exe: calls.append(("apply_rules", exe)) or succeeded
    )
    monkeypatch.setattr(
        firewall, "remove_rules", lambda: calls.append(("remove_rules",)) or succeeded
    )

    with monkeypatch.context() as context:
        # Только на время вызова: pytest-qt сам зовёт instance() при разборе.
        context.setattr(app.QApplication, "instance", must_not_run)
        result = main(["DuoInput.exe", argument])

    expected = ("apply_rules", Path(BINARY)) if action == "apply_rules" else (action,)
    assert result == exit_code
    assert calls == [("log",), expected]


def test_the_firewall_switches_are_the_ones_the_rule_module_defines():
    from duo_input.persistence import firewall

    assert [argument for argument, _ in FIREWALL_SWITCHES.values()] == [
        firewall.INSTALL_ARGUMENT,
        firewall.REMOVE_ARGUMENT,
    ]
    assert firewall.CHECK_ARGUMENT == "--check-firewall-rules"


def _status(**fields):
    from duo_input.persistence import firewall

    if "missing" in fields:
        fields["missing"] = firewall.required_rules("x")
    if "blocks" in fields:
        fields["blocks"] = (firewall.BlockRule("id", "name"),)
    return firewall.FirewallStatus(**fields)


CHECK_OUTCOMES = {
    "satisfied": ({}, 0),
    "rules missing": ({"missing": True}, 1),
    "local block": ({"blocks": True}, 1),
    "unknown": ({"known": False}, 1),
    "policy": ({"policy_blocked": True}, 2),
    "policy and missing": ({"policy_blocked": True, "missing": True}, 2),
}


@pytest.mark.parametrize("fields, exit_code", CHECK_OUTCOMES.values(), ids=CHECK_OUTCOMES.keys())
def test_the_check_switch_tells_the_installer_whether_elevating_would_help(
    monkeypatch, fields, exit_code
):
    """0 - всё в порядке, UAC не нужен (обновление поверх рабочей установки);
    1 - нужна починка, или проверить не удалось - тогда лучше поставить;
    2 - запрет политикой: повышение не поможет, UAC не показываем."""
    import sys

    from duo_input import app
    from duo_input.persistence import firewall

    calls: list[tuple] = []

    def must_not_run(*_args, **_kwargs):
        raise AssertionError("the check switch must not start the application or elevate")

    monkeypatch.setattr(app, "single_instance_lock", must_not_run)
    monkeypatch.setattr(app, "configure_application", lambda: calls.append(("log",)))
    monkeypatch.setattr(app, "running_program", lambda: Path(BINARY))
    monkeypatch.setattr(firewall, "apply_rules", must_not_run)
    monkeypatch.setattr(
        firewall, "check_rules", lambda exe: calls.append(("check_rules", exe)) or _status(**fields)
    )

    with monkeypatch.context() as context:
        context.setattr(app.QApplication, "instance", must_not_run)
        result = main(["DuoInput.exe", "--check-firewall-rules"])

    assert result == exit_code
    assert calls == [("log",), ("check_rules", Path(BINARY))]
