"""Ask Windows how a physical mouse's HID report descriptor declares its buttons.

The question this answers: is a mouse's button usage range one contiguous run
declared in a single Input item (Usage Minimum 1, Usage Maximum 5, one shot),
or is the run split across two or more Input items (say, Usage Min 1 / Max 3
in one item and Usage Min 4 / Max 5 in another)? Our firmware's descriptor
parser keeps only the first Button-page Input item it finds, so a split
declaration silently loses whatever buttons live in the second item. Reading
the raw descriptor bytes by hand is possible but easy to miscount; Windows
already parsed the descriptor when it enumerated the device, and the HID
parser API (hid.dll's HidP_* functions, fed by SetupAPI-discovered device
paths) will hand back exactly the button-capability records the OS derived
from it. If HidP_GetButtonCaps returns more than one entry on Usage Page 0x09
(Button) for the same report ID, that is this project's split-declaration
shape, straight from the source of truth.

Usage:

    python tools/dump_mouse_button_caps.py

Plug the mouse in first (or run this while it is already plugged in) and
just run the script -- no arguments, no admin rights, nothing to install.
It opens every HID device present with zero-access handles (see below),
reads capabilities only, and closes everything again. It does not write to
or configure any device, and it does not need the Duo Input hardware to be
attached; it only cares about USB HID devices already visible to Windows.

Two things about how this has to be done, because both are easy to get
wrong and both cause the whole thing to silently fail:

* CreateFileW must ask for dwDesiredAccess=0 ("query access"), not
  GENERIC_READ. Windows holds the HID collection for a mouse open
  exclusively (usually the mouse class driver has it for real reads), so a
  GENERIC_READ open fails with ACCESS_DENIED even though nothing is wrong.
  A zero-access handle only asks "let me query you," which Windows grants,
  and it is all HidD_Get*/HidP_Get* need.

* SetupDiGetDeviceInterfaceDetailW takes a cbSize that is the size of the
  *fixed* part of SP_DEVICE_INTERFACE_DETAIL_DATA_W (the DWORD cbSize field
  itself), not the size of the variable-length buffer you allocated for the
  device path that follows it. On 64-bit Python that fixed size is 8 bytes
  (4 bytes of cbSize plus 4 bytes of alignment padding before the WCHAR
  array), not sizeof(DWORD)=4 and not the buffer length. Pass the wrong
  number and every call fails with ERROR_INVALID_USER_BUFFER (some
  SetupAPI calls are documented with hardcoded 8-on-64-bit / 6-on-32-bit
  because of exactly this).

Everything else follows the ordinary SetupAPI/hid.dll device-enumeration
dance: HidD_GetHidGuid to get the HID device interface GUID,
SetupDiGetClassDevs(DIGCF_PRESENT | DIGCF_DEVICEINTERFACE) to get a device
info set for it, SetupDiEnumDeviceInterfaces + SetupDiGetDeviceInterfaceDetailW
to walk it and get device paths, CreateFileW to open each path, then
HidD_GetAttributes / HidD_GetPreparsedData / HidP_GetCaps /
HidP_GetButtonCaps / HidP_GetValueCaps to read what Windows parsed out of
the descriptor.
"""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys

# ---------------------------------------------------------------------------
# Win32 constants
# ---------------------------------------------------------------------------

DIGCF_PRESENT = 0x00000002
DIGCF_DEVICEINTERFACE = 0x00000010

FILE_SHARE_READ = 0x00000001
FILE_SHARE_WRITE = 0x00000002
OPEN_EXISTING = 3

ERROR_INSUFFICIENT_BUFFER = 122
ERROR_NO_MORE_ITEMS = 259

INVALID_HANDLE_VALUE = wintypes.HANDLE(-1).value

HIDP_STATUS_SUCCESS = 0x00110000

# HIDP_REPORT_TYPE
HidP_Input = 0
HidP_Output = 1
HidP_Feature = 2

