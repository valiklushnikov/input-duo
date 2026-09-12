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
    assert out.value is None


def test_guid_round_trips_through_its_string_form():
    text = "{0000000C-0000-0000-C000-000000000046}"  # IID_IStream

    guid = guid_from_string(text)

    assert guid.Data1 == 0x0000000C
    assert guid.Data4[7] == 0x46
