"""Страница общего буфера: состояние, связывание и два переключателя.

Отпечаток показан целиком не для красоты: это то самое число, по которому
человек может убедиться, что связан именно с тем компьютером, с которым думал.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
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
    auto_incoming_toggled = Signal(bool)
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

        self.auto_incoming_checkbox = QCheckBox(
            self.tr(
                "\u0417\u0430\u0433\u0440\u0443\u0436\u0430\u0442\u044c \u0432\u0445\u043e\u0434\u044f\u0449\u0438\u0435 "
                "\u0444\u0430\u0439\u043b\u044b \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438"
            ),
            self,
        )
        self.auto_incoming_checkbox.toggled.connect(self.auto_incoming_toggled)

        self.autostart_checkbox = QCheckBox(self.tr("Запускать вместе с Windows"), self)
        self.autostart_checkbox.toggled.connect(self.autostart_toggled)

        self.transfer_label = QLabel(self)
        self.cancel_button = QPushButton(self.tr("\u041e\u0442\u043c\u0435\u043d\u0438\u0442\u044c"), self)
        self.cancel_button.setEnabled(False)
        self.cancel_button.clicked.connect(self.cancel_requested)

        # Одна строка на оба случая: выпадающий список - адреса, которые
        # сообщила плата; ввод - ручной адрес, который главнее них, пока его
        # не сотрут. Программная подстановка идёт под blockSignals и ручным
        # вводом не считается.
        self._manual = False
        #: Последний адрес, который показал show_address_in_use. Нужен,
        #: чтобы отличить настоящую правку от editingFinished на голой
        #: потере фокуса - оно срабатывает и без единого нажатия клавиши.
        self._auto_text = ""
        #: Последний текст, который действительно ушёл сигналом
        #: address_changed. Нужен, чтобы один и тот же выбор не улетал
        #: дважды: клик по элементу списка шлёт activated, а следующая
        #: обычная потеря фокуса - editingFinished с тем же текстом. None -
        #: ещё ни разу не отправляли, поэтому пустая строка не гасится сама.
        self._last_emitted: str | None = None
        self.address_combo = QComboBox(self)
        self.address_combo.setEditable(True)
        self.address_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.address_combo.lineEdit().setPlaceholderText(
            self.tr("Адрес второго компьютера, если поиск не нашёл")
        )
        self.address_combo.lineEdit().editingFinished.connect(self._on_address_edited)
        self.address_combo.activated.connect(self._on_address_chosen)

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
        peer_layout.addWidget(self.address_combo)

        layout = QVBoxLayout(self)
        layout.addWidget(peer_box)
        layout.addWidget(self.sharing_checkbox)
        layout.addWidget(self.files_checkbox)
        layout.addWidget(self.auto_incoming_checkbox)
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

    def set_auto_incoming_checked(self, checked: bool) -> None:
        self.auto_incoming_checkbox.blockSignals(True)
        self.auto_incoming_checkbox.setChecked(checked)
        self.auto_incoming_checkbox.blockSignals(False)

    def add_event(self, text: str) -> None:
        """Добавить строку в список последних событий (§12), самый новый - сверху."""
        self.events_list.insertItem(0, text)
        while self.events_list.count() > EVENTS_LIMIT:
            self.events_list.takeItem(self.events_list.count() - 1)

    @property
    def is_manual(self) -> bool:
        return self._manual

    def set_board_addresses(self, addresses: list[str]) -> None:
        """Заполнить список адресами, которые сообщила плата.

        Что набрано или выбрано, сохраняется: перезаполнение списка не
        должно стирать ручной ввод у человека из-под курсора.
        """
        text = self.address_combo.currentText()
        self.address_combo.blockSignals(True)
        self.address_combo.clear()
        self.address_combo.addItems(addresses)
        self.address_combo.setEditText(text)
        self.address_combo.blockSignals(False)

    def set_manual_address(self, address: str) -> None:
        self._manual = bool(address)
        if not self._manual:
            self._auto_text = ""  # выходим из ручного режима - не тащить за собой старый показанный адрес
        self._show(address, self.tr("введён вручную") if address else "")

    def show_address_in_use(self, address: str) -> None:
        if self._manual:
            return
        self._auto_text = address
        self._show(address, self.tr("найден автоматически"))

    def _show(self, address: str, source: str) -> None:
        self.address_combo.blockSignals(True)
        self.address_combo.setEditText(address)
        self.address_combo.blockSignals(False)
        self.address_combo.setToolTip(source)

    def _on_address_edited(self) -> None:
        text = self.address_combo.currentText().strip()
        if not self._manual and (not text or text == self._auto_text):
            # В автоматическом режиме editingFinished срабатывает и на
            # обычной потере фокуса, без единой нажатой клавиши. Текст,
            # совпадающий с тем, что уже показано (в том числе пустой,
            # пока плата ничего не нашла), - это не редактирование.
            return
        self._commit(text)

    def _on_address_chosen(self, index: int) -> None:
        text = self.address_combo.itemText(index).strip()
        self._commit(text)

    def _commit(self, text: str) -> None:
        """Зафиксировать выбор человека и сообщить о нём - но только один раз.

        Клик по элементу списка шлёт activated, а следующая обычная потеря
        фокуса - editingFinished с тем же самым текстом: то же самое
        значение не должно улетать address_changed второй раз, иначе
        координатор увидит его как новую команду и переподключится заново.
        """
        self._manual = bool(text)
        if not self._manual:
            self._auto_text = ""  # то же самое - вышли в автоматический режим, не тащить старый адрес
        self.address_combo.setToolTip(self.tr("введён вручную") if text else "")
        if text == self._last_emitted:
            return
        self._last_emitted = text
        self.address_changed.emit(text)

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
