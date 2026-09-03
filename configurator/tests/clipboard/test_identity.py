"""Идентичность узла: создаётся один раз и переживает перезапуск."""

from __future__ import annotations

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

    assert fingerprint_of(identity.certificate_pem) == identity.fingerprint
