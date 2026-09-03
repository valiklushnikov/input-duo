"""Закрытие окна: выход или уход в трей, в зависимости от того, включена ли фича."""

from __future__ import annotations

from PySide6.QtCore import QSettings
from PySide6.QtGui import QCloseEvent

from duo_input.app import build_main_window
from duo_input.ui.tray import TrayIcon


def _settings(tmp_path, enabled: bool) -> QSettings:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("clipboard/enabled", enabled)
    return settings


def test_closing_quits_when_sharing_is_off(qtbot, tmp_path):
    window = build_main_window(settings=_settings(tmp_path, False))
    qtbot.addWidget(window)

    assert window.background_mode is False

    # isVisible() после close() был бы False в обеих ветках (Qt прячет окно
    # и когда событие принято, и когда оно отвергнуто, но окно спрятано
    # вручную) - это не отличает ветки друг от друга. Отличает их
    # accept()/ignore() события, поэтому проверяем именно его: мутация
    # "всегда уходить в трей" должна уронить эту проверку.
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted() is True


def test_closing_hides_when_sharing_is_on(qtbot, tmp_path):
    window = build_main_window(settings=_settings(tmp_path, True))
    qtbot.addWidget(window)
    window.show()

    window.close()

    assert window.background_mode is True
    assert window.isVisible() is False

    # Симметричная проверка: мутация "всегда закрываться насовсем" должна
    # уронить именно эту ветку, а не только предыдущий тест.
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