USAGE_PAGE_GENERIC_DESKTOP = 0x01
USAGE_PAGE_BUTTON = 0x09
USAGE_POINTER = 0x01
USAGE_MOUSE = 0x02

# On 64-bit Windows, SetupDiGetDeviceInterfaceDetailW wants the size of the
# fixed part of SP_DEVICE_INTERFACE_DETAIL_DATA_W -- the DWORD cbSize field
# plus the padding that natural alignment inserts before the WCHAR array
# that follows it -- not sizeof(DWORD) and not the buffer size. This is a
# long-documented quirk: the "right" value is 8 on 64-bit processes and 6 on
# 32-bit ones, not something computed with ctypes.sizeof() on a struct we
# define ourselves (a struct with a 1-element WCHAR array would size as 6
# even on 64-bit, which is the wrong answer here).
DEVICE_INTERFACE_DETAIL_CBSIZE = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6


# ---------------------------------------------------------------------------
# Structures
# ---------------------------------------------------------------------------


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("InterfaceClassGuid", GUID),
        ("Flags", wintypes.DWORD),
        ("Reserved", ctypes.c_void_p),
    ]


class HIDD_ATTRIBUTES(ctypes.Structure):
    _fields_ = [
        ("Size", wintypes.ULONG),
        ("VendorID", wintypes.USHORT),
        ("ProductID", wintypes.USHORT),
        ("VersionNumber", wintypes.USHORT),
    ]


class HIDP_CAPS(ctypes.Structure):
    """hidpi.h: note the USHORT Reserved[17] sitting between the report
    lengths and the link-collection/cap counts -- skip it and every field
    after is read from the wrong offset."""

    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("UsagePage", wintypes.USHORT),
        ("InputReportByteLength", wintypes.USHORT),
        ("OutputReportByteLength", wintypes.USHORT),
        ("FeatureReportByteLength", wintypes.USHORT),
        ("Reserved", wintypes.USHORT * 17),
        ("NumberLinkCollectionNodes", wintypes.USHORT),
        ("NumberInputButtonCaps", wintypes.USHORT),
        ("NumberInputValueCaps", wintypes.USHORT),
        ("NumberInputDataIndices", wintypes.USHORT),
        ("NumberOutputButtonCaps", wintypes.USHORT),
        ("NumberOutputValueCaps", wintypes.USHORT),
        ("NumberOutputDataIndices", wintypes.USHORT),
        ("NumberFeatureButtonCaps", wintypes.USHORT),
        ("NumberFeatureValueCaps", wintypes.USHORT),
        ("NumberFeatureDataIndices", wintypes.USHORT),
    ]


class _ButtonCapsRange(ctypes.Structure):
    _fields_ = [
        ("UsageMin", wintypes.USHORT),
        ("UsageMax", wintypes.USHORT),
        ("StringMin", wintypes.USHORT),
        ("StringMax", wintypes.USHORT),
        ("DesignatorMin", wintypes.USHORT),
        ("DesignatorMax", wintypes.USHORT),
        ("DataIndexMin", wintypes.USHORT),
        ("DataIndexMax", wintypes.USHORT),
    ]


class _ButtonCapsNotRange(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("Reserved1", wintypes.USHORT),
        ("StringIndex", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT),
        ("DesignatorIndex", wintypes.USHORT),
        ("Reserved3", wintypes.USHORT),
        ("DataIndex", wintypes.USHORT),
        ("Reserved4", wintypes.USHORT),
    ]


class _ButtonCapsUnion(ctypes.Union):
    _fields_ = [
        ("Range", _ButtonCapsRange),
        ("NotRange", _ButtonCapsNotRange),
    ]


