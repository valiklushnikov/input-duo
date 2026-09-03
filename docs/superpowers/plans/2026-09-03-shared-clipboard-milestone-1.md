# Shared Clipboard Milestone 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Дать Duo Input общий буфер обмена между двумя компьютерами Windows: `Ctrl+C` на одном, `Ctrl+V` на другом, содержимое уходит по локальной сети в момент вставки.

**Architecture:** Один процесс. Новый пакет `duo_input/clipboard/` зависит только от `QtCore` и `QtNetwork` и держит всю логику: снимок локального буфера, ленивую отдачу удалённого, TLS-соединение с единственным доверенным пиром, обнаружение и парринг. Интерфейс (трей и страница) живёт отдельно в `duo_input/ui/` и подсистему только отображает.

**Tech Stack:** Python 3.12, PySide6 6.10.1 (`QtCore`, `QtNetwork`, `QtWidgets`), `cryptography`, pytest 9.1.1 + pytest-qt 4.5.0, Nuitka 4.1.3, Inno Setup.

**Spec:** `docs/superpowers/specs/2026-09-03-shared-clipboard-design.md`

## Global Constraints

- Платформа milestone 1 — **только Windows ↔ Windows**. macOS не реализуется; кода с ветвлением по macOS в этом плане нет.
- `duo_input/clipboard/**` **никогда не импортирует `QtWidgets`**. Это проверяется тестом в задаче 15.
- Содержимое буфера **никогда не пишется на диск**. Ни временных файлов, ни истории.
- Пока `clipboard/enabled` выключен, **ни один сокет не открывается** и ни один снимок не делается.
- Файлы никак не входят в milestone 1. `clipboard/files_enabled` существует и всегда `False`.
- Ровно один доверенный пир.
- Порт TCP **47654**, UDP-группа обнаружения **239.255.76.67**, порт **47655**.
- Потолок содержимого — **32 МиБ** (`33554432` байт); больший снимок не объявляется.
- Версия протокола буфера: major **1**, minor **0**; своя, не связанная с `protocol/schema.json`.
- Все новые видимые строки идут в оба каталога переводов, русский по умолчанию (`duo_input/i18n.py`).
- Тесты запускаются из каталога `configurator/`: `../.venv/Scripts/python.exe -m pytest <путь> -q`.
- Каждая задача заканчивается коммитом. Сообщение коммита — по-английски, как в истории репозитория.

## Структура файлов

```
configurator/src/duo_input/clipboard/__init__.py        пустой маркер пакета
configurator/src/duo_input/clipboard/identity.py        origin_id, ключ, сертификат, отпечаток
configurator/src/duo_input/clipboard/trust.py           TrustStore: единственный доверенный пир
configurator/src/duo_input/clipboard/offer.py           ClipboardOffer и дескрипторы
configurator/src/duo_input/clipboard/wire.py            типы сообщений и кадрирование
configurator/src/duo_input/clipboard/backend.py         ClipboardBackend, ClipboardSnapshot, RemoteMimeData
configurator/src/duo_input/clipboard/windows_backend.py наблюдение за QClipboard и публикация
configurator/src/duo_input/clipboard/peer.py            PeerLink: одно TLS-соединение
configurator/src/duo_input/clipboard/listener.py        QSslServer, принимающий только доверенного
configurator/src/duo_input/clipboard/discovery.py       UDP-маячок
configurator/src/duo_input/clipboard/pairing.py         код сравнения и поток парринга
configurator/src/duo_input/clipboard/service.py         ClipboardService: правила и связывание
configurator/src/duo_input/clipboard/coordinator.py     соединение, парринг, переподключение
configurator/src/duo_input/ui/tray.py                   QSystemTrayIcon и меню
configurator/src/duo_input/ui/clipboard_page.py         страница «Общий буфер»
configurator/src/duo_input/persistence/autostart.py     ярлык в shell:startup
```

Тесты — `configurator/tests/clipboard/`, по файлу на модуль.

---

### Task 1: Спайк — действительно ли `retrieveData` вызывается лениво

Это шлагбаум. Вся ленивая модель держится на допущении, что Qt на Windows запрашивает данные в момент вставки, а не при `setMimeData`. Если допущение ложно, дальше идти нельзя: спека переделывается.

**Files:**
- Create: `configurator/tests/clipboard/spike_lazy_mimedata.py`
- Create: `docs/superpowers/records/2026-09-03-lazy-clipboard-spike.md`

**Interfaces:**
- Consumes: ничего.
- Produces: записанный ответ «да/нет» и решение. Production-кода не оставляет.

- [ ] **Step 1: Написать скрипт спайка**

```python
"""Спайк: зовёт ли Windows retrieveData лениво, в момент вставки?

Не тест. Запускается руками на настоящей Windows-сессии:
    cd configurator && ../.venv/Scripts/python.exe tests/clipboard/spike_lazy_mimedata.py
"""

from __future__ import annotations

import sys

from PySide6.QtCore import QMimeData, QTimer
from PySide6.QtWidgets import QApplication

CALLS: list[str] = []


class LazyMimeData(QMimeData):
    def formats(self):  # noqa: N802 - Qt API
        return ["text/plain"]

    def retrieveData(self, mime_type, preferred_type):  # noqa: N802 - Qt API
        CALLS.append(mime_type)
        print(f"retrieveData({mime_type!r}) вызван, всего вызовов: {len(CALLS)}")
        return "ленивое содержимое"


def main() -> int:
    application = QApplication(sys.argv)
    clipboard = application.clipboard()
    clipboard.setMimeData(LazyMimeData())

    print(f"после setMimeData вызовов: {len(CALLS)}")
    print("Теперь вставьте (Ctrl+V) в Блокнот. Окно закроется через 30 секунд.")

    QTimer.singleShot(30_000, application.quit)
    application.exec()
    print(f"итого вызовов retrieveData: {len(CALLS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Запустить на настоящей Windows-сессии**

Run: `cd configurator && ../.venv/Scripts/python.exe tests/clipboard/spike_lazy_mimedata.py`

Затем вставить в Блокнот.

Ожидаемый благоприятный исход: после `setMimeData` вызовов **ноль** (или только служебные), а после `Ctrl+V` появляется вызов с `text/plain`, и в Блокноте оказывается «ленивое содержимое».

- [ ] **Step 3: Записать результат**

Создать `docs/superpowers/records/2026-09-03-lazy-clipboard-spike.md` с точным выводом скрипта, версией Windows, версией PySide6 и однозначным выводом: ленивая отдача подтверждена или нет.

- [ ] **Step 4: Шлагбаум**

Если вызовов после `setMimeData` оказалось столько же, сколько форматов, то есть Qt материализовал данные сразу — **остановиться и вернуться к спеке**. Запасные пути названы в разделе 5 спеки: нативный `IDataObject` через COM либо отказ от ленивости для мелких данных. Не начинать задачу 2.

- [ ] **Step 5: Commit**

```bash
git add configurator/tests/clipboard/spike_lazy_mimedata.py docs/superpowers/records/2026-09-03-lazy-clipboard-spike.md
git commit -m "Find out when Windows actually asks for clipboard data"
```

---

### Task 2: Идентичность узла

**Files:**
- Create: `configurator/src/duo_input/clipboard/__init__.py`
- Create: `configurator/src/duo_input/clipboard/identity.py`
- Create: `configurator/tests/clipboard/test_identity.py`
- Modify: `configurator/requirements-build.txt`

**Interfaces:**
- Consumes: `duo_input.persistence.locations.application_directory`.
- Produces: `NodeIdentity` — датакласс с полями `origin_id: str`, `certificate_pem: bytes`, `key_pem: bytes`, `fingerprint: str`; функция `load_or_create(directory: Path) -> NodeIdentity`; функция `fingerprint_of(certificate_pem: bytes) -> str`.

- [ ] **Step 1: Написать падающие тесты**

```python
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
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_identity.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard'`

- [ ] **Step 3: Установить зависимость и записать её**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pip install "cryptography==46.0.3"`

Затем дописать в `configurator/requirements-build.txt`, после строки `PySide6==6.10.1`:

```
# Qt читает ключи, но не создаёт их: сертификат узла для взаимного TLS
# генерируется здесь.
cryptography==46.0.3
```

Если установленная версия отличается — записать ту, что встала, точным пином.

- [ ] **Step 4: Написать реализацию**

`configurator/src/duo_input/clipboard/__init__.py` — пустой файл.

```python
"""Кто мы для второго компьютера: постоянный идентификатор и сертификат.

Доверие в этой подсистеме держится на отпечатке сертификата, а не на сроке
действия и не на цепочке подписей, поэтому сертификат самоподписанный и живёт
десять лет. Истёкший сертификат означал бы, что связь между двумя спаренными
компьютерами однажды молча перестала работать по календарю.
"""

from __future__ import annotations

import datetime
import hashlib
import uuid
from dataclasses import dataclass
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
    certificate_pem: bytes
    key_pem: bytes

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
```

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_identity.py -q`
Expected: PASS, 4 теста

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/clipboard configurator/tests/clipboard/test_identity.py configurator/requirements-build.txt
git commit -m "Give each machine a lasting name and a key to prove it"
```

---

### Task 3: Хранилище доверия

**Files:**
- Create: `configurator/src/duo_input/clipboard/trust.py`
- Create: `configurator/tests/clipboard/test_trust.py`

**Interfaces:**
- Consumes: ничего из предыдущих задач.
- Produces: `TrustedPeer` — датакласс `origin_id: str`, `machine_name: str`, `fingerprint: str`, `last_address: str`; `TrustStore(path: Path)` с методами `peer() -> TrustedPeer | None`, `remember(peer: TrustedPeer) -> None`, `forget() -> None`, `update_address(address: str) -> None`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Доверенный пир: ровно один, переживает перезапуск, забывается начисто."""

from __future__ import annotations

from duo_input.clipboard.trust import TrustedPeer, TrustStore

PEER = TrustedPeer(
    origin_id="a" * 32,
    machine_name="LAPTOP-ONE",
    fingerprint="b" * 64,
    last_address="192.168.1.10",
)


def test_empty_store_has_no_peer(tmp_path):
    assert TrustStore(tmp_path / "peers.json").peer() is None


def test_remembered_peer_survives_a_restart(tmp_path):
    path = tmp_path / "peers.json"
    TrustStore(path).remember(PEER)

    assert TrustStore(path).peer() == PEER


def test_remembering_a_second_peer_replaces_the_first(tmp_path):
    path = tmp_path / "peers.json"
    store = TrustStore(path)
    store.remember(PEER)
    other = TrustedPeer("c" * 32, "LAPTOP-TWO", "d" * 64, "192.168.1.11")
    store.remember(other)

    assert store.peer() == other


def test_forgetting_leaves_nothing_behind(tmp_path):
    path = tmp_path / "peers.json"
    store = TrustStore(path)
    store.remember(PEER)
    store.forget()

    assert store.peer() is None
    assert TrustStore(path).peer() is None


def test_address_is_updated_without_touching_the_fingerprint(tmp_path):
    path = tmp_path / "peers.json"
    store = TrustStore(path)
    store.remember(PEER)
    store.update_address("192.168.1.99")

    remembered = TrustStore(path).peer()
    assert remembered.last_address == "192.168.1.99"
    assert remembered.fingerprint == PEER.fingerprint


def test_unreadable_file_is_treated_as_no_peer(tmp_path):
    path = tmp_path / "peers.json"
    path.write_text("{ это не json", "utf-8")

    assert TrustStore(path).peer() is None
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_trust.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.trust'`

- [ ] **Step 3: Написать реализацию**

```python
"""Кто нам свой. Ровно один компьютер, закреплённый отпечатком.

Файл повреждён - значит доверенного пира нет. Это единственный безопасный
ответ: догадываться о содержимом хранилища доверия означало бы принять
соединение, которое пользователь никогда не подтверждал.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path


@dataclass(frozen=True)
class TrustedPeer:
    """Второй компьютер, подтверждённый пользователем на обеих машинах."""

    origin_id: str
    machine_name: str
    fingerprint: str
    last_address: str


class TrustStore:
    """Единственный доверенный пир, хранимый рядом с журналами."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def peer(self) -> TrustedPeer | None:
        try:
            raw = json.loads(self._path.read_text("utf-8"))
        except (OSError, ValueError):
            return None
        if not isinstance(raw, dict):
            return None
        try:
            return TrustedPeer(
                origin_id=str(raw["origin_id"]),
                machine_name=str(raw["machine_name"]),
                fingerprint=str(raw["fingerprint"]),
                last_address=str(raw["last_address"]),
            )
        except KeyError:
            return None

    def remember(self, peer: TrustedPeer) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(asdict(peer), indent=2), "utf-8")

    def forget(self) -> None:
        self._path.unlink(missing_ok=True)

    def update_address(self, address: str) -> None:
        current = self.peer()
        if current is None:
            return
        self.remember(replace(current, last_address=address))


__all__ = ["TrustStore", "TrustedPeer"]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_trust.py -q`
Expected: PASS, 6 тестов

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/trust.py configurator/tests/clipboard/test_trust.py
git commit -m "Remember exactly one computer, by its fingerprint"
```

---

### Task 4: Объявление буфера

**Files:**
- Create: `configurator/src/duo_input/clipboard/offer.py`
- Create: `configurator/tests/clipboard/test_offer.py`

**Interfaces:**
- Consumes: ничего.
- Produces: `ContentDescriptor` — `mime: str`, `size: int`, `sha256: str`; `ClipboardOffer` — `origin_id: str`, `seq: int`, `descriptors: tuple[ContentDescriptor, ...]`, методы `to_dict() -> dict`, `mimes() -> tuple[str, ...]`, `digest_of(mime: str) -> str | None` и classmethod `from_dict(raw: dict) -> ClipboardOffer`; функция `describe(payloads: dict[str, bytes]) -> tuple[ContentDescriptor, ...]`; константа `MAX_CONTENT_BYTES = 33_554_432`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Объявление: что мы говорим о буфере, не отдавая его содержимого."""

from __future__ import annotations

import hashlib

import pytest

from duo_input.clipboard.offer import (
    MAX_CONTENT_BYTES,
    ClipboardOffer,
    ContentDescriptor,
    describe,
)


def test_describe_records_size_and_digest_but_not_the_bytes():
    payload = "привет".encode("utf-8")

    descriptors = describe({"text/plain": payload})

    assert descriptors == (
        ContentDescriptor(
            mime="text/plain",
            size=len(payload),
            sha256=hashlib.sha256(payload).hexdigest(),
        ),
    )


def test_describe_orders_descriptors_by_mime_so_two_machines_agree():
    descriptors = describe({"text/plain": b"a", "image/png": b"b"})

    assert [descriptor.mime for descriptor in descriptors] == ["image/png", "text/plain"]


def test_offer_survives_a_round_trip_through_a_dictionary():
    offer = ClipboardOffer(
        origin_id="a" * 32,
        seq=7,
        descriptors=describe({"text/plain": b"hello"}),
    )

    assert ClipboardOffer.from_dict(offer.to_dict()) == offer


def test_digest_of_finds_the_named_format_and_nothing_else():
    offer = ClipboardOffer("a" * 32, 1, describe({"text/plain": b"hello"}))

    assert offer.digest_of("text/plain") == hashlib.sha256(b"hello").hexdigest()
    assert offer.digest_of("image/png") is None


def test_offer_refuses_a_payload_over_the_ceiling():
    with pytest.raises(ValueError, match="32"):
        describe({"image/png": b"x" * (MAX_CONTENT_BYTES + 1)})


def test_from_dict_refuses_a_dictionary_missing_a_field():
    with pytest.raises(ValueError):
        ClipboardOffer.from_dict({"origin_id": "a" * 32, "seq": 1})
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_offer.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.offer'`

