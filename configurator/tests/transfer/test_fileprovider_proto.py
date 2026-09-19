"""Contract tests for the shared clang XPC protocol loader (Task 2).

darwin-only: exercises PyObjC + the compiled libduofpproto.dylib. Point the
loader at the freshly built dylib via DUO_FP_PROTO_DYLIB (set by the test runner)
or rely on the dev build-artifact resolution.
"""
from __future__ import annotations

import ast
import inspect
import sys

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="darwin only")


def test_host_interface_constructs_from_clang_protocol():
    from duo_input.transfer import fileprovider_proto as p

    iface = p.host_interface()
    assert iface is not None


def test_extension_interface_constructs_from_clang_protocol():
    from duo_input.transfer import fileprovider_proto as p

    assert p.extension_interface() is not None


def test_formal_protocol_is_not_used():
    from duo_input.transfer import fileprovider_proto as p

    assert getattr(p, "USES_CLANG_PROTOCOL", False) is True
    # No objc.formal_protocol *usage* in the loader — clang protocol only. AST
    # ignores docstrings/comments, so a mention explaining "we don't use it" is
    # fine; only an actual `.formal_protocol` attribute access fails this guard.
    tree = ast.parse(inspect.getsource(p))
    uses = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "formal_protocol"
    ]
    assert not uses, "objc.formal_protocol must never be used as a fallback"


def test_dylib_resolution_override_wins(tmp_path, monkeypatch):
    from duo_input.transfer import fileprovider_proto as p

    fake = tmp_path / "libduofpproto.dylib"
    fake.write_bytes(b"\x00")
    monkeypatch.setenv("DUO_FP_PROTO_DYLIB", str(fake))
    assert p._resolve_dylib() == fake


def test_dylib_resolution_reports_dev_and_prod_paths(monkeypatch):
    """The loader keeps the dev build-artifact path and the bundled production
    Contents/Frameworks path explicitly separate."""
    from pathlib import Path

    from duo_input.transfer import fileprovider_proto as p

    monkeypatch.delenv("DUO_FP_PROTO_DYLIB", raising=False)
    # bundled production resolver: walks up to a Contents dir
    bundled = p._bundled_dylib(Path("/Apps/DuoInput.app/Contents/MacOS/x/y.py"))
    assert bundled == Path("/Apps/DuoInput.app/Contents/Frameworks/libduofpproto.dylib")
    # dev resolver: points inside the source tree's build products
    dev = p._dev_dylib(Path(__file__).resolve())
    assert dev is None or dev.name == "libduofpproto.dylib"
