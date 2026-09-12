# File Transfer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `Ctrl+C` on files in Windows Explorer on one computer, `Ctrl+V` in any folder on the other, streamed over the existing trusted TLS peer link with memory bounded independent of file size.

**Architecture:** A COM `IDataObject` on the receiving machine advertises
`CFSTR_FILEDESCRIPTORW` plus `CFSTR_FILECONTENTS`/`TYMED_ISTREAM`, so Explorer
itself owns the progress dialog, the cancel button, the overwrite prompt,
directory creation and the destination folder. Each `IStream::Read` on a
dedicated COM STA thread turns into one `FILE_READ` request answered by one
`FILE_CHUNK` over the peer link; a Qt-free `ChunkPipe` is the only thing the COM
thread and the Qt GUI thread share. Clipboard semantics and file transfer are
separate logical subsystems over one TLS connection.

**Tech Stack:** Python 3.12, PySide6 6.10.1 (`QtCore`/`QtNetwork` only inside the
subsystems), stdlib `ctypes` for COM, stdlib `threading` for the pipe, pytest +
pytest-qt.

**Spec:** `docs/superpowers/specs/2026-09-12-file-transfer-design.md`

## Global Constraints

Every task's requirements implicitly include this section. Values are copied
verbatim from the spec.

- **Test runner is the venv, never bare python.** Bare `python -m pytest` exits
  0 without running anything. Always
  `.venv/Scripts/python.exe -m pytest ...` from the repository root.
- **Full gate command:** `.venv/Scripts/python.exe -m pytest configurator/tests tests -q`
- **No new runtime dependency.** stdlib `ctypes` only. `pywin32` and `comtypes`
  are forbidden unless Phase 0 proves `ctypes` cannot work.
- **`transfer/` never imports `PySide6.QtWidgets`.**
- **`ctypes` is allowed only in `transfer/windows_com.py` and `transfer/windows_files.py`.**
- **`transfer/windows_com.py` never imports `PySide6` at all.** This is the
  literal boundary "the COM thread does not touch Qt".
- **`model.py`, `paths.py`, `pipe.py`, `scanner.py` import neither `PySide6` nor
  `transfer.windows_*`.**
- **Protocol:** `PROTOCOL_MAJOR` stays `1`; `PROTOCOL_MINOR` becomes `1`.
  `FILE_*` is never sent to a peer that did not advertise `files/1`.
- **Wire paths use `/` as separator** and are always relative. Conversion to `\`
  happens only when building `cFileName`. Absolute source paths never go on the
  wire.
- **`Ctrl+X` always becomes `DROPEFFECT_COPY`.** Source files are never deleted.
- **Never log file bytes. Never log full paths** — basename and size only.
- **No custom pytest markers.** The repository has none registered; gate
  session-dependent tests with `pytest.mark.skipif` on an environment variable,
  following `DUO_INPUT_HIL_WRITE` in
  `configurator/tests/integration/test_real_config_contract.py:184`.
- **Tests that need a real Windows desktop session** (COM, clipboard) must not
  run under `QT_QPA_PLATFORM=offscreen`. Gate them on
  `DUO_INPUT_COM_SESSION=1`.
- **No Cyrillic in `qtbot.keyClicks`** — it crashes with `0xC0000409` and no
  output. Type ASCII; put Russian text on the assertion side.
- **Every guard gets a mutation test.** A test that still passes with the guard
  deleted is testing nothing. Mutation tests live in
  `configurator/tests/transfer/test_guards_are_real.py`, following
  `configurator/tests/clipboard/test_guards_are_real.py`.

## File Structure

| File | Responsibility |
|---|---|
| `configurator/src/duo_input/transfer/__init__.py` | empty package marker |
| `configurator/src/duo_input/transfer/model.py` | `TransferEntry`, `SkippedEntry`, `TransferManifest`, strict dict round-trip. No Qt |
| `configurator/src/duo_input/transfer/paths.py` | `sanitize_relative_path`, `sanitize_manifest`, `UnsafePath`. No Qt |
| `configurator/src/duo_input/transfer/pipe.py` | `ChunkPipe`, `PipeClosed`, `PipeOverflow`. `threading` only |
| `configurator/src/duo_input/transfer/scanner.py` | walk source tree → manifest + absolute-path map. No Qt |
| `configurator/src/duo_input/transfer/source.py` | `SnapshotRegistry`: held descriptors, change detection, retention |
| `configurator/src/duo_input/transfer/service.py` | `FileTransferService`: both state machines, message handling, signals |
| `configurator/src/duo_input/transfer/platform_files.py` | the only `sys.platform` branch, lazy imports |
| `configurator/src/duo_input/transfer/windows_files.py` | `IDataObject`, `IEnumFORMATETC`, descriptor building, STA thread, `post_to_service` |
| `configurator/src/duo_input/transfer/windows_com.py` | vtable machinery, GUIDs, structs, `IStream`. No PySide6 |

Phases 0 and 3 add throwaway spikes under `configurator/tests/transfer/`,
following the two existing clipboard spikes.


## Spec Coverage

Every section of the spec, and the task that implements it. A blank cell here
is a gap in the plan, not a section that did not need doing — this table found
two while it was being written (§2 fact 3 and §15, now Tasks 1.14 and 1.15).

| Spec | Task |
|---|---|
| §1 scope | all phases; DEFER list is not built |
| §2 facts 1–2 (RemoteMimeData unusable, seam at formats.py:35) | 1.12 |
| §2 fact 3 (no backpressure in the transport) | 1.14 |
| §2 fact 4 (unknown type drops the link) | 1.7 |
| §2 fact 5 (nested QEventLoop) | 1.10 — separate service, no nested loop |
| §2 fact 6 (WebDAV ruled out) | decided; no task |
| §2 fact 7 (Windows share semantics) | 1.9 |
| §2 fact 8 (no pywin32/comtypes) | 2.1 |
| §3 architecture C, bare ctypes, async capability | Phase 0; 2.1–2.5 |
| §4 components | 1.1–1.11; 2.1–2.6 |
| §5 boundaries and changed files | 2.6 (four rules); 1.6, 1.7, 1.12, 1.14, 2.7, 4.1, 4.2 |
| §6 descriptors, Ctrl+X → Copy, cFileName ceiling | 2.3, 2.4 |
| §7 the bridge, its invariants, sender disk read | 2.2, 2.5, 3.2 |
| §8 six messages, offset reads, one in flight, backpressure | 1.6, 1.10, 1.11, 3.2 |
| §9 both state machines, completion source of truth, retention | 1.11, 0.5, 1.9 |
| §10 held descriptor with change detection | 1.9 |
| §11 path security, reparse points | 1.2, 1.3, 1.4, 1.5 |
| §12 scenarios 1–5 | 1.10, 1.11, 1.13, 4.4 |
| §13 capability negotiation, one TLS connection | 1.7; 4.4 row 15 |
| §14 the clipboard loop | 2.7 |
| §15 privacy in logs | 1.15 |
| §16 threading model | 2.5, 2.6 |
| §17 interface | 4.1, 4.2 |
| §18 macOS future-proofing | 2.6 |
| §19 testing strategy | the test step of every task; 4.4 |
| §20 spikes | Phase 0; 3.1 |
| §21 risks R1–R11 | R1: 0.1, 2.1 · R2: 1.2–1.4 · R3: 0.3, 2.5 · R4: 4.3 · R5: 2.4 · R6: 2.7 · R7: 0.4 · R8: 1.14, 4.4 · R9: 3.2 · R10: 1.9 · R11: 2.5, 4.1 |
| §22 open questions 1–3 | 0.5 (1 and 3); 3.2 (2) |

---

# Phase 0 — Spike 1: Explorer and COM semantics

**Purpose:** close §22 questions 1, 2 (partially) and 3 of the spec. Synthetic
data, no network, no production code.

**Hard rule:** no file under `configurator/src/duo_input/transfer/` is created in
this phase. Phase 0 produces a throwaway script, a record, and a spec update.

**Hard rule:** if architecture C hits a proven, unfixable blocker, STOP. Do not
silently fall back to architecture B. Present the evidence and a proposal.

## Task 0.1: ctypes COM vtable machinery, proven in-process

**Files:**
- Create: `configurator/tests/transfer/__init__.py` (empty)
- Create: `configurator/tests/transfer/spike_com_vtable.py`
- Test: `configurator/tests/transfer/test_spike_com_vtable.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `make_vtable(*methods) -> ctypes.Array`,
  `COMObject` with `.pointer -> ctypes.c_void_p`, `.refcount -> int`,
  `query_interface(ptr, iid) -> int`, `add_ref(ptr) -> int`,
  `release(ptr) -> int`, `guid_from_string(s) -> GUID`.
  These are throwaway names; Phase 2 re-derives them properly.

This task exists because a refcount or vtable slot error in COM crashes the
process instead of raising. Proving the machinery in-process, where pytest can
see it, is far cheaper than discovering it through Explorer.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_spike_com_vtable.py
"""Плита под COM: vtable и подсчёт ссылок обязаны работать до всякого Проводника.

Ошибка в слоте vtable или в счётчике ссылок роняет процесс, а не бросает
исключение. Поэтому машинерия проверяется здесь, внутри процесса, где pytest
её видит, а не через Проводник, где падение выглядит как исчезновение окна.
"""

from __future__ import annotations

import ctypes

from spike_com_vtable import (
    IID_IUNKNOWN,
    COMObject,
    add_ref,
    guid_from_string,
    query_interface,
    release,
)

S_OK = 0
E_NOINTERFACE = -2147467262  # 0x80004002


def test_a_fresh_object_starts_with_one_reference():
    obj = COMObject([IID_IUNKNOWN])

    assert obj.refcount == 1


def test_add_ref_and_release_walk_the_count_back_down():
    obj = COMObject([IID_IUNKNOWN])

    assert add_ref(obj.pointer) == 2
    assert add_ref(obj.pointer) == 3
    assert release(obj.pointer) == 2
    assert release(obj.pointer) == 1


def test_query_interface_for_a_supported_iid_succeeds_and_adds_a_reference():
    obj = COMObject([IID_IUNKNOWN])
    out = ctypes.c_void_p()

    result = query_interface(obj.pointer, guid_from_string(IID_IUNKNOWN), ctypes.byref(out))

    assert result == S_OK
    assert out.value == obj.pointer.value
    assert obj.refcount == 2


def test_query_interface_for_an_unsupported_iid_refuses_and_nulls_the_out_pointer():
    obj = COMObject([IID_IUNKNOWN])
    out = ctypes.c_void_p(0xDEAD)

    result = query_interface(
        obj.pointer,
        guid_from_string("{11111111-2222-3333-4444-555555555555}"),
        ctypes.byref(out),
    )

    assert result == E_NOINTERFACE
    assert out.value is None, "отказавший QueryInterface обязан обнулить out-указатель"


def test_guid_round_trips_through_its_string_form():
    text = "{0000000C-0000-0000-C000-000000000046}"  # IID_IStream

    guid = guid_from_string(text)

    assert guid.Data1 == 0x0000000C
    assert guid.Data4[7] == 0x46
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_spike_com_vtable.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'spike_com_vtable'`

- [ ] **Step 3: Write minimal implementation**

```python
# configurator/tests/transfer/spike_com_vtable.py
"""Throwaway: минимальная COM-машинерия на голом ctypes.

Не production. Phase 2 выводит это заново в transfer/windows_com.py, уже
разложенное по ответственностям. Здесь цель одна: доказать, что vtable и
подсчёт ссылок на ctypes собираются вообще.

Обратные вызовы держатся в self._callbacks намеренно: ctypes не удерживает
CFUNCTYPE-объект за нас, и собранный сборщиком мусора колбэк превращается в
переход по освобождённому адресу - то есть в падение процесса без исключения.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

IID_IUNKNOWN = "{00000000-0000-0000-C000-000000000046}"

S_OK = 0
E_NOINTERFACE = -2147467262
E_POINTER = -2147467261


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def guid_from_string(text: str) -> GUID:
    guid = GUID()
    # CLSIDFromString принимает только форму в фигурных скобках.
    result = ctypes.oledll.ole32.CLSIDFromString(ctypes.c_wchar_p(text), ctypes.byref(guid))
    if result != S_OK:  # pragma: no cover - неверная строка в тесте была бы опечаткой
        raise ValueError(f"не GUID: {text!r}")
    return guid


def _same_guid(a: GUID, b: GUID) -> bool:
    return bytes(memoryview(a).cast("B")) == bytes(memoryview(b).cast("B"))


_QUERY = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_ADDREF = ctypes.WINFUNCTYPE(ctypes.c_ulong, ctypes.c_void_p)


def make_vtable(*methods) -> ctypes.Array:
    """Массив указателей на функции - это и есть vtable."""
    table = (ctypes.c_void_p * len(methods))()
    for index, method in enumerate(methods):
        table[index] = ctypes.cast(method, ctypes.c_void_p)
    return table


class COMObject:
    """IUnknown и ничего больше: три слота, счётчик ссылок, живые колбэки."""

    def __init__(self, supported_iids: list[str]) -> None:
        self._supported = [guid_from_string(iid) for iid in supported_iids]
        self.refcount = 1

        self._callbacks = [
            _QUERY(self._query_interface),
            _ADDREF(self._add_ref),
            _ADDREF(self._release),
        ]
        self._vtable = make_vtable(*self._callbacks)
        # Объект в памяти - это указатель на vtable по нулевому смещению.
        self._slot = ctypes.c_void_p(ctypes.addressof(self._vtable))
        self.pointer = ctypes.c_void_p(ctypes.addressof(self._slot))

    def _query_interface(self, _this, riid, ppv) -> int:
        if not ppv:
            return E_POINTER
        out = ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))
        requested = ctypes.cast(riid, ctypes.POINTER(GUID)).contents
        if any(_same_guid(requested, supported) for supported in self._supported):
            out[0] = self.pointer
            self.refcount += 1
            return S_OK
        # Отказ обязан обнулить out-указатель: вызывающий по контракту COM
        # вправе прочитать его и увидеть NULL, а не мусор.
        out[0] = None
        return E_NOINTERFACE

    def _add_ref(self, _this) -> int:
        self.refcount += 1
        return self.refcount

    def _release(self, _this) -> int:
        self.refcount -= 1
        return self.refcount


def _call(pointer: ctypes.c_void_p, slot: int, prototype, *args):
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p)).contents
    entries = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))
    return prototype(entries[slot])(pointer, *args)


def query_interface(pointer, riid, ppv) -> int:
    return _call(pointer, 0, _QUERY, ctypes.byref(riid), ppv)


def add_ref(pointer) -> int:
    return _call(pointer, 1, _ADDREF)


def release(pointer) -> int:
    return _call(pointer, 2, _ADDREF)
```

Add `configurator/tests/transfer/__init__.py` as an empty file, and a
`conftest.py` so the spike module is importable by name:

```python
# configurator/tests/transfer/conftest.py
"""Спайки лежат рядом с тестами и импортируются по имени - как в tests/clipboard."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_spike_com_vtable.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add configurator/tests/transfer/
git commit -m "Prove the ctypes COM vtable machinery in-process

A refcount or vtable slot error in COM crashes the process instead of
raising, so the machinery is proven where pytest can see it before any of
it is pointed at Explorer. Throwaway: Phase 2 re-derives this properly.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 0.2: descriptor-only IDataObject on the clipboard

**Files:**
- Create: `configurator/tests/transfer/spike_virtual_files.py`
- Test: manual, in a real Windows session. No pytest test can drive Explorer.

**Interfaces:**
- Consumes: `spike_com_vtable.COMObject`, `make_vtable`, `guid_from_string`.
- Produces: a runnable script;
  `python spike_virtual_files.py --descriptor-only` offers three entries and
  logs every `GetData` call.

This is deliberately the smallest thing that can fail: no `IStream` yet. If
Explorer does not even offer a paste, the problem is in the data object, and
mixing `IStream` in would hide which half broke.

- [ ] **Step 1: Write the spike script**

```python
# configurator/tests/transfer/spike_virtual_files.py
"""Throwaway spike: принимает ли Проводник наш IDataObject.

Запуск в НАСТОЯЩЕЙ сессии Windows, не под offscreen:
    .venv\\Scripts\\python.exe configurator/tests/transfer/spike_virtual_files.py --descriptor-only

Скрипт кладёт в буфер обмена объект, объявляющий три записи (папка, файл
внутри неё и большой файл), и пишет журнал каждого обращения Проводника. На
этом шаге содержимое НЕ отдаётся вовсе: задача - узнать, доходит ли дело до
запроса содержимого.

Каждая строка журнала несёт id потока: без него утверждение "GUI не
блокируется" остаётся предположением (спека §7).
"""

from __future__ import annotations

import argparse
import ctypes
import threading
import time
from ctypes import wintypes

from spike_com_vtable import (
    E_POINTER,
    IID_IUNKNOWN,
    S_OK,
    GUID,
    COMObject,
    make_vtable,
)

IID_IDATAOBJECT = "{0000010E-0000-0000-C000-000000000046}"

DV_E_FORMATETC = -2147221404  # 0x80040064
E_NOTIMPL = -2147467263

TYMED_HGLOBAL = 1
DATADIR_GET = 1

DROPEFFECT_COPY = 1
FILE_ATTRIBUTE_DIRECTORY = 0x10
FD_FILESIZE = 0x40
FD_ATTRIBUTES = 0x04
FD_PROGRESSUI = 0x4000

_START = time.perf_counter()


def log(message: str) -> None:
    elapsed = time.perf_counter() - _START
    print(f"[{elapsed:8.3f}s tid={threading.get_ident():>6}] {message}", flush=True)


class FORMATETC(ctypes.Structure):
    _fields_ = [
        ("cfFormat", wintypes.WORD),
        ("ptd", ctypes.c_void_p),
        ("dwAspect", wintypes.DWORD),
        ("lindex", ctypes.c_long),
        ("tymed", wintypes.DWORD),
    ]


class STGMEDIUM(ctypes.Structure):
    _fields_ = [
        ("tymed", wintypes.DWORD),
        ("data", ctypes.c_void_p),
        ("pUnkForRelease", ctypes.c_void_p),
    ]


class FILETIME(ctypes.Structure):
    _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]


class FILEDESCRIPTORW(ctypes.Structure):
    _fields_ = [
        ("dwFlags", wintypes.DWORD),
        ("clsid", GUID),
        ("sizel", ctypes.c_long * 2),
        ("pointl", ctypes.c_long * 2),
        ("dwFileAttributes", wintypes.DWORD),
        ("ftCreationTime", FILETIME),
        ("ftLastAccessTime", FILETIME),
        ("ftLastWriteTime", FILETIME),
        ("nFileSizeHigh", wintypes.DWORD),
        ("nFileSizeLow", wintypes.DWORD),
        ("cFileName", ctypes.c_wchar * 260),
    ]


#: Три записи: папка, файл внутри неё, и большой файл. Байтов ни у одной нет -
#: содержимое на этом шаге не отдаётся вовсе.
ENTRIES = [
    ("Photos", True, 0),
    ("Photos\\img1.bin", False, 3 * 1024 * 1024),
    ("big.bin", False, 4 * 1024 * 1024 * 1024),
]


def register_format(name: str) -> int:
    value = ctypes.windll.user32.RegisterClipboardFormatW(ctypes.c_wchar_p(name))
    log(f"RegisterClipboardFormatW({name!r}) -> {value}")
    return value
```

- [ ] **Step 2: Append the data object and the entry point to the same file**

```python
def build_group_descriptor() -> bytes:
    """FILEGROUPDESCRIPTORW: счётчик, затем массив дескрипторов."""
    blob = bytearray(ctypes.sizeof(wintypes.DWORD))
    ctypes.memmove(
        (ctypes.c_char * len(blob)).from_buffer(blob),
        ctypes.byref(wintypes.DWORD(len(ENTRIES))),
        ctypes.sizeof(wintypes.DWORD),
    )
    for name, is_directory, size in ENTRIES:
        descriptor = FILEDESCRIPTORW()
        descriptor.dwFlags = FD_ATTRIBUTES | FD_PROGRESSUI
        descriptor.cFileName = name
        if is_directory:
            descriptor.dwFileAttributes = FILE_ATTRIBUTE_DIRECTORY
        else:
            descriptor.dwFlags |= FD_FILESIZE
            descriptor.nFileSizeHigh = size >> 32
            descriptor.nFileSizeLow = size & 0xFFFFFFFF
        blob.extend(bytes(memoryview(descriptor).cast("B")))
    return bytes(blob)


def to_hglobal(payload: bytes) -> int:
    GMEM_MOVEABLE = 0x0002
    handle = ctypes.windll.kernel32.GlobalAlloc(GMEM_MOVEABLE, len(payload))
    address = ctypes.windll.kernel32.GlobalLock(handle)
    ctypes.memmove(address, payload, len(payload))
    ctypes.windll.kernel32.GlobalUnlock(handle)
    return handle


_GETDATA = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
_QUERYGET = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)
_ENUM = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p)
_STUB = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)


class DataObject(COMObject):
    """IDataObject, объявляющий только дескриптор группы."""

    def __init__(self) -> None:
        super().__init__([IID_IUNKNOWN, IID_IDATAOBJECT])
        self.cf_descriptor = register_format("FileGroupDescriptorW")
        self.cf_contents = register_format("FileContents")
        self.cf_drop_effect = register_format("Preferred DropEffect")
        self.get_data_calls: list[tuple[int, int]] = []

        self._own = [
            _GETDATA(self._get_data),
            _STUB(self._get_data_here),
            _QUERYGET(self._query_get_data),
            _GETDATA(self._get_canonical),
            _STUB(self._set_data),
            _ENUM(self._enum_format_etc),
            _STUB(self._d_advise),
            _QUERYGET(self._d_unadvise),
            _QUERYGET(self._enum_d_advise),
        ]
        self._vtable = make_vtable(*self._callbacks, *self._own)
        self._slot = ctypes.c_void_p(ctypes.addressof(self._vtable))
        self.pointer = ctypes.c_void_p(ctypes.addressof(self._slot))

    def _get_data(self, _this, pformatetc, pmedium) -> int:
        fmt = ctypes.cast(pformatetc, ctypes.POINTER(FORMATETC)).contents
        self.get_data_calls.append((fmt.cfFormat, fmt.lindex))
        log(f"GetData(cfFormat={fmt.cfFormat}, lindex={fmt.lindex}, tymed={fmt.tymed})")
        medium = ctypes.cast(pmedium, ctypes.POINTER(STGMEDIUM)).contents
        if fmt.cfFormat == self.cf_descriptor:
            medium.tymed = TYMED_HGLOBAL
            medium.data = to_hglobal(build_group_descriptor())
            medium.pUnkForRelease = None
            return S_OK
        if fmt.cfFormat == self.cf_drop_effect:
            medium.tymed = TYMED_HGLOBAL
            medium.data = to_hglobal(DROPEFFECT_COPY.to_bytes(4, "little"))
            medium.pUnkForRelease = None
            return S_OK
        log("  -> DV_E_FORMATETC (содержимое на этом шаге не отдаётся)")
        return DV_E_FORMATETC

    def _query_get_data(self, _this, pformatetc) -> int:
        fmt = ctypes.cast(pformatetc, ctypes.POINTER(FORMATETC)).contents
        log(f"QueryGetData(cfFormat={fmt.cfFormat}, lindex={fmt.lindex})")
        if fmt.cfFormat in (self.cf_descriptor, self.cf_drop_effect, self.cf_contents):
            return S_OK
        return DV_E_FORMATETC

    def _get_data_here(self, _this, _fmt, _medium, _unused) -> int:
        return E_NOTIMPL

    def _get_canonical(self, _this, _fmt, _out) -> int:
        return E_NOTIMPL

    def _set_data(self, _this, _fmt, _medium, _release) -> int:
        return E_NOTIMPL

    def _enum_format_etc(self, _this, direction, ppenum) -> int:
        log(f"EnumFormatEtc(direction={direction})")
        if direction != DATADIR_GET:
            return E_NOTIMPL
        if not ppenum:
            return E_POINTER
        # OLE_S_USEREG нам недоступен без реестра; вернуть E_NOTIMPL законно, и
        # спайк обязан записать, обходится ли Проводник без перечислителя.
        return E_NOTIMPL

    def _d_advise(self, _this, _fmt, _flags, _sink) -> int:
        return E_NOTIMPL

    def _d_unadvise(self, _this, _connection) -> int:
        return E_NOTIMPL

    def _enum_d_advise(self, _this, _out) -> int:
        return E_NOTIMPL


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--descriptor-only", action="store_true")
    parser.add_argument("--seconds", type=int, default=120)
    arguments = parser.parse_args()

    ctypes.oledll.ole32.OleInitialize(None)
    obj = DataObject()
    result = ctypes.windll.ole32.OleSetClipboard(obj.pointer)
    log(f"OleSetClipboard -> 0x{result & 0xFFFFFFFF:08X}")
    log("Вставляйте в Проводнике. Ctrl+C в этом окне для выхода.")

    deadline = time.perf_counter() + arguments.seconds
    message = wintypes.MSG()
    while time.perf_counter() < deadline:
        # Насос сообщений обязателен: без него COM не доставит ни одного вызова.
        while ctypes.windll.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            ctypes.windll.user32.TranslateMessage(ctypes.byref(message))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(message))
        time.sleep(0.01)

    log(f"GetData calls: {obj.get_data_calls}")
    ctypes.windll.ole32.OleFlushClipboard()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: Run it in a real Windows session and record what happens**

Run, from the repository root, in an interactive session (not a CI shell):

```
.venv\Scripts\python.exe configurator/tests/transfer/spike_virtual_files.py --descriptor-only
```

Then in Explorer: open any folder, press `Ctrl+V`.

Record verbatim in a scratch file:
- does Explorer's `Paste` menu item become enabled at all?
- which `cfFormat` values arrive in `QueryGetData`, and in which order?
- is `EnumFormatEtc` called, and does `E_NOTIMPL` stop Explorer?
- does `GetData` arrive for the descriptor format?
- does Explorer create the `Photos` directory before asking for any content?
- the thread id on every line.

- [ ] **Step 4: Decide whether to continue**

Pass condition for this task: `GetData` is called for
`FileGroupDescriptorW`. That single fact proves the vtable, the clipboard
registration and the marshalling all work.

If `EnumFormatEtc` returning `E_NOTIMPL` blocks Explorer, implement a real
`IEnumFORMATETC` before continuing — that is a known-possible outcome, not a
blocker, and it is why this task is separate from Task 0.3.

If `Paste` never enables and no call arrives at all, STOP. That is the
architecture-C blocker case: gather the log and report it.

- [ ] **Step 5: Commit**

```bash
git add configurator/tests/transfer/spike_virtual_files.py
git commit -m "Spike whether Explorer accepts a hand-rolled IDataObject

Descriptor only, no content: the smallest thing that can fail. Mixing
IStream in would have hidden which half broke.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 0.3: IStream with a synthetic generator and full instrumentation

**Files:**
- Modify: `configurator/tests/transfer/spike_virtual_files.py` (add `StreamObject`, extend `_get_data`)
- Test: `configurator/tests/transfer/test_spike_com_vtable.py` (add in-process `IStream` tests)

**Interfaces:**
- Consumes: `COMObject`, `make_vtable` from Task 0.1; `FORMATETC`, `STGMEDIUM`, `FILETIME`, `GUID`, `log` from Task 0.2.
- Produces: `StreamObject(entry_index, size)` exposing `.pointer`;
  `stream_read(pointer, count) -> bytes`; `synthetic_bytes(entry_index, offset, count) -> bytes`;
  module-level `READ_LOG: list[tuple[int, int, int, int]]` of
  `(entry_index, offset, requested_cb, returned)`, plus `SEEK_LOG` and `STAT_LOG`.

No network. The stream returns a deterministic synthetic pattern, so this task
measures Explorer alone. Spike 2 (Phase 3) replaces the generator with the real
pipe.

- [ ] **Step 1: Write the failing in-process IStream test**

```python
# append to configurator/tests/transfer/test_spike_com_vtable.py
import spike_virtual_files as spike


def test_the_synthetic_stream_returns_the_pattern_it_promises():
    stream = spike.StreamObject(entry_index=1, size=10)

    first = spike.stream_read(stream.pointer, 4)
    second = spike.stream_read(stream.pointer, 4)

    assert first == spike.synthetic_bytes(1, 0, 4)
    assert second == spike.synthetic_bytes(1, 4, 4)


def test_a_read_past_the_end_returns_only_what_is_left():
    stream = spike.StreamObject(entry_index=0, size=6)

    spike.stream_read(stream.pointer, 4)
    tail = spike.stream_read(stream.pointer, 4)

    assert len(tail) == 2, "Read вправе вернуть меньше запрошенного - это законно по контракту"


def test_a_read_at_the_end_returns_nothing_rather_than_blocking():
    stream = spike.StreamObject(entry_index=0, size=2)

    spike.stream_read(stream.pointer, 2)

    assert spike.stream_read(stream.pointer, 2) == b""


def test_every_read_is_recorded_with_its_requested_and_returned_size():
    spike.READ_LOG.clear()
    stream = spike.StreamObject(entry_index=3, size=5)

    spike.stream_read(stream.pointer, 4)
    spike.stream_read(stream.pointer, 4)

    assert spike.READ_LOG == [(3, 0, 4, 4), (3, 4, 4, 1)]


def test_seeking_to_the_end_reports_the_size_explorer_was_promised():
    stream = spike.StreamObject(entry_index=2, size=4096)

    position = spike.stream_seek(stream.pointer, 0, spike.STREAM_SEEK_END)

    assert position == 4096
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_spike_com_vtable.py -v -k "stream or synthetic or seeking"`
Expected: FAIL — `AttributeError: module 'spike_virtual_files' has no attribute 'StreamObject'`

- [ ] **Step 3: Write the minimal implementation**

Append to `configurator/tests/transfer/spike_virtual_files.py`:

```python
IID_ISTREAM = "{0000000C-0000-0000-C000-000000000046}"

DV_E_TYMED = -2147221399
STG_E_INVALIDFUNCTION = -2147287039  # 0x80030001
TYMED_ISTREAM = 4
STREAM_SEEK_SET = 0
STREAM_SEEK_CUR = 1
STREAM_SEEK_END = 2

#: (entry_index, offset, requested_cb, returned) на каждый Read.
READ_LOG: list[tuple[int, int, int, int]] = []
#: (entry_index, origin, offset) на каждый Seek.
SEEK_LOG: list[tuple[int, int, int]] = []
#: entry_index на каждый Stat.
STAT_LOG: list[int] = []


def synthetic_bytes(entry_index: int, offset: int, count: int) -> bytes:
    """Детерминированный шаблон: файл 4 ГиБ нигде не хранится.

    Байт зависит и от индекса записи, и от смещения, поэтому перепутанные
    записи или смещения видны в результате, а не сливаются в одинаковые нули.
    """
    return bytes((entry_index * 7 + (offset + i) * 31) & 0xFF for i in range(count))


_READ = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, wintypes.ULONG, ctypes.c_void_p
)
_WRITE = _READ
_SEEK = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_longlong, wintypes.DWORD, ctypes.c_void_p
)
_SETSIZE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_longlong)
_COPYTO = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_longlong,
    ctypes.c_void_p, ctypes.c_void_p,
)
_COMMIT = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, wintypes.DWORD)
_REVERT = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p)
_LOCK = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_longlong, ctypes.c_longlong, wintypes.DWORD
)
_STAT = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD)
_CLONE = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)


class STATSTG(ctypes.Structure):
    _fields_ = [
        ("pwcsName", ctypes.c_wchar_p),
        ("type", wintypes.DWORD),
        ("cbSize", ctypes.c_ulonglong),
        ("mtime", FILETIME),
        ("ctime", FILETIME),
        ("atime", FILETIME),
        ("grfMode", wintypes.DWORD),
        ("grfLocksSupported", wintypes.DWORD),
        ("clsid", GUID),
        ("grfStateBits", wintypes.DWORD),
        ("reserved", wintypes.DWORD),
    ]


class StreamObject(COMObject):
    """IStream над синтетическим генератором. Ни диска, ни сети."""

    def __init__(self, entry_index: int, size: int) -> None:
        super().__init__([IID_IUNKNOWN, IID_ISTREAM])
        self.entry_index = entry_index
        self.size = size
        self.position = 0

        self._own = [
            _READ(self._read),
            _WRITE(self._write),
            _SEEK(self._seek),
            _SETSIZE(self._set_size),
            _COPYTO(self._copy_to),
            _COMMIT(self._commit),
            _REVERT(self._revert),
            _LOCK(self._lock),
            _LOCK(self._unlock),
            _STAT(self._stat),
            _CLONE(self._clone),
        ]
        self._vtable = make_vtable(*self._callbacks, *self._own)
        self._slot = ctypes.c_void_p(ctypes.addressof(self._vtable))
        self.pointer = ctypes.c_void_p(ctypes.addressof(self._slot))

    def _read(self, _this, pv, cb, pcb_read) -> int:
        available = max(0, self.size - self.position)
        count = min(int(cb), available)
        payload = synthetic_bytes(self.entry_index, self.position, count)
        READ_LOG.append((self.entry_index, self.position, int(cb), count))
        log(f"IStream::Read(entry={self.entry_index}, off={self.position}, cb={cb}) -> {count}")
        if count:
            ctypes.memmove(pv, payload, count)
        self.position += count
        if pcb_read:
            ctypes.cast(pcb_read, ctypes.POINTER(wintypes.ULONG))[0] = count
        # S_OK и при частичном чтении: S_FALSE означал бы конец потока, а это
        # другое утверждение, и Проводник вправе трактовать его как ошибку.
        return S_OK

    def _seek(self, _this, offset, origin, new_position) -> int:
        SEEK_LOG.append((self.entry_index, int(origin), int(offset)))
        log(f"IStream::Seek(entry={self.entry_index}, origin={origin}, offset={offset})")
        if origin == STREAM_SEEK_SET:
            target = int(offset)
        elif origin == STREAM_SEEK_CUR:
            target = self.position + int(offset)
        elif origin == STREAM_SEEK_END:
            target = self.size + int(offset)
        else:
            return STG_E_INVALIDFUNCTION
        if target < 0:
            return STG_E_INVALIDFUNCTION
        self.position = target
        if new_position:
            ctypes.cast(new_position, ctypes.POINTER(ctypes.c_ulonglong))[0] = target
        return S_OK

    def _stat(self, _this, pstatstg, _flags) -> int:
        STAT_LOG.append(self.entry_index)
        log(f"IStream::Stat(entry={self.entry_index})")
        if not pstatstg:
            return E_POINTER
        stat = ctypes.cast(pstatstg, ctypes.POINTER(STATSTG)).contents
        ctypes.memset(ctypes.byref(stat), 0, ctypes.sizeof(STATSTG))
        stat.type = 2  # STGTY_STREAM
        stat.cbSize = self.size
        return S_OK

    def _write(self, _this, _pv, _cb, _written) -> int:
        return STG_E_INVALIDFUNCTION

    def _set_size(self, _this, _size) -> int:
        return STG_E_INVALIDFUNCTION

    def _copy_to(self, _this, _dest, _cb, _read, _written) -> int:
        return E_NOTIMPL

    def _commit(self, _this, _flags) -> int:
        return S_OK

    def _revert(self, _this) -> int:
        return S_OK

    def _lock(self, _this, _offset, _cb, _type) -> int:
        return E_NOTIMPL

    def _unlock(self, _this, _offset, _cb, _type) -> int:
        return E_NOTIMPL

    def _clone(self, _this, _out) -> int:
        return E_NOTIMPL


def _stream_slot(pointer, index, prototype):
    vtable = ctypes.cast(pointer, ctypes.POINTER(ctypes.c_void_p)).contents
    entries = ctypes.cast(vtable, ctypes.POINTER(ctypes.c_void_p))
    return prototype(entries[index])


def stream_read(pointer: ctypes.c_void_p, count: int) -> bytes:
    """Вызвать IStream::Read через vtable - то же, что делает Проводник.

    Слот 3: после QueryInterface, AddRef и Release идёт Read.
    """
    buffer = (ctypes.c_char * count)()
    read = wintypes.ULONG(0)
    _stream_slot(pointer, 3, _READ)(pointer, buffer, count, ctypes.byref(read))
    return bytes(buffer[: read.value])


def stream_seek(pointer: ctypes.c_void_p, offset: int, origin: int) -> int:
    """Слот 5: Read, Write, затем Seek."""
    position = ctypes.c_ulonglong(0)
    _stream_slot(pointer, 5, _SEEK)(pointer, offset, origin, ctypes.byref(position))
    return position.value
```

Then serve `FileContents` from the data object. In `DataObject.__init__` add
`self.streams: list[StreamObject] = []`. In `_get_data`, replace the final
`log("  -> DV_E_FORMATETC ...")` / `return DV_E_FORMATETC` pair with:

```python
        if fmt.cfFormat == self.cf_contents:
            if not fmt.tymed & TYMED_ISTREAM:
                log(f"  -> DV_E_TYMED: Проводник просит tymed={fmt.tymed}, не ISTREAM")
                return DV_E_TYMED
            _name, is_directory, size = ENTRIES[fmt.lindex]
            if is_directory:
                return DV_E_FORMATETC
            stream = StreamObject(fmt.lindex, size)
            # Держим ссылку: без неё Python соберёт объект сборщиком мусора, а
            # Проводник уйдёт по освобождённому адресу - падение без исключения.
            self.streams.append(stream)
            medium.tymed = TYMED_ISTREAM
            medium.data = stream.pointer
            medium.pUnkForRelease = None
            return S_OK
        log("  -> DV_E_FORMATETC")
        return DV_E_FORMATETC
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_spike_com_vtable.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Run the spike against Explorer, WITHOUT async capability**

```
.venv\Scripts\python.exe configurator/tests/transfer/spike_virtual_files.py --seconds 300 > configurator/tests/transfer/spike-run-a.log 2>&1
```

In Explorer: paste into a folder. Then paste again and press Cancel midway
through the 4 GiB entry. The log answers:

| Question | Where the answer is |
|---|---|
| does `Read` happen only after `Ctrl+V`, zero times before? | `READ_LOG` empty until the paste |
| what `cb` does Explorer request? | third field of each `READ_LOG` entry |
| which thread services `Read`? | `tid=` on every line |
| is `Seek` called, with what origin? | `SEEK_LOG` |
| is `Stat` called? | `STAT_LOG` |
| order of `GetData` calls | `get_data_calls`, printed at exit |
| does the native progress dialog appear and move? | eyes |
| does Cancel stop the reads? | `READ_LOG` stops growing |

- [ ] **Step 6: Commit**

```bash
git add configurator/tests/transfer/
git commit -m "Spike IStream over a synthetic generator, no network

The 4 GiB entry is a deterministic pattern, never stored, so this run
measures Explorer alone. Spike 2 swaps the generator for the real pipe.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 0.4: the two async-capability runs

**Files:**
- Create: `configurator/tests/transfer/spike_qt_responsiveness.py`
- Modify: `configurator/tests/transfer/spike_virtual_files.py`

**Interfaces:**
- Consumes: everything from Tasks 0.1–0.3.
- Produces: `--async-capability` flag; `LIFECYCLE_LOG: list[tuple[str, int]]`;
  `spike-run-a.log` and `spike-run-b.log`; `Instrument.report() -> str`.

Spec §3 refuses to call `IDataObjectAsyncCapability` necessary on theory alone.
This task is the measurement that settles it.

- [ ] **Step 1: Write the Qt responsiveness instrument**

A visible animation is the only honest instrument here: `offscreen` has hidden
exactly this class of defect in this repository before. The numeric tick
interval is recorded alongside, because "it looked like it moved" is not a
measurement.

```python
# configurator/tests/transfer/spike_qt_responsiveness.py
"""Видимая анимация плюс замер интервалов между тиками таймера.

Зависший GUI-поток виден двумя способами: глазами - по замершей полоске, и
числом - по выпавшему интервалу. Второе обязательно, потому что "кажется,
дёрнулось" результатом измерения не является.
"""

from __future__ import annotations

import time

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QProgressBar

TICK_MS = 16


class Instrument:
    """Полоска, которая обязана двигаться, и журнал того, двигалась ли она."""

    def __init__(self) -> None:
        self.bar = QProgressBar()
        self.bar.setRange(0, 100)
        self.bar.setWindowTitle("Duo Input spike: GUI responsiveness")
        self.bar.resize(420, 40)
        self.intervals: list[float] = []
        self._value = 0
        self._last = time.perf_counter()
        self._timer = QTimer()
        self._timer.setInterval(TICK_MS)
        self._timer.timeout.connect(self._tick)

    def start(self) -> None:
        self.bar.show()
        self._last = time.perf_counter()
        self._timer.start()

    def _tick(self) -> None:
        now = time.perf_counter()
        self.intervals.append(now - self._last)
        self._last = now
        self._value = (self._value + 1) % 101
        self.bar.setValue(self._value)

    def report(self) -> str:
        if not self.intervals:
            return "нет тиков вовсе - таймер не запускался"
        ordered = sorted(self.intervals)
        p99 = ordered[min(len(ordered) - 1, int(len(ordered) * 0.99))]
        return (
            f"тиков {len(ordered)}, "
            f"медиана {ordered[len(ordered) // 2] * 1000:.1f} мс, "
            f"p99 {p99 * 1000:.1f} мс, "
            f"максимум {ordered[-1] * 1000:.1f} мс"
        )


def run(seconds: int, pump) -> str:
    """Крутить цикл Qt ``seconds``, дёргая ``pump`` каждые 5 мс, и отчитаться."""
    application = QApplication.instance() or QApplication([])
    instrument = Instrument()
    instrument.start()
    driver = QTimer()
    driver.setInterval(5)
    driver.timeout.connect(pump)
    driver.start()
    stop = QTimer()
    stop.setSingleShot(True)
    stop.setInterval(seconds * 1000)
    stop.timeout.connect(application.quit)
    stop.start()
    application.exec()
    return instrument.report()
```

- [ ] **Step 2: Write the failing test for the instrument**

The instrument is the measuring device, so it gets its own test: a broken
instrument would report a healthy GUI no matter what the GUI did.

```python
# configurator/tests/transfer/test_spike_qt_responsiveness.py
"""Прибор тоже проверяется: сломанный прибор покажет здоровый GUI при любом GUI."""

from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spike_qt_responsiveness import Instrument


def test_an_instrument_that_never_ticked_says_so_instead_of_reporting_health(qapp):
    instrument = Instrument()

    assert "нет тиков вовсе" in instrument.report()


def test_the_report_names_the_worst_interval_not_only_the_typical_one(qapp):
    instrument = Instrument()
    instrument.intervals = [0.016] * 99 + [1.400]

    report = instrument.report()

    assert "максимум 1400.0 мс" in report, (
        "прибор, показывающий только медиану, скрыл бы ровно то замирание, "
        "ради которого он и существует"
    )
```

Add `configurator/tests/transfer/conftest.py` a `qapp` fixture identical to
`configurator/tests/clipboard/conftest.py` (session-scoped `QApplication`),
alongside the `sys.path` insert from Task 0.1.

- [ ] **Step 3: Run test to verify it fails, then passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_spike_qt_responsiveness.py -v`
Expected before Step 1's file exists: FAIL, `ModuleNotFoundError`.
Expected after: PASS, 2 passed.

- [ ] **Step 4: Add IDataObjectAsyncCapability behind a flag**

```python
# append to spike_virtual_files.py
IID_IASYNCCAPABILITY = "{3D8B0590-F691-11D2-8EA9-006097DF5BD4}"

VARIANT_TRUE = -1
VARIANT_FALSE = 0

#: Каждый вызов интерфейса, в порядке поступления, с id потока.
LIFECYCLE_LOG: list[tuple[str, int]] = []


def note(event: str) -> None:
    LIFECYCLE_LOG.append((event, threading.get_ident()))
    log(f"lifecycle: {event}")


_SETASYNC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_short)
_GETASYNC = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)
_STARTOP = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)
_INOP = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_void_p)
_ENDOP = ctypes.WINFUNCTYPE(
    ctypes.c_long, ctypes.c_void_p, ctypes.c_long, ctypes.c_void_p, wintypes.DWORD
)
```

`DataObject.__init__` gains `async_capability: bool = False`, sets
`self.async_mode = False` and `self.in_operation = False`, adds
`IID_IASYNCCAPABILITY` to the supported IID list when the flag is set, and
appends the five methods **after** the nine `IDataObject` slots — order in the
vtable is the interface contract, so appending anywhere else silently calls the
wrong function:

```python
        if async_capability:
            self._own.extend([
                _SETASYNC(self._set_async_mode),
                _GETASYNC(self._get_async_mode),
                _STARTOP(self._start_operation),
                _INOP(self._in_operation_query),
                _ENDOP(self._end_operation),
            ])
```

```python
    def _set_async_mode(self, _this, do_op_async) -> int:
        note(f"SetAsyncMode({do_op_async})")
        self.async_mode = do_op_async != VARIANT_FALSE
        return S_OK

    def _get_async_mode(self, _this, out) -> int:
        note("GetAsyncMode")
        if not out:
            return E_POINTER
        ctypes.cast(out, ctypes.POINTER(ctypes.c_short))[0] = (
            VARIANT_TRUE if self.async_mode else VARIANT_FALSE
        )
        return S_OK

    def _start_operation(self, _this, _reserved) -> int:
        note("StartOperation")
        self.in_operation = True
        return S_OK

    def _in_operation_query(self, _this, out) -> int:
        if not out:
            return E_POINTER
        ctypes.cast(out, ctypes.POINTER(ctypes.c_short))[0] = (
            VARIANT_TRUE if self.in_operation else VARIANT_FALSE
        )
        return S_OK

    def _end_operation(self, _this, result, _reserved, effects) -> int:
        note(f"EndOperation(hResult=0x{result & 0xFFFFFFFF:08X}, effects={effects})")
        self.in_operation = False
        return S_OK
```

Wire `--async-capability` into `main()`, and replace the hand-rolled
`while`/`PeekMessageW` loop with `spike_qt_responsiveness.run(seconds, pump)`,
where `pump` drains the message queue once per call. This is what makes the run
measure a real Qt event loop rather than a bare loop:

```python
def make_pump():
    message = wintypes.MSG()

    def pump() -> None:
        while ctypes.windll.user32.PeekMessageW(ctypes.byref(message), None, 0, 0, 1):
            ctypes.windll.user32.TranslateMessage(ctypes.byref(message))
            ctypes.windll.user32.DispatchMessageW(ctypes.byref(message))

    return pump
```

- [ ] **Step 5: Run A, then Run B, with identical actions**

```
.venv\Scripts\python.exe configurator/tests/transfer/spike_virtual_files.py --seconds 300 > configurator/tests/transfer/spike-run-a.log 2>&1
.venv\Scripts\python.exe configurator/tests/transfer/spike_virtual_files.py --async-capability --seconds 300 > configurator/tests/transfer/spike-run-b.log 2>&1
```

In each run, in this order: paste the folder into an empty directory; paste
again and press Cancel midway through the 4 GiB entry. Same order both times,
or the comparison means nothing.

- [ ] **Step 6: Fill the six-axis comparison**

Every cell must be a fact from a log line or an observation of the screen,
never an expectation.

| Axis | Run A (no async) | Run B (async) |
|---|---|---|
| thread ids servicing `Read` | | |
| `StartOperation` / `EndOperation` seen | | |
| order of `GetData` calls | | |
| Qt tick p99 / maximum | | |
| native progress dialog moves | | |
| how Cancel manifests | | |

- [ ] **Step 7: Commit**

```bash
git add configurator/tests/transfer/
git commit -m "Measure Explorer with and without IDataObjectAsyncCapability

Two runs, identical actions, six axes, and an instrumented Qt event loop
rather than a bare message pump. The design refused to call the interface
necessary on theory; this is the measurement that settles it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 0.5: DECISION GATE — record, update the spec, close §22

**Files:**
- Create: `docs/superpowers/records/2026-09-12-explorer-virtual-files-spike.md`
- Modify: `docs/superpowers/specs/2026-09-12-file-transfer-design.md` (§1, §3, §9, §22)

**Interfaces:**
- Consumes: the two logs and the comparison table from Task 0.4.
- Produces: the final production model that Phases 1–2 implement. No code.

**This is a hard gate. Phase 1 does not begin until this task is complete.**

- [ ] **Step 1: Write the record**

Follow the two existing spike records
(`records/2026-09-03-lazy-clipboard-spike.md`,
`records/2026-09-11-macos-lazy-clipboard-spike.md`): environment, method
including any correction to the briefed method, verbatim output, analysis,
verdict. Include spec §20's nine pass criteria as a table with an explicit pass
or fail on each, plus the six-axis async comparison.

Record what was **not** established as well as what was. A criterion that could
not be exercised is not a pass.

- [ ] **Step 2: Update spec §9 with the single source of truth for completion**

Replace the subsection "Завершение сессии — источник истины определяет спайк 1"
with the measured answer, one of:

- `EndOperation` proved reliable → it becomes the authoritative
  session-completion signal, and `Release` of a stream ends only that stream;
- `EndOperation` proved absent or unreliable → name the actual signal and the
  timeout that backs it up.

State the cancel signal the same way, from the measurement, not from the list of
possibilities.

- [ ] **Step 3: Update spec §1 and §3 with the async-capability verdict**

If Run B differed observably: change the §1 entry from "**условно**" to
"обязателен" and state the measured difference in §3.

If it did not differ: remove `IDataObjectAsyncCapability` from §1's INCLUDE
list and say so in §3. The spec already commits to dropping it rather than
keeping it for insurance — honour that.

- [ ] **Step 4: Remove the closed questions from spec §22**

Questions 1 and 3 close here. Question 2 (prefetch window) stays open until
Phase 3 — but append the measured `cb` and whether `Seek` was observed to it,
since both feed that decision.

If a question could not be closed, leave it open and say why. An unclosed
question is a fact about the spike, not a failure of this task.

- [ ] **Step 5: Verify the spec has no stale cross-references or stale promises**

Run: `grep -oE "§[0-9]+" docs/superpowers/specs/2026-09-12-file-transfer-design.md | sort -u -V`
Expected: every section listed still exists as a `## N.` heading.

Run: `grep -nE "TBD|TODO|определяет спайк 1|уточняется спайком 1" docs/superpowers/specs/2026-09-12-file-transfer-design.md`
Expected: no hits — every spike-1 deferral is now answered.

- [ ] **Step 6: Commit and STOP for review**

```bash
git add docs/superpowers/records/2026-09-12-explorer-virtual-files-spike.md docs/superpowers/specs/2026-09-12-file-transfer-design.md
git commit -m "Record what the Explorer virtual-files spike established

Closes the completion-semantics and async-capability questions the design
left open, with measurement rather than a guess. The prefetch question
stays open for Phase 3 by design.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

**If architecture C hit a proven, unfixable blocker:** do not start Phase 1 and
do not switch to architecture B. Present the log, the exact mechanism that
failed, and what was tried. The fallback decision belongs to the owner, not the
implementer.

---

# Phase 1 — Transfer core

**Purpose:** everything that is not COM and not Qt-socket, TDD-first. After this
phase the protocol, the path validation, the pipe and both state machines are
proven without Explorer and without a network.

**Hard rule:** no prefetch. The correctness baseline is one `IStream::Read` → one
`FILE_READ` → one `FILE_CHUNK`. Any code that issues a second `FILE_READ` before
the first is answered belongs to Phase 3 or nowhere.

**Precondition:** Task 0.5 is committed and the spec no longer defers to spike 1.

## Task 1.1: the manifest model

**Files:**
- Create: `configurator/src/duo_input/transfer/__init__.py` (empty)
- Create: `configurator/src/duo_input/transfer/model.py`
- Test: `configurator/tests/transfer/test_model.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `ENTRY_FILE = "file"`, `ENTRY_DIRECTORY = "directory"`
  - `TransferEntry(path: str, kind: str, size: int, mtime_ns: int)` — frozen dataclass
  - `SkippedEntry(path: str, reason: str)` — frozen dataclass
  - `TransferManifest(transfer_id: str, entries: tuple[TransferEntry, ...], skipped: tuple[SkippedEntry, ...] = (), drop_effect: int = 1)` — frozen dataclass
  - `TransferManifest.total_bytes -> int`
  - `TransferManifest.to_dict() -> dict`, `TransferManifest.from_dict(raw: dict) -> TransferManifest` raising `ValueError`

Validation follows `clipboard/offer.py:from_dict` exactly: check types, never
coerce, and exclude `bool` from `int`. A `size=True` that silently became
`size=1` would produce a truncated file with no error anywhere.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_model.py
"""Манифест: что мы говорим о файлах, не отдавая ни одного байта.

Абсолютных путей здесь нет намеренно - они не уходят на провод вовсе. Путь в
записи всегда относительный и всегда с разделителем "/", чтобы манифест не
зависел от платформы, снявшей его.
"""

from __future__ import annotations

import pytest

from duo_input.transfer.model import (
    ENTRY_DIRECTORY,
    ENTRY_FILE,
    SkippedEntry,
    TransferEntry,
    TransferManifest,
)


def _manifest() -> TransferManifest:
    return TransferManifest(
        transfer_id="t-1",
        entries=(
            TransferEntry(path="Photos", kind=ENTRY_DIRECTORY, size=0, mtime_ns=1),
            TransferEntry(path="Photos/img1.jpg", kind=ENTRY_FILE, size=1024, mtime_ns=2),
            TransferEntry(path="notes.txt", kind=ENTRY_FILE, size=7, mtime_ns=3),
        ),
        skipped=(SkippedEntry(path="link", reason="reparse_point"),),
        drop_effect=1,
    )


def test_total_bytes_counts_files_and_ignores_directories():
    assert _manifest().total_bytes == 1031


def test_a_manifest_survives_a_round_trip_through_a_dictionary():
    manifest = _manifest()

    assert TransferManifest.from_dict(manifest.to_dict()) == manifest


def test_a_size_of_true_is_refused_rather_than_silently_becoming_one():
    raw = _manifest().to_dict()
    raw["entries"][1]["size"] = True

    with pytest.raises(ValueError):
        TransferManifest.from_dict(raw)


def test_an_mtime_of_true_is_refused_for_the_same_reason():
    raw = _manifest().to_dict()
    raw["entries"][1]["mtime_ns"] = True

    with pytest.raises(ValueError):
        TransferManifest.from_dict(raw)


def test_an_unknown_entry_kind_is_refused():
    raw = _manifest().to_dict()
    raw["entries"][0]["kind"] = "socket"

    with pytest.raises(ValueError):
        TransferManifest.from_dict(raw)


def test_a_missing_transfer_id_is_refused():
    raw = _manifest().to_dict()
    del raw["transfer_id"]

    with pytest.raises(ValueError):
        TransferManifest.from_dict(raw)


def test_entries_that_are_not_a_list_are_refused():
    raw = _manifest().to_dict()
    raw["entries"] = {"path": "x"}

    with pytest.raises(ValueError):
        TransferManifest.from_dict(raw)


def test_a_negative_size_is_refused():
    raw = _manifest().to_dict()
    raw["entries"][1]["size"] = -1

    with pytest.raises(ValueError):
        TransferManifest.from_dict(raw)


def test_a_manifest_with_no_skipped_entries_still_round_trips():
    manifest = TransferManifest(
        transfer_id="t-2",
        entries=(TransferEntry(path="a.bin", kind=ENTRY_FILE, size=1, mtime_ns=1),),
    )

    assert TransferManifest.from_dict(manifest.to_dict()) == manifest
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duo_input.transfer'`

- [ ] **Step 3: Write minimal implementation**

```python
# configurator/src/duo_input/transfer/model.py
"""Что уходит на второй компьютер при копировании файлов - и что не уходит.

Манифест описывает дерево и не содержит ни байта его содержимого: это та же
ленивая модель, что и у объявления буфера обмена. Абсолютных путей здесь нет
вовсе - получатель сам выбирает, куда писать, и отправитель не имеет права
этого диктовать.

Разделитель пути всегда "/", даже когда снимок снят на Windows. Обратный слэш
появляется ровно в одном месте - при сборке cFileName для Проводника, - и
благодаря этому манифест от платформы не зависит.

Проверка типов здесь строгая и не приводящая, ровно как в clipboard/offer.py:
bool исключён из int, потому что size=True, молча ставшее size=1, дало бы
усечённый файл без единой ошибки в журнале.
"""

from __future__ import annotations

from dataclasses import dataclass, field

ENTRY_FILE = "file"
ENTRY_DIRECTORY = "directory"

_KINDS = frozenset({ENTRY_FILE, ENTRY_DIRECTORY})


def _require_str(raw: dict, key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} должна быть str")
    return value


def _require_index(raw: dict, key: str) -> int:
    value = raw.get(key)
    # bool - подтип int, а size=True не должен сойти за size=1.
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} должна быть int")
    if value < 0:
        raise ValueError(f"{key} не может быть отрицательной")
    return value


@dataclass(frozen=True)
class TransferEntry:
    """Одна запись дерева: чем является, сколько весит, когда изменена."""

    path: str
    kind: str
    size: int
    mtime_ns: int

    def to_dict(self) -> dict:
        return {"path": self.path, "kind": self.kind, "size": self.size, "mtime_ns": self.mtime_ns}

    @classmethod
    def from_dict(cls, raw: dict) -> TransferEntry:
        if not isinstance(raw, dict):
            raise ValueError("запись должна быть dict")
        kind = _require_str(raw, "kind")
        if kind not in _KINDS:
            raise ValueError(f"неизвестный вид записи {kind!r}")
        return cls(
            path=_require_str(raw, "path"),
            kind=kind,
            size=_require_index(raw, "size"),
            mtime_ns=_require_index(raw, "mtime_ns"),
        )


@dataclass(frozen=True)
class SkippedEntry:
    """То, что осознанно не передаётся - и почему.

    Существует, чтобы получатель мог СКАЗАТЬ пользователю о пропуске. Молча
    неполное дерево хуже, чем неполное дерево с объяснением.
    """

    path: str
    reason: str

    def to_dict(self) -> dict:
        return {"path": self.path, "reason": self.reason}

    @classmethod
    def from_dict(cls, raw: dict) -> SkippedEntry:
        if not isinstance(raw, dict):
            raise ValueError("пропуск должен быть dict")
        return cls(path=_require_str(raw, "path"), reason=_require_str(raw, "reason"))


@dataclass(frozen=True)
class TransferManifest:
    """Неизменяемое описание одной операции копирования."""

    transfer_id: str
    entries: tuple[TransferEntry, ...]
    skipped: tuple[SkippedEntry, ...] = field(default=())
    #: DROPEFFECT_COPY. Хранится для диагностики; получатель всегда копирует.
    drop_effect: int = 1

    @property
    def total_bytes(self) -> int:
        return sum(entry.size for entry in self.entries if entry.kind == ENTRY_FILE)

    def to_dict(self) -> dict:
        return {
            "transfer_id": self.transfer_id,
            "entries": [entry.to_dict() for entry in self.entries],
            "skipped": [skip.to_dict() for skip in self.skipped],
            "total_bytes": self.total_bytes,
            "drop_effect": self.drop_effect,
        }

    @classmethod
    def from_dict(cls, raw: dict) -> TransferManifest:
        if not isinstance(raw, dict):
            raise ValueError("манифест должен быть dict")
        entries = raw.get("entries")
        if not isinstance(entries, list):
            raise ValueError("entries должна быть list")
        skipped = raw.get("skipped", [])
        if not isinstance(skipped, list):
            raise ValueError("skipped должна быть list")
        return cls(
            transfer_id=_require_str(raw, "transfer_id"),
            entries=tuple(TransferEntry.from_dict(entry) for entry in entries),
            skipped=tuple(SkippedEntry.from_dict(skip) for skip in skipped),
            drop_effect=_require_index(raw, "drop_effect") if "drop_effect" in raw else 1,
        )


__all__ = [
    "ENTRY_DIRECTORY",
    "ENTRY_FILE",
    "SkippedEntry",
    "TransferEntry",
    "TransferManifest",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_model.py -v`
Expected: PASS, 9 passed

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/ configurator/tests/transfer/test_model.py
git commit -m "Describe a file tree without carrying any of its bytes

Wire paths are relative and slash-separated so the manifest does not
depend on the platform that took it. Type checking is strict and
non-coercing, as in the clipboard offer: a size of True that silently
became 1 would produce a truncated file with no error anywhere.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 1.2: per-path sanitization

**Files:**
- Create: `configurator/src/duo_input/transfer/paths.py`
- Test: `configurator/tests/transfer/test_paths.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class UnsafePath(ValueError)`
  - `MAX_PATH_UTF16 = 259`, `MAX_DEPTH = 32`
  - `sanitize_relative_path(raw: str) -> str` — returns the canonical
    `/`-joined NFC form, or raises `UnsafePath`

This is the one place in the whole feature where security is entirely ours:
Explorer writes the files, but we build `cFileName`. Every rule gets its own
test, and Task 1.4 proves each rule is load-bearing.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_paths.py
"""Единственное место, где безопасность целиком наша.

Проводник пишет файлы, но cFileName строим мы. Всё, что здесь пропущено,
Проводник запишет послушно и туда, куда сказано.
"""

from __future__ import annotations

import pytest

from duo_input.transfer.paths import MAX_PATH_UTF16, UnsafePath, sanitize_relative_path


def test_a_plain_relative_path_passes_through_unchanged():
    assert sanitize_relative_path("Photos/img1.jpg") == "Photos/img1.jpg"


def test_backslashes_are_normalised_to_the_wire_separator():
    assert sanitize_relative_path("Photos\\img1.jpg") == "Photos/img1.jpg"


@pytest.mark.parametrize(
    "raw",
    [
        "../evil.exe",
        "..\\evil.exe",
        "Photos/../../evil.exe",
        "Photos/..",
        "..",
    ],
)
def test_any_parent_segment_is_refused(raw):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


@pytest.mark.parametrize("raw", ["./a.txt", "Photos/./a.txt", "."])
def test_any_current_directory_segment_is_refused(raw):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


@pytest.mark.parametrize(
    "raw",
    [
        "C:\\evil.exe",
        "C:evil.exe",
        "\\evil.exe",
        "/foo",
        "\\\\server\\share\\evil.exe",
        "//server/share/evil.exe",
    ],
)
def test_anything_absolute_is_refused(raw):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


def test_an_empty_segment_is_refused_because_it_hides_a_separator_trick():
    with pytest.raises(UnsafePath):
        sanitize_relative_path("Photos//img1.jpg")


def test_an_empty_path_is_refused():
    with pytest.raises(UnsafePath):
        sanitize_relative_path("")


@pytest.mark.parametrize(
    "raw",
    ["CON", "con", "PRN.txt", "aux", "NUL", "COM1", "com9.bin", "LPT1", "lpt9.dat"],
)
def test_reserved_windows_device_names_are_refused_with_or_without_an_extension(raw):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


def test_a_reserved_name_is_refused_anywhere_in_the_path_not_only_at_the_end():
    with pytest.raises(UnsafePath):
        sanitize_relative_path("Photos/NUL/img.jpg")


def test_com0_is_not_reserved_and_passes():
    assert sanitize_relative_path("COM0.txt") == "COM0.txt"


@pytest.mark.parametrize("raw", ["name.", "name ", "Photos/name./a.txt", "Photos /a.txt"])
def test_a_trailing_dot_or_space_in_a_segment_is_refused(raw):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(raw)


@pytest.mark.parametrize("bad", ["<", ">", ":", '"', "|", "?", "*"])
def test_characters_windows_forbids_in_a_name_are_refused(bad):
    with pytest.raises(UnsafePath):
        sanitize_relative_path(f"na{bad}me.txt")


def test_control_characters_are_refused():
    with pytest.raises(UnsafePath):
        sanitize_relative_path("na\x01me.txt")


def test_a_null_byte_is_refused():
    with pytest.raises(UnsafePath):
        sanitize_relative_path("na\x00me.txt")


def test_a_segment_at_the_cFileName_ceiling_passes_and_one_beyond_it_does_not():
    ok = "a" * MAX_PATH_UTF16

    assert sanitize_relative_path(ok) == ok

    with pytest.raises(UnsafePath):
        sanitize_relative_path("a" * (MAX_PATH_UTF16 + 1))


def test_the_ceiling_counts_utf16_units_not_python_characters():
    # Каждый символ вне BMP занимает ДВЕ единицы UTF-16, а cFileName - это
    # WCHAR[260]. Путь из 200 таких символов - это 400 единиц, то есть он не
    # помещается, хотя len() по-питоновски равен 200.
    astral = "\U0001F600" * 200

    with pytest.raises(UnsafePath):
        sanitize_relative_path(astral)


def test_excessive_nesting_is_refused():
    with pytest.raises(UnsafePath):
        sanitize_relative_path("/".join(["d"] * 64))


def test_unicode_is_normalised_to_nfc_so_two_spellings_become_one_name():
    decomposed = "Sa\u0301nchez.txt"  # S a + combining acute
    composed = "S\u00e1nchez.txt"

    assert sanitize_relative_path(decomposed) == composed


def test_normalisation_happens_before_the_other_checks_not_after():
    # Полноширинная точка нормализуется в обычную, и только ПОСЛЕ этого
    # сегмент оказывается заканчивающимся точкой. Проверка до нормализации
    # пропустила бы это имя.
    with pytest.raises(UnsafePath):
        sanitize_relative_path("name\uff0e")


def test_a_cyrillic_name_is_allowed_because_only_windows_rules_apply():
    assert sanitize_relative_path("Отчёт/данные.txt") == "Отчёт/данные.txt"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_paths.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duo_input.transfer.paths'`

- [ ] **Step 3: Write minimal implementation**

```python
# configurator/src/duo_input/transfer/paths.py
"""Проверка относительных путей, пришедших со второго компьютера.

Проводник записывает файлы сам, но имена в FILEGROUPDESCRIPTORW строим мы -
и это единственное место в этой функции, где безопасность целиком наша. Ни
один пир, даже доверенный, не имеет права указать, куда писать.

Отвергается МАНИФЕСТ ЦЕЛИКОМ, а не отдельная запись (см. sanitize_manifest):
пользователь, получивший девять файлов из десяти, об этом не узнает, а честный
отказ он увидит.

Нормализация Unicode идёт ПЕРЕД остальными проверками, и это не косметика:
полноширинная точка U+FF0E нормализуется в обычную, и только после этого имя
оказывается заканчивающимся точкой. Проверка до нормализации пропустила бы его.

Правила здесь - надмножество и для macOS тоже. Ослаблять их под другую
платформу незачем: запрет лишнего имени никого не ломает, а разные правила на
двух концах ломают ровно то, что должно совпадать.
"""

from __future__ import annotations

import unicodedata

#: cFileName - это WCHAR[260], то есть 259 значимых единиц UTF-16 плюс ноль.
MAX_PATH_UTF16 = 259

#: Глубина, за которой дерево перестаёт быть похожим на копирование файлов.
MAX_DEPTH = 32

_FORBIDDEN_CHARACTERS = frozenset('<>:"|?*')

_RESERVED_STEMS = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{digit}" for digit in "123456789"}
    | {f"LPT{digit}" for digit in "123456789"}
)


class UnsafePath(ValueError):
    """Путь, которого не мог бы прислать исправный второй компьютер."""


def _utf16_units(text: str) -> int:
    """Длина в единицах UTF-16, а не в символах Python.

    Символ вне BMP занимает две единицы, а поле cFileName считает именно их.
    len() здесь соврал бы вдвое в пользу злоумышленника.
    """
    return len(text.encode("utf-16-le")) // 2


def sanitize_relative_path(raw: str) -> str:
    """Каноническая относительная форма, либо ``UnsafePath``."""
    if not isinstance(raw, str):
        raise UnsafePath("путь должен быть str")
    if not raw:
        raise UnsafePath("пустой путь")

    # 1. Нормализация Unicode - первым делом, до всех остальных проверок.
    text = unicodedata.normalize("NFC", raw)

    # 2. Единый разделитель. Проверки ниже смотрят уже на сегменты.
    text = text.replace("\\", "/")

    if text.startswith("/"):
        raise UnsafePath(f"путь абсолютный: {raw!r}")
    if _utf16_units(text) > MAX_PATH_UTF16:
        raise UnsafePath(f"путь длиннее {MAX_PATH_UTF16} единиц UTF-16")

    segments = text.split("/")
    if len(segments) > MAX_DEPTH:
        raise UnsafePath(f"вложенность больше {MAX_DEPTH}")

    for segment in segments:
        _check_segment(segment, raw)

    return "/".join(segments)


def _check_segment(segment: str, raw: str) -> None:
    if not segment:
        raise UnsafePath(f"пустой сегмент пути: {raw!r}")
    if segment in {".", ".."}:
        raise UnsafePath(f"сегмент выхода за корень: {raw!r}")
    if ":" in segment:
        # Ловит и "C:\..." и "C:file" и поток NTFS "file:stream" - все три
        # являются способом уйти не туда, куда получатель разрешил.
        raise UnsafePath(f"двоеточие в имени: {raw!r}")
    if segment[-1] in {".", " "}:
        # Windows отбрасывает завершающую точку и пробел, поэтому "a." и "a"
        # столкнулись бы в одном файле, а "a .exe" перестало бы быть тем,
        # что видел пользователь.
        raise UnsafePath(f"сегмент заканчивается точкой или пробелом: {raw!r}")
    if any(character in _FORBIDDEN_CHARACTERS for character in segment):
        raise UnsafePath(f"запрещённый символ в имени: {raw!r}")
    if any(ord(character) < 0x20 for character in segment):
        raise UnsafePath(f"управляющий символ в имени: {raw!r}")
    stem = segment.split(".", 1)[0].upper()
    if stem in _RESERVED_STEMS:
        raise UnsafePath(f"зарезервированное имя устройства: {raw!r}")


__all__ = ["MAX_DEPTH", "MAX_PATH_UTF16", "UnsafePath", "sanitize_relative_path"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_paths.py -v`
Expected: PASS, 46 passed (parametrised cases counted individually)

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/paths.py configurator/tests/transfer/test_paths.py
git commit -m "Refuse every relative path Explorer would obey but should not

Explorer writes the files; we build cFileName, so this is the one place
where security is entirely ours. Unicode normalisation runs first because
U+FF0E normalises to a plain dot and only then turns the segment into one
ending in a dot. The length ceiling counts UTF-16 units, not Python
characters, because cFileName counts those and len() would understate an
astral-plane name by half.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 1.3: manifest-level sanitization

**Files:**
- Modify: `configurator/src/duo_input/transfer/paths.py`
- Test: `configurator/tests/transfer/test_paths.py` (append)

**Interfaces:**
- Consumes: `sanitize_relative_path`, `UnsafePath` from Task 1.2;
  `TransferManifest`, `TransferEntry`, `ENTRY_FILE`, `ENTRY_DIRECTORY` from Task 1.1.
- Produces:
  - `MAX_ENTRIES = 65_536`, `MAX_TOTAL_BYTES = 2 * 1024 ** 4`
  - `sanitize_manifest(manifest: TransferManifest) -> TransferManifest` — returns
    a manifest whose every `path` is canonical, or raises `UnsafePath`

Rules that cannot be decided one path at a time live here: case-insensitive
collisions, entry counts, total size.

- [ ] **Step 1: Write the failing test**

```python
# append to configurator/tests/transfer/test_paths.py
from duo_input.transfer.model import (
    ENTRY_DIRECTORY,
    ENTRY_FILE,
    TransferEntry,
    TransferManifest,
)
from duo_input.transfer.paths import MAX_ENTRIES, MAX_TOTAL_BYTES, sanitize_manifest


def _with(entries) -> TransferManifest:
    return TransferManifest(transfer_id="t", entries=tuple(entries))


def test_a_clean_manifest_comes_back_with_canonical_paths():
    manifest = _with([
        TransferEntry(path="Photos", kind=ENTRY_DIRECTORY, size=0, mtime_ns=1),
        TransferEntry(path="Photos\\img.jpg", kind=ENTRY_FILE, size=4, mtime_ns=2),
    ])

    result = sanitize_manifest(manifest)

    assert [entry.path for entry in result.entries] == ["Photos", "Photos/img.jpg"]


def test_one_bad_entry_refuses_the_whole_manifest_rather_than_dropping_it():
    manifest = _with([
        TransferEntry(path="good.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="../evil.exe", kind=ENTRY_FILE, size=1, mtime_ns=1),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_two_entries_differing_only_in_case_are_refused_as_a_collision():
    # Файловая система Windows регистронезависима: эти две записи попали бы в
    # один файл, и вторая молча затёрла бы первую.
    manifest = _with([
        TransferEntry(path="Photos/IMG.jpg", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="Photos/img.jpg", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_two_entries_colliding_only_after_normalisation_are_refused():
    manifest = _with([
        TransferEntry(path="Sa\u0301nchez.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="S\u00e1nchez.txt", kind=ENTRY_FILE, size=2, mtime_ns=2),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_an_exact_duplicate_path_is_refused():
    manifest = _with([
        TransferEntry(path="a.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
        TransferEntry(path="a.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_an_empty_manifest_is_refused_because_there_is_nothing_to_offer():
    with pytest.raises(UnsafePath):
        sanitize_manifest(_with([]))


def test_too_many_entries_are_refused():
    entries = [
        TransferEntry(path=f"f{index}", kind=ENTRY_FILE, size=0, mtime_ns=1)
        for index in range(MAX_ENTRIES + 1)
    ]

    with pytest.raises(UnsafePath):
        sanitize_manifest(_with(entries))


def test_a_total_size_above_the_ceiling_is_refused():
    manifest = _with([
        TransferEntry(path="huge.bin", kind=ENTRY_FILE, size=MAX_TOTAL_BYTES + 1, mtime_ns=1),
    ])

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)


def test_skipped_entry_paths_are_sanitised_too_because_they_reach_the_screen():
    manifest = TransferManifest(
        transfer_id="t",
        entries=(TransferEntry(path="a.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),),
        skipped=(SkippedEntry(path="../../etc/passwd", reason="reparse_point"),),
    )

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)
```

Add `SkippedEntry` to the `duo_input.transfer.model` import at the top of the
test file.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_paths.py -v -k manifest`
Expected: FAIL — `ImportError: cannot import name 'MAX_ENTRIES'`

- [ ] **Step 3: Write minimal implementation**

Append to `configurator/src/duo_input/transfer/paths.py`:

```python
#: Больше этого числа записей - уже не операция копирования, а что-то другое.
MAX_ENTRIES = 65_536

#: Потолок суммы размеров. 2 ТиБ: больше любого разумного копирования по LAN.
MAX_TOTAL_BYTES = 2 * 1024 ** 4


def sanitize_manifest(manifest):
    """Манифест с каноническими путями, либо ``UnsafePath`` на весь манифест.

    Отвергается целиком, а не по записям: получивший девять файлов из десяти
    пользователь об этом не узнает, а об отказе узнает.
    """
    from .model import ENTRY_FILE, SkippedEntry, TransferEntry

    if not manifest.entries:
        raise UnsafePath("манифест пуст - нечего предлагать")
    if len(manifest.entries) > MAX_ENTRIES:
        raise UnsafePath(f"записей больше {MAX_ENTRIES}")

    total = 0
    seen: dict[str, str] = {}
    entries: list[TransferEntry] = []
    for entry in manifest.entries:
        path = sanitize_relative_path(entry.path)
        # Сравнение по casefold, а не по lower: регистронезависимость Windows
        # и правила Unicode для сопоставления - не одно и то же.
        key = path.casefold()
        if key in seen:
            raise UnsafePath(f"две записи попадут в один файл: {seen[key]!r} и {path!r}")
        seen[key] = path
        if entry.kind == ENTRY_FILE:
            total += entry.size
            if total > MAX_TOTAL_BYTES:
                raise UnsafePath(f"суммарный размер больше {MAX_TOTAL_BYTES}")
        entries.append(
            TransferEntry(path=path, kind=entry.kind, size=entry.size, mtime_ns=entry.mtime_ns)
        )

    skipped = tuple(
        SkippedEntry(path=sanitize_relative_path(skip.path), reason=skip.reason)
        for skip in manifest.skipped
    )

    return type(manifest)(
        transfer_id=manifest.transfer_id,
        entries=tuple(entries),
        skipped=skipped,
        drop_effect=manifest.drop_effect,
    )
```

Extend `__all__` with `"MAX_ENTRIES"`, `"MAX_TOTAL_BYTES"`, `"sanitize_manifest"`.

The `model` import is inside the function on purpose: `paths.py` stays
importable on its own, and no import cycle can form between the two modules
that Phase 2 will both depend on.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_paths.py -v`
Expected: PASS, 55 passed

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/paths.py configurator/tests/transfer/test_paths.py
git commit -m "Refuse the whole manifest when any entry is unsafe

Rules that cannot be decided one path at a time live here: two names
differing only in case would land in one file on Windows and the second
would silently overwrite the first. Skipped-entry paths are sanitised too
because they reach the screen.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 1.4: prove every path guard is load-bearing

**Files:**
- Create: `configurator/tests/transfer/test_guards_are_real.py`

**Interfaces:**
- Consumes: `paths.py` internals (`_check_segment`, `_RESERVED_STEMS`,
  `_FORBIDDEN_CHARACTERS`, `_utf16_units`, `sanitize_relative_path`,
  `sanitize_manifest`).
- Produces: nothing. This task adds no production code.

Two guards in this repository passed for weeks while testing nothing. A test
that still passes with the guard deleted is not a test, so each guard is
disabled here and its test must fail.

- [ ] **Step 1: Write the test**

```python
# configurator/tests/transfer/test_guards_are_real.py
"""Проверка того, что защитные условия действительно защищают.

Тест, который проходит с удалённым условием, не тестирует ничего. Здесь каждое
условие подменяется на пропускающее, и соответствующая проверка обязана
упасть. Если она не падает - виновата проверка, а не этот файл.

Образец - configurator/tests/clipboard/test_guards_are_real.py.
"""

from __future__ import annotations

import unicodedata

import pytest

from duo_input.transfer import paths
from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.paths import UnsafePath, sanitize_manifest, sanitize_relative_path


def test_without_the_parent_segment_guard_a_traversal_would_pass(monkeypatch):
    monkeypatch.setattr(paths, "_check_segment", lambda segment, raw: None)

    assert sanitize_relative_path("../evil.exe") == "../evil.exe", (
        "проверка сегментов снята, а путь всё равно отвергнут - значит "
        "отвергает его что-то другое, и тест обхода корня проверяет не то"
    )


def test_without_the_reserved_name_table_a_device_name_would_pass(monkeypatch):
    monkeypatch.setattr(paths, "_RESERVED_STEMS", frozenset())

    assert sanitize_relative_path("NUL") == "NUL", (
        "таблица зарезервированных имён пуста, а NUL всё равно отвергнут"
    )


def test_without_the_forbidden_character_set_a_colon_free_bad_name_would_pass(monkeypatch):
    monkeypatch.setattr(paths, "_FORBIDDEN_CHARACTERS", frozenset())

    assert sanitize_relative_path("na*me.txt") == "na*me.txt", (
        "набор запрещённых символов пуст, а звёздочка всё равно отвергнута"
    )


def test_without_the_utf16_measure_an_astral_name_would_fit(monkeypatch):
    # Подменяем меру на питоновскую len: ровно та ошибка, которую _utf16_units
    # и существует, чтобы не совершить.
    monkeypatch.setattr(paths, "_utf16_units", len)

    astral = "\U0001F600" * 200

    assert sanitize_relative_path(astral) == unicodedata.normalize("NFC", astral), (
        "мера длины подменена на len(), а имя из 400 единиц UTF-16 всё равно "
        "отвергнуто - значит потолок проверяет не то, что попадёт в cFileName"
    )


def test_without_normalisation_a_fullwidth_dot_would_pass(monkeypatch):
    monkeypatch.setattr(paths.unicodedata, "normalize", lambda _form, text: text)

    assert sanitize_relative_path("name\uff0e") == "name\uff0e", (
        "нормализация отключена, а полноширинная точка всё равно отвергнута"
    )


def test_without_the_casefold_collision_check_two_names_would_share_one_file(monkeypatch):
    monkeypatch.setattr(str, "casefold", str.__str__, raising=False)
    manifest = TransferManifest(
        transfer_id="t",
        entries=(
            TransferEntry(path="IMG.jpg", kind=ENTRY_FILE, size=1, mtime_ns=1),
            TransferEntry(path="img.jpg", kind=ENTRY_FILE, size=2, mtime_ns=2),
        ),
    )

    result = sanitize_manifest(manifest)

    assert len(result.entries) == 2, (
        "сравнение без учёта регистра снято, а коллизия всё равно найдена"
    )


def test_the_entry_ceiling_is_the_reason_a_huge_manifest_is_refused():
    entries = tuple(
        TransferEntry(path=f"f{index}", kind=ENTRY_FILE, size=0, mtime_ns=1)
        for index in range(paths.MAX_ENTRIES + 1)
    )

    with pytest.raises(UnsafePath, match=str(paths.MAX_ENTRIES)):
        sanitize_manifest(TransferManifest(transfer_id="t", entries=entries))


def test_the_whole_manifest_is_refused_not_merely_the_bad_entry():
    manifest = TransferManifest(
        transfer_id="t",
        entries=(
            TransferEntry(path="good.txt", kind=ENTRY_FILE, size=1, mtime_ns=1),
            TransferEntry(path="..\\evil.exe", kind=ENTRY_FILE, size=1, mtime_ns=1),
        ),
    )

    with pytest.raises(UnsafePath):
        sanitize_manifest(manifest)
```

- [ ] **Step 2: Run the guard tests**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_guards_are_real.py -v`
Expected: PASS, 8 passed.

A failure here means the corresponding guard in `paths.py` is not the thing
doing the refusing. Fix `paths.py`, not this file.

If `test_without_the_casefold_collision_check...` cannot be made to work by
monkeypatching `str.casefold` (CPython forbids patching built-in types), replace
that test with one that patches a module-level `_collision_key` helper —
extract `_collision_key(path) -> str` from `sanitize_manifest` in `paths.py`
first, then patch it to `lambda path: path`. Extracting the helper is the
better shape anyway: it names the rule.

- [ ] **Step 3: Commit**

```bash
git add configurator/tests/transfer/test_guards_are_real.py
git commit -m "Prove each path guard is the thing doing the refusing

Two guards in this repository passed for weeks while testing nothing.
Each rule is disabled here and its test must fail; if it still passes,
something else was refusing the input and the rule's own test was
measuring the wrong thing.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 1.5: the source tree scanner

**Files:**
- Create: `configurator/src/duo_input/transfer/scanner.py`
- Test: `configurator/tests/transfer/test_scanner.py`

**Interfaces:**
- Consumes: `TransferManifest`, `TransferEntry`, `SkippedEntry`, `ENTRY_FILE`,
  `ENTRY_DIRECTORY` from Task 1.1; `sanitize_manifest` from Task 1.3.
- Produces:
  - `REASON_REPARSE_POINT = "reparse_point"`, `REASON_UNREADABLE = "unreadable"`
  - `scan(roots: Sequence[Path], transfer_id: str, drop_effect: int = 1) -> tuple[TransferManifest, dict[str, Path]]`

The second return value maps each manifest path to its absolute source path. It
stays on the sending machine and never goes on the wire — that is why it is a
separate return value rather than a manifest field.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_scanner.py
"""Обход дерева источника. Абсолютные пути остаются здесь и на провод не идут."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

from duo_input.transfer.model import ENTRY_DIRECTORY, ENTRY_FILE
from duo_input.transfer.scanner import REASON_REPARSE_POINT, scan


def test_a_single_file_becomes_one_entry_named_by_its_basename(tmp_path):
    source = tmp_path / "notes.txt"
    source.write_bytes(b"hello")

    manifest, roots = scan([source], transfer_id="t")

    assert [(entry.path, entry.kind, entry.size) for entry in manifest.entries] == [
        ("notes.txt", ENTRY_FILE, 5)
    ]
    assert roots["notes.txt"] == source


def test_several_files_keep_only_their_basenames(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"a")
    (tmp_path / "b.txt").write_bytes(b"bb")

    manifest, _roots = scan([tmp_path / "a.txt", tmp_path / "b.txt"], transfer_id="t")

    assert sorted(entry.path for entry in manifest.entries) == ["a.txt", "b.txt"]


def test_a_directory_contributes_itself_and_its_children_with_relative_paths(tmp_path):
    folder = tmp_path / "Photos"
    (folder / "raw").mkdir(parents=True)
    (folder / "img1.jpg").write_bytes(b"1234")
    (folder / "raw" / "img2.dng").write_bytes(b"56789")

    manifest, roots = scan([folder], transfer_id="t")

    assert sorted(entry.path for entry in manifest.entries) == [
        "Photos",
        "Photos/img1.jpg",
        "Photos/raw",
        "Photos/raw/img2.dng",
    ]
    assert roots["Photos/raw/img2.dng"] == folder / "raw" / "img2.dng"


def test_directories_are_marked_as_directories_and_carry_no_size(tmp_path):
    folder = tmp_path / "Empty"
    folder.mkdir()

    manifest, _roots = scan([folder], transfer_id="t")

    assert manifest.entries[0].kind == ENTRY_DIRECTORY
    assert manifest.entries[0].size == 0


def test_total_bytes_sums_only_the_files(tmp_path):
    folder = tmp_path / "Photos"
    folder.mkdir()
    (folder / "a").write_bytes(b"x" * 10)
    (folder / "b").write_bytes(b"y" * 20)

    manifest, _roots = scan([folder], transfer_id="t")

    assert manifest.total_bytes == 30


def test_a_zero_byte_file_is_offered_rather_than_skipped(tmp_path):
    empty = tmp_path / "empty.bin"
    empty.touch()

    manifest, _roots = scan([empty], transfer_id="t")

    assert manifest.entries[0].size == 0
    assert manifest.entries[0].kind == ENTRY_FILE


def test_a_unicode_name_survives_the_scan(tmp_path):
    source = tmp_path / "Отчёт.txt"
    source.write_bytes(b"x")

    manifest, roots = scan([source], transfer_id="t")

    assert manifest.entries[0].path == "Отчёт.txt"
    assert roots["Отчёт.txt"] == source


def test_the_manifest_records_the_modification_time_it_promised(tmp_path):
    source = tmp_path / "a.bin"
    source.write_bytes(b"x")

    manifest, _roots = scan([source], transfer_id="t")

    assert manifest.entries[0].mtime_ns == os.stat(source).st_mtime_ns


def test_the_scan_refuses_a_source_whose_name_windows_would_mangle(tmp_path):
    # Имя, которое sanitize_manifest отвергает, не должно пройти обход молча:
    # иначе Проводник получил бы дескриптор, который мы обещали не строить.
    from duo_input.transfer.paths import UnsafePath

    source = tmp_path / "nul.txt"
    source.write_bytes(b"x")

    with pytest.raises(UnsafePath):
        scan([source], transfer_id="t")


@pytest.mark.skipif(sys.platform != "win32", reason="junction - это Windows")
def test_a_junction_is_skipped_with_a_reason_and_not_followed(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "secret.txt").write_bytes(b"do not export")
    folder = tmp_path / "Photos"
    folder.mkdir()
    (folder / "img.jpg").write_bytes(b"ok")
    link = folder / "link"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip(f"mklink недоступен: {created.stderr.strip()}")

    manifest, _roots = scan([folder], transfer_id="t")

    assert sorted(entry.path for entry in manifest.entries) == ["Photos", "Photos/img.jpg"]
    assert [(skip.path, skip.reason) for skip in manifest.skipped] == [
        ("Photos/link", REASON_REPARSE_POINT)
    ]
    assert not any("secret" in entry.path for entry in manifest.entries), (
        "обход пошёл по junction - папка с junction на C:\\Windows выгрузила бы ОС"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_scanner.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duo_input.transfer.scanner'`

- [ ] **Step 3: Write minimal implementation**

```python
# configurator/src/duo_input/transfer/scanner.py
"""Обход того, что пользователь скопировал в Проводнике.

На выходе две вещи, и разделение между ними существенно: манифест уходит на
провод и содержит только относительные пути, а карта абсолютных путей остаётся
на этой машине. Если бы абсолютные пути лежали в манифесте, они уехали бы
второму компьютеру просто потому, что оказались в том же объекте.

По reparse point (junction, symlink) обход НЕ идёт. Папка с junction на
C:\\Windows иначе выгрузила бы операционную систему целиком. Такие записи
попадают в skipped с причиной, чтобы получатель мог о них сказать.
"""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Sequence
from pathlib import Path

from .model import ENTRY_DIRECTORY, ENTRY_FILE, SkippedEntry, TransferEntry, TransferManifest
from .paths import sanitize_manifest

logger = logging.getLogger(__name__)

REASON_REPARSE_POINT = "reparse_point"
REASON_UNREADABLE = "unreadable"


def _is_reparse_point(entry_stat: os.stat_result) -> bool:
    """Junction и symlink на Windows, symlink где угодно.

    st_file_attributes есть только на Windows, поэтому проверяется наличие
    атрибута, а не платформа: так функция остаётся тестируемой и на другой ОС.
    """
    attributes = getattr(entry_stat, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return bool(attributes & reparse)


def scan(
    roots: Sequence[Path], transfer_id: str, drop_effect: int = 1
) -> tuple[TransferManifest, dict[str, Path]]:
    """Манифест плюс карта ``относительный путь -> абсолютный источник``."""
    entries: list[TransferEntry] = []
    skipped: list[SkippedEntry] = []
    sources: dict[str, Path] = {}

    for root in roots:
        _walk(Path(root), Path(root).name, entries, skipped, sources)

    manifest = sanitize_manifest(
        TransferManifest(
            transfer_id=transfer_id,
            entries=tuple(entries),
            skipped=tuple(skipped),
            drop_effect=drop_effect,
        )
    )
    # sanitize_manifest канонизирует пути, поэтому карта пересобирается по
    # каноническим ключам - иначе поиск источника по пути из манифеста
    # промахнулся бы на любом пути, который нормализация изменила.
    canonical = {
        entry.path: sources[original]
        for entry, original in zip(manifest.entries, (e.path for e in entries), strict=True)
        if entry.kind == ENTRY_FILE
    }
    return manifest, canonical


def _walk(
    absolute: Path,
    relative: str,
    entries: list[TransferEntry],
    skipped: list[SkippedEntry],
    sources: dict[str, Path],
) -> None:
    try:
        entry_stat = os.stat(absolute, follow_symlinks=False)
    except OSError:
        # Полный путь в журнал не идёт - только имя (спека §15).
        logger.warning("не удалось прочитать %s", absolute.name)
        skipped.append(SkippedEntry(path=relative, reason=REASON_UNREADABLE))
        return

    if _is_reparse_point(entry_stat):
        skipped.append(SkippedEntry(path=relative, reason=REASON_REPARSE_POINT))
        return

    if stat.S_ISDIR(entry_stat.st_mode):
        entries.append(
            TransferEntry(
                path=relative, kind=ENTRY_DIRECTORY, size=0, mtime_ns=entry_stat.st_mtime_ns
            )
        )
        try:
            children = sorted(os.listdir(absolute))
        except OSError:
            logger.warning("не удалось перечислить %s", absolute.name)
            skipped.append(SkippedEntry(path=relative, reason=REASON_UNREADABLE))
            return
        for child in children:
            _walk(absolute / child, f"{relative}/{child}", entries, skipped, sources)
        return

    entries.append(
        TransferEntry(
            path=relative,
            kind=ENTRY_FILE,
            size=entry_stat.st_size,
            mtime_ns=entry_stat.st_mtime_ns,
        )
    )
    sources[relative] = absolute


__all__ = ["REASON_REPARSE_POINT", "REASON_UNREADABLE", "scan"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_scanner.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/scanner.py configurator/tests/transfer/test_scanner.py
git commit -m "Walk the source tree without following reparse points

Two return values, and the split matters: the manifest goes on the wire
and carries relative paths only, while the absolute-path map stays on this
machine. A folder containing a junction to C:\\Windows would otherwise
export the operating system.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 1.6: six new message types and a chunk ceiling

**Files:**
- Modify: `configurator/src/duo_input/clipboard/wire.py`
- Test: `configurator/tests/clipboard/test_wire.py` (append)

**Interfaces:**
- Consumes: existing `MessageType`, `Message`, `encode`, `FrameAssembler`, `WireError`.
- Produces:
  - `MessageType.FILE_OFFER = 10`, `TRANSFER_BEGIN = 11`, `FILE_READ = 12`,
    `FILE_CHUNK = 13`, `FILE_ERROR = 14`, `TRANSFER_END = 15`
  - `MAX_FILE_CHUNK_BYTES = 1_048_576`

The framing is already neutral to message type — its own docstring says so — so
this task adds values and a ceiling, and changes no parsing.

`MAX_FILE_CHUNK_BYTES` is deliberately far below `MAX_FRAME_BYTES` (32 MiB +
64 KiB). The frame ceiling exists to stop a malformed length field; the chunk
ceiling exists to bound memory. Conflating them would let one `FILE_CHUNK` hold
32 MiB.

- [ ] **Step 1: Write the failing test**

```python
# append to configurator/tests/clipboard/test_wire.py
from duo_input.clipboard.wire import MAX_FILE_CHUNK_BYTES


def test_the_six_file_message_types_have_the_numbers_the_protocol_promises():
    assert (
        MessageType.FILE_OFFER,
        MessageType.TRANSFER_BEGIN,
        MessageType.FILE_READ,
        MessageType.FILE_CHUNK,
        MessageType.FILE_ERROR,
        MessageType.TRANSFER_END,
    ) == (10, 11, 12, 13, 14, 15)


def test_no_message_type_number_is_used_twice():
    numbers = [int(member) for member in MessageType]

    assert len(numbers) == len(set(numbers))


def test_a_file_chunk_survives_the_round_trip_with_its_bytes_intact():
    payload = bytes(range(256)) * 16
    message = Message(
        MessageType.FILE_CHUNK,
        {"transfer_id": "t", "entry_index": 2, "offset": 4096},
        payload,
    )

    assembler = FrameAssembler()
    [decoded] = assembler.feed(encode(message))

    assert decoded == message


def test_the_chunk_ceiling_is_far_below_the_frame_ceiling():
    # Потолок кадра ловит испорченное поле длины; потолок чанка ограничивает
    # память. Если бы это было одно число, один FILE_CHUNK нёс бы 32 МиБ.
    assert MAX_FILE_CHUNK_BYTES == 1_048_576
    assert MAX_FILE_CHUNK_BYTES * 8 < MAX_FRAME_BYTES


def test_a_chunk_at_the_ceiling_still_fits_in_one_frame():
    message = Message(MessageType.FILE_CHUNK, {"offset": 0}, b"x" * MAX_FILE_CHUNK_BYTES)

    assembler = FrameAssembler()
    [decoded] = assembler.feed(encode(message))

    assert len(decoded.blob) == MAX_FILE_CHUNK_BYTES
```

Add `MAX_FRAME_BYTES` to the existing import in that file if it is not already
there.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard/test_wire.py -v -k "file or ceiling or twice"`
Expected: FAIL — `ImportError: cannot import name 'MAX_FILE_CHUNK_BYTES'`

- [ ] **Step 3: Write minimal implementation**

In `configurator/src/duo_input/clipboard/wire.py`, add to `MessageType`:

```python
    # Передача файлов. Отдельная логическая подсистема поверх того же кадра -
    # framing к типу сообщения безразличен, о чём сказано в docstring модуля.
    #
    # Старый пир, получив любой из этих типов, бросит WireError и оборвёт
    # соединение целиком, вместе с буфером обмена. Поэтому они не отправляются
    # никому, кто не объявил files/1 в HELLO - см. coordinator.CAPABILITIES.
    FILE_OFFER = 10
    TRANSFER_BEGIN = 11
    FILE_READ = 12
    FILE_CHUNK = 13
    FILE_ERROR = 14
    TRANSFER_END = 15
```

And below `MAX_FRAME_BYTES`:

```python
#: Потолок одного FILE_CHUNK: 1 МиБ.
#:
#: Отдельная величина от MAX_FRAME_BYTES намеренно. Потолок кадра существует,
#: чтобы испорченное поле длины не заставило нас выделить гигабайт; потолок
#: чанка существует, чтобы ограничить память под передачу. Одно число вместо
#: двух означало бы, что один FILE_CHUNK вправе нести 32 МиБ.
MAX_FILE_CHUNK_BYTES = 1_048_576
```

Extend `__all__` with `"MAX_FILE_CHUNK_BYTES"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard/test_wire.py -v`
Expected: PASS, all existing tests plus 5 new

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/wire.py configurator/tests/clipboard/test_wire.py
git commit -m "Add the six file-transfer message types and a chunk ceiling

The chunk ceiling is separate from the frame ceiling on purpose: the frame
ceiling stops a corrupt length field, the chunk ceiling bounds memory. One
number for both would let a single FILE_CHUNK carry 32 MiB.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 1.7: capability negotiation in HELLO

**Files:**
- Modify: `configurator/src/duo_input/clipboard/wire.py:19` (`PROTOCOL_MINOR`)
- Modify: `configurator/src/duo_input/clipboard/coordinator.py` (`_on_connected`, `_on_message`)
- Test: `configurator/tests/clipboard/test_coordinator.py` (append)

**Interfaces:**
- Consumes: `PROTOCOL_MAJOR`, `PROTOCOL_MINOR`, `Message`, `MessageType`.
- Produces:
  - `wire.PROTOCOL_MINOR == 1`
  - `wire.CAPABILITY_CLIPBOARD = "clipboard/1"`, `wire.CAPABILITY_FILES = "files/1"`,
    `wire.CAPABILITIES = ("clipboard/1", "files/1")`,
    `wire.LEGACY_CAPABILITIES = frozenset({"clipboard/1"})`
  - `ClipboardCoordinator.peer_capabilities -> frozenset[str]`
  - `ClipboardCoordinator.link -> PeerLink | None` (public accessor; Task 4.2
    needs the live link and must not reach into `_link` across a module boundary)
  - `ClipboardCoordinator.peer_supports(capability: str) -> bool`
  - signal `capabilities_known = Signal(object)` emitted with the frozenset when
    `HELLO` arrives

A peer that predates this change sends no `capabilities` key. Treating that as
"clipboard only" is what keeps its clipboard working: it never receives a
`FILE_*` frame, so it never reaches `WireError` → `_fail()`.

- [ ] **Step 1: Write the failing test**

```python
# append to configurator/tests/clipboard/test_coordinator.py
from duo_input.clipboard.wire import (
    CAPABILITIES,
    CAPABILITY_CLIPBOARD,
    CAPABILITY_FILES,
    PROTOCOL_MAJOR,
    PROTOCOL_MINOR,
)


def test_the_minor_version_moved_and_the_major_version_did_not():
    # Подъём major разорвал бы связь со всеми существующими сборками. Новые
    # типы сообщений в этом не нуждаются: их закрывает capability.
    assert (PROTOCOL_MAJOR, PROTOCOL_MINOR) == (1, 1)


def test_hello_announces_both_capabilities(coordinator_with_link):
    coordinator, link = coordinator_with_link

    hello = next(m for m in link.sent if m.type is MessageType.HELLO)

    assert hello.header["capabilities"] == list(CAPABILITIES)


def test_a_peer_that_announces_files_is_recorded_as_supporting_them(coordinator_with_link):
    coordinator, link = coordinator_with_link

    link.deliver(
        Message(
            MessageType.HELLO,
            {
                "protocol_major": PROTOCOL_MAJOR,
                "protocol_minor": PROTOCOL_MINOR,
                "origin_id": "b" * 32,
                "machine_name": "PC2",
                "capabilities": [CAPABILITY_CLIPBOARD, CAPABILITY_FILES],
            },
            b"",
        )
    )

    assert coordinator.peer_supports(CAPABILITY_FILES)


def test_a_peer_with_no_capabilities_key_is_treated_as_clipboard_only(coordinator_with_link):
    # Это и есть совместимость со старым клиентом: он не объявляет ничего,
    # мы не посылаем ему FILE_*, и его буфер обмена продолжает работать.
    coordinator, link = coordinator_with_link

    link.deliver(
        Message(
            MessageType.HELLO,
            {
                "protocol_major": PROTOCOL_MAJOR,
                "protocol_minor": 0,
                "origin_id": "b" * 32,
                "machine_name": "OldPC",
            },
            b"",
        )
    )

    assert coordinator.peer_supports(CAPABILITY_CLIPBOARD)
    assert not coordinator.peer_supports(CAPABILITY_FILES)


def test_a_capabilities_value_that_is_not_a_list_is_ignored_rather_than_trusted(
    coordinator_with_link,
):
    coordinator, link = coordinator_with_link

    link.deliver(
        Message(
            MessageType.HELLO,
            {
                "protocol_major": PROTOCOL_MAJOR,
                "protocol_minor": PROTOCOL_MINOR,
                "origin_id": "b" * 32,
                "machine_name": "PC2",
                "capabilities": "files/1",
            },
            b"",
        )
    )

    assert not coordinator.peer_supports(CAPABILITY_FILES), (
        "строка 'files/1' содержит 'files/1' как подстроку - проверка через "
        "`in` по строке приняла бы её, и мы послали бы FILE_* туда, где их не ждут"
    )


def test_capabilities_are_announced_before_they_are_known(coordinator_with_link):
    coordinator, _link = coordinator_with_link

    assert coordinator.peer_capabilities == frozenset(), (
        "до HELLO мы не знаем ничего, и это не то же самое, что знать про clipboard"
    )
```

`coordinator_with_link` is a fixture that must be added to
`configurator/tests/clipboard/test_coordinator.py` if the file does not already
have an equivalent. Read the file first and reuse whatever fake link it already
defines; if there is none, add:

```python
class _FakeLink(QObject):
    message_received = Signal(object)
    connected = Signal(str)
    disconnected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Message] = []
        self.peer_fingerprint = "ff" * 32
        self.peer_address = "192.0.2.10"

    def send(self, message: Message) -> None:
        self.sent.append(message)

    def close(self) -> None: ...

    def deliver(self, message: Message) -> None:
        """Доставить кадр так, как это сделал бы настоящий сокет."""
        self.message_received.emit(message)


@pytest.fixture
def coordinator_with_link(qapp, tmp_path):
    identity = load_or_create(tmp_path)
    trust = TrustStore(tmp_path / "peers.json")
    trust.remember(
        TrustedPeer(
            origin_id="b" * 32,
            machine_name="PC2",
            fingerprint="ff" * 32,
            last_address="192.0.2.10",
        )
    )
    coordinator = ClipboardCoordinator(
        identity=identity, trust=trust, machine_name="PC1"
    )
    link = _FakeLink()
    coordinator._on_connected(link)
    return coordinator, link
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard/test_coordinator.py -v -k capabilit`
Expected: FAIL — `ImportError: cannot import name 'CAPABILITIES'`

- [ ] **Step 3: Write minimal implementation**

In `wire.py` change `PROTOCOL_MINOR = 0` to:

```python
#: 1: HELLO несёт capabilities. Major не поднят намеренно - подъём major
#: разорвал бы связь со всеми существующими сборками, а новые типы сообщений
#: в этом не нуждаются, потому что их закрывает capability.
PROTOCOL_MINOR = 1
```

In **`wire.py`**, below `PROTOCOL_MINOR` — not in `coordinator.py`. These names
are protocol vocabulary, and `wire.py` is the module both subsystems already
share. Putting them in `coordinator.py` would make `transfer/service.py` import
the entire clipboard networking stack (discovery, listener, peer, trust,
pairing) to learn one string:

```python
CAPABILITY_CLIPBOARD = "clipboard/1"
CAPABILITY_FILES = "files/1"

#: Что умеет ЭТА сборка. Пир узнаёт это из HELLO и наоборот.
CAPABILITIES = (CAPABILITY_CLIPBOARD, CAPABILITY_FILES)

#: Что умеет пир, не назвавший ничего. Старые сборки не знают про ключ
#: capabilities вовсе, и считать их умеющими только буфер обмена - это ровно
#: то, что сохраняет им буфер обмена: FILE_* мы им не пошлём, а значит они не
#: встретят неизвестный тип и не оборвут соединение (см. wire._decode_payload).
LEGACY_CAPABILITIES = frozenset({CAPABILITY_CLIPBOARD})
```

Add the signal and state to `ClipboardCoordinator`:

```python
    capabilities_known = Signal(object)
```

In `__init__`, after `self._link = None`:

```python
        self._peer_capabilities: frozenset[str] = frozenset()
```

Add the accessors:

```python
    @property
    def peer_capabilities(self) -> frozenset[str]:
        """Что умеет пир. Пустое множество означает "ещё не знаем"."""
        return self._peer_capabilities

    def peer_supports(self, capability: str) -> bool:
        return capability in self._peer_capabilities
```

In `_on_connected`, add `"capabilities": list(CAPABILITIES)` to the `HELLO`
header. Reset the knowledge there too, because a reconnect may reach a
different build:

```python
        self._peer_capabilities = frozenset()
```

In `_on_message`, after the major-version check passes:

```python
        if message.type is MessageType.HELLO:
            if int(message.header.get("protocol_major", -1)) != PROTOCOL_MAJOR:
                self._drop(
                    "вторая машина говорит на другой версии протокола",
                    protocol_mismatch=True,
                )
                return
            self._peer_capabilities = _capabilities_from(message.header)
            self.capabilities_known.emit(self._peer_capabilities)
```

And the parser, at module level:

```python
def _capabilities_from(header: dict) -> frozenset[str]:
    """Что пир объявил - и ничего, чего он не объявил.

    Проверка типа, а не приведение: строка "files/1" содержит "files/1" как
    подстроку, поэтому проверка через `in` по строке приняла бы её, и мы
    послали бы FILE_* туда, где их не ждут. Тот же порядок строгости, что у
    ClipboardOffer.from_dict.
    """
    announced = header.get("capabilities")
    if announced is None:
        return LEGACY_CAPABILITIES
    if not isinstance(announced, list):
        logger.warning("capabilities пришли не списком - считаем пира устаревшим")
        return LEGACY_CAPABILITIES
    return frozenset(item for item in announced if isinstance(item, str))
```

Extend `wire.__all__` with the four capability names, and import them into
`coordinator.py` (`from .wire import CAPABILITIES, CAPABILITY_CLIPBOARD,
CAPABILITY_FILES, LEGACY_CAPABILITIES`) so existing `coordinator.CAPABILITIES`
spellings keep working. Also add the public link accessor:

```python
    @property
    def link(self):
        """Живая связь, либо None. Нужна передаче файлов - а частный атрибут,
        пересекающий границу модуля, это та форма, которая гниёт первой."""
        return self._link
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard/test_coordinator.py -v`
Expected: PASS, all existing tests plus 6 new

- [ ] **Step 5: Run the whole clipboard suite to prove nothing regressed**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard -q`
Expected: PASS. A failure here means an existing test asserted the exact `HELLO`
header; update that assertion rather than removing the capability.

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/clipboard/wire.py configurator/src/duo_input/clipboard/coordinator.py configurator/tests/clipboard/test_coordinator.py
git commit -m "Negotiate capabilities in HELLO without bumping the major version

A peer predating this change sends no capabilities key, and treating that
as clipboard-only is exactly what keeps its clipboard working: it never
receives a FILE_* frame, so it never reaches WireError and never drops the
connection. The parser checks the type rather than coercing, because the
string \"files/1\" contains \"files/1\" as a substring.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 1.8: ChunkPipe — the only thing the two threads share

**Files:**
- Create: `configurator/src/duo_input/transfer/pipe.py`
- Test: `configurator/tests/transfer/test_pipe.py`

**Interfaces:**
- Consumes: nothing. `threading` only — no Qt, by boundary rule.
- Produces:
  - `class PipeClosed(Exception)` with `.reason: str`
  - `class PipeOverflow(Exception)`
  - `ChunkPipe(capacity_chunks: int = 1)`
  - `.push(data: bytes) -> None` — never blocks; raises `PipeOverflow` above capacity
  - `.take(max_bytes: int) -> bytes` — never blocks; `b""` when empty
  - `.wait(timeout: float) -> bool` — blocks until data, finish or close;
    returns `False` on timeout; raises `PipeClosed` if closed
  - `.finish() -> None` — normal end of stream
  - `.close(reason: str) -> None` — abnormal end; wakes every waiter at once
  - `.depth -> int`, `.high_water -> int`, `.finished -> bool`, `.closed_reason -> str | None`

`take` and `wait` are separate on purpose. The COM thread must issue a
`FILE_READ` *before* it blocks, and a single combined `read()` would either
block before requesting or require the pipe to know how to request — which
would put a Qt object inside the one class that must stay Qt-free.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_pipe.py
"""Единственное, что разделяют COM-поток и GUI-поток Qt.

Здесь нет ни Qt, ни сокетов, ни COM - поэтому очередь проверяется настоящими
потоками, а не имитацией, и утверждения о блокировке и пробуждении являются
утверждениями о том, что произойдёт в бою.
"""

from __future__ import annotations

import threading
import time

import pytest

from duo_input.transfer.pipe import ChunkPipe, PipeClosed, PipeOverflow


def test_a_fresh_pipe_is_empty_and_hands_back_nothing_without_blocking():
    pipe = ChunkPipe()

    assert pipe.depth == 0
    assert pipe.take(4096) == b""


def test_what_was_pushed_comes_back_in_order():
    pipe = ChunkPipe(capacity_chunks=2)

    pipe.push(b"abc")
    pipe.push(b"de")

    assert pipe.take(10) == b"abc"
    assert pipe.take(10) == b"de"


def test_take_never_returns_more_than_asked_and_keeps_the_remainder():
    pipe = ChunkPipe()
    pipe.push(b"abcdef")

    assert pipe.take(2) == b"ab"
    assert pipe.take(2) == b"cd"
    assert pipe.take(99) == b"ef"


def test_pushing_above_capacity_is_refused_rather_than_buffered():
    # Это и есть доказуемый потолок памяти: очередь физически не может вырасти
    # больше окна, сколько бы отправитель ни присылал.
    pipe = ChunkPipe(capacity_chunks=1)
    pipe.push(b"first")

    with pytest.raises(PipeOverflow):
        pipe.push(b"second")


def test_the_high_water_mark_records_the_deepest_the_queue_ever_got():
    pipe = ChunkPipe(capacity_chunks=3)

    pipe.push(b"a")
    pipe.push(b"b")
    pipe.take(1)
    pipe.take(1)

    assert pipe.depth == 0
    assert pipe.high_water == 2


def test_wait_returns_false_on_timeout_instead_of_hanging_for_ever():
    pipe = ChunkPipe()

    started = time.perf_counter()
    assert pipe.wait(timeout=0.05) is False
    assert time.perf_counter() - started < 1.0


def test_a_blocked_waiter_wakes_when_a_chunk_arrives():
    pipe = ChunkPipe()
    woke = threading.Event()

    def consumer() -> None:
        pipe.wait(timeout=5.0)
        woke.set()

    thread = threading.Thread(target=consumer, daemon=True)
    thread.start()
    time.sleep(0.05)
    pipe.push(b"payload")

    assert woke.wait(timeout=2.0), "push не разбудил ожидающего"
    thread.join(timeout=2.0)


def test_a_blocked_waiter_wakes_immediately_when_the_pipe_is_closed():
    # Отзывчивость отмены держится ровно на этом: заблокированный Read обязан
    # вернуть управление сразу, а не через таймаут.
    pipe = ChunkPipe()
    result: list[str] = []

    def consumer() -> None:
        try:
            pipe.wait(timeout=30.0)
        except PipeClosed as error:
            result.append(error.reason)

    thread = threading.Thread(target=consumer, daemon=True)
    thread.start()
    time.sleep(0.05)
    started = time.perf_counter()
    pipe.close("cancelled")
    thread.join(timeout=2.0)

    assert result == ["cancelled"]
    assert time.perf_counter() - started < 1.0, "закрытие ждало таймаута вместо notify_all"


def test_every_waiter_wakes_on_close_not_only_the_first():
    pipe = ChunkPipe()
    woken = []
    lock = threading.Lock()

    def consumer() -> None:
        try:
            pipe.wait(timeout=30.0)
        except PipeClosed:
            with lock:
                woken.append(1)

    threads = [threading.Thread(target=consumer, daemon=True) for _ in range(3)]
    for thread in threads:
        thread.start()
    time.sleep(0.1)
    pipe.close("link lost")
    for thread in threads:
        thread.join(timeout=2.0)

    assert len(woken) == 3, "close() пробудил не всех - notify() вместо notify_all()"


def test_take_after_close_raises_rather_than_pretending_the_stream_ended():
    pipe = ChunkPipe()
    pipe.close("link lost")

    with pytest.raises(PipeClosed):
        pipe.take(10)


def test_buffered_bytes_are_still_readable_after_finish():
    pipe = ChunkPipe(capacity_chunks=2)
    pipe.push(b"tail")
    pipe.finish()

    assert pipe.take(10) == b"tail"
    assert pipe.take(10) == b""
    assert pipe.finished is True


def test_wait_after_finish_returns_at_once_without_raising():
    pipe = ChunkPipe()
    pipe.finish()

    assert pipe.wait(timeout=5.0) is True


def test_pushing_after_finish_is_refused():
    pipe = ChunkPipe()
    pipe.finish()

    with pytest.raises(PipeClosed):
        pipe.push(b"late")


def test_closing_twice_keeps_the_first_reason():
    pipe = ChunkPipe()

    pipe.close("cancelled")
    pipe.close("link lost")

    assert pipe.closed_reason == "cancelled", (
        "второй close перезаписал причину - пользователь увидел бы не то, что произошло"
    )


def test_the_queue_never_exceeds_its_capacity_under_a_real_producer_and_consumer():
    pipe = ChunkPipe(capacity_chunks=4)
    stop = threading.Event()

    def producer() -> None:
        while not stop.is_set():
            try:
                pipe.push(b"x" * 1024)
            except PipeOverflow:
                time.sleep(0.001)

    thread = threading.Thread(target=producer, daemon=True)
    thread.start()
    for _ in range(2000):
        pipe.take(1024)
    stop.set()
    thread.join(timeout=2.0)

    assert pipe.high_water <= 4, (
        f"глубина доходила до {pipe.high_water} при окне 4 - потолок памяти не доказан"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_pipe.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duo_input.transfer.pipe'`

- [ ] **Step 3: Write minimal implementation**

```python
# configurator/src/duo_input/transfer/pipe.py
"""Ограниченная очередь между COM-потоком и GUI-потоком Qt.

Это ЕДИНСТВЕННОЕ, что два потока разделяют, и поэтому здесь нет ни Qt, ни
сокетов, ни COM - только threading. Правило границы (boundary-тест) запрещает
этому модулю импортировать PySide6: иначе однажды кто-нибудь дёрнет
QSslSocket из COM-потока, потому что "так короче".

take() и wait() разделены намеренно. COM-поток обязан отправить FILE_READ
ПЕРЕД тем, как заблокироваться, а один совмещённый read() либо блокировался бы
до запроса, либо потребовал бы, чтобы очередь умела запрашивать сама - то есть
держала бы внутри себя Qt-объект, чего этот модуль существует чтобы избежать.

Потолок памяти здесь доказуем, а не обещан: push выше ёмкости отказывает, так
что очередь физически не может вырасти больше окна, сколько бы отправитель ни
присылал.
"""

from __future__ import annotations

import threading


class PipeClosed(Exception):
    """Очередь закрыта не по-хорошему: отмена, разрыв, ошибка."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class PipeOverflow(Exception):
    """Попытка положить в очередь больше, чем разрешает окно."""


class ChunkPipe:
    """Байты от сети к потребителю, с окном и с мгновенным пробуждением."""

    def __init__(self, capacity_chunks: int = 1) -> None:
        if capacity_chunks < 1:
            raise ValueError("окно не может быть меньше одного чанка")
        self._capacity = capacity_chunks
        self._condition = threading.Condition()
        self._chunks: list[bytes] = []
        self._offset = 0
        self._finished = False
        self._closed_reason: str | None = None
        self._high_water = 0

    # ------------------------------------------------------------------ состояние

    @property
    def depth(self) -> int:
        with self._condition:
            return len(self._chunks)

    @property
    def high_water(self) -> int:
        with self._condition:
            return self._high_water

    @property
    def finished(self) -> bool:
        with self._condition:
            return self._finished

    @property
    def closed_reason(self) -> str | None:
        with self._condition:
            return self._closed_reason

    # ------------------------------------------------------------------ сторона сети

    def push(self, data: bytes) -> None:
        """Положить чанк. Никогда не блокирует - её зовёт GUI-поток."""
        with self._condition:
            if self._closed_reason is not None:
                raise PipeClosed(self._closed_reason)
            if self._finished:
                raise PipeClosed("поток уже завершён")
            if len(self._chunks) >= self._capacity:
                raise PipeOverflow(
                    f"в очереди уже {len(self._chunks)} чанков при окне {self._capacity}"
                )
            self._chunks.append(data)
            self._high_water = max(self._high_water, len(self._chunks))
            self._condition.notify_all()

    def finish(self) -> None:
        """Данных больше не будет, и это нормальный конец."""
        with self._condition:
            self._finished = True
            self._condition.notify_all()

    def close(self, reason: str) -> None:
        """Конец не нормальный. Первая причина побеждает.

        Второй close не перезаписывает причину: пользователь должен увидеть
        то, что произошло первым (отмену), а не то, что случилось следом как
        её последствие (разрыв).
        """
        with self._condition:
            if self._closed_reason is None:
                self._closed_reason = reason
            # notify_all, не notify: ждущих может быть несколько, и разбудить
            # одного означало бы оставить остальных висеть до таймаута.
            self._condition.notify_all()

    # ------------------------------------------------------------------ сторона потребителя

    def take(self, max_bytes: int) -> bytes:
        """До ``max_bytes`` байт. Никогда не блокирует; ``b""`` если пусто."""
        with self._condition:
            if self._closed_reason is not None:
                raise PipeClosed(self._closed_reason)
            if not self._chunks:
                return b""
            head = self._chunks[0]
            end = min(len(head), self._offset + max_bytes)
            payload = head[self._offset : end]
            if end >= len(head):
                self._chunks.pop(0)
                self._offset = 0
                self._condition.notify_all()
            else:
                self._offset = end
            return payload

    def wait(self, timeout: float) -> bool:
        """Дождаться данных, завершения или закрытия.

        ``True`` - есть что взять либо поток завершён; ``False`` - вышел срок;
        ``PipeClosed`` - закрыто.
        """
        with self._condition:
            if self._closed_reason is not None:
                raise PipeClosed(self._closed_reason)
            if self._chunks or self._finished:
                return True
            self._condition.wait(timeout)
            if self._closed_reason is not None:
                raise PipeClosed(self._closed_reason)
            return bool(self._chunks) or self._finished


__all__ = ["ChunkPipe", "PipeClosed", "PipeOverflow"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_pipe.py -v`
Expected: PASS, 15 passed

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/pipe.py configurator/tests/transfer/test_pipe.py
git commit -m "Bound the memory between the COM thread and the Qt thread

The ceiling is provable rather than promised: push above the window is
refused, so the queue physically cannot grow past it no matter how fast
the sender is. take() and wait() are separate because the COM thread must
issue its FILE_READ before it blocks, and a combined read() would have to
know how to request - putting a Qt object inside the one class that must
stay Qt-free.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 1.9: SnapshotRegistry — held descriptors and change detection

**Files:**
- Create: `configurator/src/duo_input/transfer/source.py`
- Test: `configurator/tests/transfer/test_source.py`

**Interfaces:**
- Consumes: `TransferManifest`, `TransferEntry`, `ENTRY_FILE` from Task 1.1.
- Produces:
  - `class SourceChanged(Exception)`, `class SourceMissing(Exception)`
  - `REASON_SOURCE_CHANGED = "source_changed"`, `REASON_SOURCE_MISSING = "source_missing"`
  - `RETENTION = 4`
  - `SnapshotRegistry()` with
    `.publish(manifest, sources: dict[str, Path]) -> None`,
    `.read(transfer_id: str, entry_index: int, offset: int, length: int) -> bytes`,
    `.release(transfer_id: str) -> None`,
    `.transfer_ids -> tuple[str, ...]`,
    `.serving -> frozenset[str]`

This implements spec §10 exactly, including its honest limitation. The measured
facts it relies on: while we hold the descriptor, another process can write and
truncate but cannot delete or rename, and `os.fstat(fd)` reports the change.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_source.py
"""Удерживаемый дескриптор с обнаружением изменений - не "неизменяемый снимок".

Измерено на целевой платформе (спека §2, факт 7): пока мы держим дескриптор,
другой процесс МОЖЕТ писать и усекать файл, но НЕ МОЖЕТ удалить или
переименовать его. Отсюда обе половины этих тестов - и то, что мы защищаем, и
то, чего мы не обещаем.
"""

from __future__ import annotations

import os
import sys

import pytest

from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.source import (
    RETENTION,
    SnapshotRegistry,
    SourceChanged,
    SourceMissing,
)


def _publish(registry, tmp_path, transfer_id="t-1", name="a.bin", payload=b"0123456789"):
    source = tmp_path / name
    source.write_bytes(payload)
    entry = TransferEntry(
        path=name,
        kind=ENTRY_FILE,
        size=len(payload),
        mtime_ns=os.stat(source).st_mtime_ns,
    )
    manifest = TransferManifest(transfer_id=transfer_id, entries=(entry,))
    registry.publish(manifest, {name: source})
    return source


def test_reading_an_offset_range_returns_exactly_those_bytes(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    assert registry.read("t-1", 0, 3, 4) == b"3456"


def test_reads_are_idempotent_so_the_same_range_twice_returns_the_same_bytes(tmp_path):
    # На этом стоит поддержка Seek и повторного Ctrl+V: чтения адресуются
    # смещением, а не позицией в потоке.
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    assert registry.read("t-1", 0, 2, 3) == registry.read("t-1", 0, 2, 3)


def test_reads_may_arrive_out_of_order(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    tail = registry.read("t-1", 0, 8, 2)
    head = registry.read("t-1", 0, 0, 2)

    assert (head, tail) == (b"01", b"89")


def test_a_read_past_the_end_returns_only_what_is_there(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    assert registry.read("t-1", 0, 8, 100) == b"89"


def test_a_read_at_the_end_returns_nothing(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    assert registry.read("t-1", 0, 10, 10) == b""


def test_a_zero_byte_file_reads_as_nothing_rather_than_failing(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path, name="empty.bin", payload=b"")

    assert registry.read("t-1", 0, 0, 10) == b""


def test_the_first_read_marks_the_snapshot_as_serving(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    assert registry.serving == frozenset()

    registry.read("t-1", 0, 0, 1)

    assert registry.serving == frozenset({"t-1"})


def test_a_file_written_under_us_is_detected_and_refused(tmp_path):
    registry = SnapshotRegistry()
    source = _publish(registry, tmp_path)
    registry.read("t-1", 0, 0, 1)

    with open(source, "r+b") as handle:
        handle.write(b"X")

    with pytest.raises(SourceChanged):
        registry.read("t-1", 0, 0, 4)


def test_a_file_truncated_under_us_is_detected_and_refused(tmp_path):
    registry = SnapshotRegistry()
    source = _publish(registry, tmp_path)
    registry.read("t-1", 0, 0, 1)

    with open(source, "r+b") as handle:
        handle.truncate(2)

    with pytest.raises(SourceChanged):
        registry.read("t-1", 0, 0, 4)


def test_a_source_that_vanished_before_the_first_read_is_reported_as_missing(tmp_path):
    registry = SnapshotRegistry()
    source = _publish(registry, tmp_path)
    source.unlink()

    with pytest.raises(SourceMissing):
        registry.read("t-1", 0, 0, 4)


def test_an_unknown_transfer_id_is_reported_as_missing_not_as_a_crash(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    with pytest.raises(SourceMissing):
        registry.read("no-such-transfer", 0, 0, 4)


def test_an_entry_index_outside_the_manifest_is_reported_as_missing(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path)

    with pytest.raises(SourceMissing):
        registry.read("t-1", 99, 0, 4)


@pytest.mark.skipif(sys.platform != "win32", reason="поведение разделения доступа - Windows")
def test_while_we_hold_the_descriptor_the_path_cannot_be_substituted(tmp_path):
    # Измеренное свойство: os.remove и os.rename отказывают с winerror 32.
    # Это и есть защита от TOCTOU - путь открывается один раз, дальше работа
    # идёт с файловым объектом, а не с именем.
    registry = SnapshotRegistry()
    source = _publish(registry, tmp_path)
    registry.read("t-1", 0, 0, 1)

    with pytest.raises(OSError):
        os.remove(source)
    with pytest.raises(OSError):
        os.rename(source, tmp_path / "swapped.bin")


def test_releasing_a_snapshot_closes_its_descriptor_and_forgets_it(tmp_path):
    registry = SnapshotRegistry()
    source = _publish(registry, tmp_path)
    registry.read("t-1", 0, 0, 1)

    registry.release("t-1")

    assert registry.transfer_ids == ()
    # Дескриптор закрыт - значит путь снова можно удалить.
    os.remove(source)


def test_the_newest_snapshot_and_every_serving_one_are_kept(tmp_path):
    registry = SnapshotRegistry()
    _publish(registry, tmp_path, transfer_id="old", name="old.bin")
    registry.read("old", 0, 0, 1)  # old становится SERVING
    for index in range(RETENTION + 2):
        _publish(registry, tmp_path, transfer_id=f"new-{index}", name=f"n{index}.bin")

    assert "old" in registry.transfer_ids, (
        "снимок с активной передачей вытеснен - сценарий 1 сломан: смена буфера "
        "обмена оборвала бы идущую передачу"
    )
    assert f"new-{RETENTION + 1}" in registry.transfer_ids


def test_snapshots_that_are_neither_newest_nor_serving_are_evicted(tmp_path):
    registry = SnapshotRegistry()
    for index in range(RETENTION + 3):
        _publish(registry, tmp_path, transfer_id=f"t-{index}", name=f"f{index}.bin")

    assert len(registry.transfer_ids) <= RETENTION
    assert "t-0" not in registry.transfer_ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_source.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duo_input.transfer.source'`

- [ ] **Step 3: Write minimal implementation**

```python
# configurator/src/duo_input/transfer/source.py
"""Снимки на отправляющей машине: что обещано и откуда это читать.

Модель называется УДЕРЖИВАЕМЫМ ДЕСКРИПТОРОМ С ОБНАРУЖЕНИЕМ ИЗМЕНЕНИЙ, а не
"неизменяемым снимком", и разница существенна. Измерено на целевой платформе
(спека §2, факт 7): пока мы держим дескриптор, другой процесс МОЖЕТ писать в
файл и усекать его, но НЕ МОЖЕТ его удалить или переименовать.

Отсюда две половины:

- чего модель НЕ даёт: байтовой неизменяемости. Мы обнаруживаем чужую запись
  и отказываем, но предотвратить её не можем. Изменение той же длины с
  восстановленным mtime не обнаруживается вовсе - это названное остаточное
  ограничение, а не недосмотр.
- что модель даёт: подмена пути невозможна. Путь открывается ОДИН раз, дальше
  работа идёт с файловым объектом. Классического TOCTOU "проверили путь, потом
  открыли другой файл" здесь не существует.

Сверка идёт через os.fstat(fd) - через НАШ дескриптор, а не через путь.
Поэтому TOCTOU отсутствует и внутри самой проверки.

Неизменяемым здесь является МАНИФЕСТ, а не байты.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from .model import ENTRY_FILE, TransferManifest

logger = logging.getLogger(__name__)

REASON_SOURCE_CHANGED = "source_changed"
REASON_SOURCE_MISSING = "source_missing"

#: Сколько снимков держим. Снимки - это пути и метаданные, не байты, поэтому
#: потолок дешёвый. Последний плюс все обслуживаемые сохраняются всегда.
RETENTION = 4


class SourceChanged(Exception):
    """Файл изменился между объявлением и чтением."""


class SourceMissing(Exception):
    """Снимка, записи или файла нет."""


@dataclass
class _Snapshot:
    manifest: TransferManifest
    sources: dict[str, Path]
    handles: dict[int, int] = field(default_factory=dict)
    serving: bool = False

    def close(self) -> None:
        for descriptor in self.handles.values():
            try:
                os.close(descriptor)
            except OSError:  # pragma: no cover - закрытие дважды не должно ронять
                logger.debug("дескриптор уже закрыт")
        self.handles.clear()


class SnapshotRegistry:
    """Снимки по transfer_id. Живёт в GUI-потоке и только в нём."""

    def __init__(self) -> None:
        self._snapshots: dict[str, _Snapshot] = {}
        self._order: list[str] = []

    @property
    def transfer_ids(self) -> tuple[str, ...]:
        return tuple(self._order)

    @property
    def serving(self) -> frozenset[str]:
        return frozenset(key for key, snap in self._snapshots.items() if snap.serving)

    def publish(self, manifest: TransferManifest, sources: dict[str, Path]) -> None:
        """Запомнить обещанное. Файлы НЕ открываются - объявление ленивое."""
        self._snapshots[manifest.transfer_id] = _Snapshot(
            manifest=manifest, sources=dict(sources)
        )
        if manifest.transfer_id in self._order:
            self._order.remove(manifest.transfer_id)
        self._order.append(manifest.transfer_id)
        self._evict()

    def read(self, transfer_id: str, entry_index: int, offset: int, length: int) -> bytes:
        """Байты из удерживаемого дескриптора, со сверкой на каждом чтении."""
        snapshot = self._snapshots.get(transfer_id)
        if snapshot is None:
            raise SourceMissing(f"снимок {transfer_id!r} неизвестен или вытеснен")
        if not 0 <= entry_index < len(snapshot.manifest.entries):
            raise SourceMissing(f"записи {entry_index} нет в манифесте")
        entry = snapshot.manifest.entries[entry_index]
        if entry.kind != ENTRY_FILE:
            raise SourceMissing(f"запись {entry.path!r} - не файл")

        descriptor = snapshot.handles.get(entry_index)
        if descriptor is None:
            descriptor = self._open_and_verify(snapshot, entry_index, entry)
            snapshot.handles[entry_index] = descriptor
            snapshot.serving = True

        self._verify_unchanged(descriptor, entry)
        return self._read_at(descriptor, offset, length)

    def release(self, transfer_id: str) -> None:
        snapshot = self._snapshots.pop(transfer_id, None)
        if snapshot is not None:
            snapshot.close()
        if transfer_id in self._order:
            self._order.remove(transfer_id)

    def release_all(self) -> None:
        for transfer_id in list(self._order):
            self.release(transfer_id)

    # ------------------------------------------------------------------ внутреннее

    def _open_and_verify(self, snapshot: _Snapshot, entry_index: int, entry) -> int:
        path = snapshot.sources.get(entry.path)
        if path is None:
            raise SourceMissing(f"для записи {entry.path!r} нет источника")
        try:
            descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
        except OSError as error:
            # Полное имя пути в журнал не идёт (спека §15).
            raise SourceMissing(f"не удалось открыть {path.name!r}: {error.strerror}") from error
        try:
            self._verify_unchanged(descriptor, entry)
        except SourceChanged:
            os.close(descriptor)
            raise
        return descriptor

    def _verify_unchanged(self, descriptor: int, entry) -> None:
        """Сверка через НАШ дескриптор, не через путь."""
        stat_result = os.fstat(descriptor)
        if stat_result.st_size != entry.size:
            raise SourceChanged(f"размер {entry.path!r} изменился")
        if stat_result.st_mtime_ns != entry.mtime_ns:
            raise SourceChanged(f"время изменения {entry.path!r} изменилось")

    @staticmethod
    def _read_at(descriptor: int, offset: int, length: int) -> bytes:
        """Чтение по смещению через lseek плюс read.

        os.pread был бы уместнее, но на целевой платформе его нет: проверено
        на CPython 3.12.10 под Windows - hasattr(os, "pread") равно False.
        Ветка "pread, если он есть" была бы мёртвым кодом, а мёртвый код здесь
        уже находили мутационные прогоны.

        lseek плюс read безопасны без атомарности потому, что дескриптор
        принадлежит GUI-потоку и только ему: COM-поток до него не дотягивается
        вовсе (граница §7 спецификации, правило 3 boundary-теста).
        """
        os.lseek(descriptor, offset, os.SEEK_SET)
        return os.read(descriptor, length)

    def _evict(self) -> None:
        """Последний снимок и все обслуживаемые остаются всегда.

        Без этого смена буфера обмена во время передачи оборвала бы её - и
        сценарий 1 спецификации перестал бы держаться структурно.
        """
        while len(self._order) > RETENTION:
            for transfer_id in list(self._order):
                if transfer_id == self._order[-1]:
                    continue
                if self._snapshots[transfer_id].serving:
                    continue
                self.release(transfer_id)
                break
            else:
                # Всё оставшееся либо самое новое, либо обслуживается: вытеснять
                # нечего, и превышение потолка здесь законно.
                return


__all__ = [
    "REASON_SOURCE_CHANGED",
    "REASON_SOURCE_MISSING",
    "RETENTION",
    "SnapshotRegistry",
    "SourceChanged",
    "SourceMissing",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_source.py -v`
Expected: PASS, 17 passed

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/source.py configurator/tests/transfer/test_source.py
git commit -m "Hold one descriptor per entry and detect change through it

Measured on the target platform: while we hold the descriptor another
process can write and truncate but cannot delete or rename, and
os.fstat(fd) reports the change. So this is a held descriptor with change
detection, not an immutable snapshot - the weaker name is the true one,
and the residual limitation is named rather than hidden.

Retention keeps the newest snapshot and every serving one, which is what
makes a clipboard change during an active transfer structurally harmless
rather than merely discouraged.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 1.10: FileTransferService — the sending side

**Files:**
- Create: `configurator/src/duo_input/transfer/service.py`
- Test: `configurator/tests/transfer/test_service_sender.py`

**Interfaces:**
- Consumes: `SnapshotRegistry`, `SourceChanged`, `SourceMissing`,
  `REASON_SOURCE_CHANGED`, `REASON_SOURCE_MISSING` (Task 1.9); `scan` (Task 1.5);
  `MessageType`, `Message`, `MAX_FILE_CHUNK_BYTES` (Task 1.6);
  `CAPABILITY_FILES` (Task 1.7).
- Produces:
  - `REASON_BAD_REQUEST = "bad_request"`
  - `FileTransferService(QObject)` with
    `.attach_link(link) -> None`, `.detach_link() -> None`,
    `.set_peer_capabilities(capabilities: frozenset[str]) -> None`,
    `.handle_message(message: Message) -> None`,
    `.offer_local_files(paths: Sequence[Path]) -> str | None` returning the
    `transfer_id` or `None` when nothing was offered,
    `.snapshots -> SnapshotRegistry`
  - signals `offer_sent = Signal(str)`, `send_failed = Signal(str)`

The sending side is built first because it can be tested with nothing but a fake
link — no COM, no pipe, no threads.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_service_sender.py
"""Отправляющая сторона: отвечает на запросы и никогда не толкает сама.

Ни одного FILE_CHUNK не возникает без FILE_READ. Это и есть backpressure -
не правило, которое надо соблюдать, а невозможность.
"""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QObject, Signal

from duo_input.clipboard.wire import (
    CAPABILITY_CLIPBOARD,
    CAPABILITY_FILES,
    MAX_FILE_CHUNK_BYTES,
    Message,
    MessageType,
)
from duo_input.transfer.service import FileTransferService


class _FakeLink(QObject):
    message_received = Signal(object)
    disconnected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)

    def close(self) -> None: ...


@pytest.fixture
def sender(qapp):
    service = FileTransferService()
    link = _FakeLink()
    service.attach_link(link)
    service.set_peer_capabilities(frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES}))
    return service, link


def _sent(link, kind):
    return [message for message in link.sent if message.type is kind]


def test_copying_a_file_sends_one_offer_describing_it(sender, tmp_path):
    service, link = sender
    source = tmp_path / "notes.txt"
    source.write_bytes(b"hello")

    transfer_id = service.offer_local_files([source])

    [offer] = _sent(link, MessageType.FILE_OFFER)
    assert offer.header["transfer_id"] == transfer_id
    assert [entry["path"] for entry in offer.header["entries"]] == ["notes.txt"]
    assert offer.header["total_bytes"] == 5


def test_the_offer_carries_no_bytes_at_all(sender, tmp_path):
    service, link = sender
    source = tmp_path / "secret.bin"
    source.write_bytes(b"CLASSIFIED")

    service.offer_local_files([source])

    [offer] = _sent(link, MessageType.FILE_OFFER)
    assert offer.blob == b""
    assert b"CLASSIFIED" not in repr(offer.header).encode()


def test_nothing_is_offered_to_a_peer_that_does_not_advertise_files(qapp, tmp_path):
    # Старый клиент, получив FILE_OFFER, бросил бы WireError и оборвал бы
    # соединение вместе с буфером обмена.
    service = FileTransferService()
    link = _FakeLink()
    service.attach_link(link)
    service.set_peer_capabilities(frozenset({CAPABILITY_CLIPBOARD}))
    source = tmp_path / "a.txt"
    source.write_bytes(b"x")

    assert service.offer_local_files([source]) is None
    assert link.sent == []


def test_nothing_is_offered_when_there_is_no_link(qapp, tmp_path):
    service = FileTransferService()
    source = tmp_path / "a.txt"
    source.write_bytes(b"x")

    assert service.offer_local_files([source]) is None


def test_a_read_request_is_answered_with_exactly_those_bytes(sender, tmp_path):
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])

    service.handle_message(
        Message(
            MessageType.FILE_READ,
            {"transfer_id": transfer_id, "entry_index": 0, "offset": 3, "length": 4},
            b"",
        )
    )

    [chunk] = _sent(link, MessageType.FILE_CHUNK)
    assert chunk.blob == b"3456"
    assert chunk.header == {"transfer_id": transfer_id, "entry_index": 0, "offset": 3}


def test_no_chunk_is_ever_sent_without_a_request(sender, tmp_path):
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"x" * 4096)

    service.offer_local_files([source])

    assert _sent(link, MessageType.FILE_CHUNK) == [], (
        "чанк уехал без запроса - быстрый отправитель смог бы съесть память получателя"
    )


def test_a_request_longer_than_the_chunk_ceiling_is_refused(sender, tmp_path):
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"x" * 16)
    transfer_id = service.offer_local_files([source])

    service.handle_message(
        Message(
            MessageType.FILE_READ,
            {
                "transfer_id": transfer_id,
                "entry_index": 0,
                "offset": 0,
                "length": MAX_FILE_CHUNK_BYTES + 1,
            },
            b"",
        )
    )

    assert _sent(link, MessageType.FILE_CHUNK) == []
    [error] = _sent(link, MessageType.FILE_ERROR)
    assert error.header["reason"] == "bad_request"


@pytest.mark.parametrize(
    "header",
    [
        {"transfer_id": "t", "entry_index": 0, "offset": -1, "length": 4},
        {"transfer_id": "t", "entry_index": 0, "offset": 0, "length": 0},
        {"transfer_id": "t", "entry_index": 0, "offset": 0, "length": -4},
        {"transfer_id": "t", "entry_index": -1, "offset": 0, "length": 4},
        {"transfer_id": "t", "entry_index": 0, "offset": True, "length": 4},
        {"transfer_id": "t", "entry_index": 0, "length": 4},
    ],
)
def test_a_malformed_request_is_refused_without_reading_anything(sender, header):
    service, link = sender

    service.handle_message(Message(MessageType.FILE_READ, header, b""))

    assert _sent(link, MessageType.FILE_CHUNK) == []
    assert _sent(link, MessageType.FILE_ERROR)


def test_a_request_for_an_unknown_transfer_is_answered_with_source_missing(sender):
    service, link = sender

    service.handle_message(
        Message(
            MessageType.FILE_READ,
            {"transfer_id": "never-offered", "entry_index": 0, "offset": 0, "length": 4},
            b"",
        )
    )

    [error] = _sent(link, MessageType.FILE_ERROR)
    assert error.header["reason"] == "source_missing"


def test_a_source_changed_under_us_is_answered_with_source_changed(sender, tmp_path):
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])
    service.handle_message(
        Message(
            MessageType.FILE_READ,
            {"transfer_id": transfer_id, "entry_index": 0, "offset": 0, "length": 2},
            b"",
        )
    )
    with open(source, "r+b") as handle:
        handle.write(b"X")

    service.handle_message(
        Message(
            MessageType.FILE_READ,
            {"transfer_id": transfer_id, "entry_index": 0, "offset": 2, "length": 2},
            b"",
        )
    )

    [error] = _sent(link, MessageType.FILE_ERROR)
    assert error.header["reason"] == "source_changed"


def test_a_second_copy_gets_its_own_transfer_id(sender, tmp_path):
    service, link = sender
    first_file = tmp_path / "a.bin"
    first_file.write_bytes(b"a")
    second_file = tmp_path / "b.bin"
    second_file.write_bytes(b"b")

    first = service.offer_local_files([first_file])
    second = service.offer_local_files([second_file])

    assert first != second


def test_a_transfer_end_releases_the_snapshot_and_its_descriptor(sender, tmp_path):
    service, _link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])
    service.handle_message(
        Message(
            MessageType.FILE_READ,
            {"transfer_id": transfer_id, "entry_index": 0, "offset": 0, "length": 2},
            b"",
        )
    )

    service.handle_message(
        Message(
            MessageType.TRANSFER_END,
            {"transfer_id": transfer_id, "session_id": "s-1", "status": "completed"},
            b"",
        )
    )

    assert transfer_id not in service.snapshots.transfer_ids
    os.remove(source)  # дескриптор закрыт, значит удаление проходит


def test_a_copy_during_an_active_transfer_does_not_release_the_earlier_snapshot(
    sender, tmp_path
):
    # Сценарий 1 спецификации, на стороне отправителя.
    service, _link = sender
    first_file = tmp_path / "a.bin"
    first_file.write_bytes(b"0123456789")
    second_file = tmp_path / "b.bin"
    second_file.write_bytes(b"x")
    first = service.offer_local_files([first_file])
    service.handle_message(
        Message(
            MessageType.FILE_READ,
            {"transfer_id": first, "entry_index": 0, "offset": 0, "length": 2},
            b"",
        )
    )

    service.offer_local_files([second_file])

    assert first in service.snapshots.transfer_ids
    assert first in service.snapshots.serving


def test_losing_the_link_releases_every_snapshot(sender, tmp_path):
    service, link = sender
    source = tmp_path / "a.bin"
    source.write_bytes(b"0123456789")
    transfer_id = service.offer_local_files([source])
    service.handle_message(
        Message(
            MessageType.FILE_READ,
            {"transfer_id": transfer_id, "entry_index": 0, "offset": 0, "length": 2},
            b"",
        )
    )

    link.disconnected.emit("соединение закрыто")

    assert service.snapshots.transfer_ids == ()
    os.remove(source)


def test_an_unsafe_source_name_is_not_offered_and_is_reported(sender, tmp_path):
    service, link = sender
    source = tmp_path / "nul.txt"
    source.write_bytes(b"x")
    failures: list[str] = []
    service.send_failed.connect(failures.append)

    assert service.offer_local_files([source]) is None
    assert link.sent == []
    assert failures
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_service_sender.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duo_input.transfer.service'`

- [ ] **Step 3: Write minimal implementation**

```python
# configurator/src/duo_input/transfer/service.py
"""Правила передачи файлов. Ни сокетов, ни COM, ни Win32.

Отдельный QObject, а не расширение ClipboardService, и причина конкретная:
ClipboardService._fetch крутит вложенный QEventLoop в GUI-потоке, и файловый
конечный автомат внутри этого цикла получал бы свои сообщения реентрантно.
Здесь вложенных циклов нет вовсе.

Отправитель НИКОГДА не толкает данные сам - только отвечает на FILE_READ.
Поэтому быстрый отправитель структурно не может съесть память медленного
получателя: это не правило, которое надо соблюдать, а невозможность.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from pathlib import Path

from PySide6.QtCore import QObject, Signal

from ..clipboard.wire import CAPABILITY_FILES, MAX_FILE_CHUNK_BYTES, Message, MessageType
from .paths import UnsafePath
from .scanner import scan
from .source import (
    REASON_SOURCE_CHANGED,
    REASON_SOURCE_MISSING,
    SnapshotRegistry,
    SourceChanged,
    SourceMissing,
)

logger = logging.getLogger(__name__)

REASON_BAD_REQUEST = "bad_request"

#: DROPEFFECT_COPY. Ctrl+X на источнике сюда не доходит: удаление файлов на
#: другой машине по сети необратимо и отложено в отдельный milestone.
DROP_EFFECT_COPY = 1


def _index(header: dict, key: str) -> int:
    """Неотрицательное целое из заголовка. bool исключён - как в offer.py."""
    value = header.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{key} должна быть int")
    if value < 0:
        raise ValueError(f"{key} не может быть отрицательной")
    return value


class FileTransferService(QObject):
    """Обе стороны передачи. В этой задаче реализована отправляющая."""

    offer_sent = Signal(str)
    send_failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._link = None
        self._peer_capabilities: frozenset[str] = frozenset()
        self._snapshots = SnapshotRegistry()

    @property
    def snapshots(self) -> SnapshotRegistry:
        return self._snapshots

    @property
    def peer_supports_files(self) -> bool:
        return CAPABILITY_FILES in self._peer_capabilities

    # ------------------------------------------------------------------ связь

    def attach_link(self, link) -> None:
        self._link = link
        link.disconnected.connect(self._on_link_lost)

    def detach_link(self) -> None:
        if self._link is None:
            return
        self._link = None
        self._peer_capabilities = frozenset()
        self._snapshots.release_all()

    def set_peer_capabilities(self, capabilities: frozenset[str]) -> None:
        self._peer_capabilities = frozenset(capabilities)

    def _on_link_lost(self, reason: str) -> None:
        logger.info("передача файлов: связь потеряна (%s)", reason)
        self._link = None
        self._peer_capabilities = frozenset()
        self._snapshots.release_all()

    def _send(self, message: Message) -> None:
        if self._link is not None:
            self._link.send(message)

    # ------------------------------------------------------------------ отправитель

    def offer_local_files(self, paths: Sequence[Path]) -> str | None:
        """Объявить скопированное. Возвращает transfer_id либо None."""
        if self._link is None or not self.peer_supports_files:
            # Старый пир, получив FILE_OFFER, бросил бы WireError и оборвал
            # бы соединение вместе с буфером обмена.
            return None
        if not paths:
            return None

        transfer_id = uuid.uuid4().hex
        try:
            manifest, sources = scan(paths, transfer_id, DROP_EFFECT_COPY)
        except UnsafePath as error:
            # В журнал - причина, но не полный путь (спека §15).
            logger.warning("файлы не объявлены: %s", error)
            self.send_failed.emit(str(error))
            return None
        except OSError as error:
            logger.warning("файлы не объявлены: %s", error.strerror)
            self.send_failed.emit(str(error))
            return None

        self._snapshots.publish(manifest, sources)
        self._send(Message(MessageType.FILE_OFFER, manifest.to_dict(), b""))
        self.offer_sent.emit(transfer_id)
        return transfer_id

    def handle_message(self, message: Message) -> None:
        if message.type is MessageType.FILE_READ:
            self._answer_read(message)
        elif message.type is MessageType.TRANSFER_END:
            self._on_transfer_end(message)

    def _answer_read(self, message: Message) -> None:
        header = message.header
        try:
            transfer_id = header["transfer_id"]
            if not isinstance(transfer_id, str):
                raise ValueError("transfer_id должна быть str")
            entry_index = _index(header, "entry_index")
            offset = _index(header, "offset")
            length = _index(header, "length")
            if length == 0:
                raise ValueError("length не может быть нулевой")
            if length > MAX_FILE_CHUNK_BYTES:
                raise ValueError(f"length больше потолка {MAX_FILE_CHUNK_BYTES}")
        except (KeyError, ValueError) as error:
            logger.warning("запрос чтения отвергнут: %s", error)
            self._send_error(header, REASON_BAD_REQUEST)
            return

        try:
            payload = self._snapshots.read(transfer_id, entry_index, offset, length)
        except SourceChanged as error:
            logger.warning("источник изменился: %s", error)
            self._send_error(header, REASON_SOURCE_CHANGED)
            return
        except (SourceMissing, OSError) as error:
            logger.warning("источник недоступен: %s", error)
            self._send_error(header, REASON_SOURCE_MISSING)
            return

        self._send(
            Message(
                MessageType.FILE_CHUNK,
                {"transfer_id": transfer_id, "entry_index": entry_index, "offset": offset},
                payload,
            )
        )

    def _send_error(self, header: dict, reason: str) -> None:
        self._send(
            Message(
                MessageType.FILE_ERROR,
                {
                    "transfer_id": header.get("transfer_id", ""),
                    "entry_index": header.get("entry_index", -1),
                    "offset": header.get("offset", -1),
                    "reason": reason,
                },
                b"",
            )
        )

    def _on_transfer_end(self, message: Message) -> None:
        transfer_id = message.header.get("transfer_id")
        if isinstance(transfer_id, str):
            self._snapshots.release(transfer_id)


__all__ = ["DROP_EFFECT_COPY", "REASON_BAD_REQUEST", "FileTransferService"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_service_sender.py -v`
Expected: PASS, 21 passed (parametrised cases counted individually)

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/service.py configurator/tests/transfer/test_service_sender.py
git commit -m "Answer read requests and never push a chunk unasked

The sending side is a separate QObject rather than an extension of
ClipboardService, because that class runs a nested QEventLoop in the GUI
thread and a file state machine inside it would receive its own messages
reentrantly.

A fast sender structurally cannot exhaust a slow receiver's memory: there
is no code path that emits FILE_CHUNK without a FILE_READ, and a test
asserts that.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 1.11: FileTransferService — the receiving side and its state machine

**Files:**
- Modify: `configurator/src/duo_input/transfer/service.py`
- Test: `configurator/tests/transfer/test_service_receiver.py`

**Interfaces:**
- Consumes: everything from Task 1.10; `ChunkPipe`, `PipeClosed`,
  `PipeOverflow` (Task 1.8); `TransferManifest` (Task 1.1);
  `sanitize_manifest`, `UnsafePath` (Task 1.3).
- Produces, added to `FileTransferService`:
  - `class TransferState(StrEnum)`: `IDLE`, `OFFERED`, `TRANSFERRING`,
    `COMPLETED`, `CANCELLED`, `DISCONNECTED`, `FAILED`
  - `.state -> TransferState`, `.offered_manifest -> TransferManifest | None`
  - `.open_pipe(transfer_id: str, entry_index: int) -> ChunkPipe`
  - `.request_read(transfer_id: str, entry_index: int, offset: int, length: int) -> None`
    — safe to invoke from another thread via `QMetaObject.invokeMethod`
  - `.close_pipe(transfer_id: str, entry_index: int, reason: str | None) -> None`
  - `.finish_session(status: str) -> None`
  - signals `offer_received = Signal(object)`, `transfer_started = Signal(object)`,
    `transfer_progress = Signal(int, int)`, `transfer_completed = Signal()`,
    `transfer_failed = Signal(str)`, `transfer_cancelled = Signal()`

One outstanding read per pipe, enforced here. `request_read` refuses a second
request for a pipe that has one in flight.

**The completion signal is whatever Task 0.5 recorded.** If that record named
`EndOperation` authoritative, `finish_session` is called from the data object's
`EndOperation`; if it named something else, wire that instead. Do not re-derive
it from this plan.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_service_receiver.py
"""Принимающая сторона: автомат, окно в один запрос, и мгновенная отмена."""

from __future__ import annotations

import os

import pytest
from PySide6.QtCore import QObject, Signal

from duo_input.clipboard.wire import (
    CAPABILITY_CLIPBOARD,
    CAPABILITY_FILES,
    Message,
    MessageType,
)
from duo_input.transfer.model import ENTRY_DIRECTORY, ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.pipe import PipeClosed
from duo_input.transfer.service import FileTransferService, TransferState


class _FakeLink(QObject):
    message_received = Signal(object)
    disconnected = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.sent: list[Message] = []

    def send(self, message: Message) -> None:
        self.sent.append(message)

    def close(self) -> None: ...


def _offer(transfer_id="t-1", size=10):
    return TransferManifest(
        transfer_id=transfer_id,
        entries=(
            TransferEntry(path="Photos", kind=ENTRY_DIRECTORY, size=0, mtime_ns=1),
            TransferEntry(path="Photos/a.bin", kind=ENTRY_FILE, size=size, mtime_ns=2),
        ),
    )


@pytest.fixture
def receiver(qapp):
    service = FileTransferService()
    link = _FakeLink()
    service.attach_link(link)
    service.set_peer_capabilities(frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES}))
    return service, link


def _deliver_offer(service, manifest):
    service.handle_message(Message(MessageType.FILE_OFFER, manifest.to_dict(), b""))


def _sent(link, kind):
    return [message for message in link.sent if message.type is kind]


def test_a_fresh_service_is_idle():
    service = FileTransferService()

    assert service.state is TransferState.IDLE


def test_a_valid_offer_moves_to_offered_and_transfers_nothing(receiver):
    service, link = receiver
    received = []
    service.offer_received.connect(received.append)

    _deliver_offer(service, _offer())

    assert service.state is TransferState.OFFERED
    assert received[0].transfer_id == "t-1"
    assert _sent(link, MessageType.FILE_READ) == [], "объявление не должно ничего качать"


def test_an_offer_with_an_unsafe_path_is_refused_and_leaves_us_idle(receiver):
    service, _link = receiver
    manifest = TransferManifest(
        transfer_id="t-evil",
        entries=(TransferEntry(path="..\\evil.exe", kind=ENTRY_FILE, size=1, mtime_ns=1),),
    )

    _deliver_offer(service, manifest)

    assert service.state is TransferState.IDLE
    assert service.offered_manifest is None


def test_a_malformed_offer_is_refused_without_raising_inside_the_slot(receiver):
    service, _link = receiver

    service.handle_message(Message(MessageType.FILE_OFFER, {"entries": "nope"}, b""))

    assert service.state is TransferState.IDLE


def test_opening_a_pipe_moves_to_transferring_and_announces_the_session(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    started = []
    service.transfer_started.connect(started.append)

    service.open_pipe("t-1", 1)

    assert service.state is TransferState.TRANSFERRING
    assert started[0].transfer_id == "t-1"
    [begin] = _sent(link, MessageType.TRANSFER_BEGIN)
    assert begin.header["transfer_id"] == "t-1"


def test_requesting_a_read_sends_exactly_one_file_read(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    service.open_pipe("t-1", 1)

    service.request_read("t-1", 1, 0, 4)

    [read] = _sent(link, MessageType.FILE_READ)
    assert read.header == {
        "transfer_id": "t-1",
        "entry_index": 1,
        "offset": 0,
        "length": 4,
    }


def test_a_second_request_while_one_is_in_flight_is_refused(receiver):
    # Окно в один запрос - это correctness baseline фазы 1. Второй запрос
    # означал бы предвыборку, а с ней Seek потребовал бы инвалидации.
    service, link = receiver
    _deliver_offer(service, _offer())
    service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 0, 4)

    service.request_read("t-1", 1, 4, 4)

    assert len(_sent(link, MessageType.FILE_READ)) == 1


def test_a_chunk_lands_in_the_pipe_and_clears_the_in_flight_slot(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 0, 4)

    service.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {"transfer_id": "t-1", "entry_index": 1, "offset": 0},
            b"0123",
        )
    )

    assert pipe.take(4) == b"0123"
    service.request_read("t-1", 1, 4, 4)
    assert len(_sent(link, MessageType.FILE_READ)) == 2


def test_a_chunk_for_an_offset_we_did_not_request_is_dropped(receiver):
    # Без этой проверки устаревший чанк после Seek попал бы в поток, и
    # Проводник записал бы байты не туда.
    service, _link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 0, 4)

    service.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {"transfer_id": "t-1", "entry_index": 1, "offset": 999},
            b"stale",
        )
    )

    assert pipe.take(10) == b""


def test_a_chunk_for_another_transfer_is_dropped(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 0, 4)

    service.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {"transfer_id": "other", "entry_index": 1, "offset": 0},
            b"wrong",
        )
    )

    assert pipe.take(10) == b""


def test_an_empty_chunk_finishes_the_pipe_rather_than_hanging(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    service.request_read("t-1", 1, 10, 4)

    service.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {"transfer_id": "t-1", "entry_index": 1, "offset": 10},
            b"",
        )
    )

    assert pipe.finished is True


def test_progress_counts_the_bytes_that_actually_arrived(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer(size=10))
    service.open_pipe("t-1", 1)
    seen: list[tuple[int, int]] = []
    service.transfer_progress.connect(lambda done, total: seen.append((done, total)))

    service.request_read("t-1", 1, 0, 4)
    service.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {"transfer_id": "t-1", "entry_index": 1, "offset": 0},
            b"0123",
        )
    )

    assert seen == [(4, 10)]


def test_a_file_error_fails_the_session_and_closes_the_pipe(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)
    failures: list[str] = []
    service.transfer_failed.connect(failures.append)

    service.handle_message(
        Message(
            MessageType.FILE_ERROR,
            {
                "transfer_id": "t-1",
                "entry_index": 1,
                "offset": 0,
                "reason": "source_changed",
            },
            b"",
        )
    )

    assert service.state is TransferState.FAILED
    assert failures == ["source_changed"]
    with pytest.raises(PipeClosed):
        pipe.take(4)


def test_losing_the_link_moves_to_disconnected_and_wakes_every_pipe(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)

    link.disconnected.emit("соединение закрыто")

    assert service.state is TransferState.DISCONNECTED
    with pytest.raises(PipeClosed):
        pipe.take(4)


def test_cancelling_closes_the_pipe_and_reports_the_status_to_the_sender(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    pipe = service.open_pipe("t-1", 1)

    service.finish_session("cancelled")

    assert service.state is TransferState.CANCELLED
    with pytest.raises(PipeClosed):
        pipe.take(4)
    [end] = _sent(link, MessageType.TRANSFER_END)
    assert end.header["status"] == "cancelled"


def test_completing_reports_completed_exactly_once(receiver):
    service, link = receiver
    _deliver_offer(service, _offer())
    service.open_pipe("t-1", 1)
    completions: list[int] = []
    service.transfer_completed.connect(lambda: completions.append(1))

    service.finish_session("completed")
    service.finish_session("completed")

    assert completions == [1], "второй вызов завершил сессию повторно"
    assert len(_sent(link, MessageType.TRANSFER_END)) == 1


def test_a_newer_offer_replaces_an_idle_one(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer(transfer_id="first"))

    _deliver_offer(service, _offer(transfer_id="second"))

    assert service.offered_manifest.transfer_id == "second"


def test_a_newer_offer_does_not_interrupt_an_active_transfer(receiver):
    # Сценарий 1 спецификации, на стороне получателя.
    service, _link = receiver
    _deliver_offer(service, _offer(transfer_id="first"))
    pipe = service.open_pipe("first", 1)

    _deliver_offer(service, _offer(transfer_id="second"))

    assert service.state is TransferState.TRANSFERRING
    service.request_read("first", 1, 0, 4)
    service.handle_message(
        Message(
            MessageType.FILE_CHUNK,
            {"transfer_id": "first", "entry_index": 1, "offset": 0},
            b"abcd",
        )
    )
    assert pipe.take(4) == b"abcd", "новое объявление оборвало идущую передачу"


def test_opening_a_pipe_for_a_directory_entry_is_refused(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer())

    with pytest.raises(ValueError):
        service.open_pipe("t-1", 0)


def test_opening_a_pipe_for_an_unknown_transfer_is_refused(receiver):
    service, _link = receiver
    _deliver_offer(service, _offer())

    with pytest.raises(ValueError):
        service.open_pipe("no-such", 1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_service_receiver.py -v`
Expected: FAIL — `ImportError: cannot import name 'TransferState'`

- [ ] **Step 3: Write minimal implementation**

Add to `configurator/src/duo_input/transfer/service.py`:

```python
from enum import StrEnum

from PySide6.QtCore import Slot

from .model import ENTRY_FILE, TransferManifest
from .paths import sanitize_manifest
from .pipe import ChunkPipe, PipeOverflow


class TransferState(StrEnum):
    IDLE = "idle"
    OFFERED = "offered"
    TRANSFERRING = "transferring"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


#: Активные состояния - те, в которых новое объявление НЕ имеет права вытеснить
#: то, что уже отдано Проводнику (сценарий 1 спецификации).
_ACTIVE = frozenset({TransferState.TRANSFERRING})
```

Extend `FileTransferService.__init__`:

```python
        self._state = TransferState.IDLE
        self._offered: TransferManifest | None = None
        self._session_id: str | None = None
        self._active_transfer_id: str | None = None
        #: (transfer_id, entry_index) -> ChunkPipe
        self._pipes: dict[tuple[str, int], ChunkPipe] = {}
        #: (transfer_id, entry_index) -> ожидаемое смещение, либо отсутствует
        self._in_flight: dict[tuple[str, int], int] = {}
        self._received_bytes = 0
```

Add the receiving half:

```python
    @property
    def state(self) -> TransferState:
        return self._state

    @property
    def offered_manifest(self) -> TransferManifest | None:
        return self._offered

    # ------------------------------------------------------------------ получатель

    def _on_remote_offer(self, message: Message) -> None:
        try:
            manifest = sanitize_manifest(TransferManifest.from_dict(message.header))
        except (UnsafePath, ValueError) as error:
            # Ни падения внутри слота Qt, ни частичного дерева: объявление
            # отвергается целиком, и это записано в журнал.
            logger.warning("объявление файлов отвергнуто: %s", error)
            return
        self._offered = manifest
        if self._state not in _ACTIVE:
            self._state = TransferState.OFFERED
        self.offer_received.emit(manifest)

    def open_pipe(self, transfer_id: str, entry_index: int) -> ChunkPipe:
        """Проводник запросил содержимое записи - завести под неё очередь."""
        manifest = self._offered
        if manifest is None or manifest.transfer_id != transfer_id:
            raise ValueError(f"объявление {transfer_id!r} неизвестно")
        if not 0 <= entry_index < len(manifest.entries):
            raise ValueError(f"записи {entry_index} нет в объявлении")
        if manifest.entries[entry_index].kind != ENTRY_FILE:
            raise ValueError("у каталога нет содержимого")

        key = (transfer_id, entry_index)
        pipe = ChunkPipe(capacity_chunks=1)
        self._pipes[key] = pipe

        if self._state is not TransferState.TRANSFERRING:
            self._state = TransferState.TRANSFERRING
            self._session_id = uuid.uuid4().hex
            self._active_transfer_id = transfer_id
            self._received_bytes = 0
            self._send(
                Message(
                    MessageType.TRANSFER_BEGIN,
                    {"transfer_id": transfer_id, "session_id": self._session_id},
                    b"",
                )
            )
            self.transfer_started.emit(manifest)
        return pipe

    @Slot(str, int, int, int)
    def request_read(self, transfer_id: str, entry_index: int, offset: int, length: int) -> None:
        """Запросить байты. Вызывается из COM-потока через invokeMethod.

        Слот, а не обычный метод, именно поэтому: QMetaObject.invokeMethod с
        QueuedConnection - единственный способ, которым COM-поток вправе
        тронуть этот объект, и он требует слота.
        """
        key = (transfer_id, entry_index)
        if key not in self._pipes:
            return
        if key in self._in_flight:
            # Окно в один запрос. Второй запрос означал бы предвыборку, а с
            # ней Seek потребовал бы инвалидации устаревших чанков - пласт
            # состояния, который фаза 1 не заводит (спека §8).
            return
        self._in_flight[key] = offset
        self._send(
            Message(
                MessageType.FILE_READ,
                {
                    "transfer_id": transfer_id,
                    "entry_index": entry_index,
                    "offset": offset,
                    "length": min(length, MAX_FILE_CHUNK_BYTES),
                },
                b"",
            )
        )

    def _on_chunk(self, message: Message) -> None:
        transfer_id = message.header.get("transfer_id")
        entry_index = message.header.get("entry_index")
        offset = message.header.get("offset")
        if not isinstance(transfer_id, str) or not isinstance(entry_index, int):
            return
        key = (transfer_id, entry_index)
        pipe = self._pipes.get(key)
        if pipe is None:
            return
        if self._in_flight.get(key) != offset:
            # Чанк, которого мы не просили (или просили и передумали после
            # Seek). Отдать его Проводнику значило бы записать байты не туда.
            logger.debug("чанк на смещение %r отброшен как неожидаемый", offset)
            return
        del self._in_flight[key]

        if not message.blob:
            pipe.finish()
            return
        try:
            pipe.push(message.blob)
        except PipeOverflow:
            logger.warning("очередь переполнена - чанк отброшен")
            return
        except PipeClosed:
            return
        self._received_bytes += len(message.blob)
        total = self._offered.total_bytes if self._offered is not None else 0
        self.transfer_progress.emit(self._received_bytes, total)

    def _on_file_error(self, message: Message) -> None:
        reason = str(message.header.get("reason", "unknown"))
        self._close_all_pipes(reason)
        self._state = TransferState.FAILED
        self._send_transfer_end("failed")
        self.transfer_failed.emit(reason)

    def close_pipe(self, transfer_id: str, entry_index: int, reason: str | None = None) -> None:
        """Проводник отпустил ЭТОТ поток. Сессию это само по себе не завершает."""
        key = (transfer_id, entry_index)
        pipe = self._pipes.pop(key, None)
        self._in_flight.pop(key, None)
        if pipe is not None and reason is not None:
            pipe.close(reason)

    def finish_session(self, status: str) -> None:
        """Сессия окончена. Источник истины - тот, что записал спайк 1 (§9).

        Повторный вызов ничего не делает: Проводник вправе сообщить о
        завершении и через EndOperation, и через отпускание последнего потока,
        и двойное завершение не должно порождать два TRANSFER_END.
        """
        if self._state not in _ACTIVE:
            return
        self._close_all_pipes(status if status != "completed" else None)
        self._send_transfer_end(status)
        if status == "completed":
            self._state = TransferState.COMPLETED
            self.transfer_completed.emit()
        elif status == "cancelled":
            self._state = TransferState.CANCELLED
            self.transfer_cancelled.emit()
        else:
            self._state = TransferState.FAILED
            self.transfer_failed.emit(status)

    def _send_transfer_end(self, status: str) -> None:
        if self._active_transfer_id is None:
            return
        self._send(
            Message(
                MessageType.TRANSFER_END,
                {
                    "transfer_id": self._active_transfer_id,
                    "session_id": self._session_id or "",
                    "status": status,
                },
                b"",
            )
        )
        self._active_transfer_id = None
        self._session_id = None

    def _close_all_pipes(self, reason: str | None) -> None:
        for pipe in self._pipes.values():
            if reason is None:
                pipe.finish()
            else:
                pipe.close(reason)
        self._pipes.clear()
        self._in_flight.clear()
```

Add the signals to the class body:

```python
    offer_received = Signal(object)
    transfer_started = Signal(object)
    transfer_progress = Signal(int, int)
    transfer_completed = Signal()
    transfer_failed = Signal(str)
    transfer_cancelled = Signal()
```

Extend `handle_message`:

```python
        elif message.type is MessageType.FILE_OFFER:
            self._on_remote_offer(message)
        elif message.type is MessageType.FILE_CHUNK:
            self._on_chunk(message)
        elif message.type is MessageType.FILE_ERROR:
            self._on_file_error(message)
        elif message.type is MessageType.TRANSFER_BEGIN:
            # Информационное: отправителю оно говорит, что снимок обслуживается.
            pass
```

And extend `_on_link_lost` and `detach_link` to close every pipe and move the
state:

```python
        self._close_all_pipes("связь потеряна")
        self._state = TransferState.DISCONNECTED
```

Import `PipeClosed` alongside `ChunkPipe` and `PipeOverflow`. Extend `__all__`
with `"TransferState"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_service_receiver.py -v`
Expected: PASS, 20 passed

- [ ] **Step 5: Run both service suites together**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/transfer/service.py configurator/tests/transfer/test_service_receiver.py
git commit -m "Drive the receiving state machine with one read in flight

A second request for a pipe that already has one is refused, so there is
no prefetch to invalidate after a Seek - that state is Phase 3's to add,
and only if a measurement asks for it.

A chunk whose offset we did not request is dropped rather than pushed,
because handing it to Explorer would write bytes to the wrong place. A
newer offer cannot interrupt an active transfer, which is how scenario 1
holds structurally instead of by convention.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 1.12: carry local file paths out of the clipboard boundary

**Files:**
- Modify: `configurator/src/duo_input/clipboard/backend.py` (`ClipboardSnapshot`)
- Modify: `configurator/src/duo_input/clipboard/formats.py` (add `local_file_paths`)
- Modify: `configurator/src/duo_input/clipboard/windows_backend.py` (`snapshot_from`, `_take_snapshot`)
- Test: `configurator/tests/clipboard/test_formats.py` (append)
- Test: `configurator/tests/clipboard/test_windows_backend.py` (append)

**Interfaces:**
- Consumes: existing `ClipboardSnapshot`, `collect_payloads`, `snapshot_from`.
- Produces:
  - `formats.local_file_paths(mime_data) -> tuple[str, ...]`
  - `ClipboardSnapshot(payloads, file_paths=())` with `.file_paths: tuple[str, ...]`

`formats.py:35` already strips `file://` from `text/uri-list` on purpose, and it
must keep doing so — a `file://` URL from the other machine would be a broken
path here. This task adds a *separate* channel for the paths rather than
un-stripping the existing one.

The backend's `_take_snapshot` currently returns early on
`if not snapshot.payloads`, then retries three times. A file-only copy has no
payloads at all, so today it produces three retries and silence. That condition
is the actual change.

- [ ] **Step 1: Write the failing test**

```python
# append to configurator/tests/clipboard/test_formats.py
from duo_input.clipboard.formats import local_file_paths


def test_local_files_are_reported_separately_from_web_links(qapp, tmp_path):
    source = tmp_path / "notes.txt"
    source.write_bytes(b"x")
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(source)), QUrl("https://example.com/a")])

    assert local_file_paths(mime_data) == (str(source),)


def test_web_links_still_exclude_local_files_from_the_synced_uri_list(qapp, tmp_path):
    # formats.py:35 вырезает file:// намеренно, и должен продолжать: путь с
    # другой машины здесь был бы несуществующим.
    source = tmp_path / "notes.txt"
    source.write_bytes(b"x")
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(source)), QUrl("https://example.com/a")])

    assert collect_payloads(mime_data)["text/uri-list"] == b"https://example.com/a\r\n"


def test_a_clipboard_with_only_local_files_syncs_no_payload_at_all(qapp, tmp_path):
    source = tmp_path / "notes.txt"
    source.write_bytes(b"x")
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(source))])

    assert collect_payloads(mime_data) == {}
    assert local_file_paths(mime_data) == (str(source),)


def test_no_urls_means_no_local_files(qapp):
    mime_data = QMimeData()
    mime_data.setText("plain")

    assert local_file_paths(mime_data) == ()
```

```python
# append to configurator/tests/clipboard/test_windows_backend.py
def test_a_file_only_copy_produces_a_snapshot_carrying_the_paths(qapp, tmp_path):
    source = tmp_path / "notes.txt"
    source.write_bytes(b"x")
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(source))])

    snapshot = snapshot_from(mime_data)

    assert snapshot.payloads == {}
    assert snapshot.file_paths == (str(source),)


def test_a_file_only_copy_is_emitted_rather_than_retried_into_silence(qapp, tmp_path):
    # До этой правки _take_snapshot возвращался на `not snapshot.payloads`,
    # трижды пробовал заново и замолкал: копирование файла не порождало
    # ни одного события.
    source = tmp_path / "notes.txt"
    source.write_bytes(b"x")
    clipboard = qapp.clipboard()
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(source))])
    clipboard.setMimeData(mime_data)
    backend = WindowsClipboardBackend(clipboard)
    taken: list[object] = []
    backend.snapshot_taken.connect(taken.append)

    backend._take_snapshot()

    assert taken, "снимок с файлами и без payload не был объявлен вовсе"
    assert taken[0].file_paths == (str(source),)


def test_a_private_clipboard_reports_neither_payloads_nor_paths(qapp, tmp_path):
    source = tmp_path / "secret.txt"
    source.write_bytes(b"x")
    mime_data = QMimeData()
    mime_data.setUrls([QUrl.fromLocalFile(str(source))])
    mime_data.setData(PRIVATE_MARKERS[0], QByteArray(b"0"))

    snapshot = snapshot_from(mime_data)

    assert snapshot.payloads == {}
    assert snapshot.file_paths == (), (
        "маркер приватности обошёл путь файлов - менеджер паролей, "
        "копирующий файл, отправил бы его"
    )
```

Add whatever imports those two files lack (`QUrl`, `QByteArray`,
`collect_payloads`, `PRIVATE_MARKERS`, `WindowsClipboardBackend`,
`snapshot_from`) — read each file first and extend its existing import block.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard/test_formats.py configurator/tests/clipboard/test_windows_backend.py -v -k "local or file"`
Expected: FAIL — `ImportError: cannot import name 'local_file_paths'`

- [ ] **Step 3: Write minimal implementation**

In `formats.py`, after `web_uri_list`:

```python
def local_file_paths(mime_data: QMimeData) -> tuple[str, ...]:
    """Локальные пути скопированного, отдельным каналом от синхронизируемых форматов.

    Отдельным - и это не мелочь. web_uri_list() вырезает file:// намеренно и
    должен продолжать: путь, приехавший с другой машины, здесь указывал бы в
    пустоту. Поэтому пути идут своим каналом, а не возвращаются в
    text/uri-list.

    Порядок сохраняется тот, в котором их дал Проводник: пользователь выделял
    файлы в каком-то порядке, и менять его без причины незачем.
    """
    if not mime_data.hasUrls():
        return ()
    return tuple(
        url.toLocalFile() for url in mime_data.urls() if url.isLocalFile()
    )
```

Extend `__all__` with `"local_file_paths"`.

In `backend.py`, change `ClipboardSnapshot`:

```python
class ClipboardSnapshot:
    """Локальный снимок буфера: что скопировали на этой машине.

    ``file_paths`` пуст почти всегда - он непуст ровно тогда, когда
    скопировали файлы в Проводнике. Снимок может нести пути и НЕ нести ни
    одного payload: копирование файла не даёт синхронизируемых форматов
    вовсе, потому что file:// из text/uri-list вырезается намеренно.
    """

    def __init__(
        self, payloads: dict[str, bytes], file_paths: tuple[str, ...] = ()
    ) -> None:
        self.payloads = dict(payloads)
        self.file_paths = tuple(file_paths)

    def payload(self, mime: str) -> bytes | None:
        return self.payloads.get(mime)

    @property
    def is_empty(self) -> bool:
        """Нечего ни объявлять, ни передавать."""
        return not self.payloads and not self.file_paths
```

In `windows_backend.py`:

```python
def snapshot_from(mime_data) -> ClipboardSnapshot:
    """Взять из буфера то, что мы умеем синхронизировать и передавать.

    Маркер приватности гасит и payload, и пути: менеджер паролей, положивший
    в буфер файл, не должен отправить его на вторую машину.
    """
    if is_private(list(mime_data.formats())):
        return ClipboardSnapshot({})
    return ClipboardSnapshot(collect_payloads(mime_data), local_file_paths(mime_data))
```

and in `_take_snapshot`, replace the two `not snapshot.payloads` conditions with
`snapshot.is_empty`:

```python
    def _take_snapshot(self) -> None:
        snapshot = snapshot_from(self._clipboard.mimeData())
        # is_empty, а не `not payloads`: копирование файлов в Проводнике не
        # даёт ни одного синхронизируемого формата, поэтому прежнее условие
        # трижды перечитывало буфер и замолкало - копирование файла не
        # порождало ни одного события вовсе.
        if snapshot.is_empty and self._attempts < RETRY_LIMIT:
            self._attempts += 1
            self._debounce.start()
            return
        if snapshot.is_empty:
            return
        self._local = snapshot
        self.snapshot_taken.emit(snapshot)
```

Import `local_file_paths` in `windows_backend.py`.

- [ ] **Step 4: Run test to verify it passes, then the whole clipboard suite**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard -q`
Expected: PASS. `ClipboardService.on_local_snapshot` still returns early on
`not snapshot.payloads`, so a file-only snapshot produces no clipboard offer —
that is correct and no existing test should change.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/ configurator/tests/clipboard/
git commit -m "Report copied file paths on their own channel

formats.py strips file:// from the synced uri-list on purpose and keeps
doing so: a path from the other machine would point at nothing here. So
the paths get a separate channel rather than un-stripping that one.

The backend's retry condition was the real change. A file-only copy has
no synced payload at all, so the old `not payloads` check re-read the
clipboard three times and then went silent - copying a file produced no
event whatsoever.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 1.13: end to end over real TLS on loopback, without COM

**Files:**
- Create: `configurator/tests/transfer/test_end_to_end.py`

**Interfaces:**
- Consumes: `FileTransferService` (Tasks 1.10–1.11); `PeerLink`, `PeerListener`,
  `NodeIdentity`, `load_or_create` from `clipboard/`.
- Produces: nothing. This task adds no production code.

This proves the protocol over a real `QSslSocket` with real framing, driving the
receiver from a plain worker thread in place of Explorer. Every size and shape
from spec §19 is exercised here; Phase 3 replaces the worker thread with the
real COM stream.

- [ ] **Step 1: Write the test**

```python
# configurator/tests/transfer/test_end_to_end.py
"""Настоящий TLS на loopback, настоящее кадрирование, без COM.

Роль Проводника играет обычный рабочий поток, который дёргает ChunkPipe так
же, как это будет делать IStream: запросить, подождать, взять. Поэтому этот
файл проверяет протокол и мост, но не COM - COM приходит в фазе 3.
"""

from __future__ import annotations

import hashlib
import os
import threading

import pytest
from PySide6.QtCore import QMetaObject, Qt, Q_ARG

from duo_input.clipboard.coordinator import TCP_PORT
from duo_input.clipboard.wire import CAPABILITY_CLIPBOARD, CAPABILITY_FILES
from duo_input.clipboard.identity import load_or_create
from duo_input.clipboard.listener import PeerListener
from duo_input.clipboard.peer import PeerLink
from duo_input.transfer.model import ENTRY_FILE
from duo_input.transfer.pipe import PipeClosed
from duo_input.transfer.service import FileTransferService, TransferState

CAPS = frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES})


def deterministic_bytes(size: int, seed: int = 0) -> bytes:
    """Данные генерируются, а не хранятся: гигантских фикстур в репозитории нет."""
    return bytes((seed + index * 131) & 0xFF for index in range(size))


@pytest.fixture
def linked_pair(qtbot, tmp_path):
    """Два сервиса на настоящем TLS-соединении через loopback."""
    sender_identity = load_or_create(tmp_path / "sender")
    receiver_identity = load_or_create(tmp_path / "receiver")

    listener = PeerListener(receiver_identity)
    listener.expect(sender_identity.fingerprint)
    assert listener.listen(0), "не удалось занять порт на loopback"

    incoming: list[PeerLink] = []
    listener.link_ready.connect(incoming.append)

    outgoing = PeerLink(sender_identity)
    outgoing.connect_to("127.0.0.1", listener.port, receiver_identity.fingerprint)
    qtbot.waitUntil(lambda: incoming and outgoing.is_open, timeout=10000)

    sender = FileTransferService()
    sender.attach_link(outgoing)
    sender.set_peer_capabilities(CAPS)
    outgoing.message_received.connect(sender.handle_message)

    receiver = FileTransferService()
    receiver.attach_link(incoming[0])
    receiver.set_peer_capabilities(CAPS)
    incoming[0].message_received.connect(receiver.handle_message)

    yield sender, receiver

    outgoing.close()
    listener.stop()


def drain(qtbot, service, transfer_id, entry_index, total) -> bytes:
    """Проводник в миниатюре: запросить, подождать, взять - до конца файла.

    Работает в РАБОЧЕМ потоке, как настоящий IStream, и трогает сервис только
    через invokeMethod с QueuedConnection. Ни одного обращения к сокету отсюда.
    """
    pipe = service.open_pipe(transfer_id, entry_index)
    collected = bytearray()
    failure: list[BaseException] = []

    def worker() -> None:
        try:
            while len(collected) < total:
                chunk = pipe.take(65536)
                if chunk:
                    collected.extend(chunk)
                    continue
                QMetaObject.invokeMethod(
                    service,
                    "request_read",
                    Qt.ConnectionType.QueuedConnection,
                    Q_ARG(str, transfer_id),
                    Q_ARG(int, entry_index),
                    Q_ARG(int, len(collected)),
                    Q_ARG(int, 65536),
                )
                if not pipe.wait(timeout=10.0):
                    raise TimeoutError("чанк не пришёл за 10 с")
        except BaseException as error:  # noqa: BLE001 - переносим в основной поток
            failure.append(error)

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    qtbot.waitUntil(lambda: not thread.is_alive(), timeout=30000)
    thread.join(timeout=5.0)
    if failure:
        raise failure[0]
    return bytes(collected)


@pytest.mark.parametrize("size", [0, 1, 1024, 1024 * 1024])
def test_a_file_of_each_size_arrives_byte_for_byte(qtbot, linked_pair, tmp_path, size):
    sender, receiver = linked_pair
    payload = deterministic_bytes(size)
    source = tmp_path / "payload.bin"
    source.write_bytes(payload)

    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)

    assert drain(qtbot, receiver, transfer_id, 0, size) == payload


def test_a_hundred_megabyte_file_arrives_with_the_digest_it_promised(
    qtbot, linked_pair, tmp_path
):
    sender, receiver = linked_pair
    size = 100 * 1024 * 1024
    source = tmp_path / "big.bin"
    block = deterministic_bytes(1024 * 1024)
    with open(source, "wb") as handle:
        for _ in range(size // len(block)):
            handle.write(block)
    expected = hashlib.sha256(block * (size // len(block))).hexdigest()

    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)

    received = drain(qtbot, receiver, transfer_id, 0, size)

    assert hashlib.sha256(received).hexdigest() == expected


def test_a_nested_directory_arrives_with_its_structure(qtbot, linked_pair, tmp_path):
    sender, receiver = linked_pair
    folder = tmp_path / "Photos"
    (folder / "raw").mkdir(parents=True)
    (folder / "img1.jpg").write_bytes(b"first")
    (folder / "raw" / "img2.dng").write_bytes(b"second")

    sender.offer_local_files([folder])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)

    manifest = receiver.offered_manifest
    assert sorted(entry.path for entry in manifest.entries) == [
        "Photos",
        "Photos/img1.jpg",
        "Photos/raw",
        "Photos/raw/img2.dng",
    ]


def test_a_unicode_filename_survives_the_wire(qtbot, linked_pair, tmp_path):
    sender, receiver = linked_pair
    source = tmp_path / "Отчёт за квартал.txt"
    source.write_bytes(b"payload")

    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)

    assert receiver.offered_manifest.entries[0].path == "Отчёт за квартал.txt"
    assert drain(qtbot, receiver, transfer_id, 0, 7) == b"payload"


def test_nothing_is_transferred_until_a_pipe_is_opened(qtbot, linked_pair, tmp_path):
    sender, receiver = linked_pair
    source = tmp_path / "unread.bin"
    source.write_bytes(deterministic_bytes(4096))

    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)
    qtbot.wait(300)

    assert sender.snapshots.serving == frozenset(), (
        "отправитель начал обслуживать снимок до того, как получатель начал вставку"
    )
    assert transfer_id in sender.snapshots.transfer_ids


def test_a_disconnect_midway_wakes_the_blocked_reader(qtbot, linked_pair, tmp_path):
    sender, receiver = linked_pair
    source = tmp_path / "big.bin"
    source.write_bytes(deterministic_bytes(8 * 1024 * 1024))

    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)
    pipe = receiver.open_pipe(transfer_id, 0)
    receiver.request_read(transfer_id, 0, 0, 65536)
    qtbot.waitUntil(lambda: pipe.depth > 0, timeout=5000)

    receiver._on_link_lost("тест разорвал связь")

    assert receiver.state is TransferState.DISCONNECTED
    with pytest.raises(PipeClosed):
        pipe.take(1024)


def test_the_peak_queue_depth_never_exceeds_one_chunk(qtbot, linked_pair, tmp_path):
    # Потолок памяти при одном запросе в полёте - размер ОДНОГО чанка.
    sender, receiver = linked_pair
    size = 8 * 1024 * 1024
    source = tmp_path / "big.bin"
    source.write_bytes(deterministic_bytes(size))

    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)
    pipe = receiver.open_pipe(transfer_id, 0)
    drain_thread_result = drain(qtbot, receiver, transfer_id, 0, size)

    assert len(drain_thread_result) == size
    assert pipe.high_water <= 1, (
        f"глубина очереди доходила до {pipe.high_water} при окне 1"
    )


def test_a_source_deleted_before_the_paste_fails_the_session(qtbot, linked_pair, tmp_path):
    sender, receiver = linked_pair
    source = tmp_path / "vanishing.bin"
    source.write_bytes(deterministic_bytes(1024))
    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)
    os.remove(source)

    failures: list[str] = []
    receiver.transfer_failed.connect(failures.append)
    receiver.open_pipe(transfer_id, 0)
    receiver.request_read(transfer_id, 0, 0, 1024)
    qtbot.waitUntil(lambda: failures, timeout=5000)

    assert failures == ["source_missing"]
    assert receiver.state is TransferState.FAILED


def test_pasting_twice_transfers_the_same_snapshot_again(qtbot, linked_pair, tmp_path):
    sender, receiver = linked_pair
    payload = deterministic_bytes(4096)
    source = tmp_path / "twice.bin"
    source.write_bytes(payload)
    transfer_id = sender.offer_local_files([source])
    qtbot.waitUntil(lambda: receiver.offered_manifest is not None, timeout=5000)

    first = drain(qtbot, receiver, transfer_id, 0, 4096)
    receiver.finish_session("completed")
    qtbot.wait(100)
    receiver._state = TransferState.OFFERED  # Проводник снова просит содержимое
    second = drain(qtbot, receiver, transfer_id, 0, 4096)

    assert first == second == payload
```

- [ ] **Step 2: Run the test**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_end_to_end.py -v`
Expected: PASS, 13 passed. The 100 MiB case is slow — expect tens of seconds.

If `test_pasting_twice...` needs `receiver._state` poked directly, that is a
signal the public API is missing a "the shell asked again" entry point. Add
`FileTransferService.reopen_session()` that moves `COMPLETED` back to `OFFERED`,
and use it instead of touching `_state`. A test that reaches into a private
attribute is describing a gap in the interface.

- [ ] **Step 3: Run the full gate**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests tests -q`
Expected: PASS. This is the first point where the whole repository must be
green with the new subsystem in it.

- [ ] **Step 4: Commit**

```bash
git add configurator/tests/transfer/test_end_to_end.py
git commit -m "Prove the protocol over real TLS with Explorer's role faked

A plain worker thread drives ChunkPipe exactly as IStream will - request,
wait, take - and touches the service only through invokeMethod with a
queued connection, never the socket. So this covers the protocol and the
bridge while leaving COM to Phase 3.

Data is generated rather than stored: no large fixture enters the
repository, and the 100 MiB case is checked by digest.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 1.14: bound the socket buffers in PeerLink

**Files:**
- Modify: `configurator/src/duo_input/clipboard/peer.py` (`_wire_up`, `send`)
- Test: `configurator/tests/clipboard/test_peer_link.py` (append)

**Interfaces:**
- Consumes: existing `PeerLink`, `MAX_FRAME_BYTES`, `MAX_FILE_CHUNK_BYTES`.
- Produces:
  - `peer.READ_BUFFER_BYTES = MAX_FILE_CHUNK_BYTES * 4`
  - `peer.WRITE_HIGH_WATER_BYTES = MAX_FILE_CHUNK_BYTES * 4`
  - `PeerLink.bytes_to_write -> int`
  - `PeerLink.write_congested -> bool`
  - signal `PeerLink.congestion_changed = Signal(bool)`

Spec §2, fact 3: `send()` is an unconditional `socket.write()` and `QSslSocket`
buffers without limit, while `_on_ready_read` feeds `readAll()` into a
`FrameAssembler` that also grows without limit. One read in flight bounds what
*we* ask for, but it does not bound what a buggy or hostile peer sends: an
unrequested flood of `FILE_CHUNK` frames is dropped by
`FileTransferService._on_chunk` only **after** Qt has buffered it and the
assembler has built it.

`setReadBufferSize` is the knob that makes the receive side bounded regardless
of what arrives. This task is small, and it is the difference between a memory
bound we can prove and one that depends on the peer behaving.

- [ ] **Step 1: Write the failing test**

```python
# append to configurator/tests/clipboard/test_peer_link.py
from duo_input.clipboard.peer import READ_BUFFER_BYTES, WRITE_HIGH_WATER_BYTES


def test_the_read_buffer_is_bounded_so_a_flood_cannot_grow_it(qapp, tmp_path):
    # Один запрос в полёте ограничивает то, что просим МЫ. Он не ограничивает
    # то, что пришлёт сломанный или враждебный пир.
    identity = load_or_create(tmp_path)
    link = PeerLink(identity)
    socket = QSslSocket(link)

    link._wire_up(socket)

    assert socket.readBufferSize() == READ_BUFFER_BYTES
    assert socket.readBufferSize() != 0, (
        "нулевой readBufferSize означает 'без границы' - именно то, что "
        "этот тест существует чтобы запретить"
    )


def test_the_read_buffer_leaves_room_for_several_chunks_but_not_for_a_flood():
    assert READ_BUFFER_BYTES >= MAX_FILE_CHUNK_BYTES, (
        "буфер меньше одного чанка заставил бы Qt резать каждый кадр"
    )
    assert READ_BUFFER_BYTES < MAX_FRAME_BYTES


def test_a_link_with_no_socket_reports_no_pending_bytes(qapp, tmp_path):
    link = PeerLink(load_or_create(tmp_path))

    assert link.bytes_to_write == 0
    assert not link.write_congested


def test_congestion_is_reported_when_the_write_queue_passes_the_high_water(
    qapp, tmp_path, monkeypatch
):
    identity = load_or_create(tmp_path)
    link = PeerLink(identity)
    socket = QSslSocket(link)
    link._wire_up(socket)
    monkeypatch.setattr(
        type(socket), "bytesToWrite", lambda _self: WRITE_HIGH_WATER_BYTES + 1
    )

    assert link.write_congested


def test_the_congestion_signal_fires_only_when_the_state_actually_changes(
    qapp, tmp_path, monkeypatch
):
    identity = load_or_create(tmp_path)
    link = PeerLink(identity)
    socket = QSslSocket(link)
    link._wire_up(socket)
    seen: list[bool] = []
    link.congestion_changed.connect(seen.append)
    pending = [WRITE_HIGH_WATER_BYTES + 1]
    monkeypatch.setattr(type(socket), "bytesToWrite", lambda _self: pending[0])

    link._check_congestion()
    link._check_congestion()
    pending[0] = 0
    link._check_congestion()

    assert seen == [True, False], (
        "сигнал повторился при неизменившемся состоянии - подписчик получал бы "
        "поток одинаковых уведомлений вместо двух переходов"
    )
```

Add `MAX_FILE_CHUNK_BYTES`, `MAX_FRAME_BYTES`, `QSslSocket` and `load_or_create`
to that file's imports if absent.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard/test_peer_link.py -v -k "buffer or congest or pending"`
Expected: FAIL — `ImportError: cannot import name 'READ_BUFFER_BYTES'`

- [ ] **Step 3: Write minimal implementation**

In `configurator/src/duo_input/clipboard/peer.py`, below the imports:

```python
#: Сколько Qt разрешено держать непрочитанным в сокете.
#:
#: Ноль (по умолчанию) означает "без границы", и до передачи файлов это было
#: безвредно: 32 МиБ потолка кадра сам по себе был границей. С файлами - нет.
#: Один запрос в полёте ограничивает то, что просим МЫ, но не то, что
#: пришлёт сломанный или враждебный пир: поток незапрошенных FILE_CHUNK
#: отбрасывается в FileTransferService._on_chunk лишь ПОСЛЕ того, как Qt его
#: сложил, а FrameAssembler собрал.
#:
#: Четыре чанка, а не один: меньше одного заставило бы Qt резать каждый кадр,
#: и сборка шла бы по кусочкам без всякой пользы.
READ_BUFFER_BYTES = MAX_FILE_CHUNK_BYTES * 4

#: За этой отметкой очередь записи считается затором.
WRITE_HIGH_WATER_BYTES = MAX_FILE_CHUNK_BYTES * 4
```

Add to `PeerLink`:

```python
    congestion_changed = Signal(bool)
```

In `__init__`, add `self._congested = False`.

In `_wire_up`, after `socket.setParent(self)`:

```python
        socket.setReadBufferSize(READ_BUFFER_BYTES)
        socket.bytesWritten.connect(lambda _count: self._check_congestion())
```

And the accessors:

```python
    @property
    def bytes_to_write(self) -> int:
        if self._socket is None:
            return 0
        return int(self._socket.bytesToWrite())

    @property
    def write_congested(self) -> bool:
        return self.bytes_to_write > WRITE_HIGH_WATER_BYTES

    def _check_congestion(self) -> None:
        """Сообщать о ПЕРЕХОДАХ, а не о состоянии на каждый записанный байт.

        bytesWritten приходит часто; сигнал на каждый его приход превратил бы
        подписчика в получателя потока одинаковых уведомлений.
        """
        congested = self.write_congested
        if congested == self._congested:
            return
        self._congested = congested
        self.congestion_changed.emit(congested)
```

Import `MAX_FILE_CHUNK_BYTES` from `.wire`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/clipboard/peer.py configurator/tests/clipboard/test_peer_link.py
git commit -m "Bound the socket read buffer so a flood cannot grow it

One read in flight bounds what we ask for; it does not bound what a buggy
or hostile peer sends. Unrequested chunks are dropped only after Qt has
buffered them and the assembler has built them, so the default unlimited
readBufferSize was the gap between a memory bound we can prove and one
that depends on the peer behaving.

Congestion is reported on transitions, not on every written byte, or the
subscriber would receive a stream of identical notifications instead of
two edges.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 1.15: prove the logs carry no bytes and no full paths

**Files:**
- Create: `configurator/tests/transfer/test_privacy.py`

**Interfaces:**
- Consumes: `scan`, `SnapshotRegistry`, `FileTransferService`.
- Produces: nothing. This task adds no production code — it asserts a property
  the earlier tasks were written to have.

Spec §15 forbids logging file bytes ever, and full paths anywhere. That is the
kind of requirement every module respects on the day it is written and one
module breaks six weeks later during a debugging session. A test makes the
breakage visible immediately instead of at the next code review.

- [ ] **Step 1: Write the test**

```python
# configurator/tests/transfer/test_privacy.py
"""Спека §15: байты файлов - никогда, полные пути - нигде.

Это требование, которое соблюдают все модули в день написания и нарушает один
модуль через шесть недель, во время отладки. Тест делает нарушение видимым
сразу, а не на следующем ревью.
"""

from __future__ import annotations

import logging
import os

import pytest

from duo_input.clipboard.wire import CAPABILITY_CLIPBOARD, CAPABILITY_FILES, Message, MessageType
from duo_input.transfer.scanner import scan
from duo_input.transfer.service import FileTransferService
from duo_input.transfer.source import SnapshotRegistry

SECRET = b"SUPER-SECRET-FILE-CONTENTS-0123456789"
CAPS = frozenset({CAPABILITY_CLIPBOARD, CAPABILITY_FILES})


class _Link:
    """Минимальная связь: журнал проверяется, сеть здесь не нужна."""

    def __init__(self) -> None:
        self.sent: list[Message] = []
        from PySide6.QtCore import QObject, Signal

        self.disconnected = _Signal()

    def send(self, message: Message) -> None:
        self.sent.append(message)


class _Signal:
    def connect(self, _slot) -> None: ...


def test_scanning_never_logs_a_full_path(caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    unreadable = tmp_path / "нельзя-читать"
    unreadable.mkdir()
    (unreadable / "inner.bin").write_bytes(SECRET)
    os.chmod(unreadable, 0o000)
    try:
        scan([unreadable], transfer_id="t")
    except Exception:
        pass
    finally:
        os.chmod(unreadable, 0o700)

    logged = "\n".join(record.getMessage() for record in caplog.records)

    assert str(tmp_path) not in logged, (
        "полный путь попал в журнал - он раскрывает структуру диска "
        "пользователя, а для диагностики достаточно имени"
    )


def test_reading_a_file_never_logs_its_bytes(caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    source = tmp_path / "secret.bin"
    source.write_bytes(SECRET)
    manifest, sources = scan([source], transfer_id="t")
    registry = SnapshotRegistry()
    registry.publish(manifest, sources)

    payload = registry.read("t", 0, 0, len(SECRET))

    assert payload == SECRET
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert b"SUPER-SECRET" not in logged.encode()
    assert "SUPER-SECRET" not in logged


def test_a_source_that_vanished_is_reported_by_name_not_by_path(caplog, tmp_path):
    caplog.set_level(logging.DEBUG)
    source = tmp_path / "gone.bin"
    source.write_bytes(SECRET)
    manifest, sources = scan([source], transfer_id="t")
    registry = SnapshotRegistry()
    registry.publish(manifest, sources)
    os.remove(source)

    with pytest.raises(Exception):
        registry.read("t", 0, 0, 4)

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert str(tmp_path) not in logged


def test_a_rejected_manifest_is_logged_without_the_offending_path_in_full(
    caplog, qapp
):
    caplog.set_level(logging.DEBUG)
    service = FileTransferService()

    service.handle_message(
        Message(
            MessageType.FILE_OFFER,
            {
                "transfer_id": "t",
                "entries": [
                    {
                        "path": "..\\\\..\\\\Users\\\\victim\\\\secret.docx",
                        "kind": "file",
                        "size": 1,
                        "mtime_ns": 1,
                    }
                ],
            },
            b"",
        )
    )

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert logged, "отвергнутое объявление не оставило следа вовсе - это тоже плохо"
    assert "victim" not in logged, (
        "путь враждебного пира попал в журнал целиком; в нём может быть что "
        "угодно, включая то, что не должно оказаться в файле журнала"
    )


def test_a_chunk_is_never_logged_even_at_debug_level(caplog, qapp, tmp_path):
    caplog.set_level(logging.DEBUG)
    service = FileTransferService()
    link = _Link()
    service.attach_link(link)
    service.set_peer_capabilities(CAPS)
    source = tmp_path / "secret.bin"
    source.write_bytes(SECRET)
    transfer_id = service.offer_local_files([source])

    service.handle_message(
        Message(
            MessageType.FILE_READ,
            {
                "transfer_id": transfer_id,
                "entry_index": 0,
                "offset": 0,
                "length": len(SECRET),
            },
            b"",
        )
    )

    assert link.sent[-1].blob == SECRET
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "SUPER-SECRET" not in logged
```

- [ ] **Step 2: Run the test**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_privacy.py -v`
Expected: PASS, 5 passed.

A failure here means a real privacy leak in a module written earlier in this
phase. Fix that module's logging call — reduce it to `path.name` or a reason
string — rather than loosening the assertion.

Note that `os.chmod(path, 0o000)` does not block directory listing on Windows.
If `test_scanning_never_logs_a_full_path` cannot produce a failure to log, swap
the trigger: pass a path that does not exist at all (`tmp_path / "missing"`),
which reaches the same `logger.warning` in `_walk`.

- [ ] **Step 3: Commit**

```bash
git add configurator/tests/transfer/test_privacy.py
git commit -m "Assert the logs carry neither file bytes nor full paths

Every module respects this on the day it is written and one module breaks
it six weeks later while somebody is debugging. The test makes that
visible immediately.

A hostile peer's rejected path is checked too: it can contain anything,
including things that should not end up in a log file on disk.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```


---

# Phase 2 — Production Windows virtual files

**Purpose:** move the mechanism Phase 0 proved into `transfer/windows_com.py` and
`transfer/windows_files.py`, decomposed by responsibility rather than copied as
one block.

**Precondition:** Task 0.5 is committed. The completion signal this phase wires
is the one that record named — do not re-derive it.

**Hard rule:** `windows_com.py` must not import `PySide6`. That is the literal
boundary "the COM thread does not touch Qt", and Task 2.6 enforces it.

**What is genuinely new here, not a port:** the `IStream` in Task 2.2 reads from
a `ChunkPipe` and requests through a callback, where the spike's read from a
synthetic generator. That is the bridge, and it gets the most test attention.

## Task 2.1: windows_com.py — COM primitives, no Qt

**Files:**
- Create: `configurator/src/duo_input/transfer/windows_com.py`
- Test: `configurator/tests/transfer/test_windows_com.py`

**Interfaces:**
- Consumes: nothing. stdlib `ctypes` and `threading` only.
- Produces:
  - `S_OK = 0`, `S_FALSE = 1`, `E_POINTER`, `E_NOINTERFACE`, `E_NOTIMPL`,
    `E_FAIL`, `DV_E_FORMATETC`, `DV_E_TYMED`, `STG_E_INVALIDFUNCTION`,
    `STG_E_READFAULT`
  - `IID_IUNKNOWN`, `IID_IDATAOBJECT`, `IID_IENUMFORMATETC`, `IID_ISTREAM`,
    `IID_IASYNCCAPABILITY`
  - `TYMED_HGLOBAL = 1`, `TYMED_ISTREAM = 4`, `DATADIR_GET = 1`,
    `STREAM_SEEK_SET/CUR/END`, `DROPEFFECT_COPY = 1`,
    `FILE_ATTRIBUTE_DIRECTORY`, `FD_*` flags
  - `GUID`, `FORMATETC`, `STGMEDIUM`, `FILETIME`, `FILEDESCRIPTORW`, `STATSTG`
  - `guid_from_string(text: str) -> GUID`, `same_guid(a, b) -> bool`
  - `make_vtable(*callbacks) -> ctypes.Array`
  - `register_clipboard_format(name: str) -> int`
  - `to_hglobal(payload: bytes) -> int`
  - `filetime_from_ns(mtime_ns: int) -> FILETIME`
  - `class ComObject` — `IUnknown` base with `.pointer`, `.refcount`,
    `.add_interface(iid)`, `.extend_vtable(callbacks)`, `.on_last_release`
      hook

Port the structures and helpers from `spike_com_vtable.py` and
`spike_virtual_files.py` earlier in this document. Two things change on the way
in, and both are why this is not a copy:

1. **`ComObject` gains a real release hook.** The spike let the refcount go to
   zero and did nothing. Production must know, because `Release` on a stream is
   how Explorer says "I am done with this file" — spec §9.
2. **`filetime_from_ns` is new.** The spike passed no timestamps; production
   carries `mtime_ns` from the manifest into `ftLastWriteTime`.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_windows_com.py
"""Примитивы COM. Ни одного импорта PySide6 в модуле, который они описывают.

Ошибка здесь роняет процесс, а не бросает исключение, поэтому проверяется
всё: смещения в структурах, подсчёт ссылок, хук последнего Release и
преобразование времени.
"""

from __future__ import annotations

import ctypes
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="COM - это Windows")

from duo_input.transfer.windows_com import (
    DROPEFFECT_COPY,
    E_NOINTERFACE,
    FILE_ATTRIBUTE_DIRECTORY,
    IID_IDATAOBJECT,
    IID_IUNKNOWN,
    S_OK,
    TYMED_ISTREAM,
    ComObject,
    FILEDESCRIPTORW,
    call_add_ref,
    call_query_interface,
    call_release,
    filetime_from_ns,
    guid_from_string,
    register_clipboard_format,
    same_guid,
    to_hglobal,
)


def test_a_fresh_object_holds_one_reference():
    assert ComObject([IID_IUNKNOWN]).refcount == 1


def test_the_reference_count_walks_up_and_back_down():
    obj = ComObject([IID_IUNKNOWN])

    assert call_add_ref(obj.pointer) == 2
    assert call_release(obj.pointer) == 1


def test_query_interface_grants_a_supported_iid_and_counts_the_reference():
    obj = ComObject([IID_IUNKNOWN, IID_IDATAOBJECT])
    out = ctypes.c_void_p()

    result = call_query_interface(obj.pointer, guid_from_string(IID_IDATAOBJECT), out)

    assert result == S_OK
    assert out.value == obj.pointer.value
    assert obj.refcount == 2


def test_query_interface_refuses_an_unsupported_iid_and_nulls_the_out_pointer():
    obj = ComObject([IID_IUNKNOWN])
    out = ctypes.c_void_p(0xDEAD)

    result = call_query_interface(
        obj.pointer, guid_from_string("{11111111-2222-3333-4444-555555555555}"), out
    )

    assert result == E_NOINTERFACE
    assert out.value is None


def test_the_last_release_fires_the_hook_exactly_once():
    # Release на потоке - это то, чем Проводник говорит "с этим файлом всё".
    # Спайк давал счётчику уйти в ноль и не делал ничего.
    released: list[int] = []
    obj = ComObject([IID_IUNKNOWN])
    obj.on_last_release = lambda: released.append(1)

    call_add_ref(obj.pointer)
    call_release(obj.pointer)
    assert released == []

    call_release(obj.pointer)
    assert released == [1]

    call_release(obj.pointer)
    assert released == [1], "хук сработал повторно на уже освобождённом объекте"


def test_guid_parses_from_its_braced_string_form():
    guid = guid_from_string("{0000000C-0000-0000-C000-000000000046}")

    assert guid.Data1 == 0x0000000C
    assert guid.Data4[7] == 0x46


def test_two_guids_compare_by_value_not_by_identity():
    assert same_guid(guid_from_string(IID_IUNKNOWN), guid_from_string(IID_IUNKNOWN))
    assert not same_guid(guid_from_string(IID_IUNKNOWN), guid_from_string(IID_IDATAOBJECT))


def test_the_file_descriptor_structure_is_the_size_windows_expects():
    # 592 байта в 64-разрядной сборке. Расхождение здесь означает, что
    # Проводник прочитает наши поля по неверным смещениям и покажет мусорные
    # имена и размеры - без единой ошибки.
    assert ctypes.sizeof(FILEDESCRIPTORW) == 592


def test_the_file_name_field_holds_260_wide_characters():
    descriptor = FILEDESCRIPTORW()
    descriptor.cFileName = "a" * 259

    assert descriptor.cFileName == "a" * 259


def test_a_modification_time_round_trips_into_a_filetime():
    # 1 января 2020, 00:00:00 UTC в наносекундах Unix.
    filetime = filetime_from_ns(1_577_836_800_000_000_000)
    combined = (filetime.dwHighDateTime << 32) | filetime.dwLowDateTime

    # FILETIME считает интервалы по 100 нс от 1601-01-01.
    assert combined == (1_577_836_800 + 11_644_473_600) * 10_000_000


def test_a_zero_modification_time_does_not_become_a_negative_filetime():
    filetime = filetime_from_ns(0)

    assert filetime.dwHighDateTime >= 0
    assert filetime.dwLowDateTime >= 0


def test_registering_the_same_clipboard_format_twice_returns_the_same_id():
    first = register_clipboard_format("DuoInputTestFormat")
    second = register_clipboard_format("DuoInputTestFormat")

    assert first == second != 0


def test_a_payload_survives_the_trip_through_a_global_handle():
    payload = DROPEFFECT_COPY.to_bytes(4, "little")

    handle = to_hglobal(payload)

    address = ctypes.windll.kernel32.GlobalLock(handle)
    try:
        assert ctypes.string_at(address, 4) == payload
    finally:
        ctypes.windll.kernel32.GlobalUnlock(handle)
        ctypes.windll.kernel32.GlobalFree(handle)


def test_the_constants_have_the_values_the_shell_defines():
    assert (TYMED_ISTREAM, DROPEFFECT_COPY, FILE_ATTRIBUTE_DIRECTORY) == (4, 1, 0x10)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_windows_com.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duo_input.transfer.windows_com'`

- [ ] **Step 3: Write the implementation**

Port `GUID`, `FORMATETC`, `STGMEDIUM`, `FILETIME`, `FILEDESCRIPTORW`, `STATSTG`,
`guid_from_string`, `make_vtable`, `register_clipboard_format` (from the spike's
`register_format`), `to_hglobal` and the constant blocks from Tasks 0.1–0.4
above, with this module docstring:

```python
"""Примитивы COM на голом ctypes. Ни PySide6, ни duo_input.ui, ни сокетов.

Этот модуль - буквальная граница "COM-поток не трогает Qt". Правило проверяется
boundary-тестом (задача 2.6), и оно не стилистическое: без него однажды
кто-нибудь дёрнет QSslSocket из COM-потока, потому что так короче, и получит
дефект, который воспроизводится раз в сто запусков.

Нужны только СТАНДАРТНЫЕ интерфейсы - IUnknown, IDataObject, IEnumFORMATETC,
IStream, IDataObjectAsyncCapability. Ни typelib, ни кодогенерации, ни записи в
реестр: OLE уже содержит готовые proxy/stub для IDataObject и IStream, и
именно поэтому межпроцессный маршалинг работает без нашего участия.

Обратные вызовы удерживаются в self._callbacks: ctypes не держит CFUNCTYPE за
нас, а собранный сборщиком мусора колбэк - это переход по освобождённому
адресу, то есть падение процесса без исключения и без записи в журнал.
"""
```

`ComObject` differs from the spike's `COMObject` in two ways:

```python
class ComObject:
    """IUnknown плюс хук последнего Release.

    Хук - не удобство. Release на IStream - это то, чем Проводник сообщает
    "с этим файлом всё"; спайк давал счётчику уйти в ноль и не делал ничего,
    а production обязан узнать (спека §9).
    """

    def __init__(self, supported_iids: list[str]) -> None:
        self._supported = [guid_from_string(iid) for iid in supported_iids]
        self.refcount = 1
        self._released = False
        #: Вызывается ровно один раз, когда счётчик впервые достигает нуля.
        self.on_last_release = None

        self._callbacks: list = [
            _QUERY_INTERFACE(self._query_interface),
            _REF_COUNT(self._add_ref),
            _REF_COUNT(self._release),
        ]
        self._rebuild()

    def add_interface(self, iid: str) -> None:
        self._supported.append(guid_from_string(iid))

    def extend_vtable(self, callbacks: list) -> None:
        """Дописать слоты производного интерфейса ПОСЛЕ трёх слотов IUnknown.

        Порядок в vtable - это и есть контракт интерфейса. Дописать не в конец
        означает вызвать не ту функцию, и COM об этом не сообщит.
        """
        self._callbacks.extend(callbacks)
        self._rebuild()

    def _rebuild(self) -> None:
        self._vtable = make_vtable(*self._callbacks)
        self._slot = ctypes.c_void_p(ctypes.addressof(self._vtable))
        self.pointer = ctypes.c_void_p(ctypes.addressof(self._slot))

    def _query_interface(self, _this, riid, ppv) -> int:
        if not ppv:
            return E_POINTER
        out = ctypes.cast(ppv, ctypes.POINTER(ctypes.c_void_p))
        requested = ctypes.cast(riid, ctypes.POINTER(GUID)).contents
        if any(same_guid(requested, supported) for supported in self._supported):
            out[0] = self.pointer
            self.refcount += 1
            return S_OK
        out[0] = None
        return E_NOINTERFACE

    def _add_ref(self, _this) -> int:
        self.refcount += 1
        return self.refcount

    def _release(self, _this) -> int:
        self.refcount -= 1
        if self.refcount <= 0 and not self._released:
            self._released = True
            if self.on_last_release is not None:
                self.on_last_release()
        return max(self.refcount, 0)
```

And the new timestamp helper:

```python
#: Разница между эпохой FILETIME (1601-01-01) и эпохой Unix, в секундах.
_FILETIME_EPOCH_DELTA_SECONDS = 11_644_473_600


def filetime_from_ns(mtime_ns: int) -> FILETIME:
    """st_mtime_ns в FILETIME (интервалы по 100 нс от 1601-01-01).

    max(0, ...) не косметика: файл со временем до 1970 года дал бы
    отрицательное число, а FILETIME беззнаковый - Проводник показал бы дату
    из далёкого будущего.
    """
    intervals = max(0, mtime_ns // 100 + _FILETIME_EPOCH_DELTA_SECONDS * 10_000_000)
    result = FILETIME()
    result.dwLowDateTime = intervals & 0xFFFFFFFF
    result.dwHighDateTime = (intervals >> 32) & 0xFFFFFFFF
    return result
```

Add the three `call_*` helpers (ported from the spike's `query_interface`,
`add_ref`, `release`), taking `ctypes.c_void_p` out-parameters by reference
internally so the tests read cleanly.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_windows_com.py -v`
Expected: PASS, 14 passed

If `test_the_file_descriptor_structure_is_the_size_windows_expects` fails,
**do not change the assertion.** A wrong `sizeof` means Explorer will read our
fields at wrong offsets and display garbage names and sizes with no error at
all. Fix the field order and types against the `FILEDESCRIPTORW` definition.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/windows_com.py configurator/tests/transfer/test_windows_com.py
git commit -m "Port the COM primitives into a module that cannot see Qt

Two things changed on the way in from the spike, and both are why this is
not a copy. ComObject gained a real last-release hook, because Release on
a stream is how Explorer says it is done with a file and the spike let
the count reach zero and did nothing. filetime_from_ns is new, because
production carries mtime from the manifest and a pre-1970 file would
otherwise show a date in the far future.

The structure size is asserted: a wrong sizeof makes Explorer read our
fields at the wrong offsets and show garbage with no error at all.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 2.2: the IStream that reads from a ChunkPipe

**Files:**
- Modify: `configurator/src/duo_input/transfer/windows_com.py`
- Test: `configurator/tests/transfer/test_windows_stream.py`

**Interfaces:**
- Consumes: `ComObject`, the constants and prototypes from Task 2.1;
  `ChunkPipe`, `PipeClosed` (Task 1.8).
- Produces:
  - `READ_TIMEOUT_SECONDS = 30.0`
  - `PipeStream(pipe, size: int, request, on_release=None, timeout=READ_TIMEOUT_SECONDS)`
    where `request` is `Callable[[int, int], None]` taking `(offset, length)`
  - `.pointer`, `.position`, `.requested: list[tuple[int, int]]` (for tests)

**This is the bridge, and it is the whole reason the architecture holds.** The
rules it must obey, each with a test:

- it calls `request(offset, length)` **before** blocking, never after;
- it blocks on `pipe.wait`, never on a socket;
- it touches no Qt object — `request` is an opaque callable supplied by
  `windows_files.py`;
- `PipeClosed` becomes an `HRESULT`, never an exception escaping into COM;
- a partial read returns `S_OK` with a smaller `pcbRead`, because `S_FALSE`
  means end-of-stream and Explorer may treat it as an error.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_windows_stream.py
"""Мост IStream -> ChunkPipe. Ни одного обращения к Qt и ни одного к сокету.

Здесь нет ни COM-потока, ни Проводника: очередь и запрос подставляются
вручную, поэтому каждое правило моста проверяется отдельно и детерминированно.
"""

from __future__ import annotations

import ctypes
import sys
import threading
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="COM - это Windows")

from duo_input.transfer.pipe import ChunkPipe
from duo_input.transfer.windows_com import (
    S_OK,
    STG_E_READFAULT,
    STREAM_SEEK_END,
    STREAM_SEEK_SET,
    PipeStream,
    call_stream_read,
    call_stream_seek,
    call_stream_stat,
)


def _stream(size=10, timeout=2.0):
    pipe = ChunkPipe(capacity_chunks=1)
    requested: list[tuple[int, int]] = []
    stream = PipeStream(
        pipe,
        size=size,
        request=lambda offset, length: requested.append((offset, length)),
        timeout=timeout,
    )
    return stream, pipe, requested


def test_a_read_asks_for_the_bytes_before_it_blocks():
    # Порядок здесь - это и есть ленивость. Заблокироваться до запроса
    # означало бы повиснуть навсегда.
    stream, pipe, requested = _stream()
    order: list[str] = []

    def responder() -> None:
        while not requested:
            time.sleep(0.005)
        order.append("requested")
        pipe.push(b"0123")

    thread = threading.Thread(target=responder, daemon=True)
    thread.start()
    payload, result = call_stream_read(stream.pointer, 4)
    thread.join(timeout=2.0)

    assert result == S_OK
    assert payload == b"0123"
    assert order == ["requested"]
    assert requested == [(0, 4)]


def test_bytes_already_in_the_pipe_are_taken_without_a_new_request():
    stream, pipe, requested = _stream()
    pipe.push(b"abcd")

    payload, result = call_stream_read(stream.pointer, 4)

    assert (payload, result) == (b"abcd", S_OK)
    assert requested == [], "запрос ушёл, хотя данные уже лежали в очереди"


def test_the_position_advances_by_what_was_actually_returned():
    stream, pipe, _requested = _stream()
    pipe.push(b"ab")

    call_stream_read(stream.pointer, 4)

    assert stream.position == 2


def test_a_partial_read_returns_S_OK_and_a_smaller_count():
    # S_FALSE означает конец потока, и Проводник вправе трактовать его как
    # ошибку посреди файла. Частичное чтение - это S_OK с меньшим pcbRead.
    stream, pipe, _requested = _stream()
    pipe.push(b"ab")

    payload, result = call_stream_read(stream.pointer, 8)

    assert (len(payload), result) == (2, S_OK)


def test_a_read_at_the_end_of_the_file_returns_nothing_without_asking():
    stream, _pipe, requested = _stream(size=4)
    stream.position = 4

    payload, result = call_stream_read(stream.pointer, 8)

    assert (payload, result) == (b"", S_OK)
    assert requested == [], "запрос за концом файла - отправитель ответил бы пустотой"


def test_a_read_never_asks_for_more_than_remains_in_the_file():
    stream, _pipe, requested = _stream(size=6, timeout=0.1)
    stream.position = 4

    call_stream_read(stream.pointer, 1024)

    assert requested == [(4, 2)], "запрошено больше, чем осталось в файле"


def test_a_closed_pipe_becomes_an_hresult_and_never_an_exception():
    stream, pipe, _requested = _stream()
    pipe.close("cancelled")

    payload, result = call_stream_read(stream.pointer, 4)

    assert result == STG_E_READFAULT
    assert payload == b""


def test_a_pipe_closed_while_a_read_is_blocked_unblocks_it_at_once():
    # Отзывчивость отмены. Заблокированный Read обязан вернуть управление
    # сразу, а не через READ_TIMEOUT_SECONDS.
    stream, pipe, _requested = _stream(timeout=30.0)
    results: list[int] = []

    def reader() -> None:
        _payload, result = call_stream_read(stream.pointer, 4)
        results.append(result)

    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    time.sleep(0.1)
    started = time.perf_counter()
    pipe.close("cancelled")
    thread.join(timeout=3.0)

    assert results == [STG_E_READFAULT]
    assert time.perf_counter() - started < 1.0, "закрытие ждало таймаута"


def test_a_read_that_times_out_fails_rather_than_hanging_for_ever():
    stream, _pipe, _requested = _stream(timeout=0.05)

    _payload, result = call_stream_read(stream.pointer, 4)

    assert result == STG_E_READFAULT


def test_a_finished_pipe_ends_the_stream_without_an_error():
    stream, pipe, _requested = _stream(size=1024)
    pipe.finish()

    payload, result = call_stream_read(stream.pointer, 4)

    assert (payload, result) == (b"", S_OK)


def test_seeking_to_the_end_reports_the_size_from_the_manifest():
    stream, _pipe, _requested = _stream(size=4096)

    position, result = call_stream_seek(stream.pointer, 0, STREAM_SEEK_END)

    assert (position, result) == (4096, S_OK)


def test_seeking_changes_where_the_next_read_asks_from():
    stream, _pipe, requested = _stream(size=100, timeout=0.05)

    call_stream_seek(stream.pointer, 40, STREAM_SEEK_SET)
    call_stream_read(stream.pointer, 8)

    assert requested == [(40, 8)]


def test_stat_reports_the_size_explorer_was_promised():
    stream, _pipe, _requested = _stream(size=7777)

    size, result = call_stream_stat(stream.pointer)

    assert (size, result) == (7777, S_OK)


def test_the_last_release_notifies_the_owner_that_this_stream_is_done():
    # Release на потоке означает конец жизни ЭТОГО потока - и только его.
    # Сессию это само по себе не завершает (спека §9).
    pipe = ChunkPipe()
    done: list[int] = []
    stream = PipeStream(pipe, size=4, request=lambda *_: None, on_release=lambda: done.append(1))

    from duo_input.transfer.windows_com import call_release

    call_release(stream.pointer)

    assert done == [1]


def test_the_stream_module_touches_no_qt_object():
    import duo_input.transfer.windows_com as module

    assert not hasattr(module, "QObject")
    assert "PySide6" not in str(module.__dict__.keys())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_windows_stream.py -v`
Expected: FAIL — `ImportError: cannot import name 'PipeStream'`

- [ ] **Step 3: Write the implementation**

Append to `windows_com.py`:

```python
#: Сколько ждать один чанк, прежде чем признать чтение неудавшимся.
#:
#: Windows даёт отложенной отрисовке порядка 30 секунд, и вставляющее
#: приложение всё равно ждёт. Меньше - и медленная сеть выглядела бы как
#: ошибка; больше - и зависший пир вешал бы Проводник без объяснения.
READ_TIMEOUT_SECONDS = 30.0


class PipeStream(ComObject):
    """IStream, читающий из ChunkPipe и запрашивающий через колбэк.

    ЭТО МОСТ, и на нём держится вся архитектура. Правила, каждое из которых
    проверяется тестом:

    - request() зовётся ПЕРЕД блокировкой, никогда после. Заблокироваться до
      запроса означало бы повиснуть навсегда: никто не пришлёт данные, которых
      не просили.
    - блокировка происходит на pipe.wait(), никогда на сокете. Сокет
      принадлежит GUI-потоку Qt, а этот код исполняется на COM-потоке.
    - request - непрозрачный колбэк, переданный снаружи. Этот класс не знает
      ни про Qt, ни про PeerLink, ни про invokeMethod.
    - PipeClosed превращается в HRESULT. Исключение Python, вылетевшее в COM,
      - это неопределённое поведение на стороне Проводника.
    """

    def __init__(
        self,
        pipe,
        size: int,
        request,
        on_release=None,
        timeout: float = READ_TIMEOUT_SECONDS,
    ) -> None:
        super().__init__([IID_IUNKNOWN, IID_ISTREAM])
        self._pipe = pipe
        self._size = size
        self._request = request
        self._timeout = timeout
        self.position = 0
        self.on_last_release = on_release

        self.extend_vtable([
            _STREAM_READ(self._read),
            _STREAM_READ(self._write),
            _STREAM_SEEK(self._seek),
            _STREAM_SETSIZE(self._set_size),
            _STREAM_COPYTO(self._copy_to),
            _STREAM_COMMIT(self._commit),
            _STREAM_REVERT(self._revert),
            _STREAM_LOCK(self._lock),
            _STREAM_LOCK(self._unlock),
            _STREAM_STAT(self._stat),
            _STREAM_CLONE(self._clone),
        ])

    # ------------------------------------------------------------------ IStream

    def _read(self, _this, pv, cb, pcb_read) -> int:
        remaining = max(0, self._size - self.position)
        want = min(int(cb), remaining)
        if want == 0:
            # Конец файла. Запрашивать нечего - отправитель ответил бы пустотой.
            if pcb_read:
                ctypes.cast(pcb_read, ctypes.POINTER(wintypes.ULONG))[0] = 0
            return S_OK

        try:
            payload = self._pipe.take(want)
            if not payload:
                # Запрос ПЕРЕД блокировкой. Обратный порядок повис бы навсегда.
                self._request(self.position, want)
                if not self._pipe.wait(self._timeout):
                    logger.warning("чанк не пришёл за %.0f с", self._timeout)
                    return STG_E_READFAULT
                payload = self._pipe.take(want)
        except PipeClosed as error:
            # Отмена, разрыв или ошибка. В COM уходит HRESULT, не исключение.
            logger.info("поток закрыт: %s", error.reason)
            if pcb_read:
                ctypes.cast(pcb_read, ctypes.POINTER(wintypes.ULONG))[0] = 0
            return STG_E_READFAULT

        if payload:
            ctypes.memmove(pv, payload, len(payload))
        self.position += len(payload)
        if pcb_read:
            ctypes.cast(pcb_read, ctypes.POINTER(wintypes.ULONG))[0] = len(payload)
        # S_OK и при частичном чтении. S_FALSE означает конец потока, и
        # Проводник вправе трактовать его посреди файла как ошибку.
        return S_OK

    def _seek(self, _this, offset, origin, new_position) -> int:
        if origin == STREAM_SEEK_SET:
            target = int(offset)
        elif origin == STREAM_SEEK_CUR:
            target = self.position + int(offset)
        elif origin == STREAM_SEEK_END:
            target = self._size + int(offset)
        else:
            return STG_E_INVALIDFUNCTION
        if target < 0:
            return STG_E_INVALIDFUNCTION
        self.position = target
        if new_position:
            ctypes.cast(new_position, ctypes.POINTER(ctypes.c_ulonglong))[0] = target
        return S_OK

    def _stat(self, _this, pstatstg, _flags) -> int:
        if not pstatstg:
            return E_POINTER
        stat = ctypes.cast(pstatstg, ctypes.POINTER(STATSTG)).contents
        ctypes.memset(ctypes.byref(stat), 0, ctypes.sizeof(STATSTG))
        stat.type = 2  # STGTY_STREAM
        stat.cbSize = self._size
        return S_OK

    def _write(self, _this, _pv, _cb, _written) -> int:
        return STG_E_INVALIDFUNCTION

    def _set_size(self, _this, _size) -> int:
        return STG_E_INVALIDFUNCTION

    def _copy_to(self, _this, _dest, _cb, _read, _written) -> int:
        return E_NOTIMPL

    def _commit(self, _this, _flags) -> int:
        return S_OK

    def _revert(self, _this) -> int:
        return S_OK

    def _lock(self, _this, _offset, _cb, _type) -> int:
        return E_NOTIMPL

    def _unlock(self, _this, _offset, _cb, _type) -> int:
        return E_NOTIMPL

    def _clone(self, _this, _out) -> int:
        # Клонировать поток означало бы завести второе независимое положение
        # над одной очередью, у которой положения нет вообще.
        return E_NOTIMPL
```

Add `call_stream_read(pointer, count) -> tuple[bytes, int]`,
`call_stream_seek(pointer, offset, origin) -> tuple[int, int]` and
`call_stream_stat(pointer) -> tuple[int, int]` alongside the existing `call_*`
helpers, each invoking the right vtable slot (3, 5 and 12 respectively, counting
from `QueryInterface` at 0).

Add `import logging` and `logger = logging.getLogger(__name__)` at the top, and
`from .pipe import ChunkPipe, PipeClosed`. Importing `pipe` here is allowed: it
is Qt-free by its own boundary rule.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_windows_stream.py -v`
Expected: PASS, 15 passed

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/windows_com.py configurator/tests/transfer/test_windows_stream.py
git commit -m "Bridge IStream to the chunk pipe without touching Qt

This is the bridge the whole architecture rests on, so every rule has its
own test: the request goes out before the block, never after, because the
other order waits for data nobody asked for; the block is on the pipe,
never on a socket owned by another thread; PipeClosed becomes an HRESULT
because a Python exception escaping into COM is undefined behaviour on
Explorer's side; and a partial read is S_OK with a smaller count, since
S_FALSE means end-of-stream and Explorer may read that as failure.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 2.3: build FILEGROUPDESCRIPTORW from a manifest

**Files:**
- Create: `configurator/src/duo_input/transfer/windows_files.py`
- Test: `configurator/tests/transfer/test_windows_descriptor.py`

**Interfaces:**
- Consumes: `windows_com` constants and structs (Task 2.1); `TransferManifest`,
  `ENTRY_FILE`, `ENTRY_DIRECTORY` (Task 1.1).
- Produces:
  - `group_descriptor_bytes(manifest: TransferManifest) -> bytes`
  - `descriptor_entries(manifest) -> tuple[TransferEntry, ...]` — the manifest
    order, which is also the `lindex` order Explorer will use

The wire uses `/`; `cFileName` needs `\`. This is the single place that
conversion happens, and the test pins it.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_windows_descriptor.py
"""FILEGROUPDESCRIPTORW из манифеста. Единственное место, где "/" становится "\\"."""

from __future__ import annotations

import ctypes
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="дескриптор - это Windows")

from duo_input.transfer.model import ENTRY_DIRECTORY, ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.windows_com import (
    FD_FILESIZE,
    FILE_ATTRIBUTE_DIRECTORY,
    FILEDESCRIPTORW,
    filetime_from_ns,
)
from duo_input.transfer.windows_files import descriptor_entries, group_descriptor_bytes


def _manifest():
    return TransferManifest(
        transfer_id="t",
        entries=(
            TransferEntry(path="Photos", kind=ENTRY_DIRECTORY, size=0, mtime_ns=1_000_000_000),
            TransferEntry(
                path="Photos/img.jpg", kind=ENTRY_FILE, size=5_000_000_000, mtime_ns=2_000_000_000
            ),
        ),
    )


def _parse(blob: bytes):
    count = int.from_bytes(blob[:4], "little")
    stride = ctypes.sizeof(FILEDESCRIPTORW)
    parsed = []
    for index in range(count):
        start = 4 + index * stride
        descriptor = FILEDESCRIPTORW.from_buffer_copy(blob[start : start + stride])
        parsed.append(descriptor)
    return count, parsed


def test_the_blob_starts_with_the_entry_count():
    count, _descriptors = _parse(group_descriptor_bytes(_manifest()))

    assert count == 2


def test_the_blob_is_exactly_the_count_plus_the_descriptors():
    blob = group_descriptor_bytes(_manifest())

    assert len(blob) == 4 + 2 * ctypes.sizeof(FILEDESCRIPTORW)


def test_wire_slashes_become_backslashes_in_the_file_name():
    _count, descriptors = _parse(group_descriptor_bytes(_manifest()))

    assert descriptors[1].cFileName == "Photos\\img.jpg", (
        "Проводник разбирает вложенность по обратному слэшу; прямой он "
        "принял бы за часть имени файла"
    )


def test_a_directory_is_flagged_as_a_directory_and_declares_no_size():
    _count, descriptors = _parse(group_descriptor_bytes(_manifest()))

    assert descriptors[0].dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY
    assert not descriptors[0].dwFlags & FD_FILESIZE


def test_a_file_declares_its_size_across_both_halves():
    # 5 ГБ не влезают в 32 разряда. Заполнить только nFileSizeLow означало бы
    # показать Проводнику файл на 705 МБ и провалить проверку свободного места.
    _count, descriptors = _parse(group_descriptor_bytes(_manifest()))
    size = (descriptors[1].nFileSizeHigh << 32) | descriptors[1].nFileSizeLow

    assert size == 5_000_000_000
    assert descriptors[1].dwFlags & FD_FILESIZE


def test_a_file_carries_the_modification_time_from_the_manifest():
    _count, descriptors = _parse(group_descriptor_bytes(_manifest()))
    expected = filetime_from_ns(2_000_000_000)

    assert descriptors[1].ftLastWriteTime.dwLowDateTime == expected.dwLowDateTime
    assert descriptors[1].ftLastWriteTime.dwHighDateTime == expected.dwHighDateTime


def test_the_descriptor_order_is_the_manifest_order_so_lindex_lines_up():
    # Проводник просит содержимое по lindex. Любая пересортировка здесь
    # отдала бы содержимое одного файла под именем другого.
    manifest = _manifest()

    assert descriptor_entries(manifest) == manifest.entries


def test_an_empty_manifest_produces_only_a_zero_count():
    blob = group_descriptor_bytes(TransferManifest(transfer_id="t", entries=()))

    assert blob == (0).to_bytes(4, "little")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_windows_descriptor.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duo_input.transfer.windows_files'`

- [ ] **Step 3: Write minimal implementation**

```python
# configurator/src/duo_input/transfer/windows_files.py
"""Публикация удалённого дерева в буфер обмена Windows как виртуальных файлов.

Здесь живёт вся Windows-специфика верхнего уровня: сборка дескриптора,
IDataObject, поток STA и OleSetClipboard. Ядро (model, paths, pipe, scanner,
service, source) про эти вещи не знает вовсе, и boundary-тест это удерживает.

Разделитель пути на проводе - "/"; cFileName требует "\\". Преобразование
происходит РОВНО здесь, в одном месте, и тест это закрепляет: Проводник
разбирает вложенность по обратному слэшу, а прямой принял бы за часть имени.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes

from .model import ENTRY_DIRECTORY, ENTRY_FILE, TransferEntry, TransferManifest
from .windows_com import (
    FD_ATTRIBUTES,
    FD_FILESIZE,
    FD_PROGRESSUI,
    FD_WRITESTIME,
    FILE_ATTRIBUTE_DIRECTORY,
    FILEDESCRIPTORW,
    filetime_from_ns,
)

#: Обычный файл без особых атрибутов.
_FILE_ATTRIBUTE_NORMAL = 0x80


def descriptor_entries(manifest: TransferManifest) -> tuple[TransferEntry, ...]:
    """Записи в том порядке, в котором Проводник будет их адресовать по lindex.

    Порядок манифеста и есть порядок lindex. Любая пересортировка здесь отдала
    бы содержимое одного файла под именем другого - и без единой ошибки.
    """
    return manifest.entries


def group_descriptor_bytes(manifest: TransferManifest) -> bytes:
    """FILEGROUPDESCRIPTORW: счётчик, затем массив FILEDESCRIPTORW."""
    entries = descriptor_entries(manifest)
    blob = bytearray(len(entries).to_bytes(4, "little"))
    for entry in entries:
        blob.extend(bytes(memoryview(_descriptor_for(entry)).cast("B")))
    return bytes(blob)


def _descriptor_for(entry: TransferEntry) -> FILEDESCRIPTORW:
    descriptor = FILEDESCRIPTORW()
    descriptor.dwFlags = FD_ATTRIBUTES | FD_WRITESTIME | FD_PROGRESSUI
    descriptor.cFileName = entry.path.replace("/", "\\")
    descriptor.ftLastWriteTime = filetime_from_ns(entry.mtime_ns)
    if entry.kind == ENTRY_DIRECTORY:
        descriptor.dwFileAttributes = FILE_ATTRIBUTE_DIRECTORY
        return descriptor
    descriptor.dwFileAttributes = _FILE_ATTRIBUTE_NORMAL
    descriptor.dwFlags |= FD_FILESIZE
    # Оба полуслова обязательны: 5 ГБ не влезают в 32 разряда, и заполнить
    # только nFileSizeLow означало бы показать Проводнику файл на 705 МБ -
    # с неверной полосой прогресса и неверной проверкой свободного места.
    descriptor.nFileSizeHigh = (entry.size >> 32) & 0xFFFFFFFF
    descriptor.nFileSizeLow = entry.size & 0xFFFFFFFF
    return descriptor


__all__ = ["descriptor_entries", "group_descriptor_bytes"]
```

Add `FD_WRITESTIME = 0x20` to `windows_com.py` if Task 2.1 did not.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_windows_descriptor.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/windows_files.py configurator/tests/transfer/test_windows_descriptor.py
git commit -m "Build the file group descriptor from the manifest

One place converts the wire separator to a backslash, and a test pins it:
Explorer parses nesting on backslashes and would read a forward slash as
part of the filename.

Both halves of the size are filled. A 5 GB file does not fit in 32 bits,
and setting only nFileSizeLow would show Explorer a 705 MB file, with the
wrong progress bar and the wrong free-space check.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 2.4: the IDataObject and its format enumerator

**Files:**
- Modify: `configurator/src/duo_input/transfer/windows_files.py`
- Test: `configurator/tests/transfer/test_windows_data_object.py`

**Interfaces:**
- Consumes: `group_descriptor_bytes` (Task 2.3); `PipeStream`, `ComObject`,
  constants (Tasks 2.1–2.2); `TransferManifest` (Task 1.1).
- Produces:
  - `FORMAT_DESCRIPTOR_NAME = "FileGroupDescriptorW"`,
    `FORMAT_CONTENTS_NAME = "FileContents"`,
    `FORMAT_DROP_EFFECT_NAME = "Preferred DropEffect"`
  - `FORMAT_ORIGIN_NAME = "application/x-duo-input-origin"`
  - `class FormatEnumerator(ComObject)` implementing `IEnumFORMATETC`
  - `class VirtualFilesDataObject(ComObject)` constructed as
    `VirtualFilesDataObject(manifest, open_pipe, request_read, close_pipe, origin_marker, async_capability=False)`
    where `open_pipe(transfer_id, entry_index) -> ChunkPipe`,
    `request_read(transfer_id, entry_index, offset, length) -> None`,
    `close_pipe(transfer_id, entry_index, reason) -> None`
  - `.streams: dict[int, PipeStream]`, `.get_data_calls: list[tuple[int, int]]`
  - `.on_operation_finished: Callable[[int], None] | None` — set by the owner;
    called from `EndOperation` with the shell's HRESULT

`FORMAT_ORIGIN_NAME` is not decoration — see Task 2.7. Advertising it is one of
the two halves that stop our own publication coming back as a local copy.

**Whether `async_capability` defaults to True and whether the five extra methods
are present at all is decided by Task 0.5's record.** If that record found no
observable difference, delete the parameter and the five methods rather than
keeping them switched off.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_windows_data_object.py
"""IDataObject целиком, в одном процессе. Проводник приходит в фазе 4."""

from __future__ import annotations

import ctypes
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="IDataObject - это Windows")

from duo_input.transfer.model import ENTRY_DIRECTORY, ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.pipe import ChunkPipe
from duo_input.transfer.windows_com import (
    DATADIR_GET,
    DROPEFFECT_COPY,
    DV_E_FORMATETC,
    DV_E_TYMED,
    S_OK,
    TYMED_HGLOBAL,
    TYMED_ISTREAM,
    call_get_data,
    call_query_get_data,
    register_clipboard_format,
)
from duo_input.transfer.windows_files import (
    FORMAT_CONTENTS_NAME,
    FORMAT_DESCRIPTOR_NAME,
    FORMAT_DROP_EFFECT_NAME,
    FORMAT_ORIGIN_NAME,
    VirtualFilesDataObject,
)


def _manifest():
    return TransferManifest(
        transfer_id="t-1",
        entries=(
            TransferEntry(path="Photos", kind=ENTRY_DIRECTORY, size=0, mtime_ns=1),
            TransferEntry(path="Photos/a.bin", kind=ENTRY_FILE, size=8, mtime_ns=2),
        ),
    )


@pytest.fixture
def data_object():
    pipes: dict[tuple[str, int], ChunkPipe] = {}
    reads: list[tuple[str, int, int, int]] = []
    closes: list[tuple[str, int, object]] = []

    def open_pipe(transfer_id, entry_index):
        pipe = ChunkPipe(capacity_chunks=1)
        pipes[(transfer_id, entry_index)] = pipe
        return pipe

    obj = VirtualFilesDataObject(
        _manifest(),
        open_pipe=open_pipe,
        request_read=lambda *args: reads.append(args),
        close_pipe=lambda *args: closes.append(args),
        origin_marker=b"origin:1",
    )
    return obj, pipes, reads, closes


def _fmt(obj, name, lindex=-1, tymed=TYMED_HGLOBAL):
    return register_clipboard_format(name), lindex, tymed


def test_the_descriptor_format_is_offered(data_object):
    obj, *_ = data_object
    cf, lindex, tymed = _fmt(obj, FORMAT_DESCRIPTOR_NAME)

    assert call_query_get_data(obj.pointer, cf, lindex, tymed) == S_OK


def test_the_contents_format_is_offered_for_a_stream(data_object):
    obj, *_ = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)

    assert call_query_get_data(obj.pointer, cf, 1, TYMED_ISTREAM) == S_OK


def test_an_unknown_format_is_refused(data_object):
    obj, *_ = data_object

    assert call_query_get_data(obj.pointer, 0xC123, -1, TYMED_HGLOBAL) == DV_E_FORMATETC


def test_asking_for_the_descriptor_returns_the_manifest_blob(data_object):
    obj, *_ = data_object
    cf, lindex, tymed = _fmt(obj, FORMAT_DESCRIPTOR_NAME)

    result, payload = call_get_data(obj.pointer, cf, lindex, tymed)

    assert result == S_OK
    assert int.from_bytes(payload[:4], "little") == 2


def test_the_drop_effect_is_always_copy_even_after_a_cut(data_object):
    # Ctrl+X на источнике сюда не доходит: удаление файлов на другой машине
    # необратимо и отложено в отдельный milestone.
    obj, *_ = data_object
    cf, lindex, tymed = _fmt(obj, FORMAT_DROP_EFFECT_NAME)

    _result, payload = call_get_data(obj.pointer, cf, lindex, tymed)

    assert int.from_bytes(payload[:4], "little") == DROPEFFECT_COPY


def test_the_origin_marker_is_advertised_so_our_own_paste_is_recognised(data_object):
    # Без этого наша собственная публикация вернулась бы к нам как локальное
    # копирование - см. задачу 2.7.
    obj, *_ = data_object
    cf, lindex, tymed = _fmt(obj, FORMAT_ORIGIN_NAME)

    result, payload = call_get_data(obj.pointer, cf, lindex, tymed)

    assert result == S_OK
    assert payload.startswith(b"origin:1")


def test_asking_for_contents_opens_a_pipe_and_returns_a_stream(data_object):
    obj, pipes, _reads, _closes = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)

    result, medium_tymed, _payload = call_get_data(
        obj.pointer, cf, 1, TYMED_ISTREAM, want_medium=True
    )

    assert result == S_OK
    assert medium_tymed == TYMED_ISTREAM
    assert ("t-1", 1) in pipes
    assert 1 in obj.streams


def test_contents_are_refused_when_explorer_will_not_take_a_stream(data_object):
    # Спайк записал, просит ли Проводник HGLOBAL. Если просит - решение
    # принимается там, а не здесь молча.
    obj, *_ = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)

    result, _medium, _payload = call_get_data(
        obj.pointer, cf, 1, TYMED_HGLOBAL, want_medium=True
    )

    assert result == DV_E_TYMED


def test_contents_of_a_directory_entry_are_refused(data_object):
    obj, *_ = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)

    result, _medium, _payload = call_get_data(
        obj.pointer, cf, 0, TYMED_ISTREAM, want_medium=True
    )

    assert result == DV_E_FORMATETC


def test_a_contents_index_outside_the_manifest_is_refused(data_object):
    obj, *_ = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)

    result, _medium, _payload = call_get_data(
        obj.pointer, cf, 99, TYMED_ISTREAM, want_medium=True
    )

    assert result == DV_E_FORMATETC


def test_a_negative_contents_index_is_refused(data_object):
    obj, *_ = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)

    result, _medium, _payload = call_get_data(
        obj.pointer, cf, -1, TYMED_ISTREAM, want_medium=True
    )

    assert result == DV_E_FORMATETC


def test_the_stream_is_held_so_python_cannot_collect_it_under_explorer(data_object):
    # Собранный сборщиком мусора поток - это переход Проводника по
    # освобождённому адресу: падение без исключения и без записи в журнал.
    obj, *_ = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)
    call_get_data(obj.pointer, cf, 1, TYMED_ISTREAM, want_medium=True)

    import gc

    gc.collect()

    assert obj.streams[1].pointer.value is not None


def test_releasing_a_stream_tells_the_owner_which_entry_finished(data_object):
    obj, _pipes, _reads, closes = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)
    call_get_data(obj.pointer, cf, 1, TYMED_ISTREAM, want_medium=True)

    from duo_input.transfer.windows_com import call_release

    call_release(obj.streams[1].pointer)

    assert closes == [("t-1", 1, None)]


def test_a_read_on_the_stream_reaches_the_request_callback(data_object):
    obj, pipes, reads, _closes = data_object
    cf, _lindex, _tymed = _fmt(obj, FORMAT_CONTENTS_NAME)
    call_get_data(obj.pointer, cf, 1, TYMED_ISTREAM, want_medium=True)
    pipes[("t-1", 1)].push(b"12345678")

    from duo_input.transfer.windows_com import call_stream_read

    payload, result = call_stream_read(obj.streams[1].pointer, 8)

    assert (payload, result) == (b"12345678", S_OK)
    assert reads == [], "данные уже лежали в очереди - запрос был лишним"


def test_the_enumerator_lists_the_formats_we_advertise(data_object):
    obj, *_ = data_object
    from duo_input.transfer.windows_com import call_enum_format_etc

    result, formats = call_enum_format_etc(obj.pointer, DATADIR_GET)

    assert result == S_OK
    assert register_clipboard_format(FORMAT_DESCRIPTOR_NAME) in formats
    assert register_clipboard_format(FORMAT_CONTENTS_NAME) in formats
    assert register_clipboard_format(FORMAT_DROP_EFFECT_NAME) in formats
    assert register_clipboard_format(FORMAT_ORIGIN_NAME) in formats


def test_the_enumerator_refuses_the_set_direction(data_object):
    obj, *_ = data_object
    from duo_input.transfer.windows_com import E_NOTIMPL, call_enum_format_etc

    result, _formats = call_enum_format_etc(obj.pointer, 2)  # DATADIR_SET

    assert result == E_NOTIMPL


def test_every_get_data_call_is_recorded_for_diagnostics(data_object):
    obj, *_ = data_object
    cf, lindex, tymed = _fmt(obj, FORMAT_DESCRIPTOR_NAME)

    call_get_data(obj.pointer, cf, lindex, tymed)

    assert obj.get_data_calls == [(cf, lindex)]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_windows_data_object.py -v`
Expected: FAIL — `ImportError: cannot import name 'VirtualFilesDataObject'`

- [ ] **Step 3: Write the implementation**

Append to `windows_files.py`. Port `DataObject`'s nine `IDataObject` slots from
Task 0.2, with four changes, each of which is why this is not a copy:

1. the descriptor comes from `group_descriptor_bytes(manifest)`, not a
   module-level `ENTRIES` list;
2. `FileContents` returns a `PipeStream` wired to the three callbacks, not a
   synthetic generator;
3. the stream's `on_release` reports back through `close_pipe`;
4. `FORMAT_ORIGIN_NAME` is advertised and served.

```python
FORMAT_DESCRIPTOR_NAME = "FileGroupDescriptorW"
FORMAT_CONTENTS_NAME = "FileContents"
FORMAT_DROP_EFFECT_NAME = "Preferred DropEffect"

#: То же имя, что ORIGIN_MIME в clipboard/backend.py. Объявляется здесь,
#: чтобы наш собственный наблюдатель узнал свою же публикацию и не объявил
#: её обратно как локальное копирование - см. задачу 2.7.
FORMAT_ORIGIN_NAME = "application/x-duo-input-origin"


class FormatEnumerator(ComObject):
    """IEnumFORMATETC. Проводник вправе спросить, что мы вообще предлагаем.

    Спайк возвращал E_NOTIMPL и записал, обходится ли Проводник без
    перечислителя. Настоящий перечислитель здесь потому, что "обходится" и
    "обходится у всех и всегда" - разные утверждения, а цена его невелика.
    """

    def __init__(self, formats: list[tuple[int, int, int]]) -> None:
        super().__init__([IID_IUNKNOWN, IID_IENUMFORMATETC])
        self._formats = list(formats)
        self._cursor = 0
        self.extend_vtable([
            _ENUM_NEXT(self._next),
            _ENUM_SKIP(self._skip),
            _ENUM_RESET(self._reset),
            _ENUM_CLONE(self._clone),
        ])

    def _next(self, _this, celt, rgelt, pcelt_fetched) -> int:
        available = self._formats[self._cursor : self._cursor + int(celt)]
        target = ctypes.cast(rgelt, ctypes.POINTER(FORMATETC))
        for index, (cf, lindex, tymed) in enumerate(available):
            target[index].cfFormat = cf
            target[index].ptd = None
            target[index].dwAspect = 1  # DVASPECT_CONTENT
            target[index].lindex = lindex
            target[index].tymed = tymed
        self._cursor += len(available)
        if pcelt_fetched:
            ctypes.cast(pcelt_fetched, ctypes.POINTER(wintypes.ULONG))[0] = len(available)
        return S_OK if len(available) == int(celt) else S_FALSE

    def _skip(self, _this, celt) -> int:
        self._cursor = min(self._cursor + int(celt), len(self._formats))
        return S_OK

    def _reset(self, _this) -> int:
        self._cursor = 0
        return S_OK

    def _clone(self, _this, out) -> int:
        if not out:
            return E_POINTER
        clone = FormatEnumerator(self._formats)
        clone._cursor = self._cursor
        # Клон обязан пережить возврат: держим его на себе, иначе Python
        # соберёт объект, а Проводник уйдёт по освобождённому адресу.
        self._clones = getattr(self, "_clones", [])
        self._clones.append(clone)
        ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p))[0] = clone.pointer
        return S_OK


class VirtualFilesDataObject(ComObject):
    """То, что лежит в буфере обмена вместо файлов, которых здесь нет."""

    def __init__(
        self,
        manifest: TransferManifest,
        open_pipe,
        request_read,
        close_pipe,
        origin_marker: bytes,
        async_capability: bool = False,
    ) -> None:
        super().__init__([IID_IUNKNOWN, IID_IDATAOBJECT])
        self._manifest = manifest
        self._open_pipe = open_pipe
        self._request_read = request_read
        self._close_pipe = close_pipe
        self._origin_marker = origin_marker
        self.async_mode = False
        self.in_operation = False
        #: lindex -> PipeStream. Держит поток живым: собранный сборщиком мусора
        #: поток - это переход Проводника по освобождённому адресу.
        self.streams: dict[int, PipeStream] = {}
        #: Для диагностики: что и в каком порядке спросил Проводник.
        self.get_data_calls: list[tuple[int, int]] = []
        self._enumerators: list[FormatEnumerator] = []
        self._handles: list[int] = []

        self.cf_descriptor = register_clipboard_format(FORMAT_DESCRIPTOR_NAME)
        self.cf_contents = register_clipboard_format(FORMAT_CONTENTS_NAME)
        self.cf_drop_effect = register_clipboard_format(FORMAT_DROP_EFFECT_NAME)
        self.cf_origin = register_clipboard_format(FORMAT_ORIGIN_NAME)

        self.extend_vtable([
            _GET_DATA(self._get_data),
            _GET_DATA_HERE(self._get_data_here),
            _QUERY_GET_DATA(self._query_get_data),
            _GET_CANONICAL(self._get_canonical),
            _SET_DATA(self._set_data),
            _ENUM_FORMAT_ETC(self._enum_format_etc),
            _D_ADVISE(self._d_advise),
            _D_UNADVISE(self._d_unadvise),
            _ENUM_D_ADVISE(self._enum_d_advise),
        ])
        if async_capability:
            self.add_interface(IID_IASYNCCAPABILITY)
            self.extend_vtable([
                _SET_ASYNC(self._set_async_mode),
                _GET_ASYNC(self._get_async_mode),
                _START_OP(self._start_operation),
                _IN_OP(self._in_operation_query),
                _END_OP(self._end_operation),
            ])

    def _advertised(self) -> list[tuple[int, int, int]]:
        return [
            (self.cf_descriptor, -1, TYMED_HGLOBAL),
            (self.cf_contents, 0, TYMED_ISTREAM),
            (self.cf_drop_effect, -1, TYMED_HGLOBAL),
            (self.cf_origin, -1, TYMED_HGLOBAL),
        ]

    def _query_get_data(self, _this, pformatetc) -> int:
        fmt = ctypes.cast(pformatetc, ctypes.POINTER(FORMATETC)).contents
        if fmt.cfFormat == self.cf_contents:
            return S_OK if fmt.tymed & TYMED_ISTREAM else DV_E_TYMED
        if fmt.cfFormat in (self.cf_descriptor, self.cf_drop_effect, self.cf_origin):
            return S_OK
        return DV_E_FORMATETC

    def _get_data(self, _this, pformatetc, pmedium) -> int:
        fmt = ctypes.cast(pformatetc, ctypes.POINTER(FORMATETC)).contents
        self.get_data_calls.append((fmt.cfFormat, fmt.lindex))
        medium = ctypes.cast(pmedium, ctypes.POINTER(STGMEDIUM)).contents
        medium.pUnkForRelease = None

        if fmt.cfFormat == self.cf_descriptor:
            return self._hand_over_bytes(medium, group_descriptor_bytes(self._manifest))
        if fmt.cfFormat == self.cf_drop_effect:
            # Всегда COPY. Ctrl+X на источнике сюда не доходит.
            return self._hand_over_bytes(medium, DROPEFFECT_COPY.to_bytes(4, "little"))
        if fmt.cfFormat == self.cf_origin:
            return self._hand_over_bytes(medium, self._origin_marker)
        if fmt.cfFormat == self.cf_contents:
            return self._hand_over_stream(medium, fmt)
        return DV_E_FORMATETC

    def _hand_over_bytes(self, medium, payload: bytes) -> int:
        handle = to_hglobal(payload)
        self._handles.append(handle)
        medium.tymed = TYMED_HGLOBAL
        medium.data = handle
        return S_OK

    def _hand_over_stream(self, medium, fmt) -> int:
        if not fmt.tymed & TYMED_ISTREAM:
            return DV_E_TYMED
        entries = descriptor_entries(self._manifest)
        index = int(fmt.lindex)
        if not 0 <= index < len(entries):
            return DV_E_FORMATETC
        entry = entries[index]
        if entry.kind != ENTRY_FILE:
            return DV_E_FORMATETC

        transfer_id = self._manifest.transfer_id
        pipe = self._open_pipe(transfer_id, index)
        stream = PipeStream(
            pipe,
            size=entry.size,
            request=lambda offset, length, i=index: self._request_read(
                transfer_id, i, offset, length
            ),
            on_release=lambda i=index: self._close_pipe(transfer_id, i, None),
        )
        self.streams[index] = stream
        medium.tymed = TYMED_ISTREAM
        medium.data = stream.pointer
        return S_OK

    def _enum_format_etc(self, _this, direction, ppenum) -> int:
        if int(direction) != DATADIR_GET:
            return E_NOTIMPL
        if not ppenum:
            return E_POINTER
        enumerator = FormatEnumerator(self._advertised())
        self._enumerators.append(enumerator)
        ctypes.cast(ppenum, ctypes.POINTER(ctypes.c_void_p))[0] = enumerator.pointer
        return S_OK

    def _get_data_here(self, _this, _fmt, _medium) -> int:
        return E_NOTIMPL

    def _get_canonical(self, _this, _fmt, _out) -> int:
        return E_NOTIMPL

    def _set_data(self, _this, _fmt, _medium, _release) -> int:
        return E_NOTIMPL

    def _d_advise(self, _this, _fmt, _flags, _sink, _conn) -> int:
        return E_NOTIMPL

    def _d_unadvise(self, _this, _connection) -> int:
        return E_NOTIMPL

    def _enum_d_advise(self, _this, _out) -> int:
        return E_NOTIMPL
```

Port the five `IDataObjectAsyncCapability` methods verbatim from Task 0.4, with
`self.on_operation_finished` replacing the spike's `note()` — the owner needs
the completion signal, not a log line:

```python
    def _end_operation(self, _this, result, _reserved, _effects) -> int:
        self.in_operation = False
        if self.on_operation_finished is not None:
            # Источник истины о завершении сессии - тот, что записал спайк 1
            # (спека §9). НЕ переопределяйте его из этого плана.
            self.on_operation_finished(int(result))
        return S_OK
```

Add the matching `call_get_data`, `call_query_get_data` and
`call_enum_format_etc` helpers to `windows_com.py`, plus the missing
`WINFUNCTYPE` prototypes (`_GET_DATA`, `_QUERY_GET_DATA`, `_ENUM_NEXT`, …).
`call_get_data` takes `want_medium=False` and returns `(hresult, payload)` or
`(hresult, medium.tymed, payload)`, reading `HGLOBAL` contents when the medium
is `TYMED_HGLOBAL`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_windows_data_object.py -v`
Expected: PASS, 17 passed

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/ configurator/tests/transfer/test_windows_data_object.py
git commit -m "Serve descriptors and streams from one data object

Four things differ from the spike, and each is why this is a rewrite
rather than a copy: the descriptor comes from the manifest, the contents
format returns a pipe-backed stream instead of a synthetic generator, a
stream's release reports which entry finished, and the origin marker is
advertised so our own publication is not mistaken for a local copy.

Streams and enumerators are held on the object. A garbage-collected COM
object is Explorer jumping to a freed address: a crash with no exception
and nothing in the log.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 2.5: the STA thread, OleSetClipboard, and the one road back to Qt

**Files:**
- Modify: `configurator/src/duo_input/transfer/windows_files.py`
- Test: `configurator/tests/transfer/test_windows_publisher.py`

**Interfaces:**
- Consumes: `VirtualFilesDataObject` (Task 2.4); `FileTransferService`
  (Tasks 1.10–1.11).
- Produces:
  - `post_to_service(service, slot: str, *args) -> None`
  - `class WindowsFileClipboardBackend(QObject)` with
    `.start() -> None`, `.stop() -> None`,
    `.publish(manifest: TransferManifest, origin_marker: bytes) -> None`,
    `.thread_id -> int | None`, `.is_running -> bool`
  - signal `publish_failed = Signal(str)`

`post_to_service` is the **only** road from the COM thread back to Qt, and
centralising it is what makes the boundary checkable by reading one function
instead of auditing every call site.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_windows_publisher.py
"""Поток STA и единственная дорога обратно в Qt.

Тесты, которым нужна настоящая сессия Windows, отделены переменной окружения -
тем же приёмом, что DUO_INPUT_HIL_WRITE в tests/integration.
"""

from __future__ import annotations

import os
import sys
import threading

import pytest
from PySide6.QtCore import QObject, Signal, Slot

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="буфер обмена - это Windows")

NEEDS_SESSION = pytest.mark.skipif(
    os.environ.get("DUO_INPUT_COM_SESSION") != "1",
    reason="set DUO_INPUT_COM_SESSION=1 in a real desktop session",
)

from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest
from duo_input.transfer.windows_files import WindowsFileClipboardBackend, post_to_service


class _Recorder(QObject):
    got = Signal(str, int, int, int)

    def __init__(self) -> None:
        super().__init__()
        self.calls: list[tuple] = []
        self.thread_ids: list[int] = []

    @Slot(str, int, int, int)
    def request_read(self, transfer_id, entry_index, offset, length) -> None:
        self.calls.append((transfer_id, entry_index, offset, length))
        self.thread_ids.append(threading.get_ident())
        self.got.emit(transfer_id, entry_index, offset, length)


def test_a_post_from_another_thread_arrives_on_the_qt_thread(qtbot):
    # Это и есть инвариант "COM-поток не трогает Qt напрямую": вызов
    # пересекает границу через очередь Qt, а не прямым обращением.
    recorder = _Recorder()
    qt_thread = threading.get_ident()

    def worker() -> None:
        post_to_service(recorder, "request_read", "t-1", 0, 4096, 65536)

    with qtbot.waitSignal(recorder.got, timeout=5000):
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        thread.join(timeout=2.0)

    assert recorder.calls == [("t-1", 0, 4096, 65536)]
    assert recorder.thread_ids == [qt_thread], (
        "слот исполнился НЕ на Qt-потоке - queued connection не сработал, "
        "и следующий шаг тронул бы QSslSocket из чужого потока"
    )


def test_a_post_to_a_missing_slot_is_reported_rather_than_swallowed(qtbot, caplog):
    recorder = _Recorder()

    post_to_service(recorder, "no_such_slot", "t", 0, 0, 0)

    assert any("no_such_slot" in record.message for record in caplog.records)


def test_the_backend_starts_a_thread_of_its_own_and_reports_its_id(qtbot):
    backend = WindowsFileClipboardBackend()

    backend.start()
    qtbot.waitUntil(lambda: backend.thread_id is not None, timeout=5000)

    assert backend.is_running
    assert backend.thread_id != threading.get_ident(), (
        "COM живёт на GUI-потоке - тогда 20 ГБ чтения заморозили бы интерфейс"
    )
    backend.stop()
    assert not backend.is_running


def test_stopping_a_backend_that_never_started_is_harmless():
    backend = WindowsFileClipboardBackend()

    backend.stop()

    assert not backend.is_running


def test_starting_twice_does_not_create_a_second_thread(qtbot):
    backend = WindowsFileClipboardBackend()
    backend.start()
    qtbot.waitUntil(lambda: backend.thread_id is not None, timeout=5000)
    first = backend.thread_id

    backend.start()

    assert backend.thread_id == first
    backend.stop()


@NEEDS_SESSION
def test_publishing_puts_our_data_object_on_the_real_clipboard(qtbot):
    from duo_input.transfer.windows_com import register_clipboard_format
    from duo_input.transfer.windows_files import FORMAT_DESCRIPTOR_NAME

    backend = WindowsFileClipboardBackend()
    backend.start()
    qtbot.waitUntil(lambda: backend.thread_id is not None, timeout=5000)
    manifest = TransferManifest(
        transfer_id="t-1",
        entries=(TransferEntry(path="a.bin", kind=ENTRY_FILE, size=4, mtime_ns=1),),
    )

    backend.publish(manifest, origin_marker=b"origin:1")
    qtbot.wait(500)

    import ctypes

    cf = register_clipboard_format(FORMAT_DESCRIPTOR_NAME)
    assert ctypes.windll.user32.IsClipboardFormatAvailable(cf), (
        "формат дескриптора отсутствует в буфере - Проводник не предложит вставку"
    )
    backend.stop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_windows_publisher.py -v`
Expected: FAIL — `ImportError: cannot import name 'post_to_service'`

- [ ] **Step 3: Write the implementation**

```python
# append to windows_files.py — this is the ONLY place in transfer/ that
# imports PySide6 alongside ctypes.
import logging
import threading

from PySide6.QtCore import QObject, Qt, QMetaObject, Q_ARG, Signal

logger = logging.getLogger(__name__)

#: Как часто поток STA проверяет, не пора ли остановиться.
_PUMP_INTERVAL_MS = 20


def post_to_service(service, slot: str, *args) -> None:
    """ЕДИНСТВЕННАЯ дорога из COM-потока обратно в Qt.

    Одна функция, а не вызов invokeMethod по месту, - чтобы границу можно было
    проверить чтением одной функции вместо ревизии каждой точки вызова.
    Прямое обращение к слоту отсюда тронуло бы QSslSocket из чужого потока, и
    дефект проявлялся бы раз в сто запусков.
    """
    typed = []
    for value in args:
        if isinstance(value, bool) or not isinstance(value, (int, str)):
            typed.append(Q_ARG(object, value))
        elif isinstance(value, int):
            typed.append(Q_ARG(int, value))
        else:
            typed.append(Q_ARG(str, value))
    delivered = QMetaObject.invokeMethod(
        service, slot, Qt.ConnectionType.QueuedConnection, *typed
    )
    if not delivered:
        # Молча потерянный вызов означал бы навсегда заблокированный Read.
        logger.error("не удалось доставить %s в Qt-поток", slot)


class WindowsFileClipboardBackend(QObject):
    """Поток STA, владеющий буфером обмена и всеми COM-объектами.

    Выделенный поток, а не GUI-поток, по построению: STA сериализует входящие
    COM-вызовы через свой насос сообщений, поэтому наши объекты не обязаны
    быть потокобезопасными, а GUI-поток не блокируется на чтении - не как
    следствие тонкости маршалинга, а потому что COM-вызовы до него не доходят.
    """

    publish_failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._ready = threading.Event()
        self._thread_id: int | None = None
        self._pending: list[tuple] = []
        self._lock = threading.Lock()
        self._published: VirtualFilesDataObject | None = None
        self._callbacks: dict[str, object] = {}

    @property
    def thread_id(self) -> int | None:
        return self._thread_id

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_callbacks(self, open_pipe, request_read, close_pipe, on_operation_finished) -> None:
        """Колбэки, через которые COM-объекты достигают сервиса."""
        self._callbacks = {
            "open_pipe": open_pipe,
            "request_read": request_read,
            "close_pipe": close_pipe,
            "on_operation_finished": on_operation_finished,
        }

    def start(self) -> None:
        if self.is_running:
            return
        self._stopping.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, name="duo-input-com-sta", daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)

    def stop(self) -> None:
        if self._thread is None:
            return
        self._stopping.set()
        self._thread.join(timeout=5.0)
        self._thread = None
        self._thread_id = None
        self._published = None

    def publish(self, manifest: TransferManifest, origin_marker: bytes) -> None:
        """Поставить дерево в очередь на публикацию из потока STA.

        Не публикуем отсюда: OleSetClipboard обязан быть вызван на том потоке,
        который потом отвечает на GetData. Вызов с GUI-потока сделал бы
        владельцем буфера его, и все чтения пришли бы туда.
        """
        with self._lock:
            self._pending.append((manifest, origin_marker))

    # ------------------------------------------------------------------ поток STA

    def _run(self) -> None:
        ctypes.oledll.ole32.OleInitialize(None)
        self._thread_id = threading.get_ident()
        self._ready.set()
        message = wintypes.MSG()
        try:
            while not self._stopping.is_set():
                self._drain_pending()
                while ctypes.windll.user32.PeekMessageW(
                    ctypes.byref(message), None, 0, 0, 1
                ):
                    ctypes.windll.user32.TranslateMessage(ctypes.byref(message))
                    ctypes.windll.user32.DispatchMessageW(ctypes.byref(message))
                self._stopping.wait(_PUMP_INTERVAL_MS / 1000)
        finally:
            # Отдать буфер системе, иначе он умрёт вместе с потоком и
            # вставка после выхода отдала бы пустоту.
            ctypes.windll.ole32.OleFlushClipboard()
            ctypes.windll.ole32.OleUninitialize()

    def _drain_pending(self) -> None:
        with self._lock:
            pending = self._pending[-1:] if self._pending else []
            self._pending.clear()
        for manifest, origin_marker in pending:
            self._publish_now(manifest, origin_marker)

    def _publish_now(self, manifest: TransferManifest, origin_marker: bytes) -> None:
        try:
            data_object = VirtualFilesDataObject(
                manifest,
                open_pipe=self._callbacks["open_pipe"],
                request_read=self._callbacks["request_read"],
                close_pipe=self._callbacks["close_pipe"],
                origin_marker=origin_marker,
                async_capability=ASYNC_CAPABILITY_REQUIRED,
            )
            data_object.on_operation_finished = self._callbacks["on_operation_finished"]
            result = ctypes.windll.ole32.OleSetClipboard(data_object.pointer)
            if result != S_OK:
                raise OSError(f"OleSetClipboard вернул 0x{result & 0xFFFFFFFF:08X}")
            # Держим объект: буфер обмена хранит только указатель.
            self._published = data_object
        except Exception as error:  # noqa: BLE001 - падение потока STA убило бы фичу молча
            logger.exception("не удалось опубликовать файлы в буфер обмена")
            post_to_service(self, "_report_publish_failure", str(error))

    @Slot(str)
    def _report_publish_failure(self, reason: str) -> None:
        self.publish_failed.emit(reason)
```

Add at module level, set from Task 0.5's record:

```python
#: Требуется ли IDataObjectAsyncCapability. Значение поставлено по записи
#: спайка 1 (records/2026-09-12-explorer-virtual-files-spike.md), а не по
#: теории: если тот прогон не показал наблюдаемой разницы, здесь False, и
#: пять методов интерфейса из VirtualFilesDataObject удалены.
ASYNC_CAPABILITY_REQUIRED = True
```

Import `Slot` from `PySide6.QtCore`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_windows_publisher.py -v`
Expected: PASS, 5 passed and 1 skipped (the session test).

Then, in an interactive desktop session:

Run: `DUO_INPUT_COM_SESSION=1 .venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_windows_publisher.py -v`
Expected: PASS, 6 passed.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/windows_files.py configurator/tests/transfer/test_windows_publisher.py
git commit -m "Own the clipboard from a dedicated STA thread

OleSetClipboard runs on the thread that will answer GetData, because
calling it from the GUI thread would make that thread the clipboard owner
and route every read there - which is exactly what a 20 GB paste must not
do.

post_to_service is the only road from the COM thread back to Qt, so the
boundary is checkable by reading one function instead of auditing every
call site. A test asserts the slot really executes on the Qt thread: if
the queued connection silently failed, the next step would touch
QSslSocket from the wrong thread.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 2.6: platform selection and the four boundary rules

**Files:**
- Create: `configurator/src/duo_input/transfer/platform_files.py`
- Modify: `configurator/tests/clipboard/test_boundaries.py`
- Test: `configurator/tests/transfer/test_platform_files.py`

**Interfaces:**
- Consumes: `WindowsFileClipboardBackend` (Task 2.5).
- Produces:
  - `class UnsupportedPlatformError(Exception)`
  - `create_file_backend(parent=None)` returning the platform backend

Modelled on `clipboard/platform_backend.py` exactly: one `sys.platform` branch,
lazy imports inside the branches, so a macOS build never imports `ctypes` COM
code and a Windows build never imports a macOS adapter.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_platform_files.py
"""Единственная ветка по платформе в подсистеме передачи."""

from __future__ import annotations

import sys

import pytest

from duo_input.transfer.platform_files import UnsupportedPlatformError, create_file_backend


@pytest.mark.skipif(sys.platform != "win32", reason="Windows-ветка")
def test_on_windows_the_windows_backend_is_chosen(qapp):
    from duo_input.transfer.windows_files import WindowsFileClipboardBackend

    assert isinstance(create_file_backend(), WindowsFileClipboardBackend)


def test_an_unknown_platform_is_refused_loudly_rather_than_silently(monkeypatch):
    monkeypatch.setattr(sys, "platform", "haiku")

    with pytest.raises(UnsupportedPlatformError):
        create_file_backend()


def test_macos_is_refused_because_m1_does_not_implement_it(monkeypatch):
    # Спека §18: ядро от Windows не зависит, но адаптера для Finder нет, и
    # молчаливая заглушка выглядела бы как работающая фича.
    monkeypatch.setattr(sys, "platform", "darwin")

    with pytest.raises(UnsupportedPlatformError):
        create_file_backend()
```

```python
# append to configurator/tests/clipboard/test_boundaries.py
TRANSFER_PACKAGE = Path(__file__).resolve().parents[2] / "src" / "duo_input" / "transfer"

_PURE_MODULES = ("model.py", "paths.py", "pipe.py", "scanner.py")
_CTYPES_ALLOWED = ("windows_com.py", "windows_files.py")


def _transfer_modules() -> list[Path]:
    modules = sorted(TRANSFER_PACKAGE.glob("*.py"))
    assert modules, f"no modules found under {TRANSFER_PACKAGE}"
    return modules


def test_the_transfer_package_never_imports_qtwidgets():
    offenders = {
        path.name
        for path in _transfer_modules()
        if any(name.startswith("PySide6.QtWidgets") for name in _imported_modules(path))
    }

    assert offenders == set(), (
        "transfer/ должен зависеть только от QtCore, иначе его нельзя будет "
        "вынести в отдельный процесс"
    )


def test_only_the_windows_adapters_touch_ctypes():
    offenders = {
        path.name
        for path in _transfer_modules()
        if path.name not in _CTYPES_ALLOWED
        and any(name.split(".")[0] == "ctypes" for name in _imported_modules(path))
    }

    assert offenders == set(), (
        "ctypes разрешён только в windows_com.py и windows_files.py — вся "
        "нативная грязь должна быть в одном месте"
    )


def test_the_com_module_never_imports_pyside():
    # Буквальная граница "COM-поток не трогает Qt". Без неё однажды кто-нибудь
    # дёрнет QSslSocket из COM-потока, потому что так короче.
    imported = _imported_modules(TRANSFER_PACKAGE / "windows_com.py")

    assert not any(name.startswith("PySide6") for name in imported), (
        "windows_com.py исполняется на COM-потоке и не имеет права видеть Qt"
    )


def test_the_pure_core_modules_see_neither_qt_nor_the_windows_adapters():
    offenders = {}
    for name in _PURE_MODULES:
        imported = _imported_modules(TRANSFER_PACKAGE / name)
        bad = {
            candidate
            for candidate in imported
            if candidate.startswith("PySide6") or "windows_" in candidate
        }
        if bad:
            offenders[name] = bad

    assert offenders == {}, (
        "ядро (model/paths/pipe/scanner) должно оставаться проверяемым без Qt "
        f"и без Windows, а эти модули это нарушают: {offenders}"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_platform_files.py configurator/tests/clipboard/test_boundaries.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'duo_input.transfer.platform_files'`

- [ ] **Step 3: Write minimal implementation**

```python
# configurator/src/duo_input/transfer/platform_files.py
"""Выбор реализации границы платформы для передачи файлов.

Единственный sys.platform в подсистеме, по образцу
clipboard/platform_backend.py. Импорты ленивые и внутри ветвей: сборка под
macOS никогда не импортирует windows_files, а значит и windows_com, а значит и
ctypes-описания COM. В runtime-графе macOS Windows-кода нет вовсе.

macOS отвергается явно, а не заглушкой: аналог здесь -
NSFilePromiseProvider, его в M1 нет, и молчаливая заглушка выглядела бы как
работающая фича (спека §18).
"""

from __future__ import annotations

import sys


class UnsupportedPlatformError(Exception):
    """Платформа, для которой передачи файлов пока нет."""


def create_file_backend(parent=None):
    if sys.platform == "win32":
        from .windows_files import WindowsFileClipboardBackend

        return WindowsFileClipboardBackend(parent)
    raise UnsupportedPlatformError(sys.platform)


__all__ = ["UnsupportedPlatformError", "create_file_backend"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_platform_files.py configurator/tests/clipboard/test_boundaries.py -v`
Expected: PASS

If `test_only_the_windows_adapters_touch_ctypes` fails on a module you did not
expect, **move the code, do not relax the rule.** The rule is the mechanism that
keeps the macOS milestone from becoming a rewrite.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/transfer/platform_files.py configurator/tests/transfer/test_platform_files.py configurator/tests/clipboard/test_boundaries.py
git commit -m "Select the file backend by platform and enforce four boundaries

Lazy imports inside the branches, exactly as the clipboard boundary does
it, so a macOS build never imports the ctypes COM descriptions at all.

The fourth rule is the load-bearing one: windows_com.py may not import
PySide6. That is the literal statement that the COM thread does not touch
Qt, and without it someone eventually reaches for QSslSocket from the COM
thread because it is shorter.

macOS is refused out loud rather than stubbed, because a silent stub
looks like a working feature.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 2.7: close the clipboard loop the origin marker leaves open

**Files:**
- Modify: `configurator/src/duo_input/clipboard/windows_backend.py` (`is_private`)
- Test: `configurator/tests/clipboard/test_windows_backend.py` (append)
- Test: `configurator/tests/transfer/test_guards_are_real.py` (append)

**Interfaces:**
- Consumes: `ORIGIN_MIME` from `clipboard/backend.py`;
  `FORMAT_ORIGIN_NAME` from `transfer/windows_files.py` (Task 2.4).
- Produces:
  - `clipboard/windows_backend.py`: `wrapped_windows_mime(name: str) -> str`
  - `is_private` recognises both spellings

`is_private` (`windows_backend.py:47`) looks for the exact string
`application/x-duo-input-origin`. Qt surfaces an unknown native clipboard format
as `application/x-qt-windows-mime;value="<name>"`, so the exact match never
fires, the marker does nothing, and our own publication comes back as a local
copy. This is spec §14 — a defect that exists already and would only become
visible once Task 2.5 starts publishing.

- [ ] **Step 1: Write the failing test**

```python
# append to configurator/tests/clipboard/test_windows_backend.py
from duo_input.clipboard.windows_backend import wrapped_windows_mime


def test_the_bare_origin_marker_is_still_recognised():
    assert is_private([ORIGIN_MIME])


def test_the_qt_wrapped_origin_marker_is_recognised_too():
    # Qt показывает незнакомый нативный формат так, и точное сравнение строк
    # с ORIGIN_MIME здесь не срабатывало никогда.
    assert is_private([wrapped_windows_mime(ORIGIN_MIME)])


def test_the_wrapped_spelling_is_exactly_what_qt_produces():
    assert wrapped_windows_mime(ORIGIN_MIME) == (
        'application/x-qt-windows-mime;value="application/x-duo-input-origin"'
    )


def test_an_unrelated_wrapped_format_is_not_treated_as_ours():
    assert not is_private([wrapped_windows_mime("SomeOtherApplicationFormat")])


def test_our_own_virtual_file_publication_is_not_taken_for_a_local_copy(qapp):
    # Конец петли: буфер, несущий наш маркер и наши форматы виртуальных
    # файлов, не порождает ни payload, ни путей.
    from duo_input.transfer.windows_files import FORMAT_ORIGIN_NAME

    mime_data = QMimeData()
    mime_data.setData(wrapped_windows_mime(FORMAT_ORIGIN_NAME), QByteArray(b"origin:1"))

    snapshot = snapshot_from(mime_data)

    assert snapshot.is_empty
```

```python
# append to configurator/tests/transfer/test_guards_are_real.py
def test_without_the_wrapped_spelling_our_own_publication_would_loop(monkeypatch):
    """Спека §14: обе половины обязаны работать, и это проверяется снятием одной."""
    from duo_input.clipboard import windows_backend
    from duo_input.clipboard.backend import ORIGIN_MIME

    monkeypatch.setattr(windows_backend, "wrapped_windows_mime", lambda name: name)

    assert not windows_backend.is_private(
        ['application/x-qt-windows-mime;value="application/x-duo-input-origin"']
    ), (
        "обёрнутое написание снято, а маркер всё равно узнан - значит "
        "проверка петли смотрит не на то написание, которое даёт Qt"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard/test_windows_backend.py -v -k "wrapped or origin or loop"`
Expected: FAIL — `ImportError: cannot import name 'wrapped_windows_mime'`

- [ ] **Step 3: Write minimal implementation**

In `configurator/src/duo_input/clipboard/windows_backend.py`:

```python
def wrapped_windows_mime(name: str) -> str:
    """Как Qt показывает незарегистрированный у себя нативный формат буфера.

    Наш маркер происхождения объявляется через RegisterClipboardFormatW под
    собственным именем, а Qt не знает такого MIME и заворачивает его вот так.
    Точное сравнение с ORIGIN_MIME не срабатывало здесь НИКОГДА - и это не
    теория: до этой правки наша собственная публикация виртуальных файлов
    вернулась бы в тот же процесс как обычное локальное копирование,
    объявилась бы второму компьютеру и закрыла бы петлю (спека §14).
    """
    return f'application/x-qt-windows-mime;value="{name}"'


def is_private(formats: list[str]) -> bool:
    """Просило ли содержимое, чтобы его не запоминали и не пересылали."""
    if ORIGIN_MIME in formats or wrapped_windows_mime(ORIGIN_MIME) in formats:
        return True
    return any(marker in formats for marker in PRIVATE_MARKERS)
```

Extend `__all__` with `"wrapped_windows_mime"`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/clipboard configurator/tests/transfer -q`
Expected: PASS

- [ ] **Step 5: Run the full gate**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests tests -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/clipboard/windows_backend.py configurator/tests/
git commit -m "Recognise the origin marker in the spelling Qt actually produces

is_private looked for the bare MIME name, but Qt surfaces an unregistered
native clipboard format as application/x-qt-windows-mime;value=\"...\", so
the exact match never fired. The marker did nothing, and our own virtual-
file publication would have come back into this process as an ordinary
local copy and closed the loop.

A mutation test removes the wrapped spelling and demands the loop test
fail, because both halves have to work and only one of them was there.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

# Phase 3 — Spike 2 and the measurement gate

**Purpose:** measure the real chain
`Explorer → IStream → ChunkPipe → FileTransferService → PeerLink/TLS → source`
and close §22 question 2 with a number.

**Precondition:** Phase 2 is complete and the full gate is green.

**The gate, stated before any measuring:** if one outstanding `FILE_READ`
reaches **70% or more of the plain-TCP LAN ceiling**, **no window and no
prefetch are added at all.** YAGNI.

Below 70% is not permission to add a window either. It is an instruction to
localise the bottleneck first (Task 3.2, Step 3): a window is permitted only
when the evidence shows the limit really is the sequential
`FILE_READ → FILE_CHUNK` round trip, and even then only together with a
generation or read-token rule that stops a stale chunk reaching `IStream` after
a `Seek`. A bottleneck in disk read, the GUI thread, the socket or TLS is fixed
where it lives, not masked with a window.

The same discipline applies to the sender's disk read: it stays on the GUI
thread only if the p99 measurement says that is safe.

## Task 3.1: the measurement harness

**Files:**
- Create: `configurator/tests/transfer/spike_measure_bridge.py`
- Test: `configurator/tests/transfer/test_spike_measure_bridge.py`

**Interfaces:**
- Consumes: `FileTransferService`, `PeerLink`, `PeerListener`,
  `WindowsFileClipboardBackend`, `Instrument` (Task 0.4).
- Produces:
  - `Measurement` frozen dataclass with `bytes_transferred: int`,
    `elapsed_seconds: float`, `throughput_mib_s: float`,
    `peak_rss_bytes: int`, `peak_python_bytes: int`,
    `pipe_high_water: int`, `cancel_latency_seconds: float | None`,
    `gui_tick_p99_ms: float`, `gui_tick_max_ms: float`,
    `socket_bytes_to_write_max: int`, `heartbeat_gaps_seconds: list[float]`,
    `rtt_median_ms: float`, `rtt_p99_ms: float`,
    `disk_read_median_ms: float`, `disk_read_p99_ms: float`,
    `bypassed_transport_mib_s: float`
  - `Measurement.rtt_bound_mib_s(chunk_bytes: int) -> float` — what the
    sequential round trip alone would allow, which is the number Task 3.2's
    gate compares against
  - `Measurement.as_table() -> str`
  - `measure(size: int, cancel_after_bytes: int | None = None) -> Measurement`

The harness gets its own test because a broken instrument reports a healthy
system no matter what the system does — the same reason `Instrument` was tested
in Task 0.4.

- [ ] **Step 1: Write the failing test**

```python
# configurator/tests/transfer/test_spike_measure_bridge.py
"""Прибор проверяется отдельно от того, что он измеряет."""

from __future__ import annotations

import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="мост - это Windows")

from spike_measure_bridge import Measurement


def _measurement(**overrides) -> Measurement:
    fields = dict(
        bytes_transferred=1024 * 1024 * 100,
        elapsed_seconds=2.0,
        peak_rss_bytes=180 * 1024 * 1024,
        peak_python_bytes=3 * 1024 * 1024,
        pipe_high_water=1,
        cancel_latency_seconds=None,
        gui_tick_p99_ms=18.0,
        gui_tick_max_ms=41.0,
        socket_bytes_to_write_max=65536,
        rtt_median_ms=0.8,
        rtt_p99_ms=2.4,
        disk_read_median_ms=0.3,
        disk_read_p99_ms=1.1,
        bypassed_transport_mib_s=112.0,
        heartbeat_gaps_seconds=[10.0, 10.1],
    )
    fields.update(overrides)
    return Measurement(**fields)


def test_throughput_is_derived_from_the_bytes_and_the_clock():
    assert _measurement().throughput_mib_s == pytest.approx(50.0, rel=0.01)


def test_a_zero_length_run_reports_no_throughput_instead_of_dividing_by_zero():
    assert _measurement(bytes_transferred=0, elapsed_seconds=0.0).throughput_mib_s == 0.0


def test_the_table_names_the_worst_gui_tick_not_only_the_typical_one():
    table = _measurement(gui_tick_max_ms=1400.0).as_table()

    assert "1400" in table, (
        "отчёт без максимума скрыл бы ровно то замирание, ради которого "
        "измерение и делается"
    )


def test_the_table_reports_the_peak_queue_depth():
    assert "high_water" in _measurement().as_table()


def test_the_rtt_bound_says_what_one_sequential_round_trip_alone_would_allow():
    # 256 КиБ за 0.8 мс = 312 МиБ/с. Если это намного выше измеренной
    # пропускной способности, узкое место НЕ в круге, и окно его не сдвинет.
    assert _measurement().rtt_bound_mib_s(256 * 1024) == pytest.approx(312.5, rel=0.01)


def test_a_zero_rtt_reports_no_bound_instead_of_dividing_by_zero():
    assert _measurement(rtt_median_ms=0.0).rtt_bound_mib_s(256 * 1024) == 0.0


def test_the_table_names_the_bottleneck_evidence_the_gate_needs():
    table = _measurement().as_table()

    for needed in ("RTT", "чтение с диска", "транспорт без моста"):
        assert needed in table, (
            f"в отчёте нет строки {needed!r} - гейт задачи 3.2 не сможет "
            "отличить медленный круг от медленного транспорта"
        )


def test_the_table_reports_cancel_latency_as_absent_rather_than_as_zero():
    # Ноль означал бы "отмена мгновенна", а не "отмену не проверяли".
    table = _measurement(cancel_latency_seconds=None).as_table()

    assert "не измерялась" in table
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_spike_measure_bridge.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'spike_measure_bridge'`

- [ ] **Step 3: Write the harness**

```python
# configurator/tests/transfer/spike_measure_bridge.py
"""Throwaway: замер настоящей цепочки, от Проводника до файла-источника.

Запуск в НАСТОЯЩЕЙ сессии Windows:
    .venv\\Scripts\\python.exe configurator/tests/transfer/spike_measure_bridge.py --size-mib 2048

Отдельный от спайка 1 файл намеренно: там неизвестной была семантика
Проводника, здесь - поведение моста под нагрузкой. Смешивать две неизвестности
в одном эксперименте означает не узнать ни одну.
"""

from __future__ import annotations

import argparse
import time
import tracemalloc
from dataclasses import dataclass, field

MIB = 1024 * 1024


@dataclass(frozen=True)
class Measurement:
    bytes_transferred: int
    elapsed_seconds: float
    peak_rss_bytes: int
    peak_python_bytes: int
    pipe_high_water: int
    cancel_latency_seconds: float | None
    gui_tick_p99_ms: float
    gui_tick_max_ms: float
    socket_bytes_to_write_max: int
    #: Локализация узкого места. Без этих величин гейт задачи 3.2 не может
    #: отличить медленный круг от медленного транспорта, а он стоит ровно на
    #: этом различии.
    rtt_median_ms: float
    rtt_p99_ms: float
    disk_read_median_ms: float
    disk_read_p99_ms: float
    #: Пропускная способность ТОГО ЖЕ соединения с обойдённым мостом: кадры
    #: FILE_CHUNK подряд, без IStream и без очереди.
    bypassed_transport_mib_s: float
    heartbeat_gaps_seconds: list[float] = field(default_factory=list)

    @property
    def throughput_mib_s(self) -> float:
        if self.elapsed_seconds <= 0:
            return 0.0
        return self.bytes_transferred / MIB / self.elapsed_seconds

    def rtt_bound_mib_s(self, chunk_bytes: int) -> float:
        """Сколько дал бы один последовательный круг и больше ничего.

        Если это число близко к throughput_mib_s, а bypassed_transport_mib_s
        заметно выше - узкое место действительно в круге, и только тогда окно
        разрешено (задача 3.2, шаг 3).
        """
        if self.rtt_median_ms <= 0:
            return 0.0
        return chunk_bytes / MIB / (self.rtt_median_ms / 1000)

    def as_table(self) -> str:
        cancel = (
            "не измерялась"
            if self.cancel_latency_seconds is None
            else f"{self.cancel_latency_seconds * 1000:.0f} мс"
        )
        gaps = (
            f"{max(self.heartbeat_gaps_seconds):.1f} с"
            if self.heartbeat_gaps_seconds
            else "не измерялись"
        )
        return "\n".join(
            [
                "| величина | значение |",
                "|---|---|",
                f"| передано | {self.bytes_transferred / MIB:.0f} МиБ |",
                f"| время | {self.elapsed_seconds:.1f} с |",
                f"| пропускная способность | {self.throughput_mib_s:.1f} МиБ/с |",
                f"| пик RSS | {self.peak_rss_bytes / MIB:.0f} МиБ |",
                f"| пик Python (tracemalloc) | {self.peak_python_bytes / MIB:.1f} МиБ |",
                f"| pipe high_water | {self.pipe_high_water} чанков |",
                f"| задержка отмены | {cancel} |",
                f"| GUI tick p99 | {self.gui_tick_p99_ms:.1f} мс |",
                f"| GUI tick максимум | {self.gui_tick_max_ms:.1f} мс |",
                f"| socket bytesToWrite максимум | {self.socket_bytes_to_write_max} Б |",
                f"| RTT медиана / p99 | {self.rtt_median_ms:.2f} / {self.rtt_p99_ms:.2f} мс |",
                f"| чтение с диска медиана / p99 | {self.disk_read_median_ms:.2f} / {self.disk_read_p99_ms:.2f} мс |",
                f"| транспорт без моста | {self.bypassed_transport_mib_s:.1f} МиБ/с |",
                f"| наибольший промежуток heartbeat | {gaps} |",
            ]
        )
```

Then the `measure()` driver: stand up the loopback TLS pair exactly as
`test_end_to_end.py`'s `linked_pair` fixture does, start the real
`WindowsFileClipboardBackend`, publish a manifest for a generated file of the
requested size, run `spike_qt_responsiveness.Instrument` in the same process,
sample `QSslSocket.bytesToWrite()` and `ChunkPipe.high_water` on a 50 ms
`QTimer`, time every `FILE_READ`-to-`FILE_CHUNK` round trip and every
`SnapshotRegistry.read` call, make one separate back-to-back `FILE_CHUNK` pass
with the bridge bypassed to obtain `bypassed_transport_mib_s`, track `tracemalloc.get_traced_memory()` and RSS via
`ctypes.windll.psapi.GetProcessMemoryInfo`, record the interval between `PING`
frames observed on the link, and — when `cancel_after_bytes` is given — press
Explorer's Cancel and time how long the blocked `IStream::Read` takes to return.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/transfer/test_spike_measure_bridge.py -v`
Expected: PASS, 8 passed

- [ ] **Step 5: Commit**

```bash
git add configurator/tests/transfer/spike_measure_bridge.py configurator/tests/transfer/test_spike_measure_bridge.py
git commit -m "Build the bridge measurement harness and test the instrument

Separate from spike 1 on purpose: there the unknown was Explorer's
semantics, here it is the bridge under load, and one experiment holding
two unknowns answers neither.

Cancel latency reports as absent rather than zero, because zero would
claim cancellation is instant when it means nobody measured it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 3.2: DECISION GATE — measure, then decide about the window

**Files:**
- Create: `docs/superpowers/records/2026-09-12-file-transfer-bridge-measurement.md`
- Modify: `docs/superpowers/specs/2026-09-12-file-transfer-design.md` (§7, §8, §22)

**Interfaces:**
- Consumes: the harness from Task 3.1.
- Produces: the decision on the prefetch window and on where the sender reads
  from disk. **No production code unless a measurement demands it.**

- [ ] **Step 1: Run the measurements**

In a real desktop session, over the actual LAN between the two machines — not
loopback, because loopback would flatter the throughput number and this decision
turns on that number:

```
.venv\Scripts\python.exe configurator/tests/transfer/spike_measure_bridge.py --size-mib 2048 > configurator/tests/transfer/measure-plain.log 2>&1
.venv\Scripts\python.exe configurator/tests/transfer/spike_measure_bridge.py --size-mib 2048 --cancel-after-mib 512 > configurator/tests/transfer/measure-cancel.log 2>&1
```

- [ ] **Step 2: Compare the throughput against the link**

Measure the link's own ceiling first, so the comparison has a denominator:

Run: `.venv/Scripts/python.exe -c "import socket; print(socket.gethostbyname(socket.gethostname()))"`
then any plain TCP throughput check between the two machines (`iperf3`, or a
socket loop) to establish what the LAN itself delivers.

Fill in every row. The ratio is the decision variable; the rest are the
bottleneck evidence Step 3 needs.

| | value |
|---|---|
| LAN ceiling, plain TCP | |
| our throughput, one read in flight | |
| **ratio (ours / ceiling)** | |
| request/response RTT, median and p99 | |
| sender disk-read latency per chunk, median and p99 | |
| GUI tick p99 / max under load | |
| socket `bytesToWrite` maximum | |
| TLS/PeerLink throughput with the pipe bypassed | |
| peak Python memory | |
| pipe high_water | |
| cancel latency | |
| largest heartbeat gap | |

The "TLS/PeerLink throughput with the pipe bypassed" row needs its own short
run: send `FILE_CHUNK` frames of the measured chunk size back to back over the
same link with no `IStream` and no pipe, and time them. Without that number
there is no way to tell a slow round trip from a slow transport, and Step 3
turns on exactly that distinction.

- [ ] **Step 3: Apply the window gate**

The rule is fixed here, before any number exists, so that an inconvenient
measurement cannot be reinterpreted into a licence to add machinery.

**If ratio >= 70% of the plain-TCP LAN ceiling** — throughput is sufficient:

- add nothing;
- record in spec §8 that one read in flight was measured sufficient, with the
  numbers;
- close §22 question 2 as "no window needed, measured".

That is the expected and preferred outcome. Deleting an open question is
progress.

**If ratio < 70%** — this is **not** permission to add a window. It is an
instruction to localise the bottleneck first, from the rows already in the
Step 2 table:

| Evidence | What it means | What to do |
|---|---|---|
| RTT dominates: `bytes_per_chunk / RTT` ≈ our throughput, and bypassed TLS throughput is far higher | the sequential `FILE_READ → FILE_CHUNK` round trip really is the limit | a window is permitted — design it as its own task, with the rule below |
| sender disk-read p99 is a large share of RTT | the bottleneck is disk, not the protocol | move the sender's read to a worker thread (Step 4's mechanism), then re-measure |
| GUI tick p99/max is inflated | the Qt thread is starved and delaying our own replies | fix the starvation, then re-measure |
| `bytesToWrite` climbs, or bypassed TLS throughput is itself below the ceiling | the bottleneck is the socket or TLS | fix the transport, then re-measure |
| heartbeat gaps stretched | frames are being delayed behind something | find what, then re-measure |

**A window may only be designed when the evidence shows the bottleneck is
genuinely the sequential round trip.** If it is disk, the GUI thread, the socket,
TLS, or anywhere else, fix that — a window there would mask the defect while
leaving it in place, and masked defects in this repository have a habit of
resurfacing as something harder to find.

Re-measure after each fix and re-apply this gate. The 70% threshold does not
move.

**When a window is permitted**, it must carry:

- a `generation` or read-token in `FILE_READ` and echoed in `FILE_CHUNK`;
- `FileTransferService` bumping the generation on every `Seek`-induced position
  change, and dropping any chunk whose token does not match the current
  generation **before** `pipe.push`;
- a test that a stale chunk arriving after a `Seek` never reaches `IStream`;
- `ChunkPipe(capacity_chunks=N)` with N stated and justified by the measurement,
  and the memory-bound test updated to assert `high_water <= N`.

Without that rule the window is not added. This is not negotiable by
convenience: spec §8 names the rule as the condition.

Record in the measurement record which branch of the table fired, with the
numbers that put it there. "Below 70%, so we added a window" is not a finding;
"below 70% because RTT dominated at X ms against a bypassed-transport ceiling of
Y MiB/s" is.

- [ ] **Step 4: Apply the disk-read gate**

If `gui_tick_p99_ms` and `gui_tick_max_ms` under load are acceptable, the
sender's disk read stays on the GUI thread and the spec §7 paragraph is updated
with the measured numbers.

If they are not, move the sender's read to a worker thread using the **same**
`ChunkPipe` pattern inverted — `SnapshotRegistry.read` called on a reader thread,
results posted back through `post_to_service`. **The wire protocol does not
change**, and that invariance is the whole reason the escape hatch was named in
advance rather than improvised.

- [ ] **Step 5: Write the record**

Follow the existing record convention: environment, method, verbatim numbers,
analysis, decision. State plainly which of the two gates fired and which did
not. A gate that did not fire is a result, not an omission.

- [ ] **Step 6: Update the spec and close question 2**

Remove §22 question 2 if it closed. Update §8's "начальная схема потока" with
the measured verdict, replacing "гипотеза" with the number.

Run: `grep -nE "гипотеза, а не design invariant|256 KiB × 8" docs/superpowers/specs/2026-09-12-file-transfer-design.md`
Expected: no hits, or hits that now read as settled rather than open.

- [ ] **Step 7: Commit and STOP for review**

```bash
git add docs/superpowers/records/2026-09-12-file-transfer-bridge-measurement.md docs/superpowers/specs/2026-09-12-file-transfer-design.md configurator/tests/transfer/
git commit -m "Measure the bridge and settle the prefetch question

Measured over the real LAN rather than loopback, because the decision
turns on the throughput number and loopback would flatter it.

Records which of the two gates fired. A gate that did not fire is a
result: not adding a prefetch window is the preferred outcome, and
deleting an open question is progress.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

# Phase 4 — Wiring, interface, packaging, acceptance

**Purpose:** make the subsystem reachable from the running program, then prove
it with Explorer on two machines.

**Precondition:** Phase 3's gate is recorded.

**Why this phase exists as its own phase:** this repository's dominant defect is
working code with a passing test that production never reaches. Everything built
so far is exactly that until Task 4.2 wires it into `app.py` and Task 4.3 proves
the wiring through the real interface.

## Task 4.1: progress and cancel on the shared-clipboard page

**Files:**
- Modify: `configurator/src/duo_input/ui/clipboard_page.py`
- Modify: `configurator/src/duo_input/ui/tray.py:61-64`
- Test: `configurator/tests/ui/test_clipboard_page.py` (append)
- Test: `configurator/tests/ui/test_tray_lifecycle.py` (append)

**Interfaces:**
- Consumes: the signals from Tasks 1.10–1.11.
- Produces:
  - `ClipboardPage.files_toggled = Signal(bool)`
  - `ClipboardPage.cancel_requested = Signal()`
  - `ClipboardPage.set_files_checked(checked: bool)`,
    `.set_transfer_progress(done: int, total: int)`,
    `.clear_transfer()`
  - `ClipboardPage.transfer_label`, `.cancel_button`, `.files_checkbox`
  - `TrayIcon.files_toggled = Signal(bool)`, `.set_files_checked(checked: bool)`
  - `duo_input.ui.clipboard_page.human_bytes(value: int) -> str`

- [ ] **Step 1: Write the failing test**

```python
# append to configurator/tests/ui/test_clipboard_page.py
from duo_input.ui.clipboard_page import human_bytes


def test_bytes_are_shown_in_units_a_person_reads():
    assert human_bytes(0) == "0 Б"
    assert human_bytes(999) == "999 Б"
    assert human_bytes(1024) == "1.0 КБ"
    assert human_bytes(1_500_000_000) == "1.4 ГБ"


def test_progress_reads_as_received_out_of_total(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)

    page.set_transfer_progress(1_500_000_000, 8_800_000_000)

    assert page.transfer_label.text() == "Получение 1.4 ГБ / 8.2 ГБ"


def test_the_cancel_button_is_hidden_until_a_transfer_starts(qtbot):
    page = ClipboardPage()
    qtbot.addWidget(page)

    assert not page.cancel_button.isVisible() or not page.cancel_button.isEnabled()


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
    # Тест, выставляющий настройку в обход интерфейса, проходит при полностью
    # мёртвом обработчике. Поэтому - настоящая кнопка.
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
    # Иначе восстановление сохранённого состояния при старте выглядело бы как
    # нажатие пользователя и подняло бы подсистему, которую он не включал.
    page = ClipboardPage()
    qtbot.addWidget(page)
    seen: list[bool] = []
    page.files_toggled.connect(seen.append)

    page.set_files_checked(True)

    assert seen == []
```

```python
# append to configurator/tests/ui/test_tray_lifecycle.py
def test_the_files_action_is_a_working_toggle_not_a_disabled_placeholder(qtbot):
    # ui/tray.py:63 до этой правки делал setEnabled(False) - пункт меню
    # существовал и не мог ничего.
    tray = TrayIcon(QIcon(), None)

    assert tray.files_action.isEnabled()

    with qtbot.waitSignal(tray.files_toggled, timeout=1000) as blocker:
        tray.files_action.trigger()

    assert blocker.args == [True]


def test_setting_the_tray_files_check_programmatically_does_not_echo(qtbot):
    tray = TrayIcon(QIcon(), None)
    seen: list[bool] = []
    tray.files_toggled.connect(seen.append)

    tray.set_files_checked(True)

    assert seen == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_clipboard_page.py configurator/tests/ui/test_tray_lifecycle.py -v -k "files or transfer or cancel or human"`
Expected: FAIL — `ImportError: cannot import name 'human_bytes'`

- [ ] **Step 3: Write minimal implementation**

In `clipboard_page.py`:

```python
def human_bytes(value: int) -> str:
    """Размер так, как его читает человек, а не как его хранит машина.

    Десятичные приставки (КБ, а не КиБ) и порог 1024: так пишет сам
    Проводник, и расхождение с ним в одном окне выглядело бы как ошибка.
    """
    if value < 1024:
        return f"{value} Б"
    for unit in ("КБ", "МБ", "ГБ", "ТБ"):
        value /= 1024
        if value < 1024:
            return f"{value:.1f} {unit}"
    return f"{value:.1f} ПБ"
```

Add to `ClipboardPage`: the two signals, a `transfer_label` `QLabel`, a
`cancel_button` `QPushButton` (disabled at construction), a `files_checkbox`
`QCheckBox`, and:

```python
    def set_transfer_progress(self, done: int, total: int) -> None:
        self.transfer_label.setText(
            self.tr("Получение {0} / {1}").format(human_bytes(done), human_bytes(total))
        )
        self.cancel_button.setEnabled(True)

    def clear_transfer(self) -> None:
        self.transfer_label.setText("")
        self.cancel_button.setEnabled(False)

    def set_files_checked(self, checked: bool) -> None:
        """Показать состояние, не изобразив нажатие.

        Без blockSignals восстановление сохранённой настройки при старте
        выглядело бы как выбор пользователя и подняло бы подсистему, которую
        он не включал.
        """
        self.files_checkbox.blockSignals(True)
        self.files_checkbox.setChecked(checked)
        self.files_checkbox.blockSignals(False)
```

Wire `self.cancel_button.clicked.connect(self.cancel_requested)` and
`self.files_checkbox.toggled.connect(self.files_toggled)`.

In `tray.py`, replace lines 61-64 with a working toggle mirroring the existing
`sharing_action`, add `files_toggled = Signal(bool)` and `set_files_checked`
with the same `blockSignals` guard.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui -q`
Expected: PASS

Note that these run under `QT_QPA_PLATFORM=offscreen` (`tests/ui/conftest.py`),
which in this repository has hidden a whole class of defect before. The
visibility assertions above are written against `isEnabled()` rather than
`isVisible()` for that reason; the real look is confirmed in Task 4.4.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/ui/ configurator/tests/ui/
git commit -m "Turn the files toggle from a placeholder into a control

ui/tray.py:63 called setEnabled(False): the menu item existed and could
do nothing. Both toggles now report through the real widget, and the tests
click the real button, because a test that sets a setting behind the
interface passes with a completely dead handler.

Programmatic set does not echo a signal, or restoring the saved setting at
startup would look like the operator switching the feature on.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 4.2: wire the subsystem into app.py

**Files:**
- Modify: `configurator/src/duo_input/app.py` (`_ClipboardRuntime`)
- Test: `configurator/tests/ui/test_runtime_wiring.py` (append)

**Interfaces:**
- Consumes: `FileTransferService` (Tasks 1.10–1.11), `create_file_backend`
  (Task 2.6), `ClipboardPage`/`TrayIcon` signals (Task 4.1),
  `ClipboardCoordinator.capabilities_known` (Task 1.7).
- Produces:
  - `_ClipboardRuntime.transfer: FileTransferService | None`
  - `_ClipboardRuntime.set_files_enabled(enabled: bool) -> None`
  - `QSettings` key `clipboard/files_enabled` becomes real

**This task is the one that makes everything before it more than a library.**

- [ ] **Step 1: Write the failing test**

```python
# append to configurator/tests/ui/test_runtime_wiring.py
def test_with_files_enabled_the_transfer_service_is_actually_created(qapp, tmp_path, monkeypatch):
    # Работающий модуль, до которого production не дотягивается, - это
    # неработающая функция. Этот тест и есть проверка дотягивания.
    settings = _settings(tmp_path, {"clipboard/enabled": True, "clipboard/files_enabled": True})
    window = build_main_window(settings=settings)

    configure_runtime(qapp, window, settings)

    runtime = _runtime_of(qapp)
    assert runtime.transfer is not None


def test_with_files_disabled_no_transfer_service_exists(qapp, tmp_path):
    settings = _settings(tmp_path, {"clipboard/enabled": True, "clipboard/files_enabled": False})
    window = build_main_window(settings=settings)

    configure_runtime(qapp, window, settings)

    assert _runtime_of(qapp).transfer is None


def test_the_tray_toggle_starts_the_subsystem_through_the_real_menu_item(qapp, tmp_path, qtbot):
    settings = _settings(tmp_path, {"clipboard/enabled": True, "clipboard/files_enabled": False})
    window = build_main_window(settings=settings)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)

    runtime.tray.files_action.trigger()

    assert runtime.transfer is not None
    assert settings.value("clipboard/files_enabled", type=bool) is True


def test_the_page_toggle_stops_the_subsystem_through_the_real_checkbox(qapp, tmp_path):
    settings = _settings(tmp_path, {"clipboard/enabled": True, "clipboard/files_enabled": True})
    window = build_main_window(settings=settings)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)

    window.clipboard_page.files_checkbox.setChecked(False)

    assert runtime.transfer is None


def test_both_toggles_show_the_same_saved_state_at_startup(qapp, tmp_path):
    settings = _settings(tmp_path, {"clipboard/enabled": True, "clipboard/files_enabled": True})
    window = build_main_window(settings=settings)

    configure_runtime(qapp, window, settings)

    runtime = _runtime_of(qapp)
    assert window.clipboard_page.files_checkbox.isChecked()
    assert runtime.tray.files_action.isChecked()


def test_files_cannot_be_enabled_while_the_clipboard_itself_is_off(qapp, tmp_path):
    # Передача файлов живёт на том же соединении. Без подсистемы буфера
    # обмена нет ни связи, ни доверия.
    settings = _settings(tmp_path, {"clipboard/enabled": False, "clipboard/files_enabled": True})
    window = build_main_window(settings=settings)

    configure_runtime(qapp, window, settings)

    assert _runtime_of(qapp).transfer is None


def test_progress_from_the_service_reaches_the_page(qapp, tmp_path):
    settings = _settings(tmp_path, {"clipboard/enabled": True, "clipboard/files_enabled": True})
    window = build_main_window(settings=settings)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)

    runtime.transfer.transfer_progress.emit(1_500_000_000, 8_800_000_000)

    assert "1.4 ГБ" in window.clipboard_page.transfer_label.text()


def test_cancelling_from_the_page_reaches_the_service(qapp, tmp_path):
    settings = _settings(tmp_path, {"clipboard/enabled": True, "clipboard/files_enabled": True})
    window = build_main_window(settings=settings)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)
    runtime.transfer.transfer_progress.emit(1, 100)

    window.clipboard_page.cancel_button.click()

    assert runtime.transfer.state.value in {"cancelled", "idle"}


def test_the_peers_capabilities_reach_the_transfer_service(qapp, tmp_path):
    settings = _settings(tmp_path, {"clipboard/enabled": True, "clipboard/files_enabled": True})
    window = build_main_window(settings=settings)
    configure_runtime(qapp, window, settings)
    runtime = _runtime_of(qapp)

    runtime.coordinator.capabilities_known.emit(frozenset({"clipboard/1", "files/1"}))

    assert runtime.transfer.peer_supports_files


def test_with_files_disabled_no_com_object_is_registered_on_the_clipboard(qapp, tmp_path):
    settings = _settings(tmp_path, {"clipboard/enabled": True, "clipboard/files_enabled": False})
    window = build_main_window(settings=settings)

    configure_runtime(qapp, window, settings)

    runtime = _runtime_of(qapp)
    assert runtime.file_backend is None, (
        "поток COM поднят при выключенной фиче - буфер обмена оператора "
        "трогает подсистема, которую он не включал"
    )
```

Read `configurator/tests/ui/test_runtime_wiring.py` first and reuse its existing
`_settings` and `_runtime_of` helpers. If it has none, add:

```python
def _settings(tmp_path, values):
    settings = QSettings(str(tmp_path / "test.ini"), QSettings.Format.IniFormat)
    for key, value in values.items():
        settings.setValue(key, value)
    return settings


def _runtime_of(application):
    from duo_input.app import _ClipboardRuntime

    for child in application.children():
        if isinstance(child, _ClipboardRuntime):
            return child
    raise AssertionError("_ClipboardRuntime не создан")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui/test_runtime_wiring.py -v -k "files or transfer or capabilit"`
Expected: FAIL — `AttributeError: '_ClipboardRuntime' object has no attribute 'transfer'`

- [ ] **Step 3: Write minimal implementation**

In `_ClipboardRuntime.__init__`, add `self.transfer = None` and
`self.file_backend = None`, and connect `self.tray.files_toggled` to
`self.set_files_enabled`.

```python
    def set_files_enabled(self, enabled: bool) -> None:
        """Единственный вход для обоих переключателей передачи файлов."""
        self._settings.setValue("clipboard/files_enabled", enabled)
        self._window.clipboard_page.set_files_checked(enabled)
        self.tray.set_files_checked(enabled)
        if enabled and self.coordinator is not None:
            self._start_files()
        else:
            self._stop_files()

    def _start_files(self) -> None:
        if self.transfer is not None:
            return
        coordinator = self.coordinator
        if coordinator is None:
            # Передача файлов живёт на соединении подсистемы буфера обмена:
            # без неё нет ни связи, ни доверия, ни пира.
            return
        try:
            backend = create_file_backend(coordinator)
        except UnsupportedPlatformError:
            logger.info("передача файлов на этой платформе не поддерживается")
            self._settings.setValue("clipboard/files_enabled", False)
            self._window.clipboard_page.set_files_checked(False)
            self.tray.set_files_checked(False)
            return

        transfer = FileTransferService(coordinator)
        page = self._window.clipboard_page
        transfer.transfer_progress.connect(page.set_transfer_progress)
        transfer.transfer_completed.connect(page.clear_transfer)
        transfer.transfer_cancelled.connect(page.clear_transfer)
        transfer.transfer_failed.connect(lambda _reason: page.clear_transfer())
        transfer.transfer_failed.connect(
            lambda reason: page.add_event(f"передача файлов не удалась: {reason}")
        )
        transfer.send_failed.connect(
            lambda reason: page.add_event(f"файлы не объявлены: {reason}")
        )
        page.cancel_requested.connect(lambda: transfer.finish_session("cancelled"))

        backend.set_callbacks(
            open_pipe=transfer.open_pipe,
            request_read=lambda *args: post_to_service(transfer, "request_read", *args),
            close_pipe=transfer.close_pipe,
            on_operation_finished=lambda result: transfer.finish_session(
                "completed" if result == 0 else "failed"
            ),
        )
        transfer.offer_received.connect(
            lambda manifest: backend.publish(
                manifest, origin_marker=f"{manifest.transfer_id}".encode("ascii")
            )
        )
        backend.publish_failed.connect(
            lambda reason: page.add_event(f"буфер обмена не принял файлы: {reason}")
        )

        if coordinator._link is not None:
            transfer.attach_link(coordinator._link)
        coordinator.capabilities_known.connect(transfer.set_peer_capabilities)
        self._backend.snapshot_taken.connect(self._offer_files_from)

        backend.start()
        self.transfer = transfer
        self.file_backend = backend

    def _offer_files_from(self, snapshot) -> None:
        if self.transfer is None or not snapshot.file_paths:
            return
        self.transfer.offer_local_files([Path(path) for path in snapshot.file_paths])

    def _stop_files(self) -> None:
        transfer, backend = self.transfer, self.file_backend
        self.transfer = None
        self.file_backend = None
        if transfer is not None:
            transfer.detach_link()
            transfer.deleteLater()
        if backend is not None:
            backend.stop()
            backend.deleteLater()
```

Call `self._stop_files()` at the top of `_stop()`, and `set_files_enabled` from
`configure_runtime` after the clipboard subsystem is up:

```python
    files_enabled = bool(settings.value("clipboard/files_enabled", False, type=bool))
    window.clipboard_page.set_files_checked(files_enabled)
    runtime.tray.set_files_checked(files_enabled)
    window.clipboard_page.files_toggled.connect(runtime.set_files_enabled)
    if enabled and files_enabled:
        runtime.set_files_enabled(True)
```

Add the imports: `FileTransferService`, `create_file_backend`,
`UnsupportedPlatformError`, `post_to_service`, and `Path` if absent.

`coordinator._link` is reached directly here because `ClipboardCoordinator`
exposes no public accessor for the live link. Add one — `@property def link` —
rather than leaving the private reach in `app.py`; a private attribute crossing
a module boundary is the shape that rots first.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/ui -q`
Expected: PASS

- [ ] **Step 5: Run the full gate**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests tests -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add configurator/src/duo_input/ configurator/tests/ui/test_runtime_wiring.py
git commit -m "Reach the transfer subsystem from the running program

Everything before this commit was a library with passing tests that
production never called - this repository's dominant defect, seven cases
in one day at its worst. The tests here go through the real menu item and
the real checkbox for the same reason.

With the feature off, no COM thread is started at all: the operator's
clipboard is not touched by a subsystem they did not switch on.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 4.3: prove the ctypes path survives the packaged build

**Files:**
- Modify: `configurator/tests/packaging/test_dist.py`
- Modify: `configurator/src/duo_input/app.py` (`main`, new `--self-check-files`)

**Interfaces:**
- Consumes: `VirtualFilesDataObject`, `group_descriptor_bytes`.
- Produces: `--self-check-files` printing `files: ok` or `files: missing`.

Nuitka standalone and `ctypes` callbacks is risk R4. A packaged build where the
COM path silently fails looks to the user exactly like "the paste does nothing",
and no test in this repository would catch it — the same trap as the existing
`--self-check-tls`, which is why that flag exists.

- [ ] **Step 1: Write the failing test**

```python
# append to configurator/tests/packaging/test_dist.py
def test_the_packaged_build_can_actually_build_a_com_data_object():
    # Тот же приём, что --self-check-tls: недостающая нативная часть
    # выглядит у пользователя как "вставка ничего не делает", и никакой
    # обычный тест этого не поймает.
    result = subprocess.run(
        [str(EXECUTABLE), "--self-check-files"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "files: ok" in result.stdout, (
        f"ctypes-обратные вызовы не выжили упаковку: {result.stdout}{result.stderr}"
    )


def test_the_packaged_build_reports_its_descriptor_size():
    result = subprocess.run(
        [str(EXECUTABLE), "--self-check-files"],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert "descriptor: 592" in result.stdout, (
        "размер FILEDESCRIPTORW в собранной программе отличается - Проводник "
        "прочитал бы наши поля по неверным смещениям"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/packaging/test_dist.py -v -k "com or descriptor"`
Expected: FAIL, or SKIP if `dist/DuoInput` is absent — the file's existing
`pytestmark` skips the whole module without a build. Build first if needed.

- [ ] **Step 3: Write minimal implementation**

In `app.main`, beside the existing `--self-check-tls` branch and for the same
reason:

```python
    if "--self-check-files" in arguments:
        # Собранная программа должна доказать, что ctypes-обратные вызовы в
        # ней работают: сломанные упаковкой, они выглядят у пользователя как
        # "вставка ничего не делает" и никак иначе. Проверка до создания
        # QApplication, чтобы её можно было вызвать из exe без окна.
        import ctypes

        from duo_input.transfer.model import ENTRY_FILE, TransferEntry, TransferManifest
        from duo_input.transfer.pipe import ChunkPipe
        from duo_input.transfer.windows_com import FILEDESCRIPTORW
        from duo_input.transfer.windows_files import (
            VirtualFilesDataObject,
            group_descriptor_bytes,
        )

        manifest = TransferManifest(
            transfer_id="self-check",
            entries=(TransferEntry(path="a.bin", kind=ENTRY_FILE, size=4, mtime_ns=1),),
        )
        try:
            blob = group_descriptor_bytes(manifest)
            data_object = VirtualFilesDataObject(
                manifest,
                open_pipe=lambda *_: ChunkPipe(),
                request_read=lambda *_: None,
                close_pipe=lambda *_: None,
                origin_marker=b"self-check",
            )
            usable = data_object.pointer.value is not None and len(blob) > 4
        except Exception as error:  # noqa: BLE001 - отчёт важнее трассировки
            print(f"files: missing ({error})")
            return 1
        print(f"files: {'ok' if usable else 'missing'}")
        print(f"descriptor: {ctypes.sizeof(FILEDESCRIPTORW)}")
        return 0
```

- [ ] **Step 4: Build and run the check**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests/packaging/test_dist.py -v`
Expected: PASS after a build. If `files: missing` appears, the fix is a Nuitka
include directive for the `transfer` package, not a relaxed assertion.

- [ ] **Step 5: Commit**

```bash
git add configurator/src/duo_input/app.py configurator/tests/packaging/test_dist.py
git commit -m "Make the packaged build prove its COM path works

Same device as --self-check-tls, and for the same reason: a native piece
broken by packaging looks to the user like the paste doing nothing, and
no ordinary test in this repository would catch it.

The descriptor size is checked in the built program too, because a
different struct layout there makes Explorer read our fields at the wrong
offsets.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

## Task 4.4: hardware acceptance on two machines

**Files:**
- Create: `docs/superpowers/records/2026-09-12-file-transfer-acceptance.md`

**Interfaces:**
- Consumes: the whole system.
- Produces: a record. No code.

No automated test replaces this. `QT_QPA_PLATFORM=offscreen` is set for the UI
suite and has hidden a whole class of defect in this repository before, and no
test at all can drive Explorer's own paste.

- [ ] **Step 1: Run the matrix, recording each case verbatim**

Two real machines, one LAN, paired and connected, both toggles on. For each row:
`Ctrl+C` in Explorer on PC1, switch, `Ctrl+V` in a folder on PC2.

| # | Case | What must be true |
|---|---|---|
| 1 | single file | arrives byte-for-byte; digest matches |
| 2 | multiple files | all arrive; names unchanged |
| 3 | nested directory | structure recreated; no file misplaced |
| 4 | Unicode name (Cyrillic, and one astral character) | name intact on disk |
| 5 | zero-byte file | created, size 0, no error |
| 6 | file above 1 GB | completes; native progress moves; memory flat |
| 7 | repeated `Ctrl+V` | second paste works; two copies present |
| 8 | source changed between copy and paste | Explorer reports an error; no truncated file left |
| 9 | peer disconnected mid-transfer | Explorer reports an error; its partial file is gone |
| 10 | Cancel mid-transfer | stops promptly; no further network traffic |
| 11 | destination collision | Explorer's own replace/skip/keep-both dialog appears |
| 12 | `Ctrl+X` on source | paste copies; **source on PC1 still exists** |
| 13 | clipboard changed on PC1 during an active transfer | the running transfer completes unaffected |
| 14 | folder containing a junction | junction skipped; the skip is reported; its target not exported |
| 15 | old client ↔ new client | clipboard still works both ways; no file offer is sent; neither side drops the connection |

Row 15 needs a build from before Task 1.7 on one machine. `git worktree add` a
commit from before that task and build there; without that, the compatibility
claim is untested.

Row 6 must also record peak memory of the Duo Input process, so the
bounded-memory claim is evidenced rather than asserted.

- [ ] **Step 2: Write the record**

Follow `records/2026-09-12-synchronised-control-acceptance.md` and
`records/2026-09-12-macos-clipboard-parity-acceptance.md`. State explicitly
which rows were exercised and which were not, and what each one established —
separately from what it merely did not contradict.

A row that could not be run is recorded as not run. The previous acceptance
record in this repository is titled "Record what the hardware acceptance
actually established" for a reason.

- [ ] **Step 3: Run the full gate one last time**

Run: `.venv/Scripts/python.exe -m pytest configurator/tests tests -q`
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add docs/superpowers/records/2026-09-12-file-transfer-acceptance.md
git commit -m "Record what the file-transfer acceptance actually established

Fifteen rows on two machines, each with what it proved stated separately
from what it merely failed to contradict. Rows that could not be run are
recorded as not run.

Row 15 needed a build from before capability negotiation existed, because
otherwise the old-client claim is untested rather than true.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 5: Verification before completion**

Before claiming this plan complete, use `superpowers:verification-before-completion`.
Evidence before assertions: paste the gate's actual output, name any skipped
test and why it skipped, and state which acceptance rows did not run.