class HIDP_BUTTON_CAPS(ctypes.Structure):
    """hidpi.h layout used here, field for field:

        USAGE   UsagePage;
        UCHAR   ReportID;
        BOOLEAN IsAlias;
        USHORT  BitField;
        USHORT  LinkCollection;
        USAGE   LinkUsage;
        USAGE   LinkUsagePage;
        BOOLEAN IsRange;
        BOOLEAN IsStringRange;
        BOOLEAN IsDesignatorRange;
        BOOLEAN IsAbsolute;
        ULONG   Reserved[10];
        union { struct Range {...}; struct NotRange {...}; };

    BOOLEAN is a one-byte UCHAR, not a 4-byte BOOL -- use c_ubyte for it.
    The ULONG Reserved[10] must be exactly 10 elements or the union that
    follows (where UsageMin/UsageMax/DataIndexMin/DataIndexMax actually
    live) is read from the wrong offset and everything after IsAbsolute
    comes out as garbage.
    """

    _anonymous_ = ("u",)
    _fields_ = [
        ("UsagePage", wintypes.USHORT),
        ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte),
        ("BitField", wintypes.USHORT),
        ("LinkCollection", wintypes.USHORT),
        ("LinkUsage", wintypes.USHORT),
        ("LinkUsagePage", wintypes.USHORT),
        ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte),
        ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte),
        ("Reserved", wintypes.ULONG * 10),
        ("u", _ButtonCapsUnion),
    ]


class _ValueCapsRange(ctypes.Structure):
    _fields_ = [
        ("UsageMin", wintypes.USHORT),
        ("UsageMax", wintypes.USHORT),
        ("StringMin", wintypes.USHORT),
        ("StringMax", wintypes.USHORT),
        ("DesignatorMin", wintypes.USHORT),
        ("DesignatorMax", wintypes.USHORT),
        ("DataIndexMin", wintypes.USHORT),
        ("DataIndexMax", wintypes.USHORT),
    ]


class _ValueCapsNotRange(ctypes.Structure):
    _fields_ = [
        ("Usage", wintypes.USHORT),
        ("Reserved1", wintypes.USHORT),
        ("StringIndex", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT),
        ("DesignatorIndex", wintypes.USHORT),
        ("Reserved3", wintypes.USHORT),
        ("DataIndex", wintypes.USHORT),
        ("Reserved4", wintypes.USHORT),
    ]


class _ValueCapsUnion(ctypes.Union):
    _fields_ = [
        ("Range", _ValueCapsRange),
        ("NotRange", _ValueCapsNotRange),
    ]


class HIDP_VALUE_CAPS(ctypes.Structure):
    """hidpi.h layout used here, field for field:

        USAGE   UsagePage;
        UCHAR   ReportID;
        BOOLEAN IsAlias;
        USHORT  BitField;
        USHORT  LinkCollection;
        USAGE   LinkUsage;
        USAGE   LinkUsagePage;
        BOOLEAN IsRange;
        BOOLEAN IsStringRange;
        BOOLEAN IsDesignatorRange;
        BOOLEAN IsAbsolute;
        BOOLEAN HasNull;
        UCHAR   Reserved;
        USHORT  BitSize;
        USHORT  ReportCount;
        USHORT  Reserved2[5];
        ULONG   UnitsExp;
        ULONG   Units;
        LONG    LogicalMin, LogicalMax;
        LONG    PhysicalMin, PhysicalMax;
        union { struct Range {...}; struct NotRange {...}; };

    This is a different (and shorter) reserved run than HIDP_BUTTON_CAPS --
    HasNull/Reserved/BitSize/ReportCount/Reserved2[5] here, versus a flat
    ULONG Reserved[10] there. Mixing the two up shifts everything from
    BitSize onward.
    """

    _anonymous_ = ("u",)
    _fields_ = [
        ("UsagePage", wintypes.USHORT),
        ("ReportID", ctypes.c_ubyte),
        ("IsAlias", ctypes.c_ubyte),
        ("BitField", wintypes.USHORT),
        ("LinkCollection", wintypes.USHORT),
        ("LinkUsage", wintypes.USHORT),
        ("LinkUsagePage", wintypes.USHORT),
        ("IsRange", ctypes.c_ubyte),
        ("IsStringRange", ctypes.c_ubyte),
        ("IsDesignatorRange", ctypes.c_ubyte),
        ("IsAbsolute", ctypes.c_ubyte),
        ("HasNull", ctypes.c_ubyte),
        ("Reserved", ctypes.c_ubyte),
        ("BitSize", wintypes.USHORT),
        ("ReportCount", wintypes.USHORT),
        ("Reserved2", wintypes.USHORT * 5),
        ("UnitsExp", wintypes.ULONG),
        ("Units", wintypes.ULONG),
        ("LogicalMin", ctypes.c_long),
        ("LogicalMax", ctypes.c_long),
        ("PhysicalMin", ctypes.c_long),
        ("PhysicalMax", ctypes.c_long),
        ("u", _ValueCapsUnion),
    ]