- [ ] **Step 3: Написать реализацию**

```python
"""Что уходит на второй компьютер при копировании - и что не уходит.

Объявление описывает содержимое и не содержит его. Это и есть ленивая модель:
пароль, который скопировали и не вставили, машину не покидает. Отпечаток здесь
нужен дважды - чтобы получатель мог узнать уже виденное, и чтобы отправитель
мог не объявлять обратно то, что сам только что принял.

Дескрипторы всегда упорядочены по имени формата, иначе две машины получили бы
разные объявления для одного и того же буфера.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

#: Больше этого не объявляется вовсе: 32 МиБ.
MAX_CONTENT_BYTES = 33_554_432


@dataclass(frozen=True)
class ContentDescriptor:
    """Один формат буфера: чем он является, сколько весит, чем является точно."""

    mime: str
    size: int
    sha256: str


def describe(payloads: dict[str, bytes]) -> tuple[ContentDescriptor, ...]:
    """Описать снимок буфера, не копируя его наружу."""
    descriptors = []
    for mime in sorted(payloads):
        payload = payloads[mime]
        if len(payload) > MAX_CONTENT_BYTES:
            raise ValueError(
                f"{mime} занимает {len(payload)} байт, потолок 32 МиБ ({MAX_CONTENT_BYTES})"
            )
        descriptors.append(
            ContentDescriptor(mime=mime, size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
        )
    return tuple(descriptors)


@dataclass(frozen=True)
class ClipboardOffer:
    """Объявление о том, что на этой машине что-то скопировали."""

    origin_id: str
    seq: int
    descriptors: tuple[ContentDescriptor, ...]

    def mimes(self) -> tuple[str, ...]:
        return tuple(descriptor.mime for descriptor in self.descriptors)

    def digest_of(self, mime: str) -> str | None:
        for descriptor in self.descriptors:
            if descriptor.mime == mime:
                return descriptor.sha256
        return None

    def to_dict(self) -> dict:
        return {
            "origin_id": self.origin_id,
            "seq": self.seq,
            "descriptors": [
                {"mime": d.mime, "size": d.size, "sha256": d.sha256} for d in self.descriptors
            ],
        }

    @classmethod
    def from_dict(cls, raw: dict) -> ClipboardOffer:
        try:
            descriptors = tuple(
                ContentDescriptor(mime=str(d["mime"]), size=int(d["size"]), sha256=str(d["sha256"]))
                for d in raw["descriptors"]
            )
            return cls(origin_id=str(raw["origin_id"]), seq=int(raw["seq"]), descriptors=descriptors)
        except (KeyError, TypeError) as error:
            raise ValueError(f"объявление неполно: {error}") from error


__all__ = ["MAX_CONTENT_BYTES", "ClipboardOffer", "ContentDescriptor", "describe"]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_offer.py -q`
Expected: PASS, 6 тестов

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/offer.py configurator/tests/clipboard/test_offer.py
git commit -m "Describe a clipboard without sending it"
```

---

### Task 5: Кадрирование

**Files:**
- Create: `configurator/src/duo_input/clipboard/wire.py`
- Create: `configurator/tests/clipboard/test_wire.py`

**Interfaces:**
- Consumes: `MAX_CONTENT_BYTES` из `offer.py`.
- Produces: `MessageType` (IntEnum: `HELLO=1`, `OFFER=2`, `FETCH=3`, `CONTENT=4`, `CONTENT_ERROR=5`, `PING=6`, `PONG=7`, `PAIR_REQUEST=8`, `PAIR_CONFIRM=9`); `Message` — `type: MessageType`, `header: dict`, `blob: bytes`; `encode(message: Message) -> bytes`; `FrameAssembler` с методом `feed(chunk: bytes) -> list[Message]`; исключение `WireError`; константы `PROTOCOL_MAJOR = 1`, `PROTOCOL_MINOR = 0`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Кадрирование: длина, тип, заголовок, сырые байты - и сборка из кусков."""

from __future__ import annotations

import pytest

from duo_input.clipboard.wire import (
    FrameAssembler,
    Message,
    MessageType,
    WireError,
    encode,
)


def test_a_message_survives_a_round_trip():
    message = Message(MessageType.OFFER, {"seq": 3}, b"")

    assembled = FrameAssembler().feed(encode(message))

    assert assembled == [message]


def test_content_carries_both_a_header_and_raw_bytes():
    message = Message(MessageType.CONTENT, {"seq": 3, "mime": "text/plain"}, b"\x00\xffhello")

    assembled = FrameAssembler().feed(encode(message))

    assert assembled[0].blob == b"\x00\xffhello"
    assert assembled[0].header["mime"] == "text/plain"


def test_a_frame_split_across_three_chunks_still_arrives():
    raw = encode(Message(MessageType.PING, {}, b""))
    assembler = FrameAssembler()

    assert assembler.feed(raw[:1]) == []
    assert assembler.feed(raw[1:4]) == []
    assert [message.type for message in assembler.feed(raw[4:])] == [MessageType.PING]


def test_two_frames_in_one_chunk_both_arrive():
    raw = encode(Message(MessageType.PING, {}, b"")) + encode(Message(MessageType.PONG, {}, b""))

    assert [m.type for m in FrameAssembler().feed(raw)] == [MessageType.PING, MessageType.PONG]


def test_an_oversized_length_is_refused_rather_than_allocated():
    raw = (99_999_999).to_bytes(4, "big") + bytes([MessageType.PING]) + b"\x00\x00"

    with pytest.raises(WireError, match="длин"):
        FrameAssembler().feed(raw)


def test_an_unknown_type_is_refused():
    raw = encode(Message(MessageType.PING, {}, b""))
    broken = raw[:4] + bytes([200]) + raw[5:]

    with pytest.raises(WireError, match="тип"):
        FrameAssembler().feed(broken)
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_wire.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.wire'`

- [ ] **Step 3: Написать реализацию**

```python
"""Что течёт по TLS-соединению между двумя копиями Duo Input.

Все кадры устроены одинаково - длина, тип, заголовок в JSON, сырые байты, - и
разбор не зависит от типа кадра. У всех сообщений, кроме CONTENT, сырая часть
пуста; у CONTENT она и есть содержимое буфера.

Контрольной суммы здесь нет намеренно: целостность даёт TLS, а вторая проверка
поверх неё создала бы впечатление, что канал без TLS тоже допустим.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import IntEnum

from .offer import MAX_CONTENT_BYTES

PROTOCOL_MAJOR = 1
PROTOCOL_MINOR = 0

#: Потолок содержимого плюс место под заголовок.
MAX_FRAME_BYTES = MAX_CONTENT_BYTES + 65_536

_LENGTH_BYTES = 4
_TYPE_BYTES = 1
_HEADER_LENGTH_BYTES = 2


class WireError(Exception):
    """Кадр, которого не могло прислать исправное второе устройство."""


class MessageType(IntEnum):
    HELLO = 1
    OFFER = 2
    FETCH = 3
    CONTENT = 4
    CONTENT_ERROR = 5
    PING = 6
    PONG = 7
    PAIR_REQUEST = 8
    PAIR_CONFIRM = 9


@dataclass(frozen=True)
class Message:
    type: MessageType
    header: dict = field(default_factory=dict)
    blob: bytes = b""


def encode(message: Message) -> bytes:
    header = json.dumps(message.header, ensure_ascii=False).encode("utf-8")
    body = len(header).to_bytes(_HEADER_LENGTH_BYTES, "big") + header + message.blob
    payload = bytes([int(message.type)]) + body
    return len(payload).to_bytes(_LENGTH_BYTES, "big") + payload


class FrameAssembler:
    """Собирает кадры из потока байтов, как они приходят из сокета."""

    def __init__(self) -> None:
        self._buffer = bytearray()

    def feed(self, chunk: bytes) -> list[Message]:
        self._buffer.extend(chunk)
        messages: list[Message] = []
        while True:
            if len(self._buffer) < _LENGTH_BYTES:
                return messages
            length = int.from_bytes(self._buffer[:_LENGTH_BYTES], "big")
            if length > MAX_FRAME_BYTES:
                raise WireError(f"объявленная длина кадра {length} больше допустимой")
            if len(self._buffer) < _LENGTH_BYTES + length:
                return messages
            payload = bytes(self._buffer[_LENGTH_BYTES : _LENGTH_BYTES + length])
            del self._buffer[: _LENGTH_BYTES + length]
            messages.append(_decode_payload(payload))


def _decode_payload(payload: bytes) -> Message:
    if len(payload) < _TYPE_BYTES + _HEADER_LENGTH_BYTES:
        raise WireError("кадр короче собственного заголовка")
    try:
        message_type = MessageType(payload[0])
    except ValueError as error:
        raise WireError(f"неизвестный тип сообщения {payload[0]}") from error

    start = _TYPE_BYTES
    header_length = int.from_bytes(payload[start : start + _HEADER_LENGTH_BYTES], "big")
    header_start = start + _HEADER_LENGTH_BYTES
    header_end = header_start + header_length
    if header_end > len(payload):
        raise WireError("заголовок не помещается в кадр")

    try:
        header = json.loads(payload[header_start:header_end].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise WireError(f"заголовок не разбирается: {error}") from error
    if not isinstance(header, dict):
        raise WireError("заголовок не является объектом")

    return Message(message_type, header, payload[header_end:])


__all__ = [
    "MAX_FRAME_BYTES",
    "PROTOCOL_MAJOR",
    "PROTOCOL_MINOR",
    "FrameAssembler",
    "Message",
    "MessageType",
    "WireError",
    "encode",
]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_wire.py -q`
Expected: PASS, 6 тестов

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/wire.py configurator/tests/clipboard/test_wire.py
git commit -m "Frame what the two machines say to each other"
```

---

### Task 6: Граница платформы и ленивое содержимое

**Files:**
- Create: `configurator/src/duo_input/clipboard/backend.py`
- Create: `configurator/tests/clipboard/test_backend.py`

**Interfaces:**
- Consumes: `ClipboardOffer` из `offer.py`.
- Produces: `ORIGIN_MIME = "application/x-duo-input-origin"`; `base_mime(mime_type: str) -> str`; `ClipboardSnapshot` — `payloads: dict[str, bytes]`; `ContentFetcher` — вызываемое `(mime: str) -> bytes`; `RemoteMimeData(offer, fetcher)` — подкласс `QMimeData`; `ClipboardBackend` — `Protocol` с сигналом `snapshot_taken` и методами `start()`, `stop()`, `publish(offer, fetcher)`, `payload(mime) -> bytes | None`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Ленивое содержимое: данные тянутся в момент запроса, а не заранее."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from duo_input.clipboard.backend import ORIGIN_MIME, RemoteMimeData
from duo_input.clipboard.offer import ClipboardOffer, describe


def _offer() -> ClipboardOffer:
    return ClipboardOffer("a" * 32, 4, describe({"text/plain": "привет".encode("utf-8")}))


def test_formats_are_announced_without_fetching_anything():
    calls: list[str] = []
    data = RemoteMimeData(_offer(), calls.append)

    formats = data.formats()

    assert "text/plain" in formats
    assert calls == []


def test_the_origin_marker_is_announced_alongside_the_real_formats():
    data = RemoteMimeData(_offer(), lambda mime: b"")

    assert ORIGIN_MIME in data.formats()
    assert bytes(data.data(ORIGIN_MIME)) == b"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa:4"


def test_asking_for_a_format_fetches_it_once_and_caches_it():
    calls: list[str] = []

    def fetch(mime: str) -> bytes:
        calls.append(mime)
        return "привет".encode("utf-8")

    data = RemoteMimeData(_offer(), fetch)

    first = bytes(data.data("text/plain"))
    second = bytes(data.data("text/plain"))

    assert first == second == "привет".encode("utf-8")
    assert calls == ["text/plain"]


def test_a_format_that_was_never_announced_is_not_fetched():
    calls: list[str] = []
    data = RemoteMimeData(_offer(), calls.append)

    assert bytes(data.data("image/png")) == b""
    assert calls == []


def test_a_charset_parameter_still_finds_the_announced_format():
    """Qt спрашивает text/plain;charset=utf-8, а объявляли мы text/plain.

    Точное сравнение строк здесь означало бы вставку, которая не работает
    никогда и ничего об этом не сообщает. Так было замечено в спайке Task 1.
    """
    data = RemoteMimeData(_offer(), lambda mime: "привет".encode("utf-8"))

    assert bytes(data.retrieveData("text/plain;charset=utf-8", None)) == "привет".encode("utf-8")


def test_a_failed_fetch_yields_nothing_rather_than_raising():
    def fetch(mime: str) -> bytes:
        raise TimeoutError("пир не ответил")

    data = RemoteMimeData(_offer(), fetch)

    assert bytes(data.data("text/plain")) == b""
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_backend.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.backend'`

- [ ] **Step 3: Написать реализацию**

```python
"""Граница между операционной системой и всем остальным.

RemoteMimeData - это обещание, а не данные: она объявляет форматы, которые
второй компьютер согласился отдать, и идёт за содержимым только тогда, когда
кто-то действительно вставляет. Поэтому скопированный и не вставленный пароль
не покидает машину, на которой его скопировали.

Маркер происхождения объявляется рядом с настоящими форматами: по нему
собственный наблюдатель узнаёт свою же вставку и не объявляет её обратно.
"""

from __future__ import annotations

from typing import Callable, Protocol, runtime_checkable

from PySide6.QtCore import QMimeData

from .offer import ClipboardOffer

#: Формат-метка: чьё это содержимое и под каким номером.
ORIGIN_MIME = "application/x-duo-input-origin"

#: Как достать содержимое одного формата. Может бросить - это нормально.
ContentFetcher = Callable[[str], bytes]


def base_mime(mime_type: str) -> str:
    """Имя формата без параметров: ``text/plain;charset=utf-8`` -> ``text/plain``.

    Qt спрашивает содержимое под именем с параметром, а объявляли мы имя без
    него. Сравнение строк целиком означало бы вставку, которая молча не
    работает: ни ошибки, ни записи в журнале, просто пустой буфер.
    """
    return mime_type.split(";", 1)[0].strip()


class ClipboardSnapshot:
    """Локальный снимок буфера: что скопировали на этой машине."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self.payloads = dict(payloads)

    def payload(self, mime: str) -> bytes | None:
        return self.payloads.get(mime)


class RemoteMimeData(QMimeData):
    """Содержимое второго компьютера, которого здесь ещё нет."""

    def __init__(self, offer: ClipboardOffer, fetcher: ContentFetcher) -> None:
        super().__init__()
        self._offer = offer
        self._fetcher = fetcher
        self._cache: dict[str, bytes] = {}
        self._marker = f"{offer.origin_id}:{offer.seq}".encode("ascii")

    def formats(self):  # noqa: N802 - Qt API
        return [*self._offer.mimes(), ORIGIN_MIME]

    def retrieveData(self, mime_type: str, preferred_type):  # noqa: N802 - Qt API
        requested = base_mime(mime_type)
        if requested == ORIGIN_MIME:
            return self._marker
        if requested not in self._offer.mimes():
            return b""
        if requested in self._cache:
            return self._cache[requested]
        try:
            payload = self._fetcher(requested)
        except Exception:  # noqa: BLE001 - пустая вставка честнее, чем падение
            return b""
        self._cache[requested] = payload
        return payload


@runtime_checkable
class ClipboardBackend(Protocol):
    """Всё, что подсистеме нужно знать об операционной системе."""

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def publish(self, offer: ClipboardOffer, fetcher: ContentFetcher) -> None: ...

    def payload(self, mime: str) -> bytes | None: ...


__all__ = [
    "ORIGIN_MIME",
    "ClipboardBackend",
    "ClipboardSnapshot",
    "ContentFetcher",
    "RemoteMimeData",
    "base_mime",
]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_backend.py -q`
Expected: PASS, 5 тестов

