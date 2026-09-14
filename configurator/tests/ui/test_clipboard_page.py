"""Страница общего буфера: что она показывает и о чём сообщает."""

from __future__ import annotations

from duo_input.clipboard.trust import TrustedPeer
from duo_input.ui.clipboard_page import ClipboardPage, human_bytes

PEER = TrustedPeer("a" * 32, "LAPTOP-TWO", "b" * 64, "192.168.1.7")


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