# ---------------------------------------------------------------------------
# DLL bindings -- every function gets explicit argtypes/restype
# ---------------------------------------------------------------------------

_setupapi = ctypes.WinDLL("setupapi", use_last_error=True)
_hid = ctypes.WinDLL("hid", use_last_error=True)
_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

HidD_GetHidGuid = _hid.HidD_GetHidGuid
HidD_GetHidGuid.argtypes = [ctypes.POINTER(GUID)]
HidD_GetHidGuid.restype = None

HidD_GetAttributes = _hid.HidD_GetAttributes
HidD_GetAttributes.argtypes = [wintypes.HANDLE, ctypes.POINTER(HIDD_ATTRIBUTES)]
HidD_GetAttributes.restype = wintypes.BOOLEAN

HidD_GetProductString = _hid.HidD_GetProductString
HidD_GetProductString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
HidD_GetProductString.restype = wintypes.BOOLEAN

HidD_GetManufacturerString = _hid.HidD_GetManufacturerString
HidD_GetManufacturerString.argtypes = [wintypes.HANDLE, ctypes.c_void_p, wintypes.ULONG]
HidD_GetManufacturerString.restype = wintypes.BOOLEAN

HidD_GetPreparsedData = _hid.HidD_GetPreparsedData
HidD_GetPreparsedData.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
HidD_GetPreparsedData.restype = wintypes.BOOLEAN

HidD_FreePreparsedData = _hid.HidD_FreePreparsedData
HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
HidD_FreePreparsedData.restype = wintypes.BOOLEAN

HidP_GetCaps = _hid.HidP_GetCaps
HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.POINTER(HIDP_CAPS)]
HidP_GetCaps.restype = ctypes.c_long

HidP_GetButtonCaps = _hid.HidP_GetButtonCaps
HidP_GetButtonCaps.argtypes = [
    ctypes.c_int,
    ctypes.POINTER(HIDP_BUTTON_CAPS),
    ctypes.POINTER(wintypes.USHORT),
    ctypes.c_void_p,
]
HidP_GetButtonCaps.restype = ctypes.c_long

HidP_GetValueCaps = _hid.HidP_GetValueCaps
HidP_GetValueCaps.argtypes = [
    ctypes.c_int,
    ctypes.POINTER(HIDP_VALUE_CAPS),
    ctypes.POINTER(wintypes.USHORT),
    ctypes.c_void_p,
]
HidP_GetValueCaps.restype = ctypes.c_long

SetupDiGetClassDevsW = _setupapi.SetupDiGetClassDevsW
SetupDiGetClassDevsW.argtypes = [
    ctypes.POINTER(GUID),
    wintypes.LPCWSTR,
    wintypes.HWND,
    wintypes.DWORD,
]
SetupDiGetClassDevsW.restype = wintypes.HANDLE

SetupDiEnumDeviceInterfaces = _setupapi.SetupDiEnumDeviceInterfaces
SetupDiEnumDeviceInterfaces.argtypes = [
    wintypes.HANDLE,
    ctypes.c_void_p,
    ctypes.POINTER(GUID),
    wintypes.DWORD,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
]
SetupDiEnumDeviceInterfaces.restype = wintypes.BOOL

