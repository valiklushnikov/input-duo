"""Проверка правил брандмауэра при старте общего буфера - через настоящие кнопки.

Сам модуль правил подменён (``app_module.firewall``): ни один тест не
запускает PowerShell и не вызывает UAC. Всё остальное - настоящее: окно,
страница, ``configure_runtime``, диалог и его кнопки, QSettings в файле.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QObject, QSettings, Qt, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from duo_input import app as app_module
from duo_input.app import build_main_window, configure_runtime
from duo_input.clipboard.coordinator import ClipboardCoordinator
from duo_input.persistence import firewall

DECLINED_KEY = "clipboard/firewall_prompt_declined"
PROMPT_TEXT = (
    "Чтобы второй компьютер мог подключиться, Windows должна разрешить "
    "Duo Input входящие соединения в локальной сети."
)
POLICY_TEXT = "Входящие соединения запрещены политикой администратора этого компьютера."


class FakeFirewall:
    """Модуль правил: отвечает из памяти и записывает, из какого потока звали."""

    FirewallStatus = firewall.FirewallStatus

    def __init__(self, *, present: bool = False, apply_result: bool = True) -> None:
        self.present = present
        self.apply_result = apply_result
        #: Если задан - check_rules отвечает им, а не по present.
        self.status: firewall.FirewallStatus | None = None
        self.check_threads: list[threading.Thread] = []
        self.checked: list[Path] = []
        self.check_gate: threading.Event | None = None
        self.applied: list[tuple[Path, threading.Thread, int | None]] = []
        self.apply_gate: threading.Event | None = None
        self.apply_error: Exception | None = None

    def is_applicable(self) -> bool:
        return True

    def check_rules(self, exe_path):
        self.checked.append(exe_path)
        self.check_threads.append(threading.current_thread())
        if self.check_gate is not None:
            self.check_gate.wait(10)
        if self.status is not None:
            return self.status
        if self.present:
            return firewall.FirewallStatus()
        return firewall.FirewallStatus(missing=firewall.required_rules(exe_path))

    def apply_rules(self, exe_path, hwnd=None) -> bool:
        self.applied.append((exe_path, threading.current_thread(), hwnd))
        if self.apply_gate is not None:
            self.apply_gate.wait(10)
        if self.apply_error is not None:
            raise self.apply_error
        if self.apply_result:
            self.present = True
            self.status = None
        return self.apply_result


class _QuietCoordinator(ClipboardCoordinator):
    def start(self) -> None:
        """Настоящий порт 47654 тесту не нужен (и может быть занят DuoInput.exe)."""


class _Backend(QObject):
    snapshot_taken = Signal(object)

    def start(self) -> None: ...

    def stop(self) -> None: ...


#: То, что GetModuleFileNameW сообщает о собранной программе; sys.executable
#: в ней - несуществующий python.exe рядом (Nuitka).
BINARY = Path(r"C:\Program Files\Duo Input\DuoInput.exe")


@pytest.fixture
def fake(monkeypatch, tmp_path):
    fake = FakeFirewall()
    monkeypatch.setattr(app_module, "firewall", fake)
    monkeypatch.setattr(app_module, "running_program", lambda: BINARY)
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    monkeypatch.setattr(app_module, "ClipboardCoordinator", _QuietCoordinator)
    monkeypatch.setattr(
        app_module, "create_backend", lambda _clipboard, parent=None: _Backend(parent)
    )
    return fake


@pytest.fixture
def launch(qapp, qtbot, tmp_path):
    """Запуск программы с общим буфером; повторный вызов - следующий запуск
    с теми же сохранёнными настройками."""
    runtimes = []

    def start(*, enabled: bool = True):
        settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
        settings.setValue("clipboard/enabled", enabled)
        window = build_main_window(settings=settings, transport_factory=lambda: None)
        qtbot.addWidget(window)
        configure_runtime(qapp, window, settings)
        runtimes.append(_runtime_of(qapp))
        return window, settings

    yield start
    for dialog in _prompts() + _prompts(POLICY_TEXT):
        dialog.close()
    for runtime in runtimes:
        try:
            runtime.stop()
        except RuntimeError:
            pass  # тест сам удалил объект (см. тест про закрытие во время проверки)


def _runtime_of(application):
    from duo_input.app import _ClipboardRuntime

    for child in reversed(application.children()):
        if isinstance(child, _ClipboardRuntime):
            return child
    raise AssertionError("_ClipboardRuntime was not created")


def _prompts(text: str = PROMPT_TEXT) -> list[QMessageBox]:
    return [
        widget
        for widget in QApplication.topLevelWidgets()
        if isinstance(widget, QMessageBox) and widget.isVisible() and widget.text() == text
    ]


def _button(dialog: QMessageBox, text: str):
    [button] = [button for button in dialog.buttons() if button.text() == text]
    return button


def _hint_shown(window) -> bool:
    return not window.clipboard_page.firewall_hint.isHidden()


def _wait_for_prompt(qtbot) -> QMessageBox:
    qtbot.waitUntil(lambda: len(_prompts()) == 1)
    return _prompts()[0]


def test_missing_rules_ask_once_and_allow_applies_them(qtbot, fake, launch):
    window, _settings = launch()

    dialog = _wait_for_prompt(qtbot)
    qtbot.mouseClick(_button(dialog, "Разрешить"), Qt.MouseButton.LeftButton)

    qtbot.waitUntil(lambda: fake.applied and not _hint_shown(window))
    [(executable, _thread, hwnd)] = fake.applied
    # Не sys.executable: в собранном exe это несуществующий python.exe.
    assert executable == BINARY
    assert fake.checked and set(fake.checked) == {BINARY}
    # UAC - над нашим окном, а не мигающей кнопкой на панели задач.
    assert hwnd == int(window.winId())
    assert _prompts() == []


def test_the_check_and_the_repair_run_off_the_interface_thread(qtbot, fake, launch):
    """Запрос правил - секунда-другая PowerShell, применение - ещё и UAC:
    ни то, ни другое не должно держать запуск."""
    launch()
    dialog = _wait_for_prompt(qtbot)
    qtbot.mouseClick(_button(dialog, "Разрешить"), Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(fake.check_threads) == 2)

    main = threading.main_thread()
    assert all(thread is not main for thread in fake.check_threads)
    assert fake.applied[0][1] is not main


def test_later_is_remembered_and_the_hint_offers_allow_on_the_next_start(
    qtbot, fake, launch
):
    window, settings = launch()
    dialog = _wait_for_prompt(qtbot)
    qtbot.mouseClick(_button(dialog, "Позже"), Qt.MouseButton.LeftButton)

    assert settings.value(DECLINED_KEY, False, type=bool) is True
    assert _hint_shown(window)
    assert fake.applied == []

    # Следующий запуск: окно больше не появляется, строка с кнопкой - на месте.
    window, _settings = launch()
    qtbot.waitUntil(lambda: len(fake.check_threads) == 2)
    qtbot.waitUntil(lambda: _hint_shown(window))
    assert _prompts() == []

    qtbot.mouseClick(window.clipboard_page.firewall_allow_button, Qt.MouseButton.LeftButton)

    qtbot.waitUntil(lambda: fake.applied and not _hint_shown(window))


def test_a_declined_uac_is_remembered_like_later(qtbot, fake, launch):
    fake.apply_result = False
    window, settings = launch()
    dialog = _wait_for_prompt(qtbot)
    qtbot.mouseClick(_button(dialog, "Разрешить"), Qt.MouseButton.LeftButton)

    qtbot.waitUntil(lambda: settings.value(DECLINED_KEY, False, type=bool) is True)
    qtbot.waitUntil(lambda: len(fake.check_threads) == 2)
    assert _hint_shown(window)
    assert window.clipboard_page.firewall_allow_button.isEnabled()
    events = [window.clipboard_page.events_list.item(i).text()
              for i in range(window.clipboard_page.events_list.count())]
    assert any("брандмауэр" in event for event in events)

    launch()
    qtbot.waitUntil(lambda: len(fake.check_threads) == 3)
    assert _prompts() == []


def test_closing_the_prompt_counts_as_later(qtbot, fake, launch):
    _window, settings = launch()
    dialog = _wait_for_prompt(qtbot)

    dialog.close()

    qtbot.waitUntil(lambda: _prompts() == [])
    assert settings.value(DECLINED_KEY, False, type=bool) is True
    assert fake.applied == []


def test_the_prompt_is_not_repeated_when_sharing_is_switched_off_and_on(
    qtbot, fake, launch
):
    """Даже если "Позже" не сохранилось (например, настройки не пишутся) -
    в пределах одного запуска второй раз не спрашиваем."""
    window, settings = launch()
    dialog = _wait_for_prompt(qtbot)
    qtbot.mouseClick(_button(dialog, "Позже"), Qt.MouseButton.LeftButton)
    settings.remove(DECLINED_KEY)

    window.clipboard_page.sharing_checkbox.click()
    window.clipboard_page.sharing_checkbox.click()

    qtbot.waitUntil(lambda: len(fake.check_threads) == 2)
    qtbot.wait(50)
    assert _prompts() == []
    assert _hint_shown(window)


def test_present_rules_mean_no_prompt_and_no_hint(qtbot, fake, launch):
    fake.present = True
    window, _settings = launch()

    qtbot.waitUntil(lambda: len(fake.check_threads) == 1)
    qtbot.wait(50)
    assert _prompts() == []
    assert not _hint_shown(window)


def test_nothing_is_checked_while_sharing_is_off(qtbot, fake, launch):
    """Сокеты открываются только с общим буфером - и правила нужны только тогда."""
    window, _settings = launch(enabled=False)

    qtbot.wait(50)
    assert fake.check_threads == []
    assert not _hint_shown(window)


def test_nothing_is_checked_outside_a_windows_build(qtbot, fake, launch, monkeypatch):
    """Из исходников и на macOS - ни потока, ни запроса."""
    started: list[object] = []
    monkeypatch.setattr(fake, "is_applicable", lambda: False)
    monkeypatch.setattr(app_module, "_run_in_background", started.append)

    launch()

    assert started == []


def test_the_hint_button_is_disabled_while_the_rules_are_being_applied(
    qtbot, fake, launch
):
    """Второе нажатие во время UAC запустило бы второе окно UAC."""
    fake.apply_gate = threading.Event()
    window, _settings = launch()
    dialog = _wait_for_prompt(qtbot)
    qtbot.mouseClick(_button(dialog, "Позже"), Qt.MouseButton.LeftButton)
    button = window.clipboard_page.firewall_allow_button

    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: len(fake.applied) == 1)
    assert not button.isEnabled()
    qtbot.mouseClick(button, Qt.MouseButton.LeftButton)

    fake.apply_gate.set()
    qtbot.waitUntil(lambda: not _hint_shown(window))
    assert len(fake.applied) == 1
    assert button.isEnabled()


def test_a_crash_while_applying_does_not_leave_the_button_dead(qtbot, fake, launch):
    fake.apply_error = OSError("ShellExecuteExW exploded")
    window, settings = launch()
    dialog = _wait_for_prompt(qtbot)
    qtbot.mouseClick(_button(dialog, "Разрешить"), Qt.MouseButton.LeftButton)

    qtbot.waitUntil(lambda: settings.value(DECLINED_KEY, False, type=bool) is True)
    assert window.clipboard_page.firewall_allow_button.isEnabled()
    assert _hint_shown(window)


def test_a_local_block_rule_asks_for_the_same_repair(qtbot, fake, launch):
    """Наши правила на месте, а Windows держит свой запрет: для человека
    это та же беда и та же кнопка."""
    fake.status = firewall.FirewallStatus(
        blocks=(firewall.BlockRule("TCP Query User{1}c:\\x\\duoinput.exe", "duoinput.exe"),)
    )
    window, _settings = launch()

    dialog = _wait_for_prompt(qtbot)
    assert _hint_shown(window)
    qtbot.mouseClick(_button(dialog, "Разрешить"), Qt.MouseButton.LeftButton)

    qtbot.waitUntil(lambda: fake.applied and not _hint_shown(window))


def test_a_policy_block_explains_and_offers_no_repair(qtbot, fake, launch):
    fake.status = firewall.FirewallStatus(policy_blocked=True)
    window, settings = launch()

    qtbot.waitUntil(lambda: len(_prompts(POLICY_TEXT)) == 1)
    [dialog] = _prompts(POLICY_TEXT)
    page = window.clipboard_page
    assert [button.text() for button in dialog.buttons()] == ["Понятно"]
    assert _hint_shown(window)
    assert page.firewall_label.text() == POLICY_TEXT
    assert page.firewall_allow_button.isHidden()

    qtbot.mouseClick(_button(dialog, "Понятно"), Qt.MouseButton.LeftButton)

    qtbot.waitUntil(lambda: _prompts(POLICY_TEXT) == [])
    assert fake.applied == []


def test_dismissing_the_policy_notice_does_not_silence_a_later_repair_prompt(
    qtbot, fake, launch
):
    """«Понятно» - это "прочитал", а не "не спрашивай меня о починке": когда
    политику сняли и появилась беда, которую программа может исправить,
    окно с «Разрешить»/«Позже» обязано появиться."""
    fake.status = firewall.FirewallStatus(policy_blocked=True)
    _window, settings = launch()
    qtbot.waitUntil(lambda: len(_prompts(POLICY_TEXT)) == 1)
    [notice] = _prompts(POLICY_TEXT)
    qtbot.mouseClick(_button(notice, "Понятно"), Qt.MouseButton.LeftButton)
    qtbot.waitUntil(lambda: _prompts(POLICY_TEXT) == [])
    assert settings.value(DECLINED_KEY, False, type=bool) is False

    fake.status = firewall.FirewallStatus(missing=firewall.required_rules("x"))
    launch()

    dialog = _wait_for_prompt(qtbot)
    assert {button.text() for button in dialog.buttons()} == {"Разрешить", "Позже"}


def test_a_policy_block_wins_over_a_repairable_problem(qtbot, fake, launch):
    """Поставить наши правила можно, но связи это не даст - не обещаем."""
    fake.status = firewall.FirewallStatus(
        missing=firewall.required_rules("x"), policy_blocked=True
    )
    window, _settings = launch()

    qtbot.waitUntil(lambda: len(_prompts(POLICY_TEXT)) == 1)
    assert _prompts() == []
    assert window.clipboard_page.firewall_allow_button.isHidden()


def test_the_repair_hint_shows_its_own_text_and_button(qtbot, fake, launch):
    window, _settings = launch()
    dialog = _wait_for_prompt(qtbot)
    qtbot.mouseClick(_button(dialog, "Позже"), Qt.MouseButton.LeftButton)
    page = window.clipboard_page

    assert page.firewall_label.text() == (
        "Windows не разрешает Duo Input входящие соединения в локальной "
        "сети - второй компьютер не сможет подключиться."
    )
    assert not page.firewall_allow_button.isHidden()


def test_an_unknown_status_shows_nothing(qtbot, fake, launch):
    """Запрос не удался - программа не знает, есть ли беда, и не пугает."""
    fake.status = firewall.FirewallStatus(known=False)
    window, _settings = launch()

    qtbot.waitUntil(lambda: len(fake.check_threads) == 1)
    qtbot.wait(50)
    assert _prompts() == [] and _prompts(POLICY_TEXT) == []
    assert not _hint_shown(window)


def test_an_answer_that_arrives_after_the_runtime_is_gone_is_dropped_quietly(
    qtbot, fake, launch, monkeypatch
):
    """Программу закрыли, пока PowerShell ещё отвечал: фоновый поток не
    должен падать на излучении сигнала удалённого объекта."""
    import shiboken6

    threads: list[threading.Thread] = []
    errors: list[BaseException] = []

    def start_thread(work) -> None:
        thread = threading.Thread(target=work, daemon=True)
        threads.append(thread)
        thread.start()

    monkeypatch.setattr(app_module, "_run_in_background", start_thread)
    monkeypatch.setattr(threading, "excepthook", lambda args: errors.append(args.exc_value))
    fake.check_gate = threading.Event()
    launch()
    runtime = _runtime_of(QApplication.instance())
    qtbot.waitUntil(lambda: len(fake.check_threads) == 1)

    runtime.stop()
    shiboken6.delete(runtime)
    fake.check_gate.set()
    threads[0].join(10)

    assert not threads[0].is_alive()
    assert errors == []


def test_a_policy_notice_closed_by_the_program_itself_does_not_apply(qtbot, fake, launch):
    """Закрытие без кнопки (например, при выходе) даёт clickedButton() None -
    у объяснения о политике кнопки «Разрешить» нет, и None не должен сойти
    за неё."""
    fake.status = firewall.FirewallStatus(policy_blocked=True)
    launch()
    qtbot.waitUntil(lambda: len(_prompts(POLICY_TEXT)) == 1)
    [dialog] = _prompts(POLICY_TEXT)

    dialog.done(0)

    qtbot.waitUntil(lambda: _prompts(POLICY_TEXT) == [])
    qtbot.wait(50)
    assert fake.applied == []
