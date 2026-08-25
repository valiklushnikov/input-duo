import json
from pathlib import Path

import pytest

from duo_input.protocol.cobs import cobs_decode, cobs_encode
from duo_input.protocol.crc import crc16_ccitt, crc32_ieee


def load_vectors():
    return json.loads(Path("tests", "vectors", "transport_vectors.json").read_text("utf-8"))


@pytest.mark.parametrize("case", load_vectors()["cobs"], ids=lambda case: case["name"])
def test_cobs_vector(case):
    raw = bytes.fromhex(case["raw"])

    assert cobs_encode(raw).hex() == case["encoded"]
    assert cobs_decode(bytes.fromhex(case["encoded"])) == raw


@pytest.mark.parametrize("case", load_vectors()["malformed_cobs"], ids=lambda case: case["name"])
def test_cobs_decoder_rejects_malformed_vector(case):
    with pytest.raises(ValueError, match="^invalid COBS frame$"):
        cobs_decode(bytes.fromhex(case["encoded"]))


def test_crc_vectors():
    crc = load_vectors()["crc"]
    data = bytes.fromhex(crc["input"])

    assert crc16_ccitt(data) == int(crc["crc16_ccitt"], 16)
    assert crc32_ieee(data) == int(crc["crc32_ieee"], 16)


@pytest.mark.parametrize("value", ["text", 123, None])
def test_cobs_rejects_ambiguous_non_bytes_inputs(value):
    with pytest.raises(TypeError):
        cobs_encode(value)
