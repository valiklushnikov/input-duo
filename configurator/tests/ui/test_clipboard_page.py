"""Страница общего буфера: что она показывает и о чём сообщает."""

from __future__ import annotations

from duo_input.clipboard.trust import TrustedPeer
from duo_input.ui.clipboard_page import ClipboardPage

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

    assert PEER.fingerprint[:16] in page.fingerprint_label.text()


def test_toggling_sharing_reports_the_new_value(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)
    values: list[bool] = []
    page.sharing_toggled.connect(values.append)

    page.sharing_checkbox.setChecked(True)

    assert values == [True]