Если тесты падают с сообщением о недоступной платформе — значит `QMimeData` требует приложения; добавить в файл теста фикстуру `qapp` из pytest-qt, приняв её первым аргументом каждого теста.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/backend.py configurator/tests/clipboard/test_backend.py
git commit -m "Promise clipboard formats before fetching their contents"
```

---

### Task 7: Наблюдение за буфером Windows

**Files:**
- Create: `configurator/src/duo_input/clipboard/windows_backend.py`
- Create: `configurator/tests/clipboard/test_windows_backend.py`
- Modify: `configurator/pyproject.toml`

**Interfaces:**
- Consumes: `ClipboardSnapshot`, `RemoteMimeData`, `ORIGIN_MIME` из `backend.py`; `MAX_CONTENT_BYTES` из `offer.py`.
- Produces: `WindowsClipboardBackend(QObject)` — сигнал `snapshot_taken(object)` с `ClipboardSnapshot`; методы `start`, `stop`, `publish`, `payload`; модульные функции `is_private(formats: list[str]) -> bool` и `snapshot_from(mime_data) -> ClipboardSnapshot`; константы `PRIVATE_MARKERS`, `SYNCED_MIMES`, `DEBOUNCE_MS = 200`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Снимок локального буфера: что берём, что пропускаем, чего не трогаем."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from duo_input.clipboard.backend import ORIGIN_MIME
from duo_input.clipboard.offer import MAX_CONTENT_BYTES
from duo_input.clipboard.windows_backend import is_private, snapshot_from


class _FakeMimeData:
    """Утиная замена QMimeData: только то, что читает снимок."""

    def __init__(self, payloads: dict[str, bytes]) -> None:
        self._payloads = payloads

    def formats(self) -> list[str]:
        return list(self._payloads)

    def data(self, mime: str) -> bytes:
        return self._payloads.get(mime, b"")


def test_a_password_manager_marker_makes_the_clipboard_private():
    formats = [
        "text/plain",
        'application/x-qt-windows-mime;value="ExcludeClipboardContentFromMonitorProcessing"',
    ]

    assert is_private(formats) is True


def test_an_ordinary_clipboard_is_not_private():
    assert is_private(["text/plain", "text/html"]) is False


def test_our_own_marker_makes_the_clipboard_private_to_us():
    assert is_private(["text/plain", ORIGIN_MIME]) is True


def test_snapshot_keeps_only_the_formats_we_synchronise():
    data = _FakeMimeData({"text/plain": b"hello", "text/html": b"<b>hello</b>"})

    snapshot = snapshot_from(data)

    assert set(snapshot.payloads) == {"text/plain"}


def test_snapshot_of_an_oversized_payload_is_empty():
    data = _FakeMimeData({"image/png": b"x" * (MAX_CONTENT_BYTES + 1)})

    assert snapshot_from(data).payloads == {}


def test_snapshot_of_an_empty_clipboard_is_empty():
    assert snapshot_from(_FakeMimeData({})).payloads == {}
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_windows_backend.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.windows_backend'`

- [ ] **Step 3: Написать реализацию**

```python
"""Наблюдение за буфером обмена Windows и публикация в него.

Одна операция Ctrl+C порождает несколько событий: приложения выкладывают
форматы по очереди. Поэтому события собираются дебаунсом и берётся последнее
состояние, а не каждое промежуточное.

Буфер бывает заперт другим процессом, и Qt тогда отдаёт пустоту. Пустой снимок
означает не "пользователь скопировал ничего", а "прочитать не удалось", и
попытка повторяется.

Маркеры приватности Windows уважаются: их ставят менеджеры паролей, и на них же
смотрит собственная "История буфера обмена" системы. Содержимое, помеченное
так, не объявляется никогда.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QTimer, Signal

from .backend import ORIGIN_MIME, ClipboardSnapshot, ContentFetcher, RemoteMimeData
from .offer import MAX_CONTENT_BYTES, ClipboardOffer

#: Форматы, которые синхронизируются в milestone 1.
SYNCED_MIMES = ("text/plain", "text/uri-list", "image/png")

#: Просьбы не запоминать это содержимое, в том виде, в каком их показывает Qt.
PRIVATE_MARKERS = (
    'application/x-qt-windows-mime;value="ExcludeClipboardContentFromMonitorProcessing"',
    'application/x-qt-windows-mime;value="CanIncludeInClipboardHistory"',
)

#: Сколько ждать, пока приложение доложит все форматы одной операции.
DEBOUNCE_MS = 200

#: Сколько раз перечитывать буфер, если он оказался заперт.
RETRY_LIMIT = 3


def is_private(formats: list[str]) -> bool:
    """Просило ли содержимое, чтобы его не запоминали и не пересылали."""
    if ORIGIN_MIME in formats:
        return True
    return any(marker in formats for marker in PRIVATE_MARKERS)


def snapshot_from(mime_data) -> ClipboardSnapshot:
    """Взять из буфера то, что мы умеем синхронизировать, и ничего сверх."""
    formats = list(mime_data.formats())
    if is_private(formats):
        return ClipboardSnapshot({})

    payloads: dict[str, bytes] = {}
    for mime in SYNCED_MIMES:
        if mime not in formats:
            continue
        payload = bytes(mime_data.data(mime))
        if not payload or len(payload) > MAX_CONTENT_BYTES:
            continue
        payloads[mime] = payload
    return ClipboardSnapshot(payloads)


class WindowsClipboardBackend(QObject):
    """Единственная реализация границы платформы в milestone 1."""

    snapshot_taken = Signal(object)

    def __init__(self, clipboard, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._clipboard = clipboard
        self._running = False
        self._suspended = False
        self._attempts = 0
        self._published: RemoteMimeData | None = None
        self._local: ClipboardSnapshot = ClipboardSnapshot({})

        self._debounce = QTimer(self)
        self._debounce.setSingleShot(True)
        self._debounce.setInterval(DEBOUNCE_MS)
        self._debounce.timeout.connect(self._take_snapshot)

    def start(self) -> None:
        if self._running:
            return
        self._clipboard.dataChanged.connect(self._on_data_changed)
        self._running = True

    def stop(self) -> None:
        if not self._running:
            return
        self._clipboard.dataChanged.disconnect(self._on_data_changed)
        self._debounce.stop()
        self._published = None
        self._local = ClipboardSnapshot({})
        self._running = False

    def publish(self, offer: ClipboardOffer, fetcher: ContentFetcher) -> None:
        """Объявить в локальном буфере то, что лежит на втором компьютере."""
        self._published = RemoteMimeData(offer, fetcher)
        self._suspended = True
        try:
            self._clipboard.setMimeData(self._published)
        finally:
            self._suspended = False

    def payload(self, mime: str) -> bytes | None:
        return self._local.payload(mime)

    # ------------------------------------------------------------------ внутреннее

    def _on_data_changed(self) -> None:
        if self._suspended:
            return
        self._attempts = 0
        self._debounce.start()

    def _take_snapshot(self) -> None:
        snapshot = snapshot_from(self._clipboard.mimeData())
        if not snapshot.payloads and self._attempts < RETRY_LIMIT:
            self._attempts += 1
            self._debounce.start()
            return
        if not snapshot.payloads:
            return
        self._local = snapshot
        self.snapshot_taken.emit(snapshot)


__all__ = [
    "DEBOUNCE_MS",
    "PRIVATE_MARKERS",
    "RETRY_LIMIT",
    "SYNCED_MIMES",
    "WindowsClipboardBackend",
    "is_private",
    "snapshot_from",
]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_windows_backend.py -q`
Expected: PASS, 6 тестов

- [ ] **Step 5: Выяснить, работает ли настоящий `QClipboard` в offscreen**

Run:

```bash
cd configurator && ../.venv/Scripts/python.exe -c "
import os; os.environ['QT_QPA_PLATFORM']='offscreen'
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QMimeData
app = QApplication([])
data = QMimeData(); data.setText('проверка')
app.clipboard().setMimeData(data)
print('прочитано:', app.clipboard().mimeData().text())
"
```

Если печатается `проверка` — живой `QClipboard` в offscreen работает, и тест наблюдения можно писать без маркера. Если нет — добавить в `configurator/pyproject.toml`, в секцию `[tool.pytest.ini_options]`:

```toml
markers = [
    "real_clipboard: требует настоящей сессии Windows; в offscreen не запускается",
]
```

и пометить будущие тесты живого буфера этим маркером. Записать результат в сообщение коммита — следующая задача на него опирается.

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/clipboard/windows_backend.py configurator/tests/clipboard/test_windows_backend.py configurator/pyproject.toml
git commit -m "Take a clipboard snapshot, and refuse the ones marked private"
```

---

### Task 8: Правила подсистемы и подавление петель

**Files:**
- Create: `configurator/src/duo_input/clipboard/service.py`
- Create: `configurator/tests/clipboard/test_service_rules.py`

**Interfaces:**
- Consumes: `ClipboardSnapshot`, `ClipboardBackend` из `backend.py`; `ClipboardOffer`, `describe` из `offer.py`.
- Produces: `ClipboardService(QObject)` с методами `attach_backend(backend)`, `on_local_snapshot(snapshot)`, `on_remote_offer(offer)`, `content_for(mime, seq) -> bytes | None`, свойствами `own_origin_id`, `last_sent_offer`, `last_received_offer`; сигналами `offer_ready(object)` и `content_requested(object)`.

Сеть в этой задаче не участвует: `ClipboardService` испускает сигнал «есть что объявить», а кто это отправит — дело задачи 12.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Три пояса подавления петель и правило свежести объявления."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from duo_input.clipboard.backend import ClipboardSnapshot
from duo_input.clipboard.offer import ClipboardOffer, describe
from duo_input.clipboard.service import ClipboardService

OURS = "1" * 32
THEIRS = "2" * 32


class _FakeBackend:
    """Backend, который ничего не делает и всё запоминает."""

    def __init__(self) -> None:
        self.published: list[ClipboardOffer] = []
        self.started = False

    def start(self) -> None:
        self.started = True

    def stop(self) -> None:
        self.started = False

    def publish(self, offer, fetcher) -> None:
        self.published.append(offer)

    def payload(self, mime):
        return b"hello" if mime == "text/plain" else None


def _service() -> tuple[ClipboardService, _FakeBackend]:
    service = ClipboardService(own_origin_id=OURS)
    backend = _FakeBackend()
    service.attach_backend(backend)
    return service, backend


def test_a_local_copy_produces_an_offer():
    service, _ = _service()
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"hello"}))

    assert len(sent) == 1
    assert sent[0].origin_id == OURS
    assert sent[0].mimes() == ("text/plain",)


def test_sequence_numbers_increase_with_every_offer():
    service, _ = _service()
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"one"}))
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"two"}))

    assert sent[1].seq > sent[0].seq


def test_a_remote_offer_is_published_to_the_local_clipboard():
    service, backend = _service()

    offer = ClipboardOffer(THEIRS, 1, describe({"text/plain": b"remote"}))
    service.on_remote_offer(offer)

    assert backend.published == [offer]


def test_second_belt_a_snapshot_matching_what_we_just_received_is_not_offered_back():
    service, _ = _service()
    service.on_remote_offer(ClipboardOffer(THEIRS, 1, describe({"text/plain": b"remote"})))
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"remote"}))

    assert sent == []


def test_a_genuinely_new_copy_after_a_remote_one_is_offered():
    service, _ = _service()
    service.on_remote_offer(ClipboardOffer(THEIRS, 1, describe({"text/plain": b"remote"})))
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)

    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"something else"}))

    assert len(sent) == 1


def test_third_belt_an_offer_bearing_our_own_origin_is_ignored():
    service, backend = _service()

    service.on_remote_offer(ClipboardOffer(OURS, 5, describe({"text/plain": b"boomerang"})))

    assert backend.published == []


def test_a_stale_sequence_number_is_ignored():
    service, backend = _service()
    service.on_remote_offer(ClipboardOffer(THEIRS, 5, describe({"text/plain": b"new"})))
    service.on_remote_offer(ClipboardOffer(THEIRS, 4, describe({"text/plain": b"old"})))

    assert len(backend.published) == 1


def test_content_for_answers_only_for_the_offer_we_last_sent():
    service, _ = _service()
    sent: list[ClipboardOffer] = []
    service.offer_ready.connect(sent.append)
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"hello"}))
    seq = sent[0].seq

    assert service.content_for("text/plain", seq) == b"hello"
    assert service.content_for("text/plain", seq + 1) is None
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_service_rules.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.service'`

- [ ] **Step 3: Написать реализацию**