SetupDiGetDeviceInterfaceDetailW = _setupapi.SetupDiGetDeviceInterfaceDetailW
SetupDiGetDeviceInterfaceDetailW.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(SP_DEVICE_INTERFACE_DATA),
    ctypes.c_void_p,
    wintypes.DWORD,
    ctypes.POINTER(wintypes.DWORD),
    ctypes.c_void_p,
]
SetupDiGetDeviceInterfaceDetailW.restype = wintypes.BOOL

SetupDiDestroyDeviceInfoList = _setupapi.SetupDiDestroyDeviceInfoList
SetupDiDestroyDeviceInfoList.argtypes = [wintypes.HANDLE]
SetupDiDestroyDeviceInfoList.restype = wintypes.BOOL

CreateFileW = _kernel32.CreateFileW
CreateFileW.argtypes = [
    wintypes.LPCWSTR,
    wintypes.DWORD,
    wintypes.DWORD,
    ctypes.c_void_p,
    wintypes.DWORD,
    wintypes.DWORD,
    wintypes.HANDLE,
]
CreateFileW.restype = wintypes.HANDLE

CloseHandle = _kernel32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]
CloseHandle.restype = wintypes.BOOL


# ---------------------------------------------------------------------------
# Enumeration
# ---------------------------------------------------------------------------


def _last_error_text() -> str:
    return ctypes.WinError(ctypes.get_last_error()).strerror or "unknown error"


def enumerate_hid_paths() -> list[str]:
    """Return the device path of every HID device interface Windows knows
    about right now, present or not filtered further than DIGCF_PRESENT."""
    hid_guid = GUID()
    HidD_GetHidGuid(ctypes.byref(hid_guid))

    dev_info = SetupDiGetClassDevsW(
        ctypes.byref(hid_guid), None, None, DIGCF_PRESENT | DIGCF_DEVICEINTERFACE
    )
    if dev_info == INVALID_HANDLE_VALUE:
        raise OSError(f"SetupDiGetClassDevs failed: {_last_error_text()}")

    paths: list[str] = []
    try:
        index = 0
        while True:
            interface_data = SP_DEVICE_INTERFACE_DATA()
            interface_data.cbSize = ctypes.sizeof(SP_DEVICE_INTERFACE_DATA)
            ok = SetupDiEnumDeviceInterfaces(
                dev_info, None, ctypes.byref(hid_guid), index, ctypes.byref(interface_data)
            )
            index += 1
            if not ok:
                error = ctypes.get_last_error()
                if error != ERROR_NO_MORE_ITEMS:
                    print(
                        f"warning: SetupDiEnumDeviceInterfaces stopped early: "
                        f"{_last_error_text()}",
                        file=sys.stderr,
                    )
                break

            required = wintypes.DWORD(0)
            SetupDiGetDeviceInterfaceDetailW(
                dev_info, ctypes.byref(interface_data), None, 0, ctypes.byref(required), None
            )
            if required.value == 0:
                continue

            buffer = ctypes.create_string_buffer(required.value)
            # First DWORD of SP_DEVICE_INTERFACE_DETAIL_DATA_W is cbSize; it
            # must hold DEVICE_INTERFACE_DETAIL_CBSIZE (the size of the
            # struct's fixed part), not the size of this whole buffer.
            ctypes.cast(buffer, ctypes.POINTER(wintypes.DWORD))[0] = (
                DEVICE_INTERFACE_DETAIL_CBSIZE
            )
            ok = SetupDiGetDeviceInterfaceDetailW(
                dev_info,
                ctypes.byref(interface_data),
                buffer,
                required,
                None,
                None,
            )
            if not ok:
                print(
                    f"warning: could not get device path for interface {index - 1}: "
                    f"{_last_error_text()}",
                    file=sys.stderr,
                )
                continue

            # The device path (a NUL-terminated WCHAR string) follows the
            # DWORD cbSize field, i.e. starts 4 bytes into the buffer -- the
            # struct's real field offset, independent of the 8-vs-6 cbSize
            # value used above.
            path = ctypes.wstring_at(ctypes.addressof(buffer) + ctypes.sizeof(wintypes.DWORD))
            paths.append(path)
    finally:
        SetupDiDestroyDeviceInfoList(dev_info)

    return paths


