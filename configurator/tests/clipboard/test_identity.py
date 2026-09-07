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

from duo_input.clipboard.identity import (
    CERTIFICATE_FILE_NAME,
    KEY_FILE_NAME,
    ORIGIN_FILE_NAME,
    NodeIdentity,
    fingerprint_of,
    load_or_create,
)


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


# --- Согласованность тройки файлов при обрыве записи -----------------------
#
# `load_or_create` пишет ключ, сертификат и идентификатор тремя отдельными
# записями. Обрыв процесса между ними (снятие процесса, потеря питания,
# ошибка диска) может оставить на диске несогласованную тройку — например,
# новый ключ со старым сертификатом. Ниже — тесты на то, что при чтении
# такая тройка не принимается молча, а идентичность пересоздаётся целиком,
# как при первом запуске.


def _generate_rsa_identity_files(origin_id: str | None = None) -> tuple[str, bytes, bytes]:
    """Собрать независимо сгенерированные ключ и сертификат RSA-идентичности.

    Возвращает (origin_id, key_pem, certificate_pem) — самостоятельный набор,
    не связанный с тем, что создаёт сам модуль, чтобы тесты не зависели от
    его внутренней реализации.
    """
    origin_id = origin_id or uuid.uuid4().hex
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Duo Input {origin_id}")])
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
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)
    return origin_id, key_pem, certificate_pem


def _assert_returns_healthy_and_stable_identity(directory) -> NodeIdentity:
    """Загруженная идентичность рабочая и не меняется при повторном вызове.

    «Рабочая» значит: ключ соответствует открытому ключу сертификата,
    отпечаток считается, а идентификатор совпадает с тем, что записан в
    сертификате. «Не меняется» значит: второй вызов возвращает точно ту же
    идентичность, то есть пересоздание прошло тихо и до конца, а не оставило
    на диске ещё одну несогласованную тройку.
    """
    identity = load_or_create(directory)

    key = serialization.load_pem_private_key(identity.key_pem, password=None)
    certificate = x509.load_pem_x509_certificate(identity.certificate_pem)
    assert isinstance(key, rsa.RSAPrivateKey)
    assert key.public_key().public_numbers() == certificate.public_key().public_numbers()

    common_names = certificate.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    assert [attribute.value for attribute in common_names] == [
        f"Duo Input {identity.origin_id}"
    ]
    assert len(identity.fingerprint) == 64

    again = load_or_create(directory)
    assert again == identity

    return identity


def test_key_without_certificate_recreates_the_identity(tmp_path):
    _, key_pem, _ = _generate_rsa_identity_files()
    (tmp_path / KEY_FILE_NAME).write_bytes(key_pem)

    _assert_returns_healthy_and_stable_identity(tmp_path)


def test_certificate_without_key_recreates_the_identity(tmp_path):
    _, _, certificate_pem = _generate_rsa_identity_files()
    (tmp_path / CERTIFICATE_FILE_NAME).write_bytes(certificate_pem)

    _assert_returns_healthy_and_stable_identity(tmp_path)


def test_missing_origin_file_recreates_the_identity(tmp_path):
    old_origin_id, key_pem, certificate_pem = _generate_rsa_identity_files()
    (tmp_path / KEY_FILE_NAME).write_bytes(key_pem)
    (tmp_path / CERTIFICATE_FILE_NAME).write_bytes(certificate_pem)
    # node-id.txt намеренно отсутствует — как после обрыва записи.

    identity = _assert_returns_healthy_and_stable_identity(tmp_path)

    assert identity.origin_id != old_origin_id


def test_key_from_one_identity_with_certificate_from_another_recreates_the_identity(tmp_path):
    origin_a, key_pem_a, _ = _generate_rsa_identity_files()
    origin_b, _, certificate_pem_b = _generate_rsa_identity_files()
    (tmp_path / KEY_FILE_NAME).write_bytes(key_pem_a)
    (tmp_path / CERTIFICATE_FILE_NAME).write_bytes(certificate_pem_b)
    (tmp_path / ORIGIN_FILE_NAME).write_text(origin_b, "ascii")

    identity = _assert_returns_healthy_and_stable_identity(tmp_path)

    # Файлы были на месте и оба по отдельности валидны — расхождение только
    # в том, что ключ и сертификат не образуют пару. Новая идентичность не
    # должна совпадать ни с одной из двух исходных.
    assert identity.origin_id not in (origin_a, origin_b)


def test_origin_file_disagreeing_with_certificate_recreates_the_identity(tmp_path):
    origin_in_certificate, key_pem, certificate_pem = _generate_rsa_identity_files()
    origin_in_file = uuid.uuid4().hex
    (tmp_path / KEY_FILE_NAME).write_bytes(key_pem)
    (tmp_path / CERTIFICATE_FILE_NAME).write_bytes(certificate_pem)
    (tmp_path / ORIGIN_FILE_NAME).write_text(origin_in_file, "ascii")

    identity = _assert_returns_healthy_and_stable_identity(tmp_path)

    assert identity.origin_id not in (origin_in_certificate, origin_in_file)


def test_corrupted_certificate_recreates_the_identity(tmp_path):
    origin_id, key_pem, _ = _generate_rsa_identity_files()
    (tmp_path / KEY_FILE_NAME).write_bytes(key_pem)
    (tmp_path / CERTIFICATE_FILE_NAME).write_bytes(b"not a certificate, just garbage bytes")
    (tmp_path / ORIGIN_FILE_NAME).write_text(origin_id, "ascii")

    _assert_returns_healthy_and_stable_identity(tmp_path)


def test_corrupted_key_recreates_the_identity(tmp_path):
    origin_id, _, certificate_pem = _generate_rsa_identity_files()
    (tmp_path / KEY_FILE_NAME).write_bytes(b"not a private key, just garbage bytes")
    (tmp_path / CERTIFICATE_FILE_NAME).write_bytes(certificate_pem)
    (tmp_path / ORIGIN_FILE_NAME).write_text(origin_id, "ascii")

    _assert_returns_healthy_and_stable_identity(tmp_path)
