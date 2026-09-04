"""СПАЙК (одноразовый код, не production): минимальный слой COM на чистом ctypes.

Позволяет реализовать COM-интерфейс на Python без pywin32/comtypes: строит
vtable из ctypes.WINFUNCTYPE-колбэков и структуру объекта с указателем на неё.
"""

from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes

ole32 = ctypes.WinDLL("ole32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

HRESULT = ctypes.c_long
ULONG = ctypes.c_ulong
LPVOID = ctypes.c_void_p

S_OK = 0
S_FALSE = 1
E_NOTIMPL = -2147467263          # 0x80004001
E_NOINTERFACE = -2147467262      # 0x80004002
E_FAIL = -2147467259             # 0x80004005
E_INVALIDARG = -2147024809       # 0x80070057
E_OUTOFMEMORY = -2147024882      # 0x8007000E
DV_E_FORMATETC = -2147221404     # 0x80040064
DV_E_TYMED = -2147221399         # 0x80040069
DV_E_LINDEX = -2147221398        # 0x8004006A
DV_E_DVASPECT = -2147221397      # 0x8004006B
OLE_E_ADVISENOTSUPPORTED = -2147221501  # 0x80040003
STG_E_INVALIDFUNCTION = -2147287039     # 0x80030001
STG_E_MEDIUMFULL = -2147286928          # 0x80030070
STG_E_READFAULT = -2147286781           # 0x80030103
RPC_E_CHANGED_MODE = -2147417850        # 0x80010106


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_uint32),
        ("Data2", ctypes.c_uint16),
        ("Data3", ctypes.c_uint16),
        ("Data4", ctypes.c_ubyte * 8),
    ]

    def __eq__(self, other):
        return isinstance(other, GUID) and bytes(self) == bytes(other)

    def __hash__(self):
        return hash(bytes(self))

    def __repr__(self):
        buf = ctypes.create_unicode_buffer(64)
        ole32.StringFromGUID2(ctypes.byref(self), buf, 64)
        return buf.value


def guid(text: str) -> GUID:
    out = GUID()
    hr = ole32.IIDFromString(ctypes.c_wchar_p(text), ctypes.byref(out))
    if hr != 0:
        raise OSError(f"IIDFromString({text}) -> 0x{hr & 0xFFFFFFFF:08X}")
    return out


IID_IUnknown = guid("{00000000-0000-0000-C000-000000000046}")
IID_IMarshal = guid("{00000003-0000-0000-C000-000000000046}")
IID_IDataObject = guid("{0000010E-0000-0000-C000-000000000046}")
IID_IEnumFORMATETC = guid("{00000103-0000-0000-C000-000000000046}")
IID_ISequentialStream = guid("{0C733A30-2A1C-11CE-ADE5-00AA0044773D}")
IID_IStream = guid("{0000000C-0000-0000-C000-000000000046}")
IID_IAgileObject = guid("{94EA2B94-E9CC-49E0-C0FF-EE64CA8F5B90}")

# держим живыми vtable/колбэки/объекты, пока COM их не отпустит
_ALIVE: dict[int, "ComObject"] = {}
_ALIVE_LOCK = threading.Lock()


class ComObject:
    """Базовый класс: собирает vtable из self.methods()."""

    # (имя, restype, [argtypes без this])
    IIDS: tuple = ()
    METHODS: tuple = ()

    def __init__(self, log=None):
        self._log = log or (lambda *a: None)
        self._refcount = 1
        self._keepalive = []
        self._ftm = None  # ctypes.c_void_p на IUnknown free-threaded marshaler
        self._build()

    # ---- vtable ------------------------------------------------------
    def _build(self) -> None:
        protos = []
        impls = []

        qi_proto = ctypes.WINFUNCTYPE(HRESULT, LPVOID, LPVOID, LPVOID)
        addref_proto = ctypes.WINFUNCTYPE(ULONG, LPVOID)
        rel_proto = ctypes.WINFUNCTYPE(ULONG, LPVOID)
        protos += [qi_proto, addref_proto, rel_proto]
        impls += [
            qi_proto(self._query_interface),
            addref_proto(self._add_ref),
            rel_proto(self._release),
        ]

        for name, restype, argtypes in self.METHODS:
            proto = ctypes.WINFUNCTYPE(restype, LPVOID, *argtypes)
            protos.append(proto)
            impls.append(proto(self._wrap(name)))

        fields = [(f"m{i}", p) for i, p in enumerate(protos)]
        vtbl_type = type("Vtbl", (ctypes.Structure,), {"_fields_": fields})
        vtbl = vtbl_type(*impls)

        class _Obj(ctypes.Structure):
            _fields_ = [("lpVtbl", ctypes.POINTER(vtbl_type))]

        obj = _Obj()
        obj.lpVtbl = ctypes.pointer(vtbl)

        self._impls = impls
        self._vtbl = vtbl
        self._obj = obj
        self.pointer = ctypes.cast(ctypes.byref(obj), LPVOID).value
        with _ALIVE_LOCK:
            _ALIVE[self.pointer] = self

    def _wrap(self, name: str):
        method = getattr(self, name)

        def call(this, *args):
            try:
                result = method(*args)
                return S_OK if result is None else result
            except Exception as exc:  # noqa: BLE001 - спайк, не роняем shell
                self._log(f"!! исключение в {name}: {exc!r}")
                import traceback

                traceback.print_exc()
                return E_FAIL

        return call

    # ---- IUnknown ----------------------------------------------------
    def _query_interface(self, this, riid, ppv):
        if not ppv:
            return E_INVALIDARG
        ctypes.cast(ppv, ctypes.POINTER(LPVOID))[0] = None
        iid = ctypes.cast(riid, ctypes.POINTER(GUID))[0]
        if iid == IID_IUnknown or any(iid == x for x in self.IIDS):
            ctypes.cast(ppv, ctypes.POINTER(LPVOID))[0] = self.pointer
            self._add_ref(this)
            return S_OK
        if self._ftm is not None and iid == IID_IMarshal:
            unk = ctypes.cast(self._ftm, ctypes.POINTER(ctypes.POINTER(ctypes.c_void_p)))
            vt = unk[0]
            qi = ctypes.WINFUNCTYPE(HRESULT, LPVOID, LPVOID, LPVOID)(vt[0])
            return qi(self._ftm, riid, ppv)
        return E_NOINTERFACE

    def _add_ref(self, this):
        self._refcount += 1
        return self._refcount

    def _release(self, this):
        self._refcount -= 1
        count = self._refcount
        if count <= 0:
            self.on_final_release()
            with _ALIVE_LOCK:
                _ALIVE.pop(self.pointer, None)
        return max(count, 0)

    def on_final_release(self) -> None:
        pass

    def enable_free_threaded_marshaler(self) -> None:
        """Агрегируем FTM: тогда вызовы приходят прямо в поток вызывающего,
        минуя STA-очередь сообщений."""
        out = LPVOID()
        hr = ole32.CoCreateFreeThreadedMarshaler(
            LPVOID(self.pointer), ctypes.byref(out)
        )
        if hr != 0:
            raise OSError(f"CoCreateFreeThreadedMarshaler -> 0x{hr & 0xFFFFFFFF:08X}")
        self._ftm = out.value
