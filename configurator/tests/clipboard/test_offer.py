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


def test_from_dict_refuses_a_dictionary_with_wrong_types():
    with pytest.raises(ValueError):
        ClipboardOffer.from_dict(
            {
                "origin_id": "a" * 32,
                "seq": 1,
                "descriptors": [
                    {"mime": 123, "size": 5, "sha256": "abc"}  # mime должна быть строка
                ],
            }
        )
