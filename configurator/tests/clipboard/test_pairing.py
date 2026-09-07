"""Код сравнения: одинаковый на обеих машинах, разный для разных пар."""

from __future__ import annotations

from duo_input.clipboard.pairing import PairingCandidate, pairing_code

ONE = "a" * 64
TWO = "b" * 64

# Пара отпечатков, дающая код с ведущим нулём
LEADING_ZERO_ONE = "fp_000000aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
LEADING_ZERO_TWO = "fp_000008bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def test_both_machines_derive_the_same_code_whichever_way_round():
    assert pairing_code(ONE, TWO) == pairing_code(TWO, ONE)


def test_the_code_is_six_digits_with_leading_zeros_kept():
    code = pairing_code(ONE, TWO)

    assert len(code) == 6
    assert code.isdigit()


def test_leading_zeros_are_preserved_in_formatting():
    """Проверяем что ведущие нули не теряются при форматировании кода."""
    code = pairing_code(LEADING_ZERO_ONE, LEADING_ZERO_TWO)

    assert len(code) == 6
    assert code.isdigit()
    assert code.startswith("0")


def test_a_different_pair_gives_a_different_code():
    assert pairing_code(ONE, TWO) != pairing_code(ONE, "c" * 64)


def test_code_depends_on_both_fingerprints():
    """Изменение любого отпечатка должно изменить код."""
    original = pairing_code(ONE, TWO)

    # Изменяем первый отпечаток
    modified_one = pairing_code("a" * 63 + "x", TWO)
    assert original != modified_one

    # Изменяем второй отпечаток
    modified_two = pairing_code(ONE, "b" * 63 + "x")
    assert original != modified_two


def test_a_candidate_becomes_a_trusted_peer_keeping_its_address():
    candidate = PairingCandidate(
        origin_id="1" * 32,
        machine_name="LAPTOP-TWO",
        fingerprint=TWO,
        address="192.168.1.5",
        port=47654,
    )

    peer = candidate.as_trusted()

    assert peer.origin_id == "1" * 32
    assert peer.fingerprint == TWO
    assert peer.last_address == "192.168.1.5"