```python
"""Правила общего буфера: что объявлять, что публиковать, что игнорировать.

Здесь нет ни сокетов, ни Win32. Сервис получает снимки от границы платформы и
объявления от связи, а наружу выдаёт сигналы: "вот что стоит объявить" и "вот
что у нас попросили". Благодаря этому все правила проверяются без сети.

Три пояса против петли, потому что ни один не полон в одиночку. Первый - маркер
происхождения, его ставит и проверяет граница платформы. Второй - сравнение
отпечатков: то, что мы только что приняли, не уходит обратно. Третий - отказ от
объявления с нашим собственным origin_id.
"""

from __future__ import annotations

import hashlib

from PySide6.QtCore import QObject, Signal

from .backend import ClipboardSnapshot
from .offer import ClipboardOffer, describe


class ClipboardService(QObject):
    """Мозг подсистемы. Ничего не знает ни об ОС, ни о сети."""

    offer_ready = Signal(object)
    content_requested = Signal(object)

    def __init__(self, own_origin_id: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._own_origin_id = own_origin_id
        self._backend = None
        self._seq = 0
        self._last_sent: ClipboardOffer | None = None
        self._last_sent_payloads: dict[str, bytes] = {}
        self._last_received: ClipboardOffer | None = None

    @property
    def own_origin_id(self) -> str:
        return self._own_origin_id

    @property
    def last_sent_offer(self) -> ClipboardOffer | None:
        return self._last_sent

    @property
    def last_received_offer(self) -> ClipboardOffer | None:
        return self._last_received

    def attach_backend(self, backend) -> None:
        self._backend = backend

    # ------------------------------------------------------------------ локальное

    def on_local_snapshot(self, snapshot: ClipboardSnapshot) -> None:
        if not snapshot.payloads:
            return
        if self._echoes_what_we_received(snapshot):
            return

        self._seq += 1
        offer = ClipboardOffer(
            origin_id=self._own_origin_id,
            seq=self._seq,
            descriptors=describe(snapshot.payloads),
        )
        self._last_sent = offer
        self._last_sent_payloads = dict(snapshot.payloads)
        self.offer_ready.emit(offer)

    def content_for(self, mime: str, seq: int) -> bytes | None:
        """Содержимое, которое у нас просят - но только для текущего объявления."""
        if self._last_sent is None or seq != self._last_sent.seq:
            return None
        return self._last_sent_payloads.get(mime)

    # ------------------------------------------------------------------ удалённое

    def on_remote_offer(self, offer: ClipboardOffer) -> None:
        # Третий пояс: объявление вернулось к нам же.
        if offer.origin_id == self._own_origin_id:
            return
        if self._last_received is not None and offer.seq <= self._last_received.seq:
            return
        if self._backend is None:
            return

        self._last_received = offer
        self._backend.publish(offer, lambda mime: self._fetch(mime, offer))

    def _fetch(self, mime: str, offer: ClipboardOffer) -> bytes:
        request = {"seq": offer.seq, "mime": mime, "result": None}
        self.content_requested.emit(request)
        payload = request.get("result")
        if payload is None:
            raise TimeoutError(f"содержимое {mime} не получено")
        return payload

    # ------------------------------------------------------------------ пояса

    def _echoes_what_we_received(self, snapshot: ClipboardSnapshot) -> bool:
        """Второй пояс: это то, что нам только что прислали."""
        if self._last_received is None:
            return False
        for mime, payload in snapshot.payloads.items():
            expected = self._last_received.digest_of(mime)
            if expected is not None and expected == hashlib.sha256(payload).hexdigest():
                return True
        return False


__all__ = ["ClipboardService"]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_service_rules.py -q`
Expected: PASS, 8 тестов

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/service.py configurator/tests/clipboard/test_service_rules.py
git commit -m "Stop a shared clipboard from echoing itself"
```

---

### Task 9: TLS-связь между двумя машинами

**Files:**
- Create: `configurator/src/duo_input/clipboard/peer.py`
- Create: `configurator/src/duo_input/clipboard/listener.py`
- Create: `configurator/tests/clipboard/test_peer_link.py`

**Interfaces:**
- Consumes: `NodeIdentity`, `fingerprint_of` из `identity.py`; `Message`, `MessageType`, `FrameAssembler`, `encode`, `WireError` из `wire.py`.
- Produces: `ssl_configuration(identity) -> QSslConfiguration`; `PeerLink(QObject)` — сигналы `message_received(object)`, `connected(str)`, `disconnected(str)`; методы `connect_to(address: str, port: int, expected_fingerprint: str | None)`, `adopt(socket, expected_fingerprint)`, `send(message)`, `close()`, свойство `peer_fingerprint`; `PeerListener(QObject)` — сигнал `link_ready(object)`, методы `listen(port) -> bool`, `stop()`, свойство `port`.

- [ ] **Step 1: Написать падающий тест на loopback**

```python
"""Два узла в одном процессе: TLS поднимается, отпечаток закрепляется."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.listener import PeerListener
from duo_input.clipboard.peer import PeerLink
from duo_input.clipboard.wire import Message, MessageType


@pytest.fixture
def identities(tmp_path):
    return load_or_create(tmp_path / "one"), load_or_create(tmp_path / "two")


def test_a_message_crosses_a_real_tls_connection(qtbot, identities):
    server_identity, client_identity = identities

    listener = PeerListener(server_identity)
    assert listener.listen(0) is True

    incoming: list[PeerLink] = []
    listener.link_ready.connect(incoming.append)

    client = PeerLink(client_identity)
    with qtbot.waitSignal(client.connected, timeout=5000):
        client.connect_to("127.0.0.1", listener.port, server_identity.fingerprint)

    qtbot.waitUntil(lambda: incoming, timeout=5000)
    server_link = incoming[0]

    received: list[Message] = []
    server_link.message_received.connect(received.append)
    client.send(Message(MessageType.PING, {"hello": "мир"}, b""))

    qtbot.waitUntil(lambda: received, timeout=5000)
    assert received[0].type is MessageType.PING
    assert received[0].header["hello"] == "мир"

    listener.stop()
    client.close()


def test_a_wrong_fingerprint_is_refused(qtbot, identities):
    server_identity, client_identity = identities

    listener = PeerListener(server_identity)
    assert listener.listen(0) is True

    client = PeerLink(client_identity)
    with qtbot.waitSignal(client.disconnected, timeout=5000):
        client.connect_to("127.0.0.1", listener.port, "f" * 64)

    listener.stop()
    client.close()


def test_the_peer_fingerprint_is_recorded_after_the_handshake(qtbot, identities):
    server_identity, client_identity = identities

    listener = PeerListener(server_identity)
    listener.listen(0)
    client = PeerLink(client_identity)
    with qtbot.waitSignal(client.connected, timeout=5000):
        client.connect_to("127.0.0.1", listener.port, server_identity.fingerprint)

    assert client.peer_fingerprint == server_identity.fingerprint

    listener.stop()
    client.close()
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_peer_link.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.peer'`

- [ ] **Step 3: Написать `peer.py`**

```python
"""Одно защищённое соединение со вторым компьютером.

Сертификаты здесь самоподписанные, поэтому Qt справедливо считает их ошибкой.
Ошибка снимается ровно в одном случае: предъявленный сертификат совпадает по
отпечатку с закреплённым при парринге. Всё остальное - разрыв. Это единственное
место в подсистеме, где решается, свой ли собеседник.

Во время парринга закреплённого отпечатка ещё нет, и тогда принимается любой:
доверие в этот момент даёт не сертификат, а человек, сверяющий код на двух
экранах.
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QCryptographicHash, QObject, Signal
from PySide6.QtNetwork import QSsl, QSslCertificate, QSslConfiguration, QSslKey, QSslSocket

from .identity import NodeIdentity
from .wire import FrameAssembler, Message, WireError, encode


def ssl_configuration(identity: NodeIdentity) -> QSslConfiguration:
    """Наш сертификат и ключ, с требованием, чтобы пир тоже представился."""
    configuration = QSslConfiguration.defaultConfiguration()
    configuration.setLocalCertificate(QSslCertificate(QByteArray(identity.certificate_pem)))
    configuration.setPrivateKey(
        QSslKey(QByteArray(identity.key_pem), QSsl.KeyAlgorithm.Ec)
    )
    configuration.setPeerVerifyMode(QSslSocket.PeerVerifyMode.VerifyPeer)
    return configuration


def _fingerprint_of_socket(socket: QSslSocket) -> str:
    certificate = socket.peerCertificate()
    if certificate.isNull():
        return ""
    digest = certificate.digest(QCryptographicHash.Algorithm.Sha256)
    return bytes(digest.toHex()).decode("ascii")


