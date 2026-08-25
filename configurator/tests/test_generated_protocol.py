import json
from pathlib import Path

from duo_input.generated import protocol


def test_generated_values_match_schema():
    schema = json.loads(Path("protocol/schema.json").read_text("utf-8"))

    assert protocol.CDC_MAX_PAYLOAD == schema["limits"]["cdc_max_payload"] == 1024
    assert protocol.SPI_FRAME_SIZE == 64
    assert protocol.CdcMessageType.HELLO.value == schema["cdc_messages"]["HELLO"]
