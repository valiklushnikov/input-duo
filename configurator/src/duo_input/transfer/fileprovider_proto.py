"""Load the shared clang-compiled Objective-C XPC protocol contract and build
``NSXPCInterface`` objects from it (PyObjC host side).

PyObjC cannot use ``objc.formal_protocol`` for ``NSXPCInterface`` — it needs the
extended method signatures only a clang-compiled ``@protocol`` carries (Phase
10.2 OBSERVED). So the *one* protocol source
(``configurator/fileprovider/Shared/DuoFPProto.h``) is compiled into
``libduofpproto.dylib``; here we ``dlopen`` it and resolve the protocols by name.

darwin-only. This module never falls back to ``objc.formal_protocol``.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path

import objc
from Foundation import NSData, NSError, NSNumber, NSSet, NSString, NSXPCInterface

#: This module resolves the protocol contract from a clang dylib and never falls
#: back to ``objc.formal_protocol`` (which ``NSXPCInterface`` rejects). The guard
#: test asserts this flag is True and that the source contains no fallback.
USES_CLANG_PROTOCOL = True

_DYLIB_NAME = "libduofpproto.dylib"
_HOST_PROTOCOL = "DuoHostCallback"
_EXT_PROTOCOL = "DuoExtensionControl"

#: Cached CDLL handle — the dylib is loaded once per process.
_dylib: ctypes.CDLL | None = None


class ProtoDylibNotFound(RuntimeError):
    """The clang protocol dylib could not be located (see ``_resolve_dylib``)."""


def _bundled_dylib(start: Path) -> Path | None:
    """Bundled production path: ``DuoInput.app/Contents/Frameworks/libduofpproto.dylib``.

    When frozen (Nuitka ``.app``) this module lives under ``Contents/...``; walk
    up to the first ``Contents`` directory and look inside its ``Frameworks``.
    The real Nuitka packaging that puts the dylib there is Task 18.
    """
    for parent in start.parents:
        if parent.name == "Contents":
            return parent / "Frameworks" / _DYLIB_NAME
    return None


def _dev_dylib(start: Path) -> Path | None:
    """Development / test path: the xcodegen build artifact under the source tree."""
    for parent in start.parents:
        fp = parent / "configurator" / "fileprovider"
        if fp.is_dir():
            products = fp / "build" / "dd" / "Build" / "Products"
            hits = sorted(products.glob(f"*/{_DYLIB_NAME}"))
            return hits[-1] if hits else None
    return None


def _resolve_dylib() -> Path:
    """Explicit resolution order: override → bundled production → dev build artifact.

    The dev/test path (build artifact) and the bundled production path
    (``Contents/Frameworks``) are deliberately separate — a test must not pass
    only because it happened to find a build-dir copy in a production layout.
    """
    override = os.environ.get("DUO_FP_PROTO_DYLIB")
    if override:
        candidate = Path(override)
        if candidate.is_file():
            return candidate

    here = Path(__file__).resolve()

    bundled = _bundled_dylib(here)
    if bundled is not None and bundled.is_file():
        return bundled

    dev = _dev_dylib(here)
    if dev is not None and dev.is_file():
        return dev

    raise ProtoDylibNotFound(
        f"{_DYLIB_NAME} not found via DUO_FP_PROTO_DYLIB, bundled Contents/Frameworks, "
        f"or the dev build (configurator/fileprovider/build/dd/Build/Products)"
    )


def _ensure_loaded() -> None:
    global _dylib
    if _dylib is None:
        # dlopen triggers the Objective-C image loader, which registers the
        # protocols so objc.protocolNamed(...) can find them.
        _dylib = ctypes.CDLL(str(_resolve_dylib()))


def _interface(protocol_name: str, whitelist) -> NSXPCInterface:
    _ensure_loaded()
    proto = objc.protocolNamed(protocol_name)  # raises if the dylib did not register it
    iface = NSXPCInterface.interfaceWithProtocol_(proto)
    for selector, index, classes, of_reply in whitelist:
        allowed = NSSet.setWithArray_(list(classes) + [NSError])
        iface.setClasses_forSelector_argumentIndex_ofReply_(allowed, selector, index, of_reply)
    return iface


def host_interface() -> NSXPCInterface:
    """Interface the host EXPORTS (extension calls openFetch/pullChunk/cancelFetch)."""
    return _interface(
        _HOST_PROTOCOL,
        [
            (b"openFetch:entryId:reply:", 0, [NSString], True),
            (b"openFetch:entryId:reply:", 1, [NSNumber], True),
            (b"openFetch:entryId:reply:", 2, [], True),
            (b"pullChunk:reply:", 0, [NSData], True),
            (b"pullChunk:reply:", 2, [], True),
        ],
    )


def extension_interface() -> NSXPCInterface:
    """Interface the extension EXPORTS (host calls publish/retire/deleteGeneration)."""
    return _interface(
        _EXT_PROTOCOL,
        [
            (b"publishGeneration:reply:", 0, [NSData], False),
            (b"publishGeneration:reply:", 1, [], True),
            (b"retireGeneration:reply:", 1, [], True),
            (b"deleteGeneration:reply:", 1, [], True),
        ],
    )


__all__ = [
    "USES_CLANG_PROTOCOL",
    "ProtoDylibNotFound",
    "host_interface",
    "extension_interface",
]
