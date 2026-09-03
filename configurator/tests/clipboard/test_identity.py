"""Идентичность узла: создаётся один раз и переживает перезапуск."""

from __future__ import annotations

import hashlib

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from duo_input.clipboard.identity import fingerprint_of, load_or_create


def test_creates_identity_on_first_call(tmp_path):
    identity = load_or_create(tmp_path)

    assert len(identity.origin_id) == 32
    assert identity.certificate_pem.startswith(b"-----BEGIN CERTIFICATE-----")
    assert identity.key_pem.startswith(b"-----BEGIN PRIVATE KEY-----")
    assert len(identity.fingerprint) == 64


def test_second_call_returns_the_same_identity(tmp_path):
    first = load_or_create(tmp_path)
    second = load_or_create(tmp_path)

    assert second.origin_id == first.origin_id
    assert second.certificate_pem == first.certificate_pem
    assert second.fingerprint == first.fingerprint


def test_two_directories_get_different_identities(tmp_path):
    one = load_or_create(tmp_path / "a")
    two = load_or_create(tmp_path / "b")

    assert one.origin_id != two.origin_id
    assert one.fingerprint != two.fingerprint


def test_fingerprint_is_the_sha256_of_the_der_certificate(tmp_path):
    identity = load_or_create(tmp_path)

    # Независимый расчет отпечатка без использования fingerprint_of.
    certificate = x509.load_pem_x509_certificate(identity.certificate_pem)
    der = certificate.public_bytes(serialization.Encoding.DER)
    expected_fingerprint = hashlib.sha256(der).hexdigest()

    assert expected_fingerprint == identity.fingerprint


def test_certificate_uses_secp256r1_curve(tmp_path):
    identity = load_or_create(tmp_path)

    certificate = x509.load_pem_x509_certificate(identity.certificate_pem)
    public_key = certificate.public_key()

    assert isinstance(public_key, ec.EllipticCurvePublicKey)
    assert public_key.curve.name == "secp256r1"


def test_certificate_is_self_signed(tmp_path):
    identity = load_or_create(tmp_path)

    certificate = x509.load_pem_x509_certificate(identity.certificate_pem)

    assert certificate.issuer == certificate.subject


def test_certificate_valid_for_approximately_ten_years(tmp_path):
    identity = load_or_create(tmp_path)

    certificate = x509.load_pem_x509_certificate(identity.certificate_pem)

    validity = certificate.not_valid_after_utc - certificate.not_valid_before_utc
    days = validity.days

    # Проверяем диапазон от 9 до 11 лет, а не точное равенство.
    assert 9 * 365 <= days <= 11 * 365
