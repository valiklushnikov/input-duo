"""Идентичность узла: создаётся один раз и переживает перезапуск."""

from __future__ import annotations

import datetime
import hashlib
import uuid

from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec, rsa
from cryptography.x509.oid import NameOID

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


def test_certificate_uses_an_rsa_key_that_qt_schannel_can_import(tmp_path):
    identity = load_or_create(tmp_path)

    certificate = x509.load_pem_x509_certificate(identity.certificate_pem)
    public_key = certificate.public_key()

    assert isinstance(public_key, rsa.RSAPublicKey)
    assert public_key.key_size >= 2048


def test_a_persisted_legacy_ec_identity_is_replaced_before_tls_uses_it(tmp_path):
    directory = tmp_path / "legacy"
    directory.mkdir()
    old_origin = uuid.uuid4().hex
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Duo Input {old_origin}")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .sign(key, hashes.SHA256())
    )
    (directory / "node-key.pem").write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (directory / "node-cert.pem").write_bytes(
        certificate.public_bytes(serialization.Encoding.PEM)
    )
    (directory / "node-id.txt").write_text(old_origin, "ascii")

    identity = load_or_create(directory)
    loaded_key = serialization.load_pem_private_key(identity.key_pem, password=None)

    assert isinstance(loaded_key, rsa.RSAPrivateKey)
    assert identity.origin_id != old_origin


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