# ---------------------------------------------------------------------------
# Per-device inspection
# ---------------------------------------------------------------------------


class DeviceReport:
    def __init__(self, path: str) -> None:
        self.path = path
        self.error: str | None = None
        self.vendor_id = 0
        self.product_id = 0
        self.version = 0
        self.product = ""
        self.manufacturer = ""
        self.caps: HIDP_CAPS | None = None
        self.button_caps: list[HIDP_BUTTON_CAPS] = []
        self.value_caps: list[HIDP_VALUE_CAPS] = []

    @property
    def is_mouse_like(self) -> bool:
        if self.caps is None:
            return False
        return self.caps.UsagePage == USAGE_PAGE_GENERIC_DESKTOP and self.caps.Usage in (
            USAGE_POINTER,
            USAGE_MOUSE,
        )

    def one_line(self) -> str:
        if self.error:
            return f"{self.path}: {self.error}"
        ids = f"VID_{self.vendor_id:04X}&PID_{self.product_id:04X} v{self.version:04X}"
        name = self.product or "(no product string)"
        usage = "?"
        if self.caps is not None:
            usage = f"page 0x{self.caps.UsagePage:02X} usage 0x{self.caps.Usage:02X}"
        return f"{ids}  {name!r}  top-level {usage}  {self.path}"


def _get_hid_string(func, handle: wintypes.HANDLE) -> str:
    """HidD_GetProductString/HidD_GetManufacturerString: best effort. Many
    devices (especially composite ones exposing more than one HID
    collection) simply don't have one; that is normal, not a failure."""
    buffer = ctypes.create_unicode_buffer(256)
    ok = func(handle, buffer, ctypes.sizeof(buffer))
    if not ok:
        return ""
    return buffer.value


def inspect_device(path: str) -> DeviceReport:
    report = DeviceReport(path)

    # dwDesiredAccess=0: query-only access. A mouse's HID collection is
    # normally held open by the mouse class driver, so asking for
    # GENERIC_READ here gets ACCESS_DENIED; asking for nothing at all is
    # exactly what HidD_Get*/HidP_Get* need and Windows grants it even
    # while the device is "in use."
    handle = CreateFileW(
        path,
        0,
        FILE_SHARE_READ | FILE_SHARE_WRITE,
        None,
        OPEN_EXISTING,
        0,
        None,
    )
    if handle == INVALID_HANDLE_VALUE:
        report.error = f"CreateFileW failed: {_last_error_text()}"
        return report

    preparsed = ctypes.c_void_p(0)
    try:
        attributes = HIDD_ATTRIBUTES()
        attributes.Size = ctypes.sizeof(HIDD_ATTRIBUTES)
        if HidD_GetAttributes(handle, ctypes.byref(attributes)):
            report.vendor_id = attributes.VendorID
            report.product_id = attributes.ProductID
            report.version = attributes.VersionNumber

        report.product = _get_hid_string(HidD_GetProductString, handle)
        report.manufacturer = _get_hid_string(HidD_GetManufacturerString, handle)

        if not HidD_GetPreparsedData(handle, ctypes.byref(preparsed)):
            report.error = f"HidD_GetPreparsedData failed: {_last_error_text()}"
            return report

        caps = HIDP_CAPS()
        status = HidP_GetCaps(preparsed, ctypes.byref(caps))
        if status != HIDP_STATUS_SUCCESS:
            report.error = f"HidP_GetCaps failed: NTSTATUS 0x{status & 0xFFFFFFFF:08X}"
            return report
        report.caps = caps

        if caps.NumberInputButtonCaps:
            count = wintypes.USHORT(caps.NumberInputButtonCaps)
            array = (HIDP_BUTTON_CAPS * caps.NumberInputButtonCaps)()
            status = HidP_GetButtonCaps(
                HidP_Input, array, ctypes.byref(count), preparsed
            )
            if status != HIDP_STATUS_SUCCESS:
                print(
                    f"warning: HidP_GetButtonCaps failed for {path}: "
                    f"NTSTATUS 0x{status & 0xFFFFFFFF:08X}",
                    file=sys.stderr,
                )
            else:
                report.button_caps = list(array)[: count.value]

        if caps.NumberInputValueCaps:
            count = wintypes.USHORT(caps.NumberInputValueCaps)
            array = (HIDP_VALUE_CAPS * caps.NumberInputValueCaps)()
            status = HidP_GetValueCaps(
                HidP_Input, array, ctypes.byref(count), preparsed
            )
            if status != HIDP_STATUS_SUCCESS:
                print(
                    f"warning: HidP_GetValueCaps failed for {path}: "
                    f"NTSTATUS 0x{status & 0xFFFFFFFF:08X}",
                    file=sys.stderr,
                )
            else:
                report.value_caps = list(array)[: count.value]

        return report
    finally:
        if preparsed.value:
            HidD_FreePreparsedData(preparsed)
        CloseHandle(handle)


