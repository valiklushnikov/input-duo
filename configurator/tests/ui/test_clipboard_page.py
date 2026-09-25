"""Страница общего буфера: что она показывает и о чём сообщает."""

from __future__ import annotations

from PySide6.QtCore import Qt

from duo_input.clipboard.trust import TrustedPeer
from duo_input.ui.clipboard_page import ClipboardPage, human_bytes

PEER = TrustedPeer("a" * 32, "LAPTOP-TWO", "b" * 64, "192.168.1.7")


def _page(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)
    page.show()
    return page


def test_an_unpaired_page_offers_pairing(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)

    assert page.pair_button.isEnabled() is True
    assert page.forget_button.isEnabled() is False


def test_a_paired_page_shows_the_peer_and_offers_to_forget_it(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)

    page.set_peer(PEER)

    assert "LAPTOP-TWO" in page.peer_label.text()
    assert page.forget_button.isEnabled() is True


def test_the_fingerprint_is_shown_so_it_can_be_compared(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)
    page.set_peer(PEER)

    # Отпечаток показан целиком, а не сокращённо: усечение молча снижает
    # уверенность, с каким именно компьютером установлена связь.
    assert PEER.fingerprint in page.fingerprint_label.text()


def test_toggling_sharing_reports_the_new_value(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)
    values: list[bool] = []
    page.sharing_toggled.connect(values.append)

    page.sharing_checkbox.setChecked(True)

    assert values == [True]


def test_bytes_are_shown_in_units_a_person_reads():
    assert human_bytes(0) == "0 \u0411"
    assert human_bytes(999) == "999 \u0411"
    assert human_bytes(1024) == "1.0 \u041a\u0411"
    assert human_bytes(1_500_000_000) == "1.4 \u0413\u0411"
    assert human_bytes(1024**5) == "1.0 \u041f\u0411"


def test_progress_reads_as_received_out_of_total(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)

    page.set_transfer_progress(1_500_000_000, 8_800_000_000)

    assert page.transfer_label.text() == "\u041f\u043e\u043b\u0443\u0447\u0435\u043d\u0438\u0435 1.4 \u0413\u0411 / 8.2 \u0413\u0411"


def test_the_cancel_button_is_hidden_until_a_transfer_starts(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)

    assert page.cancel_button.isEnabled() is False


def test_progress_shows_the_cancel_button(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)
    page.show()

    page.set_transfer_progress(1, 100)

    assert page.cancel_button.isEnabled()


def test_clearing_a_transfer_hides_the_progress_and_the_button(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)
    page.set_transfer_progress(1, 100)

    page.clear_transfer()

    assert page.transfer_label.text() == ""
    assert not page.cancel_button.isEnabled()


def test_pressing_cancel_asks_for_a_cancellation_through_the_real_button(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)
    page.set_transfer_progress(1, 100)

    with qtbot.waitSignal(page.cancel_requested, timeout=1000):
        page.cancel_button.click()


def test_the_files_checkbox_reports_through_the_real_widget(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)

    with qtbot.waitSignal(page.files_toggled, timeout=1000) as blocker:
        page.files_checkbox.setChecked(True)

    assert blocker.args == [True]


def test_setting_the_files_checkbox_programmatically_does_not_echo_a_signal(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)
    seen: list[bool] = []
    page.files_toggled.connect(seen.append)

    page.set_files_checked(True)

    assert seen == []
    assert page.files_checkbox.isChecked() is True

    with qtbot.waitSignal(page.files_toggled, timeout=1000) as blocker:
        page.files_checkbox.click()

    assert blocker.args == [False]


# --------------------------------------------------------- address_combo


def test_typing_an_address_and_pressing_enter_reports_it_and_makes_it_manual(qtbot):
    page = _page(qtbot)
    seen = []
    page.address_changed.connect(seen.append)

    qtbot.keyClicks(page.address_combo.lineEdit(), "192.168.1.42")
    qtbot.keyClick(page.address_combo.lineEdit(), Qt.Key.Key_Return)

    assert seen[-1] == "192.168.1.42"
    assert page.is_manual is True


