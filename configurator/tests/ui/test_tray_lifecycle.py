"""Закрытие окна: решение продукта от 2026-09-03 - всегда уход в трей, а не
выход, независимо от того, включён ли общий буфер (§4 спецификации).

До этого решения резидентность была следствием включённой фичи: выключенный
общий буфер означал, что закрытие окна завершает процесс - см. историю этого
файла. Тесты ниже проверяют новое правило и его симметрию: обе настройки
общего буфера теперь ведут к одному и тому же поведению окна.
"""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent, QIcon

from duo_input.app import build_main_window
from duo_input.ui.tray import TrayIcon


def _settings(tmp_path, enabled: bool) -> QSettings:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("clipboard/enabled", enabled)
    return settings


def test_closing_hides_rather_than_quits_when_sharing_is_off(qtbot, tmp_path):
    """Новое поведение: раньше выключенный общий буфер означал закрытие
    процесса по нажатию крестика - теперь окно прячется в трей, как и при
    включённом общем буфере. Без правки это событие принималось бы
    (isAccepted() is True), и программа завершалась бы."""
    window = build_main_window(settings=_settings(tmp_path, False))
    qtbot.addWidget(window)
    window.show()

    window.close()

    assert window.isVisible() is False

    # isVisible() после close() был бы False даже если бы событие было
    # принято (Qt прячет окно перед разрушением) - это не отличало бы старое
    # поведение от нового. Отличает их accept()/ignore(), поэтому проверяем
    # именно его.
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() is False


def test_closing_hides_when_sharing_is_on(qtbot, tmp_path):
    """Симметричная проверка: включённый общий буфер вёл себя так уже раньше,
    и должен продолжать вести себя так же."""
    window = build_main_window(settings=_settings(tmp_path, True))
    qtbot.addWidget(window)
    window.show()

    window.close()

    assert window.isVisible() is False

    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() is False


def test_the_tray_reports_each_link_state_distinctly(qtbot):
    tray = TrayIcon()

    tray.set_link_state("connected")
    connected = tray.state_action.text()
    tray.set_link_state("disconnected")
    disconnected = tray.state_action.text()
    tray.set_link_state("blocked")
    blocked = tray.state_action.text()

    assert len({connected, disconnected, blocked}) == 3
    assert "брандмауэр" in blocked.lower()


def test_the_tray_toggle_emits_the_new_value(qtbot):
    tray = TrayIcon()
    values: list[bool] = []
    tray.sharing_toggled.connect(values.append)

    tray.sharing_action.setChecked(True)
    # PySide6 6.10 требует явно выбрать перегрузку triggered(bool):
    # безадресный .emit(True) резолвится в triggered() без аргумента и
    # падает с TypeError, хотя сигнал объявлен и как triggered(bool).
    tray.sharing_action.triggered[bool].emit(True)

    assert values == [True]


def test_the_files_action_is_a_working_toggle_not_a_disabled_placeholder(qtbot):
    tray = TrayIcon(QIcon(), None)

    assert tray.files_action.isEnabled()

    with qtbot.waitSignal(tray.files_toggled, timeout=1000) as blocker:
        tray.files_action.trigger()

    assert blocker.args == [True]


def test_setting_the_tray_files_check_programmatically_does_not_echo(qtbot):
    tray = TrayIcon(QIcon(), None)
    seen: list[bool] = []
    tray.files_toggled.connect(seen.append)

    tray.set_files_checked(True)

    assert seen == []
    assert tray.files_action.isChecked() is True

    with qtbot.waitSignal(tray.files_toggled, timeout=1000) as blocker:
        tray.files_action.trigger()

    assert blocker.args == [False]