class PeerLink(QObject):
    """Кадры туда и обратно по одному TLS-соединению."""

    message_received = Signal(object)
    connected = Signal(str)
    disconnected = Signal(str)

    def __init__(self, identity: NodeIdentity, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._identity = identity
        self._socket: QSslSocket | None = None
        self._assembler = FrameAssembler()
        self._expected_fingerprint: str | None = None
        self._peer_fingerprint = ""

    @property
    def peer_fingerprint(self) -> str:
        return self._peer_fingerprint

    @property
    def is_open(self) -> bool:
        return self._socket is not None and self._socket.isEncrypted()

    def connect_to(self, address: str, port: int, expected_fingerprint: str | None) -> None:
        self._expected_fingerprint = expected_fingerprint
        socket = QSslSocket(self)
        socket.setSslConfiguration(ssl_configuration(self._identity))
        self._wire_up(socket)
        socket.connectToHostEncrypted(address, port)

    def adopt(self, socket: QSslSocket, expected_fingerprint: str | None) -> None:
        """Принять уже зашифрованный входящий сокет."""
        self._expected_fingerprint = expected_fingerprint
        self._wire_up(socket)
        self._on_encrypted()

    def send(self, message: Message) -> None:
        if self._socket is None:
            return
        self._socket.write(encode(message))

    def close(self) -> None:
        if self._socket is not None:
            self._socket.abort()
            self._socket = None

    # ------------------------------------------------------------------ внутреннее

    def _wire_up(self, socket: QSslSocket) -> None:
        self._socket = socket
        socket.setParent(self)
        socket.sslErrors.connect(self._on_ssl_errors)
        socket.encrypted.connect(self._on_encrypted)
        socket.readyRead.connect(self._on_ready_read)
        socket.disconnected.connect(lambda: self._fail("соединение закрыто"))

    def _on_ssl_errors(self, errors) -> None:
        socket = self._socket
        if socket is None:
            return
        fingerprint = _fingerprint_of_socket(socket)
        if self._expected_fingerprint is None and fingerprint:
            # Парринг: доверие ещё не выдано, его сейчас выдаст человек.
            socket.ignoreSslErrors()
            return
        if fingerprint and fingerprint == self._expected_fingerprint:
            socket.ignoreSslErrors()
            return
        self._fail("сертификат не тот, что был закреплён")

    def _on_encrypted(self) -> None:
        socket = self._socket
        if socket is None:
            return
        self._peer_fingerprint = _fingerprint_of_socket(socket)
        if self._expected_fingerprint and self._peer_fingerprint != self._expected_fingerprint:
            self._fail("сертификат не тот, что был закреплён")
            return
        self.connected.emit(self._peer_fingerprint)

    def _on_ready_read(self) -> None:
        socket = self._socket
        if socket is None:
            return
        try:
            messages = self._assembler.feed(bytes(socket.readAll()))
        except WireError as error:
            self._fail(str(error))
            return
        for message in messages:
            self.message_received.emit(message)

    def _fail(self, reason: str) -> None:
        if self._socket is not None:
            self._socket.abort()
            self._socket = None
        self.disconnected.emit(reason)


__all__ = ["PeerLink", "ssl_configuration"]
```

- [ ] **Step 4: Написать `listener.py`**

```python
"""Приём входящего соединения от второго компьютера."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QHostAddress, QSslServer, QSslSocket

from .identity import NodeIdentity
from .peer import PeerLink, ssl_configuration


class PeerListener(QObject):
    """Слушает порт и выдаёт готовую связь на каждое принятое соединение."""

    link_ready = Signal(object)

    def __init__(self, identity: NodeIdentity, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._identity = identity
        self._expected_fingerprint: str | None = None
        self._server = QSslServer(self)
        self._server.setSslConfiguration(ssl_configuration(identity))
        self._server.pendingConnectionAvailable.connect(self._on_pending)
        self._links: list[PeerLink] = []

    @property
    def port(self) -> int:
        return int(self._server.serverPort())

    @property
    def is_listening(self) -> bool:
        return self._server.isListening()

    def expect(self, fingerprint: str | None) -> None:
        """Чей отпечаток считать своим. None означает режим парринга."""
        self._expected_fingerprint = fingerprint

    def listen(self, port: int) -> bool:
        return self._server.listen(QHostAddress.SpecialAddress.Any, port)

    def stop(self) -> None:
        for link in self._links:
            link.close()
        self._links.clear()
        self._server.close()

    def _on_pending(self) -> None:
        while True:
            socket = self._server.nextPendingConnection()
            if socket is None:
                return
            link = PeerLink(self._identity, self)
            link.adopt(socket, self._expected_fingerprint)
            self._links.append(link)
            self.link_ready.emit(link)


__all__ = ["PeerListener"]
```

- [ ] **Step 5: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_peer_link.py -q`
Expected: PASS, 3 теста

Если `test_a_message_crosses_a_real_tls_connection` виснет: проверить, что `PeerListener.expect` вызван до `listen` либо оставлен `None`; при `None` сервер принимает любой сертификат, что для этого теста и нужно.

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/clipboard/peer.py configurator/src/duo_input/clipboard/listener.py configurator/tests/clipboard/test_peer_link.py
git commit -m "Talk to the other computer over TLS, and only to the pinned one"
```

---

### Task 10: Обнаружение в локальной сети

**Files:**
- Create: `configurator/src/duo_input/clipboard/discovery.py`
- Create: `configurator/tests/clipboard/test_discovery_beacon.py`

**Interfaces:**
- Consumes: `PROTOCOL_MAJOR` из `wire.py`.
- Produces: `Beacon` — датакласс `origin_id: str`, `machine_name: str`, `fingerprint: str`, `port: int`, `protocol_major: int`; `encode_beacon(beacon) -> bytes`; `decode_beacon(raw: bytes, own_origin_id: str) -> Beacon | None`; `Discovery(QObject)` — сигнал `peer_seen(object, str)`, методы `start(beacon)`, `stop()`; константы `GROUP_ADDRESS = "239.255.76.67"`, `BEACON_PORT = 47655`, `BEACON_INTERVAL_MS = 2000`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Маячок: что он несёт, чего не несёт и чьи маячки игнорируются."""

from __future__ import annotations

import json

from duo_input.clipboard.discovery import Beacon, decode_beacon, encode_beacon
from duo_input.clipboard.wire import PROTOCOL_MAJOR

OURS = "1" * 32
THEIRS = "2" * 32

BEACON = Beacon(
    origin_id=THEIRS,
    machine_name="LAPTOP-TWO",
    fingerprint="f" * 64,
    port=47654,
    protocol_major=PROTOCOL_MAJOR,
)


def test_a_beacon_survives_a_round_trip():
    assert decode_beacon(encode_beacon(BEACON), OURS) == BEACON


def test_our_own_beacon_is_ignored():
    ours = Beacon(OURS, "LAPTOP-ONE", "a" * 64, 47654, PROTOCOL_MAJOR)

    assert decode_beacon(encode_beacon(ours), OURS) is None


def test_a_beacon_from_another_protocol_generation_is_ignored():
    stranger = Beacon(THEIRS, "LAPTOP-TWO", "f" * 64, 47654, PROTOCOL_MAJOR + 1)

    assert decode_beacon(encode_beacon(stranger), OURS) is None


def test_rubbish_on_the_wire_is_ignored_rather_than_raising():
    assert decode_beacon(b"\x00\x01 not json", OURS) is None
    assert decode_beacon(json.dumps({"origin_id": THEIRS}).encode("utf-8"), OURS) is None


def test_a_beacon_never_carries_clipboard_content():
    raw = json.loads(encode_beacon(BEACON).decode("utf-8"))

    assert set(raw) == {"origin_id", "machine_name", "fingerprint", "port", "protocol_major"}
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_discovery_beacon.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.discovery'`

- [ ] **Step 3: Написать реализацию**

```python
"""Как две копии Duo Input находят друг друга в локальной сети.

Маячок рассылается только тогда, когда идёт парринг или когда спаренный
компьютер не найден по последнему известному адресу. Постоянная рассылка
означала бы, что присутствие устройства видно всей сети всё время, а платят за
это удобством, которого нет: после парринга адрес уже известен.

Маячок не даёт доверия. Он говорит "здесь есть Duo Input" и ничего больше;
доверие выдаётся человеком при парринге.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtNetwork import QHostAddress, QUdpSocket

from .wire import PROTOCOL_MAJOR

GROUP_ADDRESS = "239.255.76.67"
BEACON_PORT = 47655
BEACON_INTERVAL_MS = 2000


@dataclass(frozen=True)
class Beacon:
    """Всё, что один узел говорит о себе вслух."""

    origin_id: str
    machine_name: str
    fingerprint: str
    port: int
    protocol_major: int


def encode_beacon(beacon: Beacon) -> bytes:
    return json.dumps(asdict(beacon), ensure_ascii=False).encode("utf-8")


def decode_beacon(raw: bytes, own_origin_id: str) -> Beacon | None:
    """Разобрать чужой маячок. Свой, чужого поколения и мусор дают None."""
    try:
        parsed = json.loads(raw.decode("utf-8"))
        beacon = Beacon(
            origin_id=str(parsed["origin_id"]),
            machine_name=str(parsed["machine_name"]),
            fingerprint=str(parsed["fingerprint"]),
            port=int(parsed["port"]),
            protocol_major=int(parsed["protocol_major"]),
        )
    except (UnicodeDecodeError, ValueError, KeyError, TypeError):
        return None

    if beacon.origin_id == own_origin_id:
        return None
    if beacon.protocol_major != PROTOCOL_MAJOR:
        return None
    return beacon


class Discovery(QObject):
    """Рассылает свой маячок и слушает чужие."""

    peer_seen = Signal(object, str)

    def __init__(self, own_origin_id: str, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._own_origin_id = own_origin_id
        self._beacon: Beacon | None = None
        self._socket = QUdpSocket(self)
        self._socket.readyRead.connect(self._on_ready_read)
        self._timer = QTimer(self)
        self._timer.setInterval(BEACON_INTERVAL_MS)
        self._timer.timeout.connect(self._announce)

    def start(self, beacon: Beacon) -> bool:
        self._beacon = beacon
        bound = self._socket.bind(
            QHostAddress.SpecialAddress.AnyIPv4,
            BEACON_PORT,
            QUdpSocket.BindFlag.ShareAddress | QUdpSocket.BindFlag.ReuseAddressHint,
        )
        if not bound:
            return False
        self._socket.joinMulticastGroup(QHostAddress(GROUP_ADDRESS))
        self._announce()
        self._timer.start()
        return True

    def stop(self) -> None:
        self._timer.stop()
        self._socket.leaveMulticastGroup(QHostAddress(GROUP_ADDRESS))
        self._socket.close()

    def _announce(self) -> None:
        if self._beacon is None:
            return
        self._socket.writeDatagram(
            encode_beacon(self._beacon), QHostAddress(GROUP_ADDRESS), BEACON_PORT
        )

    def _on_ready_read(self) -> None:
        while self._socket.hasPendingDatagrams():
            datagram = self._socket.receiveDatagram()
            beacon = decode_beacon(bytes(datagram.data()), self._own_origin_id)
            if beacon is not None:
                self.peer_seen.emit(beacon, datagram.senderAddress().toString())


__all__ = [
    "BEACON_INTERVAL_MS",
    "BEACON_PORT",
    "GROUP_ADDRESS",
    "Beacon",
    "Discovery",
    "decode_beacon",
    "encode_beacon",
]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_discovery_beacon.py -q`
Expected: PASS, 5 тестов

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/discovery.py configurator/tests/clipboard/test_discovery_beacon.py
git commit -m "Let the two machines find each other, and say nothing else"
```

---

### Task 11: Код парринга

**Files:**
- Create: `configurator/src/duo_input/clipboard/pairing.py`
- Create: `configurator/tests/clipboard/test_pairing.py`

**Interfaces:**
- Consumes: `TrustedPeer` из `trust.py`.
- Produces: `pairing_code(one: str, two: str) -> str`; `PairingCandidate` — `origin_id: str`, `machine_name: str`, `fingerprint: str`, `address: str`, `port: int`, метод `as_trusted() -> TrustedPeer`; константа `PAIRING_WINDOW_MS = 120_000`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Код сравнения: одинаковый на обеих машинах, разный для разных пар."""

from __future__ import annotations

from duo_input.clipboard.pairing import PairingCandidate, pairing_code

ONE = "a" * 64
TWO = "b" * 64


def test_both_machines_derive_the_same_code_whichever_way_round():
    assert pairing_code(ONE, TWO) == pairing_code(TWO, ONE)


def test_the_code_is_six_digits_with_leading_zeros_kept():
    code = pairing_code(ONE, TWO)

    assert len(code) == 6
    assert code.isdigit()


def test_a_different_pair_gives_a_different_code():
    assert pairing_code(ONE, TWO) != pairing_code(ONE, "c" * 64)


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
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_pairing.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.pairing'`

- [ ] **Step 3: Написать реализацию**

```python
"""Как два компьютера становятся друг для друга своими.

Код сравнивается, а не вводится. Код, который человек вводит, надо где-то
показать, и он превращается в секрет, который надо защищать. Сравнение секретом
не является: атакующий, вклинившийся посередине, предъявит сторонам разные
сертификаты, а значит - разные коды, и человек это увидит.

Отпечатки сортируются, потому что иначе две стороны получили бы разные числа из
одной и той же пары.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass

from .trust import TrustedPeer

#: Сколько длится открытое окно парринга.
PAIRING_WINDOW_MS = 120_000

_CODE_DIGITS = 6
_CODE_MODULUS = 10**_CODE_DIGITS


def pairing_code(one: str, two: str) -> str:
    """Шестизначный код, одинаковый на обеих машинах."""
    joined = "".join(sorted((one, two))).encode("ascii")
    digest = hashlib.sha256(joined).digest()
    value = int.from_bytes(digest[:8], "big") % _CODE_MODULUS
    return str(value).zfill(_CODE_DIGITS)


@dataclass(frozen=True)
class PairingCandidate:
    """Компьютер, который представился, но ещё не подтверждён человеком."""

    origin_id: str
    machine_name: str
    fingerprint: str
    address: str
    port: int

    def as_trusted(self) -> TrustedPeer:
        return TrustedPeer(
            origin_id=self.origin_id,
            machine_name=self.machine_name,
            fingerprint=self.fingerprint,
            last_address=self.address,
        )


__all__ = ["PAIRING_WINDOW_MS", "PairingCandidate", "pairing_code"]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_pairing.py -q`
Expected: PASS, 4 теста

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/pairing.py configurator/tests/clipboard/test_pairing.py
git commit -m "Confirm a pairing by comparing a number, not typing one"
```

---

### Task 12: Сквозная работа двух узлов

Здесь `ClipboardService` соединяется со связью: объявления уходят, содержимое запрашивается и приходит, heartbeat живёт, разрыв переподключается.

**Files:**
- Modify: `configurator/src/duo_input/clipboard/service.py`
- Create: `configurator/tests/clipboard/test_end_to_end.py`

**Interfaces:**
- Consumes: `PeerLink`, `PeerListener`, `Message`, `MessageType`, `ClipboardOffer`.
- Produces: у `ClipboardService` появляются методы `attach_link(link: PeerLink)`, `detach_link()`, `handle_message(message: Message)`; сигналы `link_state_changed(str)` и `content_failed(str)`; константы модуля `HEARTBEAT_MS = 10_000`, `SILENCE_LIMIT_MS = 30_000`, `FETCH_TIMEOUT_MS = 5_000`, `TRANSFER_TIMEOUT_MS = 30_000`.

- [ ] **Step 1: Написать падающий сквозной тест**

```python
"""Два сервиса в одном процессе: копия на одном становится вставкой на другом."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from duo_input.clipboard.backend import ClipboardSnapshot
from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.listener import PeerListener
from duo_input.clipboard.offer import ClipboardOffer
from duo_input.clipboard.peer import PeerLink
from duo_input.clipboard.service import ClipboardService


class _RecordingBackend:
    def __init__(self) -> None:
        self.published: list[ClipboardOffer] = []
        self.fetchers: list[object] = []

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def publish(self, offer, fetcher) -> None:
        self.published.append(offer)
        self.fetchers.append(fetcher)

    def payload(self, mime):
        return None


@pytest.fixture
def pair(qtbot, tmp_path):
    """Два узла, соединённых настоящим TLS на loopback."""
    server_identity = load_or_create(tmp_path / "server")
    client_identity = load_or_create(tmp_path / "client")

    listener = PeerListener(server_identity)
    listener.listen(0)

    server_service = ClipboardService(server_identity.origin_id)
    server_backend = _RecordingBackend()
    server_service.attach_backend(server_backend)

    links: list[PeerLink] = []
    listener.link_ready.connect(links.append)

    client_service = ClipboardService(client_identity.origin_id)
    client_backend = _RecordingBackend()
    client_service.attach_backend(client_backend)

    client_link = PeerLink(client_identity)
    with qtbot.waitSignal(client_link.connected, timeout=5000):
        client_link.connect_to("127.0.0.1", listener.port, server_identity.fingerprint)
    qtbot.waitUntil(lambda: links, timeout=5000)

    client_service.attach_link(client_link)
    server_service.attach_link(links[0])

    yield server_service, server_backend, client_service, client_backend

    listener.stop()
    client_link.close()


def test_a_copy_on_one_side_is_offered_to_the_other(qtbot, pair):
    server_service, server_backend, client_service, _ = pair

    client_service.on_local_snapshot(ClipboardSnapshot({"text/plain": "привет".encode("utf-8")}))

    qtbot.waitUntil(lambda: server_backend.published, timeout=5000)
    assert server_backend.published[0].mimes() == ("text/plain",)


def test_content_arrives_only_when_it_is_asked_for(qtbot, pair):
    server_service, server_backend, client_service, _ = pair
    payload = "привет".encode("utf-8")

    client_service.on_local_snapshot(ClipboardSnapshot({"text/plain": payload}))
    qtbot.waitUntil(lambda: server_backend.published, timeout=5000)

    fetched = server_backend.fetchers[0]("text/plain")

    assert fetched == payload


def test_asking_for_a_stale_offer_fails_rather_than_returning_the_wrong_thing(qtbot, pair):
    server_service, server_backend, client_service, _ = pair

    client_service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"first"}))
    qtbot.waitUntil(lambda: server_backend.published, timeout=5000)
    stale_fetcher = server_backend.fetchers[0]

    client_service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"second"}))
    qtbot.waitUntil(lambda: len(server_backend.published) == 2, timeout=5000)

    with pytest.raises(TimeoutError):
        stale_fetcher("text/plain")
```

- [ ] **Step 2: Убедиться, что тест падает**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_end_to_end.py -q`
Expected: FAIL, `AttributeError: 'ClipboardService' object has no attribute 'attach_link'`

- [ ] **Step 3: Дописать `service.py`**

Добавить в начало файла, к существующим импортам:

```python
from PySide6.QtCore import QEventLoop, QObject, QTimer, Signal

from .wire import Message, MessageType
```

Добавить константы модуля после импортов:

```python
#: Как часто мы напоминаем о себе и сколько молчания считаем разрывом.
HEARTBEAT_MS = 10_000
SILENCE_LIMIT_MS = 30_000

#: Сколько ждём первого ответа на запрос содержимого и всю передачу целиком.
FETCH_TIMEOUT_MS = 5_000
TRANSFER_TIMEOUT_MS = 30_000
```

Добавить два сигнала в класс, рядом с существующими:

```python
    link_state_changed = Signal(str)
    content_failed = Signal(str)
```

Дописать в `__init__`, после существующих полей:

```python
        self._link = None
        self._pending_fetch: dict[str, bytes] | None = None
        self._fetch_loop: QEventLoop | None = None
        self._heartbeat = QTimer(self)
        self._heartbeat.setInterval(HEARTBEAT_MS)
        self._heartbeat.timeout.connect(self._send_ping)
```

Добавить методы:

```python
    # ------------------------------------------------------------------ связь

    def attach_link(self, link) -> None:
        """Взять готовое соединение и начать по нему разговаривать."""
        self._link = link
        link.message_received.connect(self.handle_message)
        link.disconnected.connect(self._on_link_lost)
        self.offer_ready.connect(self._send_offer)
        self._heartbeat.start()
        self.link_state_changed.emit("connected")

    def detach_link(self) -> None:
        if self._link is None:
            return
        self._heartbeat.stop()
        try:
            self.offer_ready.disconnect(self._send_offer)
        except (RuntimeError, TypeError):
            pass
        self._link = None
        self.link_state_changed.emit("disconnected")

    def handle_message(self, message: Message) -> None:
        if message.type is MessageType.OFFER:
            self.on_remote_offer(ClipboardOffer.from_dict(message.header))
        elif message.type is MessageType.FETCH:
            self._answer_fetch(message)
        elif message.type is MessageType.CONTENT:
            self._receive_content(message)
        elif message.type is MessageType.CONTENT_ERROR:
            self._receive_content_error(message)
        elif message.type is MessageType.PING:
            self._send(Message(MessageType.PONG, {}, b""))

    def _send(self, message: Message) -> None:
        if self._link is not None:
            self._link.send(message)

    def _send_ping(self) -> None:
        self._send(Message(MessageType.PING, {}, b""))

    def _send_offer(self, offer: ClipboardOffer) -> None:
        self._send(Message(MessageType.OFFER, offer.to_dict(), b""))

    def _on_link_lost(self, reason: str) -> None:
        self._heartbeat.stop()
        self._link = None
        if self._fetch_loop is not None:
            self._fetch_loop.quit()
        self.link_state_changed.emit(f"disconnected: {reason}")

    def _answer_fetch(self, message: Message) -> None:
        mime = str(message.header.get("mime", ""))
        seq = int(message.header.get("seq", -1))
        payload = self.content_for(mime, seq)
        if payload is None:
            self._send(
                Message(
                    MessageType.CONTENT_ERROR,
                    {"seq": seq, "mime": mime, "reason": "объявление устарело"},
                    b"",
                )
            )
            return
        self._send(Message(MessageType.CONTENT, {"seq": seq, "mime": mime}, payload))

    def _receive_content(self, message: Message) -> None:
        if self._pending_fetch is None:
            return
        if str(message.header.get("mime")) != self._pending_fetch["mime"]:
            return
        self._pending_fetch["payload"] = message.blob
        if self._fetch_loop is not None:
            self._fetch_loop.quit()

    def _receive_content_error(self, message: Message) -> None:
        if self._pending_fetch is None:
            return
        self._pending_fetch["error"] = str(message.header.get("reason", "отказ"))
        if self._fetch_loop is not None:
            self._fetch_loop.quit()
```

Удалить сигнал `content_requested` из класса: после этой задачи содержимое
запрашивается по сети напрямую, и сигнал, который никто не слушает, — это
обещание расширяемости, за которым ничего нет.

Заменить метод `_fetch` целиком на сетевой:

```python
    def _fetch(self, mime: str, offer: ClipboardOffer) -> bytes:
        """Забрать содержимое у пира. Синхронно - этого требует буфер обмена.

        Вставляющее приложение ждёт ответа, поэтому здесь крутится вложенный
        цикл событий. Обработка новых снимков на это время не нужна и не
        выполняется: снимок делает граница платформы, а она вызвала нас сама.
        """
        if self._link is None:
            raise TimeoutError("нет связи со вторым компьютером")

        self._pending_fetch = {"mime": mime, "payload": None, "error": None}
        self._send(Message(MessageType.FETCH, {"seq": offer.seq, "mime": mime}, b""))

        loop = QEventLoop()
        self._fetch_loop = loop
        QTimer.singleShot(TRANSFER_TIMEOUT_MS, loop.quit)
        loop.exec()
        self._fetch_loop = None

        pending, self._pending_fetch = self._pending_fetch, None
        if pending["error"] is not None:
            self.content_failed.emit(pending["error"])
            raise TimeoutError(pending["error"])
        if pending["payload"] is None:
            self.content_failed.emit("второй компьютер не ответил")
            raise TimeoutError("второй компьютер не ответил")
        return pending["payload"]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_end_to_end.py tests/clipboard/test_service_rules.py -q`
Expected: PASS, 11 тестов (8 старых правил и 3 сквозных)

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/service.py configurator/tests/clipboard/test_end_to_end.py
git commit -m "Carry a clipboard across the link, on request"
```

---

### Task 13: Трей и жизненный цикл приложения

**Files:**
- Create: `configurator/src/duo_input/ui/tray.py`
- Modify: `configurator/src/duo_input/app.py`
- Modify: `configurator/src/duo_input/ui/main_window.py:908-916`
- Create: `configurator/tests/ui/test_tray_lifecycle.py`

**Interfaces:**
- Consumes: `ClipboardService`.
- Produces: `TrayIcon(QSystemTrayIcon)` — сигналы `open_requested()`, `quit_requested()`, `sharing_toggled(bool)`; поля `open_action`, `state_action`, `sharing_action`, `files_action`, `quit_action`; метод `set_link_state(state: str)`; словарь `STATE_LABELS`; у `MainWindow` — свойство `background_mode: bool`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Закрытие окна: выход или уход в трей, в зависимости от того, включена ли фича."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from duo_input.app import build_main_window
from duo_input.ui.tray import TrayIcon


def _settings(tmp_path, enabled: bool) -> QSettings:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("clipboard/enabled", enabled)
    return settings


def test_closing_quits_when_sharing_is_off(qtbot, tmp_path):
    window = build_main_window(settings=_settings(tmp_path, False))
    qtbot.addWidget(window)

    assert window.background_mode is False


def test_closing_hides_when_sharing_is_on(qtbot, tmp_path):
    window = build_main_window(settings=_settings(tmp_path, True))
    qtbot.addWidget(window)
    window.show()

    window.close()

    assert window.background_mode is True
    assert window.isVisible() is False


def test_the_tray_reports_each_link_state_distinctly(qtbot):
    tray = TrayIcon()

    tray.set_link_state("connected")
    connected = tray.state_action.text()
    tray.set_link_state("disconnected")
    disconnected = tray.state_action.text()
    tray.set_link_state("blocked")
    blocked = tray.state_action.text()

    assert len({connected, disconnected, blocked}) == 3
    assert "брандмауэр" in blocked.lower()


def test_the_tray_toggle_emits_the_new_value(qtbot):
    tray = TrayIcon()
    values: list[bool] = []
    tray.sharing_toggled.connect(values.append)

    tray.sharing_action.setChecked(True)
    tray.sharing_action.triggered.emit(True)

    assert values == [True]
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/ui/test_tray_lifecycle.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.ui.tray'`

- [ ] **Step 3: Написать `ui/tray.py`**

```python
"""Значок в области уведомлений: единственное, что видно при скрытом окне.

Пункт "Передача файлов" присутствует и всегда выключен: он появится в
следующем этапе, а меню, которое меняет состав между версиями, читается хуже,
чем меню с честно недоступным пунктом.
"""

from __future__ import annotations

from PySide6.QtCore import QCoreApplication, Signal
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

STATE_LABELS = {
    "connected": QCoreApplication.translate("TrayIcon", "Второй компьютер на связи"),
    "disconnected": QCoreApplication.translate("TrayIcon", "Нет связи со вторым компьютером"),
    "unpaired": QCoreApplication.translate("TrayIcon", "Компьютеры не связаны"),
    "searching": QCoreApplication.translate("TrayIcon", "Поиск второго компьютера"),
    # Отдельная строка, а не общее "нет связи": иначе пользователь пойдёт чинить
    # сеть, которая исправна, вместо того чтобы разрешить программе в брандмауэре.
    "blocked": QCoreApplication.translate(
        "TrayIcon", "Windows не разрешила подключение — проверьте брандмауэр"
    ),
}


class TrayIcon(QSystemTrayIcon):
    """Открыть, показать состояние, включить и выключить, выйти."""

    open_requested = Signal()
    quit_requested = Signal()
    sharing_toggled = Signal(bool)

    def __init__(self, icon: QIcon | None = None, parent=None) -> None:
        super().__init__(parent)
        if icon is not None:
            self.setIcon(icon)

        self._menu = QMenu()

        self.open_action = QAction(self.tr("Открыть Duo Input"), self._menu)
        self.open_action.triggered.connect(self.open_requested)
        self._menu.addAction(self.open_action)

        self._menu.addSeparator()

        self.state_action = QAction(STATE_LABELS["unpaired"], self._menu)
        self.state_action.setEnabled(False)
        self._menu.addAction(self.state_action)

        self.sharing_action = QAction(self.tr("Общий буфер обмена"), self._menu)
        self.sharing_action.setCheckable(True)
        self.sharing_action.triggered.connect(self.sharing_toggled)
        self._menu.addAction(self.sharing_action)

        self.files_action = QAction(self.tr("Передача файлов"), self._menu)
        self.files_action.setCheckable(True)
        self.files_action.setEnabled(False)
        self._menu.addAction(self.files_action)

        self._menu.addSeparator()

        self.quit_action = QAction(self.tr("Выход"), self._menu)
        self.quit_action.triggered.connect(self.quit_requested)
        self._menu.addAction(self.quit_action)

        self.setContextMenu(self._menu)
        self.activated.connect(self._on_activated)
        self.set_link_state("unpaired")

    def set_link_state(self, state: str) -> None:
        label = STATE_LABELS.get(state, STATE_LABELS["disconnected"])
        self.state_action.setText(label)
        self.setToolTip(f"Duo Input — {label}")

    def _on_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.open_requested.emit()


__all__ = ["STATE_LABELS", "TrayIcon"]
```

- [ ] **Step 4: Изменить `MainWindow.closeEvent`**

Заменить метод `closeEvent` в `configurator/src/duo_input/ui/main_window.py` на:

```python
    def closeEvent(self, event) -> None:  # noqa: N802 - Qt override
        """Закрыть окно. Выйти или уйти в трей - решает общий буфер.

        Конфигурация живёт на устройстве, поэтому спрашивать о несохранённом
        нечего. Но если общий буфер включён, окно - не всё приложение: за ним
        стоит подсистема, которую закрытие окна останавливать не должно.
        Резидентность включается вместе с фичей и выключается вместе с ней:
        тот, кто настраивает макросы, не получает вечно висящую программу.
        """
        if self.background_mode:
            self.hide()
            event.ignore()
            return
        event.accept()
```

Добавить свойство рядом с ним:

```python
    @property
    def background_mode(self) -> bool:
        """Продолжает ли приложение работать после закрытия окна."""
        return bool(self._settings.value("clipboard/enabled", False, type=bool))
```

- [ ] **Step 5: Изменить `app.py`**

В `main`, после `apply_theme(application)` и до создания окна, добавить:

```python
    # Резидентность включается вместе с общим буфером и только вместе с ним.
    settings = QSettings()
    if bool(settings.value("clipboard/enabled", False, type=bool)):
        application.setQuitOnLastWindowClosed(False)
```

Добавить импорт `from PySide6.QtCore import QSettings` в начало файла.

Заменить строку в docstring модуля:

```python
"""Entry point for the Duo Input configurator.

The application runs without administrator rights and collects no telemetry.
It opens network connections only inside the local network, only to a computer
the operator explicitly paired with, and only while the shared clipboard is
switched on. With the shared clipboard off, no socket is ever opened.
"""
```

- [ ] **Step 6: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/ui/test_tray_lifecycle.py tests/ui/test_main_window.py -q`
Expected: PASS

Если существующий тест закрытия окна упал — он проверял старое поведение; обновить его так, чтобы он явно ставил `clipboard/enabled` в `False` и продолжал проверять выход.

- [ ] **Step 7: Commit**

```bash
git add configurator/src/duo_input/ui/tray.py configurator/src/duo_input/app.py configurator/src/duo_input/ui/main_window.py configurator/tests/ui/test_tray_lifecycle.py
git commit -m "Keep working after the window closes, but only when asked to"
```

---

### Task 14: Страница «Общий буфер» и автозапуск

**Files:**
- Create: `configurator/src/duo_input/ui/clipboard_page.py`
- Create: `configurator/src/duo_input/persistence/autostart.py`
- Modify: `configurator/src/duo_input/ui/main_window.py:100-120,205-220`
- Create: `configurator/tests/ui/test_clipboard_page.py`
- Create: `configurator/tests/clipboard/test_autostart.py`

**Interfaces:**
- Consumes: `TrustStore`, `TrustedPeer`, `pairing_code`.
- Produces: `ClipboardPage(QWidget)` — сигналы `sharing_toggled(bool)`, `pair_requested()`, `forget_requested()`, `autostart_toggled(bool)`; методы `set_link_state(state)`, `set_peer(peer)`; `shortcut_path() -> Path`, `is_enabled() -> bool`, `enable(target: Path) -> None`, `disable() -> None` в `autostart.py`; в `MainWindow` — `PAGE_CLIPBOARD`, добавленная в `PAGE_ORDER` перед `PAGE_DIAGNOSTICS`.

- [ ] **Step 1: Написать падающие тесты автозапуска**

```python
"""Автозапуск: ярлык, который видно и который убирается руками."""

from __future__ import annotations

from pathlib import Path

from duo_input.persistence import autostart


def test_disabled_by_default(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "startup_directory", lambda: tmp_path)

    assert autostart.is_enabled() is False


def test_enabling_creates_a_file_in_the_startup_folder(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "startup_directory", lambda: tmp_path)

    autostart.enable(Path("C:/Program Files/Duo Input/DuoInput.exe"))

    assert autostart.is_enabled() is True
    assert autostart.shortcut_path().exists()


def test_disabling_removes_it(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "startup_directory", lambda: tmp_path)
    autostart.enable(Path("C:/Program Files/Duo Input/DuoInput.exe"))

    autostart.disable()

    assert autostart.is_enabled() is False


def test_disabling_twice_is_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(autostart, "startup_directory", lambda: tmp_path)

    autostart.disable()
    autostart.disable()

    assert autostart.is_enabled() is False
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_autostart.py -q`
Expected: FAIL, `ImportError: cannot import name 'autostart'`

- [ ] **Step 3: Написать `persistence/autostart.py`**

```python
r"""Запуск вместе с системой - ярлыком, а не ключом реестра.

Ярлык в папке автозагрузки виден в диспетчере задач и удаляется руками. Ключ в
реестре не виден никому, кроме того, кто знает, где искать. Программа, которая
незаметно прописывается в автозапуск, выглядит ровно как то, чего пользователей
учат опасаться, - а прав администратора не требует ни то, ни другое.
"""

from __future__ import annotations

import os
from pathlib import Path

SHORTCUT_NAME = "Duo Input.cmd"


def startup_directory() -> Path:
    r"""Папка автозагрузки текущего пользователя."""
    appdata = os.environ.get("APPDATA")
    base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
    return base / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"


def shortcut_path() -> Path:
    return startup_directory() / SHORTCUT_NAME


def is_enabled() -> bool:
    return shortcut_path().is_file()


def enable(target: Path) -> None:
    """Создать запись автозагрузки, запускающую программу скрытой."""
    path = shortcut_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'@start "" "{target}"\n', "utf-8")


def disable() -> None:
    shortcut_path().unlink(missing_ok=True)


__all__ = [
    "SHORTCUT_NAME",
    "disable",
    "enable",
    "is_enabled",
    "shortcut_path",
    "startup_directory",
]
```

- [ ] **Step 4: Написать падающий тест страницы**

```python
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
```

- [ ] **Step 5: Написать `ui/clipboard_page.py`**

```python
"""Страница общего буфера: состояние, связывание и два переключателя.

Отпечаток показан целиком не для красоты: это то самое число, по которому
человек может убедиться, что связан именно с тем компьютером, с которым думал.
"""

from __future__ import annotations

from PySide6.QtCore import Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from duo_input.clipboard.trust import TrustedPeer
from duo_input.ui.tray import STATE_LABELS


class ClipboardPage(QWidget):
    """Всё, что оператор делает с общим буфером, кроме самого копирования."""

    sharing_toggled = Signal(bool)
    autostart_toggled = Signal(bool)
    pair_requested = Signal()
    forget_requested = Signal()
    address_changed = Signal(str)

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

        self.autostart_checkbox = QCheckBox(self.tr("Запускать вместе с Windows"), self)
        self.autostart_checkbox.toggled.connect(self.autostart_toggled)

        self.address_field = QLineEdit(self)
        self.address_field.setPlaceholderText(self.tr("Адрес второго компьютера, если поиск не нашёл"))
        self.address_field.editingFinished.connect(
            lambda: self.address_changed.emit(self.address_field.text().strip())
        )

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
        peer_layout.addWidget(self.address_field)

        layout = QVBoxLayout(self)
        layout.addWidget(peer_box)
        layout.addWidget(self.sharing_checkbox)
        layout.addWidget(self.autostart_checkbox)
        layout.addStretch(1)

    def set_link_state(self, state: str) -> None:
        self.state_label.setText(STATE_LABELS.get(state, STATE_LABELS["disconnected"]))

    def set_peer(self, peer: TrustedPeer | None) -> None:
        if peer is None:
            self.peer_label.setText(self.tr("Компьютер не выбран"))
            self.fingerprint_label.setText("")
            self.forget_button.setEnabled(False)
            return
        self.peer_label.setText(f"{peer.machine_name} ({peer.last_address})")
        self.fingerprint_label.setText(self.tr("Отпечаток: {0}").format(peer.fingerprint))
        self.forget_button.setEnabled(True)


__all__ = ["ClipboardPage"]
```

- [ ] **Step 6: Подключить страницу к `MainWindow`**

В блоке констант страниц (`main_window.py:100-118`) добавить `PAGE_CLIPBOARD` после `PAGE_MOUSE` и включить его в `PAGE_ORDER` перед `PAGE_DIAGNOSTICS`. Там же, где создаются остальные страницы, создать `self.clipboard_page = ClipboardPage(self)` и добавить её в тот же стек и в тот же список заголовков, что и соседние страницы, с заголовком `self.tr("Общий буфер")`.

`ClipboardPage` не является страницей редактора конфигурации, поэтому в `_editor_pages` её добавлять **нельзя**: иначе она попадёт в сбор изменений проекта.

- [ ] **Step 7: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_autostart.py tests/ui/test_clipboard_page.py tests/ui/test_main_window_pages.py -q`
Expected: PASS

- [ ] **Step 8: Обновить переводы**

Run: `cd configurator && ../.venv/Scripts/python.exe ../tools/update_translations.py`

Перевести новые строки на английский в каталоге `en`; русские остаются исходными.

- [ ] **Step 9: Commit**

```bash
git add configurator/src/duo_input/ui/clipboard_page.py configurator/src/duo_input/persistence/autostart.py configurator/src/duo_input/ui/main_window.py configurator/tests/ui/test_clipboard_page.py configurator/tests/clipboard/test_autostart.py configurator/src/duo_input/resources/translations
git commit -m "Give the shared clipboard a page and a way to start with Windows"
```

---

### Task 15: Координатор — парринг, переподключение, отказ брандмауэра

До этой задачи части существуют, но никто их не соединяет: никто не рассылает маячок, никто не шлёт `PAIR_REQUEST`, никто не переподключается после разрыва. Координатор — единственное место, где всё это сходится.

**Files:**
- Create: `configurator/src/duo_input/clipboard/coordinator.py`
- Create: `configurator/tests/clipboard/test_coordinator.py`

**Interfaces:**
- Consumes: `NodeIdentity`, `TrustStore`, `TrustedPeer`, `PeerLink`, `PeerListener`, `Discovery`, `Beacon`, `ClipboardService`, `pairing_code`, `PairingCandidate`, `Message`, `MessageType`, `PROTOCOL_MAJOR`, `PROTOCOL_MINOR`.
- Produces: `reconnect_delay_ms(attempt: int) -> int`; `LinkState` (StrEnum: `UNPAIRED`, `SEARCHING`, `CONNECTED`, `DISCONNECTED`, `BLOCKED`); `ClipboardCoordinator(QObject)` — сигналы `state_changed(str)`, `pairing_code_ready(str, object)`, `peer_changed(object)`; методы `start()`, `stop()`, `begin_pairing()`, `confirm_pairing(candidate)`, `forget_peer()`, `set_manual_address(address)`; свойства `state`, `peer`; константы `TCP_PORT = 47654`, `RECONNECT_DELAYS_MS = (1000, 2000, 4000, 8000, 30000)`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Координатор: задержки переподключения, состояния и закрепление доверия."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from duo_input.clipboard.coordinator import (
    RECONNECT_DELAYS_MS,
    ClipboardCoordinator,
    LinkState,
    reconnect_delay_ms,
)
from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.pairing import PairingCandidate
from duo_input.clipboard.trust import TrustStore


def test_reconnect_delays_grow_and_then_stop_growing():
    delays = [reconnect_delay_ms(attempt) for attempt in range(7)]

    assert delays[: len(RECONNECT_DELAYS_MS)] == list(RECONNECT_DELAYS_MS)
    assert delays[-1] == RECONNECT_DELAYS_MS[-1]
    assert delays[-2] == RECONNECT_DELAYS_MS[-1]


def test_a_negative_attempt_still_gives_the_first_delay():
    assert reconnect_delay_ms(-1) == RECONNECT_DELAYS_MS[0]


def test_an_unpaired_coordinator_starts_unpaired(tmp_path):
    coordinator = ClipboardCoordinator(
        identity=load_or_create(tmp_path / "id"),
        trust=TrustStore(tmp_path / "peers.json"),
        machine_name="LAPTOP-ONE",
    )

    assert coordinator.state is LinkState.UNPAIRED
    assert coordinator.peer is None


def test_confirming_a_pairing_remembers_the_peer(tmp_path):
    trust = TrustStore(tmp_path / "peers.json")
    coordinator = ClipboardCoordinator(
        identity=load_or_create(tmp_path / "id"),
        trust=trust,
        machine_name="LAPTOP-ONE",
    )
    candidate = PairingCandidate("2" * 32, "LAPTOP-TWO", "f" * 64, "192.168.1.5", 47654)

    coordinator.confirm_pairing(candidate)

    assert trust.peer().origin_id == "2" * 32
    assert coordinator.peer.machine_name == "LAPTOP-TWO"


def test_forgetting_a_peer_returns_to_unpaired(tmp_path):
    trust = TrustStore(tmp_path / "peers.json")
    coordinator = ClipboardCoordinator(
        identity=load_or_create(tmp_path / "id"),
        trust=trust,
        machine_name="LAPTOP-ONE",
    )
    coordinator.confirm_pairing(
        PairingCandidate("2" * 32, "LAPTOP-TWO", "f" * 64, "192.168.1.5", 47654)
    )

    coordinator.forget_peer()

    assert trust.peer() is None
    assert coordinator.state is LinkState.UNPAIRED


def test_a_port_that_cannot_be_listened_on_is_reported_as_blocked(tmp_path, monkeypatch):
    coordinator = ClipboardCoordinator(
        identity=load_or_create(tmp_path / "id"),
        trust=TrustStore(tmp_path / "peers.json"),
        machine_name="LAPTOP-ONE",
    )
    coordinator.confirm_pairing(
        PairingCandidate("2" * 32, "LAPTOP-TWO", "f" * 64, "192.168.1.5", 47654)
    )
    monkeypatch.setattr(coordinator._listener, "listen", lambda port: False)

    states: list[str] = []
    coordinator.state_changed.connect(states.append)
    coordinator.start()

    assert LinkState.BLOCKED.value in states


def test_the_pairing_code_is_offered_for_confirmation(tmp_path):
    identity = load_or_create(tmp_path / "id")
    coordinator = ClipboardCoordinator(
        identity=identity,
        trust=TrustStore(tmp_path / "peers.json"),
        machine_name="LAPTOP-ONE",
    )
    offered: list[tuple[str, object]] = []
    coordinator.pairing_code_ready.connect(lambda code, candidate: offered.append((code, candidate)))

    candidate = PairingCandidate("2" * 32, "LAPTOP-TWO", "f" * 64, "192.168.1.5", 47654)
    coordinator._offer_pairing(candidate)

    code, offered_candidate = offered[0]
    assert len(code) == 6 and code.isdigit()
    assert offered_candidate is candidate
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_coordinator.py -q`
Expected: FAIL, `ModuleNotFoundError: No module named 'duo_input.clipboard.coordinator'`

- [ ] **Step 3: Написать реализацию**

```python
"""Единственное место, где части подсистемы соединяются друг с другом.

Маячок рассылается только во время парринга и только тогда, когда спаренный
компьютер не найден по последнему известному адресу: постоянная рассылка
означала бы, что присутствие устройства видно всей сети всё время.

Звонит тот, чей origin_id меньше. Это решает гонку встречных соединений без
переговоров. Во время парринга origin_id пира ещё не известен, поэтому там
соединяются оба, и лишнее соединение закрывается, как только HELLO назвал обе
стороны.

Отказ брандмауэра отличается от отсутствия связи и сообщается отдельно: иначе
пользователь начнёт чинить сеть, которая исправна.
"""

from __future__ import annotations

from enum import StrEnum

from PySide6.QtCore import QObject, QTimer, Signal

from .discovery import Beacon, Discovery
from .identity import NodeIdentity
from .listener import PeerListener
from .pairing import PAIRING_WINDOW_MS, PairingCandidate, pairing_code
from .peer import PeerLink
from .service import SILENCE_LIMIT_MS, ClipboardService
from .trust import TrustStore, TrustedPeer
from .wire import PROTOCOL_MAJOR, PROTOCOL_MINOR, Message, MessageType

TCP_PORT = 47654

#: Пауза перед повторной попыткой: растёт и упирается в потолок.
RECONNECT_DELAYS_MS = (1000, 2000, 4000, 8000, 30000)


class LinkState(StrEnum):
    UNPAIRED = "unpaired"
    SEARCHING = "searching"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    BLOCKED = "blocked"


def reconnect_delay_ms(attempt: int) -> int:
    """Сколько ждать перед попыткой номер ``attempt``, считая с нуля."""
    if attempt < 0:
        return RECONNECT_DELAYS_MS[0]
    return RECONNECT_DELAYS_MS[min(attempt, len(RECONNECT_DELAYS_MS) - 1)]


class ClipboardCoordinator(QObject):
    """Владеет связью, обнаружением, доверием и сервисом правил."""

    state_changed = Signal(str)
    pairing_code_ready = Signal(str, object)
    peer_changed = Signal(object)

    def __init__(
        self,
        identity: NodeIdentity,
        trust: TrustStore,
        machine_name: str,
        service: ClipboardService | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._identity = identity
        self._trust = trust
        self._machine_name = machine_name
        self._service = service if service is not None else ClipboardService(identity.origin_id)

        self._listener = PeerListener(identity, self)
        self._listener.link_ready.connect(self._on_incoming_link)
        self._discovery = Discovery(identity.origin_id, self)
        self._discovery.peer_seen.connect(self._on_peer_seen)

        self._link: PeerLink | None = None
        self._attempt = 0
        self._pairing = False
        self._manual_address = ""
        self._state = LinkState.UNPAIRED if trust.peer() is None else LinkState.DISCONNECTED

        self._retry = QTimer(self)
        self._retry.setSingleShot(True)
        self._retry.timeout.connect(self._try_connect)

        self._pairing_window = QTimer(self)
        self._pairing_window.setSingleShot(True)
        self._pairing_window.setInterval(PAIRING_WINDOW_MS)
        self._pairing_window.timeout.connect(self._end_pairing)

        self._silence = QTimer(self)
        self._silence.setSingleShot(True)
        self._silence.setInterval(SILENCE_LIMIT_MS)
        self._silence.timeout.connect(lambda: self._drop("второй компьютер молчит"))

    # ------------------------------------------------------------------ состояние

    @property
    def state(self) -> LinkState:
        return self._state

    @property
    def peer(self) -> TrustedPeer | None:
        return self._trust.peer()

    @property
    def service(self) -> ClipboardService:
        return self._service

    def _set_state(self, state: LinkState) -> None:
        if state is self._state:
            return
        self._state = state
        self.state_changed.emit(state.value)

    # ------------------------------------------------------------------ жизненный цикл

    def start(self) -> None:
        self._listener.expect(self.peer.fingerprint if self.peer else None)
        if not self._listener.listen(TCP_PORT):
            self._set_state(LinkState.BLOCKED)
            return
        if self.peer is None:
            self._set_state(LinkState.UNPAIRED)
            return
        self._attempt = 0
        self._try_connect()

    def stop(self) -> None:
        self._retry.stop()
        self._silence.stop()
        self._pairing_window.stop()
        self._discovery.stop()
        self._listener.stop()
        if self._link is not None:
            self._link.close()
            self._link = None
        self._service.detach_link()
        self._set_state(LinkState.UNPAIRED if self.peer is None else LinkState.DISCONNECTED)

    def set_manual_address(self, address: str) -> None:
        self._manual_address = address.strip()
        if self.peer is not None:
            self._attempt = 0
            self._try_connect()

    # ------------------------------------------------------------------ парринг

    def begin_pairing(self) -> None:
        self._pairing = True
        self._listener.expect(None)
        self._discovery.start(
            Beacon(
                origin_id=self._identity.origin_id,
                machine_name=self._machine_name,
                fingerprint=self._identity.fingerprint,
                port=TCP_PORT,
                protocol_major=PROTOCOL_MAJOR,
            )
        )
        self._pairing_window.start()
        self._set_state(LinkState.SEARCHING)

    def confirm_pairing(self, candidate: PairingCandidate) -> None:
        """Человек сверил код и подтвердил. Только теперь появляется доверие."""
        self._trust.remember(candidate.as_trusted())
        self._end_pairing()
        self._listener.expect(candidate.fingerprint)
        self.peer_changed.emit(self._trust.peer())
        self._attempt = 0
        self._try_connect()

    def forget_peer(self) -> None:
        self._trust.forget()
        if self._link is not None:
            self._link.close()
            self._link = None
        self._service.detach_link()
        self._listener.expect(None)
        self.peer_changed.emit(None)
        self._set_state(LinkState.UNPAIRED)

    def _end_pairing(self) -> None:
        self._pairing = False
        self._pairing_window.stop()
        self._discovery.stop()

    def _offer_pairing(self, candidate: PairingCandidate) -> None:
        code = pairing_code(self._identity.fingerprint, candidate.fingerprint)
        self.pairing_code_ready.emit(code, candidate)

    def _on_peer_seen(self, beacon: Beacon, address: str) -> None:
        if not self._pairing:
            # Спаренный компьютер сменил адрес - этого достаточно, чтобы позвонить.
            if self.peer is not None and beacon.origin_id == self.peer.origin_id:
                self._trust.update_address(address)
                self._try_connect()
            return
        self._offer_pairing(
            PairingCandidate(
                origin_id=beacon.origin_id,
                machine_name=beacon.machine_name,
                fingerprint=beacon.fingerprint,
                address=address,
                port=beacon.port,
            )
        )

    # ------------------------------------------------------------------ соединение

    def _address(self) -> str:
        if self._manual_address:
            return self._manual_address
        peer = self.peer
        return peer.last_address if peer else ""

    def _try_connect(self) -> None:
        peer = self.peer
        address = self._address()
        if peer is None or not address:
            self._start_looking()
            return
        if peer.origin_id < self._identity.origin_id:
            # Звонит меньший. Мы больше - ждём звонка, но следим за молчанием.
            self._silence.start()
            return

        link = PeerLink(self._identity, self)
        link.connected.connect(lambda _fingerprint: self._on_connected(link))
        link.disconnected.connect(self._on_disconnected)
        link.connect_to(address, TCP_PORT, peer.fingerprint)

    def _start_looking(self) -> None:
        """Адрес неизвестен: включить маячок, пока пир не найдётся."""
        self._set_state(LinkState.SEARCHING)
        self._discovery.start(
            Beacon(
                origin_id=self._identity.origin_id,
                machine_name=self._machine_name,
                fingerprint=self._identity.fingerprint,
                port=TCP_PORT,
                protocol_major=PROTOCOL_MAJOR,
            )
        )

    def _on_incoming_link(self, link: PeerLink) -> None:
        if self._link is not None:
            link.close()
            return
        self._on_connected(link)

    def _on_connected(self, link: PeerLink) -> None:
        self._link = link
        self._attempt = 0
        self._retry.stop()
        self._discovery.stop()
        link.send(
            Message(
                MessageType.HELLO,
                {
                    "protocol_major": PROTOCOL_MAJOR,
                    "protocol_minor": PROTOCOL_MINOR,
                    "origin_id": self._identity.origin_id,
                    "machine_name": self._machine_name,
                },
                b"",
            )
        )
        link.message_received.connect(self._on_message)
        self._service.attach_link(link)
        self._silence.start()
        self._set_state(LinkState.CONNECTED)

    def _on_message(self, message: Message) -> None:
        self._silence.start()
        if message.type is MessageType.HELLO:
            if int(message.header.get("protocol_major", -1)) != PROTOCOL_MAJOR:
                self._drop("вторая машина говорит на другой версии протокола")

    def _on_disconnected(self, reason: str) -> None:
        self._drop(reason)

    def _drop(self, reason: str) -> None:
        self._silence.stop()
        if self._link is not None:
            self._link.close()
            self._link = None
        self._service.detach_link()
        self._set_state(LinkState.DISCONNECTED)
        self._retry.start(reconnect_delay_ms(self._attempt))
        self._attempt += 1


__all__ = [
    "RECONNECT_DELAYS_MS",
    "TCP_PORT",
    "ClipboardCoordinator",
    "LinkState",
    "reconnect_delay_ms",
]
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_coordinator.py -q`
Expected: PASS, 7 тестов

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/coordinator.py configurator/tests/clipboard/test_coordinator.py
git commit -m "Join the pieces: pair, connect, and come back after a drop"
```

---

### Task 16: Собрать production-путь

До этой задачи всё написанное работает только в тестах. `app.main` о подсистеме не знает — то есть функции нет, сколько бы тестов ни было зелёными. Эта задача её включает.

**Files:**
- Modify: `configurator/src/duo_input/app.py`
- Create: `configurator/tests/ui/test_runtime_wiring.py`

**Interfaces:**
- Consumes: `ClipboardCoordinator`, `WindowsClipboardBackend`, `TrayIcon`, `ClipboardPage`, `load_or_create`, `TrustStore`, `application_directory`.
- Produces: в `app.py` — `single_instance_lock(name: str) -> QLocalServer | None`; `configure_runtime(application, window, settings) -> ClipboardCoordinator | None`.

- [ ] **Step 1: Написать падающие тесты**

```python
"""Production-путь: то, что собрано в main, а не только в тестах."""

from __future__ import annotations

from PySide6.QtCore import QSettings

from duo_input import app as app_module
from duo_input.app import build_main_window, configure_runtime, single_instance_lock


def _settings(tmp_path, enabled: bool) -> QSettings:
    settings = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings.setValue("clipboard/enabled", enabled)
    return settings


def test_nothing_is_built_while_sharing_is_off(qtbot, qapp, tmp_path):
    window = build_main_window(settings=_settings(tmp_path, False))
    qtbot.addWidget(window)

    assert configure_runtime(qapp, window, _settings(tmp_path, False)) is None


def test_the_coordinator_is_built_when_sharing_is_on(qtbot, qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    coordinator = configure_runtime(qapp, window, settings)

    assert coordinator is not None
    coordinator.stop()


def test_switching_sharing_on_stops_the_program_quitting_with_the_window(
    qtbot, qapp, tmp_path, monkeypatch
):
    monkeypatch.setattr(app_module, "application_directory", lambda: tmp_path)
    settings = _settings(tmp_path, True)
    window = build_main_window(settings=settings)
    qtbot.addWidget(window)

    coordinator = configure_runtime(qapp, window, settings)

    assert qapp.quitOnLastWindowClosed() is False
    coordinator.stop()


def test_the_second_instance_cannot_take_the_lock(qapp):
    first = single_instance_lock("duo-input-test-lock")
    second = single_instance_lock("duo-input-test-lock")

    assert first is not None
    assert second is None

    first.close()


def test_main_calls_configure_runtime(qapp, monkeypatch):
    """Функция, до которой main не дотягивается, — это отсутствующая функция.

    Подменяется экземпляр приложения, а не класс: QApplication приходит из C++,
    и подмена метода на классе там не держится.
    """
    called: list[bool] = []

    monkeypatch.setattr(app_module, "configure_runtime", lambda *args: called.append(True))
    monkeypatch.setattr(app_module, "single_instance_lock", lambda *args, **kwargs: object())
    monkeypatch.setattr(app_module, "start_window", lambda window: None)
    monkeypatch.setattr(app_module, "build_main_window", lambda **kwargs: object())
    monkeypatch.setattr(qapp, "exec", lambda: 0)

    app_module.main([])

    assert called == [True]
```

- [ ] **Step 2: Убедиться, что тесты падают**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/ui/test_runtime_wiring.py -q`
Expected: FAIL, `ImportError: cannot import name 'configure_runtime'`

- [ ] **Step 3: Дописать `app.py`**

Добавить импорты:

```python
import socket

from PySide6.QtNetwork import QLocalServer, QLocalSocket

from duo_input.clipboard.coordinator import ClipboardCoordinator
from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.trust import TrustStore
from duo_input.clipboard.windows_backend import WindowsClipboardBackend
from duo_input.persistence.locations import application_directory
from duo_input.ui.tray import TrayIcon
```

Добавить функции:

```python
SINGLE_INSTANCE_NAME = "duo-input-single-instance"


def single_instance_lock(name: str = SINGLE_INSTANCE_NAME) -> QLocalServer | None:
    """Захватить имя. ``None`` означает, что программа уже запущена.

    Два процесса дрались бы за один порт и оба реагировали бы на изменение
    буфера обмена - то есть петля возникла бы на одной машине, без всякой сети.
    """
    probe = QLocalSocket()
    probe.connectToServer(name)
    if probe.waitForConnected(100):
        probe.disconnectFromServer()
        return None

    QLocalServer.removeServer(name)
    server = QLocalServer()
    if not server.listen(name):
        return None
    return server


def configure_runtime(application, window, settings) -> ClipboardCoordinator | None:
    """Собрать общий буфер, если он включён. Иначе не открывать ни одного сокета."""
    if not bool(settings.value("clipboard/enabled", False, type=bool)):
        return None

    application.setQuitOnLastWindowClosed(False)

    directory = application_directory()
    identity = load_or_create(directory)
    coordinator = ClipboardCoordinator(
        identity=identity,
        trust=TrustStore(directory / "peers.json"),
        machine_name=socket.gethostname(),
        parent=application,
    )

    backend = WindowsClipboardBackend(application.clipboard(), coordinator)
    coordinator.service.attach_backend(backend)
    backend.snapshot_taken.connect(coordinator.service.on_local_snapshot)
    backend.start()

    tray = TrayIcon(application.windowIcon(), application)
    tray.open_requested.connect(window.showNormal)
    tray.quit_requested.connect(application.quit)
    tray.sharing_action.setChecked(True)
    coordinator.state_changed.connect(tray.set_link_state)
    coordinator.state_changed.connect(window.clipboard_page.set_link_state)
    coordinator.peer_changed.connect(window.clipboard_page.set_peer)
    window.clipboard_page.set_peer(coordinator.peer)
    window.clipboard_page.pair_requested.connect(coordinator.begin_pairing)
    window.clipboard_page.forget_requested.connect(coordinator.forget_peer)
    window.clipboard_page.address_changed.connect(coordinator.set_manual_address)
    tray.show()

    coordinator.start()
    return coordinator
```

В `main`, вместо ранее добавленного блока с `setQuitOnLastWindowClosed`, поставить:

```python
    lock = single_instance_lock()
    if lock is None:
        # Программа уже работает - её окно поднимет сам пользователь из трея.
        return 0

    settings = QSettings()
    window = build_main_window(translations=translations, settings=settings)
    configure_runtime(application, window, settings)
    start_window(window)
    return application.exec()
```

- [ ] **Step 4: Убедиться, что тесты проходят**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/ui/test_runtime_wiring.py -q`
Expected: PASS, 5 тестов

- [ ] **Step 5: Прогнать весь набор**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest -q`
Expected: PASS целиком

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/app.py configurator/tests/ui/test_runtime_wiring.py
git commit -m "Make the shared clipboard something the program actually starts"
```

---

### Task 17: Защитные тесты и контракт сборки

Эта задача проверяет не поведение, а обещания: что подсистема осталась отделимой, что каждое защитное условие действительно работает и что собранная программа умеет то, что обещает.

**Files:**
- Create: `configurator/tests/clipboard/test_boundaries.py`
- Modify: `configurator/tests/packaging/test_dist.py`
- Create: `configurator/tests/clipboard/test_guards_are_real.py`
- Create: `docs/user/clipboard-ru.md`

**Interfaces:**
- Consumes: всё построенное выше.
- Produces: тесты и пользовательскую документацию. Production-кода не добавляет.

- [ ] **Step 1: Написать тест границы**

```python
"""Подсистема должна оставаться отделимой от интерфейса."""

from __future__ import annotations

import ast
from pathlib import Path

PACKAGE = Path("src", "duo_input", "clipboard")


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text("utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


def test_the_clipboard_package_never_imports_qtwidgets():
    offenders = {
        path.name
        for path in PACKAGE.glob("*.py")
        if any(name.startswith("PySide6.QtWidgets") for name in _imported_modules(path))
    }

    assert offenders == set(), (
        "clipboard/ должен зависеть только от QtCore и QtNetwork, иначе его "
        "нельзя будет вынести в отдельный процесс вместе с передачей файлов"
    )


def test_the_clipboard_package_never_imports_the_ui():
    offenders = {
        path.name
        for path in PACKAGE.glob("*.py")
        if any(name.startswith("duo_input.ui") for name in _imported_modules(path))
    }

    assert offenders == set()
```

- [ ] **Step 2: Запустить и убедиться, что он проходит**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_boundaries.py -q`
Expected: PASS, 2 теста. Если падает — вынести виновный импорт, а не ослабить тест.

- [ ] **Step 3: Написать мутационную проверку защитных условий**

```python
"""Проверка того, что защитные условия действительно защищают.

Тест, который проходит и с удалённым условием, не тестирует ничего. Здесь
каждое условие подменяется на пропускающее, и соответствующая проверка обязана
упасть. Если она не падает - виновата проверка, а не этот файл.
"""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

from duo_input.clipboard.backend import ClipboardSnapshot
from duo_input.clipboard.offer import ClipboardOffer, describe
from duo_input.clipboard.service import ClipboardService

OURS = "1" * 32
THEIRS = "2" * 32


class _Backend:
    def __init__(self) -> None:
        self.published = []

    def start(self) -> None: ...

    def stop(self) -> None: ...

    def publish(self, offer, fetcher) -> None:
        self.published.append(offer)

    def payload(self, mime):
        return None


def test_without_the_second_belt_the_clipboard_would_echo(monkeypatch):
    service = ClipboardService(OURS)
    service.attach_backend(_Backend())
    service.on_remote_offer(ClipboardOffer(THEIRS, 1, describe({"text/plain": b"remote"})))

    monkeypatch.setattr(ClipboardService, "_echoes_what_we_received", lambda self, snapshot: False)

    sent = []
    service.offer_ready.connect(sent.append)
    service.on_local_snapshot(ClipboardSnapshot({"text/plain": b"remote"}))

    assert sent, "второй пояс снят, а эха нет - значит проверка эха ничего не проверяет"


def test_without_the_third_belt_our_own_offer_would_come_back(monkeypatch):
    service = ClipboardService(OURS)
    backend = _Backend()
    service.attach_backend(backend)

    original = ClipboardService.on_remote_offer

    def without_origin_check(self, offer):
        return original(self, ClipboardOffer(THEIRS, offer.seq, offer.descriptors))

    monkeypatch.setattr(ClipboardService, "on_remote_offer", without_origin_check)
    service.on_remote_offer(ClipboardOffer(OURS, 9, describe({"text/plain": b"boomerang"})))

    assert backend.published, "третий пояс снят, а объявление не прошло - проверка мертва"


def test_the_size_ceiling_is_the_reason_a_huge_payload_is_refused():
    from duo_input.clipboard.offer import MAX_CONTENT_BYTES

    with pytest.raises(ValueError):
        describe({"image/png": b"x" * (MAX_CONTENT_BYTES + 1)})

    describe({"image/png": b"x" * MAX_CONTENT_BYTES})
```

- [ ] **Step 4: Запустить мутационную проверку**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/clipboard/test_guards_are_real.py -q`
Expected: PASS, 3 теста

- [ ] **Step 5: Дописать контракт сборки**

Добавить в конец `configurator/tests/packaging/test_dist.py`:

```python
def test_the_built_program_can_actually_open_a_tls_connection(dist: Path):
    """TLS должен работать внутри сборки, а не только в среде разработки.

    Недостающая криптографическая библиотека выглядит у пользователя как "нет
    связи" и никак иначе, поэтому её отсутствие ловится здесь, а не в отзывах.
    """
    executable = dist / "DuoInput.exe"
    result = subprocess.run(
        [str(executable), "--self-check-tls"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "tls: ok" in result.stdout.lower(), result.stdout + result.stderr
```

Добавить `import subprocess` в начало файла.

Затем добавить в `configurator/src/duo_input/app.py`, в самое начало `main`, до создания `QApplication`:

```python
    arguments = list(argv) if argv is not None else sys.argv
    if "--self-check-tls" in arguments:
        # Собранная программа должна уметь доказать, что TLS в ней работает.
        from PySide6.QtNetwork import QSslSocket

        print(f"tls: {'ok' if QSslSocket.supportsSsl() else 'missing'}")
        print(f"backend: {QSslSocket.activeBackend()}")
        return 0
```

- [ ] **Step 6: Написать документацию пользователя**

Создать `docs/user/clipboard-ru.md`, по образцу `docs/user/quick-start-ru.md`: что делает общий буфер; что Duo Input должен быть запущен на обоих компьютерах; как связать компьютеры и зачем сверять шестизначный код; что содержимое уходит в момент вставки, а не копирования; что менеджеры паролей исключаются из синхронизации; что делать, если Windows спросила про брандмауэр; что делать, если компьютеры не нашли друг друга (ручной адрес); что файлы появятся позже.

- [ ] **Step 7: Прогнать всё**

Run: `cd configurator && ../.venv/Scripts/python.exe -m pytest -q`
Expected: PASS целиком. Тесты сборки пропускаются, если `dist/DuoInput` не собран, — это нормально.

- [ ] **Step 8: Commit**

```bash
git add configurator/tests/clipboard/test_boundaries.py configurator/tests/clipboard/test_guards_are_real.py configurator/tests/packaging/test_dist.py configurator/src/duo_input/app.py docs/user/clipboard-ru.md
git commit -m "Prove the guards guard and the build can really speak TLS"
```

---

### Task 18: Приёмка на двух настоящих машинах

Автотесты не покрывают то, ради чего всё это писалось. Эта задача — единственная, где участвуют два физических компьютера.

**Files:**
- Create: `docs/superpowers/records/2026-09-03-shared-clipboard-acceptance.md`

- [ ] **Step 1: Собрать программу**

Run: `configurator/packaging/nuitka-build.ps1`, затем `cd configurator && ../.venv/Scripts/python.exe -m pytest tests/packaging -q`

- [ ] **Step 2: Пройти список приёмки**

Отметить каждый пункт результатом, а не галочкой:

- [ ] Установка на обе машины; при выключенном общем буфере брандмауэр не спрашивает ничего.
- [ ] Включение общего буфера на обеих машинах; диалог брандмауэра появился и был принят.
- [ ] Связывание: шестизначный код совпал на обоих экранах; подтверждение потребовалось на обеих машинах.
- [ ] Отказ от подтверждения на одной из машин не создаёт доверия ни на одной.
- [ ] Текст: копирование на ПК1, вставка на ПК2. Кириллица и латиница.
- [ ] Ссылка из браузера.
- [ ] Изображение: снимок экрана на ПК1, вставка в редактор на ПК2.
- [ ] Обратное направление для всех трёх типов.
- [ ] Петля: после вставки на ПК2 ничего не возвращается на ПК1.
- [ ] Менеджер паролей: скопированный из него пароль **не** появляется на втором компьютере.
- [ ] Закрытие окна: приложение остаётся в трее, синхронизация продолжает работать.
- [ ] Выход через трей: синхронизация прекращается.
- [ ] Автозапуск: после перезагрузки приложение стартовало скрытым и связь восстановилась.
- [ ] Разрыв: отключить Wi-Fi на ПК1, убедиться, что трей ПК2 показывает разрыв, вернуть сеть, убедиться в переподключении без ручных действий.
- [ ] Копирование на ПК1, затем выход из программы на ПК1: вставка на ПК2 не срабатывает и сообщает об этом, буфер ПК2 не испорчен.
- [ ] Сеть с изоляцией клиентов или разные подсети: ручной адрес соединяет машины.

- [ ] **Step 3: Записать результат**

Создать `docs/superpowers/records/2026-09-03-shared-clipboard-acceptance.md` с результатами по каждому пункту, версиями Windows на обеих машинах и любыми расхождениями с ожиданием.

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/records/2026-09-03-shared-clipboard-acceptance.md
git commit -m "Record what the shared clipboard did on two real machines"
```

---

## Что этот план намеренно не делает

Файлы, macOS, историю буфера, второго пира, сигнал активного хоста от RP2040 и вынос подсистемы в отдельный процесс. Каждое из этого разобрано в разделах 13–15 спеки с причиной и способом добавить позже.