# ---------------------------------------------------------------------------
# Printing
# ---------------------------------------------------------------------------


def _bool(value: int) -> str:
    return "yes" if value else "no"


def print_button_cap(index: int, cap: HIDP_BUTTON_CAPS) -> None:
    flagged = " <-- Button page" if cap.UsagePage == USAGE_PAGE_BUTTON else ""
    print(f"    ButtonCaps[{index}]{flagged}")
    print(f"      UsagePage=0x{cap.UsagePage:02X}  ReportID={cap.ReportID}  "
          f"IsRange={_bool(cap.IsRange)}  IsAbsolute={_bool(cap.IsAbsolute)}")
    if cap.IsRange:
        print(f"      UsageMin=0x{cap.Range.UsageMin:02X}  "
              f"UsageMax=0x{cap.Range.UsageMax:02X}  "
              f"DataIndexMin={cap.Range.DataIndexMin}  "
              f"DataIndexMax={cap.Range.DataIndexMax}")
    else:
        print(f"      Usage=0x{cap.NotRange.Usage:02X}  "
              f"DataIndex={cap.NotRange.DataIndex}")
    print(f"      LinkCollection={cap.LinkCollection}  "
          f"LinkUsagePage=0x{cap.LinkUsagePage:02X}  LinkUsage=0x{cap.LinkUsage:02X}  "
          f"BitField=0x{cap.BitField:04X}")


def print_value_cap(index: int, cap: HIDP_VALUE_CAPS) -> None:
    print(f"    ValueCaps[{index}]")
    if cap.IsRange:
        usage_text = f"UsageMin=0x{cap.Range.UsageMin:02X} UsageMax=0x{cap.Range.UsageMax:02X}"
    else:
        usage_text = f"Usage=0x{cap.NotRange.Usage:02X}"
    print(f"      UsagePage=0x{cap.UsagePage:02X}  {usage_text}  ReportID={cap.ReportID}")
    print(f"      BitSize={cap.BitSize}  ReportCount={cap.ReportCount}  "
          f"LogicalMin={cap.LogicalMin}  LogicalMax={cap.LogicalMax}  "
          f"IsAbsolute={_bool(cap.IsAbsolute)}")


