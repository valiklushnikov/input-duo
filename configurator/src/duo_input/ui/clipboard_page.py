"""Страница общего буфера: состояние, связывание и два переключателя.

Отпечаток показан целиком не для красоты: это то самое число, по которому
человек может убедиться, что связан именно с тем компьютером, с которым думал.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from duo_input.clipboard.trust import TrustedPeer
from duo_input.ui.tray import STATE_LABELS

#: Сколько последних событий держим на экране - не журнал целиком, а то, что
#: помогает понять, что произошло только что (§12).
EVENTS_LIMIT = 20


def human_bytes(value: int) -> str:
    """Format a byte count with readable binary units."""
    if value < 1024:
        return f"{value} \u0411"
    for unit in ("\u041a\u0411", "\u041c\u0411", "\u0413\u0411", "\u0422\u0411"):
        value /= 1024
        if value < 1024:
            return f"{value:.1f} {unit}"
    value /= 1024
    return f"{value:.1f} \u041f\u0411"


class ClipboardPage(QWidget):
    """Всё, что оператор делает с общим буфером, кроме самого копирования."""

    sharing_toggled = Signal(bool)
    files_toggled = Signal(bool)
    autostart_toggled = Signal(bool)
    pair_requested = Signal()
    forget_requested = Signal()
    address_changed = Signal(str)
    cancel_requested = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)

        self.state_label = QLabel(STATE_LABELS["unpaired"], self)
        self.peer_label = QLabel(self.tr("Компьютер не выбран"), self)
        self.fingerprint_label = QLabel("", self)
        self.fingerprint_label.setWordWrap(True)

        self.pair_button = QPushButton(self.tr("Связать компьютеры"), self)
        self.pair_button.clicked.connect(self.pair_requested)

        self.forget_button = QPushButton(self.tr("Забыть компьютер"), self)
        self.forget_button.setEnabled(False)
        self.forget_button.clicked.connect(self.forget_requested)

        self.sharing_checkbox = QCheckBox(self.tr("Общий буфер обмена"), self)
        self.sharing_checkbox.toggled.connect(self.sharing_toggled)

        self.files_checkbox = QCheckBox(self.tr("\u041f\u0435\u0440\u0435\u0434\u0430\u0447\u0430 \u0444\u0430\u0439\u043b\u043e\u0432"), self)
        self.files_checkbox.toggled.connect(self.files_toggled)

        self.autostart_checkbox = QCheckBox(self.tr("Запускать вместе с Windows"), self)
        self.autostart_checkbox.toggled.connect(self.autostart_toggled)

        self.transfer_label = QLabel(self)
        self.cancel_button = QPushButton(self.tr("\u041e\u0442\u043c\u0435\u043d\u0438\u0442\u044c"), self)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested)

        self.address_field = QLineEdit(self)
        self.address_field.setPlaceholderText(self.tr("Адрес второго компьютера, если поиск не нашёл"))
        self.address_field.editingFinished.connect(
            lambda: self.address_changed.emit(self.address_field.text().strip())
        )

        self.events_list = QListWidget(self)
        self.events_list.setMaximumHeight(120)
        self.events_list.setAccessibleName(self.tr("Последние события"))

        events_box = QGroupBox(self.tr("Последние события"), self)
        events_layout = QVBoxLayout(events_box)
        events_layout.addWidget(self.events_list)

        peer_box = QGroupBox(self.tr("Второй компьютер"), self)
        peer_layout = QVBoxLayout(peer_box)
        peer_layout.addWidget(self.state_label)
        peer_layout.addWidget(self.peer_label)
        peer_layout.addWidget(self.fingerprint_label)
        buttons = QHBoxLayout()
        buttons.addWidget(self.pair_button)
        buttons.addWidget(self.forget_button)
        buttons.addStretch(1)
        peer_layout.addLayout(buttons)
        peer_layout.addWidget(self.address_field)

        layout = QVBoxLayout(self)
        layout.addWidget(peer_box)
        layout.addWidget(self.sharing_checkbox)
        layout.addWidget(self.files_checkbox)
        transfer_layout = QHBoxLayout()
        transfer_layout.addWidget(self.transfer_label)
        transfer_layout.addWidget(self.cancel_button)
        layout.addLayout(transfer_layout)
        layout.addWidget(self.autostart_checkbox)
        layout.addWidget(events_box)
        layout.addStretch(1)

    def set_link_state(self, state: str) -> None:
        self.state_label.setText(STATE_LABELS.get(state, STATE_LABELS["disconnected"]))

    def set_sharing_checked(self, enabled: bool) -> None:
        """Отразить состояние переключателя, не порождая новый sharing_toggled.

        Без блокировки сигналов чтение сохранённого состояния при запуске
        само становилось бы новым переключением - обратная связь, а не
        отображение состояния. Используется и при старте, и треем, чтобы
        страница и трей всегда показывали одно и то же (C1).
        """
        self.sharing_checkbox.blockSignals(True)
        self.sharing_checkbox.setChecked(enabled)
        self.sharing_checkbox.blockSignals(False)

    def set_autostart_checked(self, enabled: bool) -> None:
        self.autostart_checkbox.blockSignals(True)
        self.autostart_checkbox.setChecked(enabled)
        self.autostart_checkbox.blockSignals(False)

    def set_transfer_progress(self, done: int, total: int) -> None:
        self.transfer_label.setText(
            self.tr("\u041f\u043e\u043b\u0443\u0447\u0435\u043d\u0438\u0435 {0} / {1}").format(
                human_bytes(done), human_bytes(total)
            )
        )
        self.cancel_button.setEnabled(True)

    def clear_transfer(self) -> None:
        self.transfer_label.setText("")
        self.cancel_button.setEnabled(False)

    def set_files_checked(self, checked: bool) -> None:
        self.files_checkbox.blockSignals(True)
        self.files_checkbox.setChecked(checked)
        self.files_checkbox.blockSignals(False)

    def add_event(self, text: str) -> None:
        """Добавить строку в список последних событий (§12), самый новый - сверху."""
        self.events_list.insertItem(0, text)
        while self.events_list.count() > EVENTS_LIMIT:
            self.events_list.takeItem(self.events_list.count() - 1)

    def set_peer(self, peer: TrustedPeer | None) -> None:
        if peer is None:
            self.peer_label.setText(self.tr("Компьютер не выбран"))
            self.fingerprint_label.setText("")
            self.forget_button.setEnabled(False)
            return
        self.peer_label.setText(f"{peer.machine_name} ({peer.last_address})")
        self.fingerprint_label.setText(self.tr("Отпечаток: {0}").format(peer.fingerprint))
        self.forget_button.setEnabled(True)


__all__ = ["ClipboardPage", "human_bytes"]