def test_board_addresses_fill_the_drop_down_without_reporting_anything(qtbot):
    page = _page(qtbot)
    seen = []
    page.address_changed.connect(seen.append)

    page.set_board_addresses(["192.168.1.7", "10.0.0.2"])

    items = [page.address_combo.itemText(i) for i in range(page.address_combo.count())]
    assert items == ["192.168.1.7", "10.0.0.2"]
    assert seen == []
    assert page.is_manual is False


def test_choosing_from_the_list_is_a_manual_choice(qtbot):
    page = _page(qtbot)
    page.set_board_addresses(["192.168.1.7", "10.0.0.2"])
    seen = []
    page.address_changed.connect(seen.append)

    page.address_combo.activated.emit(1)  # то, что шлёт QComboBox при выборе мышью

    assert seen == ["10.0.0.2"]
    assert page.is_manual is True


def test_clearing_the_field_returns_to_automatic(qtbot):
    page = _page(qtbot)
    page.set_manual_address("192.168.1.42")
    seen = []
    page.address_changed.connect(seen.append)

    page.address_combo.lineEdit().selectAll()
    qtbot.keyClick(page.address_combo.lineEdit(), Qt.Key.Key_Delete)
    qtbot.keyClick(page.address_combo.lineEdit(), Qt.Key.Key_Return)

    assert seen[-1] == ""
    assert page.is_manual is False


def test_the_address_in_use_is_shown_in_automatic_mode_only(qtbot):
    page = _page(qtbot)
    seen = []
    page.address_changed.connect(seen.append)

    page.show_address_in_use("10.0.0.2")
    assert page.address_combo.currentText() == "10.0.0.2"
    assert page.is_manual is False
    assert seen == []

    page.set_manual_address("192.168.1.42")
    page.show_address_in_use("10.0.0.2")
    assert page.address_combo.currentText() == "192.168.1.42"


def test_refilling_the_list_keeps_what_is_typed(qtbot):
    page = _page(qtbot)
    page.set_manual_address("192.168.1.42")
    page.set_board_addresses(["10.0.0.2"])
    assert page.address_combo.currentText() == "192.168.1.42"


def test_a_popup_choice_followed_by_an_ordinary_focus_loss_reports_once(qtbot):
    """Клик по элементу шлёт activated; следующая обычная потеря фокуса
    (editingFinished) для того же самого текста не должна переслать
    address_changed второй раз - иначе координатор видит новую команду и
    переподключается заново на ровном месте (review, round 1, finding 1)."""
    page = _page(qtbot)
    page.set_board_addresses(["192.168.1.7", "10.0.0.2"])
    seen = []
    page.address_changed.connect(seen.append)

    page.address_combo.setCurrentIndex(1)
    page.address_combo.activated.emit(1)
    page.address_combo.lineEdit().editingFinished.emit()

    assert seen == ["10.0.0.2"]
    assert page.is_manual is True


def test_a_typed_address_followed_by_a_later_focus_loss_reports_once(qtbot):
    """То же самое, но для набранного вручную адреса, а не выбранного
    мышью: Enter коммитит текст, следующая потеря фокуса без правки не
    должна отправить его снова."""
    page = _page(qtbot)
    seen = []
    page.address_changed.connect(seen.append)

    qtbot.keyClicks(page.address_combo.lineEdit(), "192.168.1.42")
    qtbot.keyClick(page.address_combo.lineEdit(), Qt.Key.Key_Return)
    page.address_combo.lineEdit().editingFinished.emit()  # обычная потеря фокуса, текст не менялся

    assert seen == ["192.168.1.42"]
    assert page.is_manual is True


def test_choosing_the_same_item_twice_reports_once(qtbot):
    page = _page(qtbot)
    page.set_board_addresses(["192.168.1.7", "10.0.0.2"])
    seen = []
    page.address_changed.connect(seen.append)

    page.address_combo.activated.emit(1)
    page.address_combo.activated.emit(1)

    assert seen == ["10.0.0.2"]