def print_mouse_report(report: DeviceReport) -> None:
    assert report.caps is not None
    print("=" * 78)
    print(f"MOUSE-LIKE DEVICE: {report.path}")
    print(f"  VendorID=0x{report.vendor_id:04X}  ProductID=0x{report.product_id:04X}  "
          f"VersionNumber=0x{report.version:04X}")
    print(f"  Product: {report.product or '(none reported)'}")
    print(f"  Manufacturer: {report.manufacturer or '(none reported)'}")
    print(f"  Top-level collection: UsagePage=0x{report.caps.UsagePage:02X}  "
          f"Usage=0x{report.caps.Usage:02X}")
    print(f"  InputReportByteLength: {report.caps.InputReportByteLength}")
    print(f"  NumberInputButtonCaps: {report.caps.NumberInputButtonCaps}  "
          f"NumberInputValueCaps: {report.caps.NumberInputValueCaps}")

    if report.button_caps:
        print("  Button caps (HidP_GetButtonCaps, HidP_Input):")
        for index, cap in enumerate(report.button_caps):
            print_button_cap(index, cap)
    else:
        print("  Button caps: none returned")

    if report.value_caps:
        print("  Value caps (HidP_GetValueCaps, HidP_Input):")
        for index, cap in enumerate(report.value_caps):
            print_value_cap(index, cap)
    else:
        print("  Value caps: none returned")

    button_page_caps = [c for c in report.button_caps if c.UsagePage == USAGE_PAGE_BUTTON]
    print("  Verdict:")
    if not button_page_caps:
        print("    No Button-page (0x09) Input caps were reported for this device.")
        return

    print(f"    {len(button_page_caps)} Button-page Input cap "
          f"{'entry' if len(button_page_caps) == 1 else 'entries'} found.")
    by_report_id: dict[int, list[HIDP_BUTTON_CAPS]] = {}
    for cap in button_page_caps:
        by_report_id.setdefault(cap.ReportID, []).append(cap)

    split_found = False
    for report_id, caps_for_id in by_report_id.items():
        if len(caps_for_id) > 1:
            split_found = True
            ranges = []
            for cap in caps_for_id:
                if cap.IsRange:
                    ranges.append(f"{cap.Range.UsageMin}-{cap.Range.UsageMax}")
                else:
                    ranges.append(str(cap.NotRange.Usage))
            print(f"    Report ID {report_id}: {len(caps_for_id)} separate Input items "
                  f"declare buttons ({', '.join(ranges)}). This mouse declares its "
                  f"buttons across MULTIPLE Input items -- the split shape our "
                  f"descriptor parser used to drop buttons from.")
    if not split_found:
        for report_id, caps_for_id in by_report_id.items():
            cap = caps_for_id[0]
            if cap.IsRange:
                print(f"    Report ID {report_id}: one Input item, contiguous run "
                      f"{cap.Range.UsageMin}-{cap.Range.UsageMax}. Not split.")
            else:
                print(f"    Report ID {report_id}: one Input item, single usage "
                      f"{cap.NotRange.Usage}. Not split.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    try:
        paths = enumerate_hid_paths()
    except OSError as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    if not paths:
        print("No HID devices were found on this system at all "
              "(SetupDiEnumDeviceInterfaces returned nothing present).")
        return 0

    reports = [inspect_device(path) for path in paths]

    mouse_reports = [report for report in reports if report.is_mouse_like]
    other_reports = [report for report in reports if not report.is_mouse_like]

    print(f"Found {len(reports)} HID device interface(s): "
          f"{len(mouse_reports)} mouse/pointer-like, {len(other_reports)} other.")
    print()

    if mouse_reports:
        for report in mouse_reports:
            if report.error:
                print("=" * 78)
                print(f"MOUSE-LIKE DEVICE (failed to fully read): {report.path}")
                print(f"  {report.error}")
            else:
                print_mouse_report(report)
        print("=" * 78)
    else:
        print("No mouse- or pointer-like top-level collection (Generic Desktop "
              "page, Usage 0x01 Pointer or 0x02 Mouse) was found among the "
              "attached HID devices. If the mouse under investigation is not "
              "plugged in, that is expected -- plug it in and run this again.")

    print()
    print(f"Other HID devices ({len(other_reports)}):")
    if other_reports:
        for report in other_reports:
            print(f"  {report.one_line()}")
    else:
        print("  (none)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
