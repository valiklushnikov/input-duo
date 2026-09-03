"""Кто мы для второго компьютера: постоянный идентификатор и сертификат.

Доверие в этой подсистеме держится на отпечатке сертификата, а не на сроке
действия и не на цепочке подписей, поэтому сертификат самоподписанный и живёт
десять лет. Истёкший сертификат означал бы, что связь между двумя спаренными
компьютерами однажды молча перестала работать по календарю.

Если хотя бы одного из трёх файлов идентичности нет на диске, идентичность
создаётся заново целиком, а не достраивается. Это означает, что отпечаток
меняется, и ранее спаренная машина перестанет принимать соединение, потребуя
повторного парринга. Это осознанный выбор в пользу отказа, а не в пользу
молчаливого продолжения с несогласованной парой.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

KEY_FILE_NAME = "node-key.pem"
CERTIFICATE_FILE_NAME = "node-cert.pem"
ORIGIN_FILE_NAME = "node-id.txt"

CERTIFICATE_YEARS = 10


@dataclass(frozen=True)
class NodeIdentity:
    """Постоянное имя этого узла и ключ, которым он себя доказывает."""

    origin_id: str
    certificate_pem: bytes = field(repr=False)
    key_pem: bytes = field(repr=False)

    @property
    def fingerprint(self) -> str:
        return fingerprint_of(self.certificate_pem)


def fingerprint_of(certificate_pem: bytes) -> str:
    """SHA-256 сертификата в DER — то, что закрепляется при парринге."""
    certificate = x509.load_pem_x509_certificate(certificate_pem)
    der = certificate.public_bytes(serialization.Encoding.DER)
    return hashlib.sha256(der).hexdigest()


def load_or_create(directory: Path) -> NodeIdentity:
    """Прочитать идентичность из каталога, создав её при первом обращении."""
    directory.mkdir(parents=True, exist_ok=True)
    key_path = directory / KEY_FILE_NAME
    certificate_path = directory / CERTIFICATE_FILE_NAME
    origin_path = directory / ORIGIN_FILE_NAME

    if key_path.is_file() and certificate_path.is_file() and origin_path.is_file():
        return NodeIdentity(
            origin_id=origin_path.read_text("ascii").strip(),
            certificate_pem=certificate_path.read_bytes(),
            key_pem=key_path.read_bytes(),
        )

    origin_id = uuid.uuid4().hex
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, f"Duo Input {origin_id}")])
    now = datetime.datetime.now(datetime.UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=365 * CERTIFICATE_YEARS))
        .sign(key, hashes.SHA256())
    )

    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    certificate_pem = certificate.public_bytes(serialization.Encoding.PEM)

    key_path.write_bytes(key_pem)
    certificate_path.write_bytes(certificate_pem)
    origin_path.write_text(origin_id, "ascii")

    return NodeIdentity(origin_id=origin_id, certificate_pem=certificate_pem, key_pem=key_pem)


__all__ = [
    "CERTIFICATE_FILE_NAME",
    "KEY_FILE_NAME",
    "ORIGIN_FILE_NAME",
    "NodeIdentity",
    "fingerprint_of",
    "load_or_create",
]