def test_leaving_manual_mode_does_not_leave_a_stale_auto_text(qtbot):
    """Обратно в автоматическом режиме адрес, который когда-то показал
    show_address_in_use, не должен считаться «тем же самым», если плата с
    тех пор его не подтверждала: иначе набранный вручную адрес, случайно
    совпавший со старым автоматическим, молча проглатывается (review, round
    1, finding 3)."""
    page = _page(qtbot)
    page.show_address_in_use("10.0.0.2")
    page.set_manual_address("192.168.1.42")

    page.address_combo.lineEdit().selectAll()
    qtbot.keyClick(page.address_combo.lineEdit(), Qt.Key.Key_Delete)
    qtbot.keyClick(page.address_combo.lineEdit(), Qt.Key.Key_Return)  # назад в автоматический режим

    seen = []
    page.address_changed.connect(seen.append)
    qtbot.keyClicks(page.address_combo.lineEdit(), "10.0.0.2")
    qtbot.keyClick(page.address_combo.lineEdit(), Qt.Key.Key_Return)

    assert seen == ["10.0.0.2"]
    assert page.is_manual is True


def test_calling_set_manual_address_empty_also_drops_the_stale_auto_text(qtbot):
    """Тот же дефект, что и выше, но по программному пути set_manual_address("")
    вместо очистки через интерфейс - у него своя строка сброса."""
    page = _page(qtbot)
    page.show_address_in_use("10.0.0.2")
    page.set_manual_address("192.168.1.42")
    page.set_manual_address("")  # программный возврат в автоматический режим

    seen = []
    page.address_changed.connect(seen.append)
    qtbot.keyClicks(page.address_combo.lineEdit(), "10.0.0.2")
    qtbot.keyClick(page.address_combo.lineEdit(), Qt.Key.Key_Return)

    assert seen == ["10.0.0.2"]
    assert page.is_manual is True


def test_showing_an_address_does_not_leak_any_qt_signal(qtbot):
    """blockSignals в _show: setEditText сам по себе рассылает
    currentTextChanged/editTextChanged, даже когда ни editingFinished, ни
    activated не срабатывают. Без blockSignals эти сигналы утекли бы наружу."""
    page = _page(qtbot)
    texts = []
    page.address_combo.currentTextChanged.connect(texts.append)

    page.show_address_in_use("10.0.0.2")

    assert texts == []


def test_refilling_the_list_does_not_leak_any_qt_signal(qtbot):
    """blockSignals в set_board_addresses: тот же утекающий сигнал, только
    при перезаполнении списка, а не при показе одного адреса."""
    page = _page(qtbot)
    texts = []
    page.address_combo.currentTextChanged.connect(texts.append)

    page.set_board_addresses(["192.168.1.7", "10.0.0.2"])

    assert texts == []


def test_leaving_the_field_without_typing_keeps_automatic_mode(qtbot):
    """editingFinished срабатывает и просто на потере фокуса - без правки текста
    это не должно превращать автоматический режим в ручной (постановление
    контролёра, точнее черновика задачи)."""
    page = _page(qtbot)
    seen = []
    page.address_changed.connect(seen.append)

    page.show_address_in_use("10.0.0.2")
    page.address_combo.lineEdit().setFocus()
    qtbot.waitUntil(lambda: page.address_combo.lineEdit().hasFocus())

    # Текст не менялся - editingFinished здесь означает только потерю фокуса.
    page.address_combo.lineEdit().editingFinished.emit()

    assert page.is_manual is False
    assert seen == []


def test_clearing_to_empty_in_automatic_mode_stays_automatic(qtbot):
    """Пустой текст в автоматическом режиме - не редактирование, даже когда
    он отличается от последнего показанного адреса (ruling, вторая половина
    условия «or empty»)."""
    page = _page(qtbot)
    seen = []
    page.address_changed.connect(seen.append)

    page.show_address_in_use("10.0.0.2")
    page.address_combo.lineEdit().selectAll()
    qtbot.keyClick(page.address_combo.lineEdit(), Qt.Key.Key_Delete)
    page.address_combo.lineEdit().editingFinished.emit()

    assert page.is_manual is False
    assert seen == []
