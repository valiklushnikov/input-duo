"""Значок в области уведомлений: единственное, что видно при скрытом окне.

Пункт "Передача файлов" присутствует и всегда выключен: он появится в
следующем этапе, а меню, которое меняет состав между версиями, читается хуже,
чем меню с честно недоступным пунктом.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

STATE_LABELS = {
    "connected": QCoreApplication.translate("TrayIcon", "Второй компьютер на связи"),
    "disconnected": QCoreApplication.translate("TrayIcon", "Нет связи со вторым компьютером"),
    "unpaired": QCoreApplication.translate("TrayIcon", "Компьютеры не связаны"),
    "searching": QCoreApplication.translate("TrayIcon", "Поиск второго компьютера"),
    # Отдельная строка, а не общее "нет связи": иначе пользователь пойдёт чинить
    # сеть, которая исправна, вместо того чтобы разрешить программе в брандмауэре.
    "blocked": QCoreApplication.translate(
        "TrayIcon", "Windows не разрешила подключение — проверьте брандмауэр"
    ),
    # Отдельная строка, а не общее "нет связи": повторные попытки здесь не
    # исправят ничего сами - без обновления второй машины связь не заработает.
    "protocol_mismatch": QCoreApplication.translate(
        "TrayIcon", "Обновите вторую машину — версии протокола различаются"
    ),
}


class TrayIcon(QSystemTrayIcon):
    """Открыть, показать состояние, включить и выключить, выйти."""

    open_requested = Signal()
    quit_requested = Signal()
    sharing_toggled = Signal(bool)

    def __init__(self, icon: QIcon | None = None, parent=None) -> None:
        super().__init__(parent)
        if icon is not None:
            self.setIcon(icon)

        self._menu = QMenu()

        self.open_action = QAction(self.tr("Открыть Duo Input"), self._menu)
        self.open_action.triggered.connect(self.open_requested)
        self._menu.addAction(self.open_action)

        self._menu.addSeparator()

        self.state_action = QAction(STATE_LABELS["unpaired"], self._menu)
        self.state_action.setEnabled(False)
        self._menu.addAction(self.state_action)

        self.sharing_action = QAction(self.tr("Общий буфер обмена"), self._menu)
        self.sharing_action.setCheckable(True)
        self.sharing_action.triggered.connect(self.sharing_toggled)
        self._menu.addAction(self.sharing_action)

        self.files_action = QAction(self.tr("Передача файлов"), self._menu)
        self.files_action.setCheckable(True)
        self.files_action.setEnabled(False)
        self._menu.addAction(self.files_action)

        self._menu.addSeparator()

        self.quit_action = QAction(self.tr("Выход"), self._menu)
        self.quit_action.triggered.connect(self.quit_requested)
        self._menu.addAction(self.quit_action)

        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)
        self.set_link_state("unpaired")

    def set_link_state(self, state: str) -> None:
        label = STATE_LABELS.get(state, STATE_LABELS["disconnected"])
        self.state_action.setText(label)
        self.setToolTip(f"Duo Input — {label}")

    def set_sharing_checked(self, enabled: bool) -> None:
        """Отразить состояние, не порождая новый sharing_toggled (C1: страница
        и трей обязаны показывать одно и то же, даже если его выставили не
        через сам трей)."""
        self.sharing_action.blockSignals(True)
        self.sharing_action.setChecked(enabled)
        self.sharing_action.blockSignals(False)

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_requested.emit()


__all__ = ["STATE_LABELS", "TrayIcon"]
