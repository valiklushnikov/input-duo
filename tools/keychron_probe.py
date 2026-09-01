"""Read the probe build's textual GET_DIAGNOSTICS payload from a real U1."""

from __future__ import annotations

import argparse
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "configurator" / "src"))

from PySide6.QtCore import QCoreApplication, QIODevice  # noqa: E402
from PySide6.QtSerialPort import QSerialPort  # noqa: E402

from duo_input.device.service import REQUESTED_CAPABILITIES  # noqa: E402
from duo_input.generated.protocol import CdcMessageType  # noqa: E402
from duo_input.protocol.frame import CdcFrame, decode_cdc_frame, encode_cdc_frame  # noqa: E402


def exchange(port: QSerialPort, frame: CdcFrame) -> CdcFrame:
    wire = encode_cdc_frame(frame)
    if port.write(wire) != len(wire) or not port.waitForBytesWritten(1000):
        raise RuntimeError(f"write failed: {port.errorString()}")

    reply = bytearray()
    while not reply.endswith(b"\0"):
        if not port.waitForReadyRead(2000):
            raise RuntimeError(f"U1 did not answer: {port.errorString()}")
        reply.extend(port.readAll().data())
    return decode_cdc_frame(bytes(reply))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("port")
    args = parser.parse_args()

    app = QCoreApplication.instance() or QCoreApplication([])
    port = QSerialPort()
    port.setPortName(args.port)
    port.setBaudRate(115200)
    if not port.open(QIODevice.OpenModeFlag.ReadWrite):
        raise RuntimeError(f"could not open {args.port}: {port.errorString()}")
    port.setDataTerminalReady(True)

    hello = exchange(
        port,
        CdcFrame(CdcMessageType.HELLO, 1, struct.pack("<I", REQUESTED_CAPABILITIES)),
    )
    if hello.type is not CdcMessageType.DEVICE_INFO or not hello.payload or hello.payload[0] != 0:
        raise RuntimeError(f"HELLO refused: {hello.payload.hex()}")

    diagnostics = exchange(port, CdcFrame(CdcMessageType.GET_DIAGNOSTICS, 2, b""))
    if diagnostics.type is not CdcMessageType.GET_DIAGNOSTICS or not diagnostics.payload:
        raise RuntimeError("unexpected diagnostics reply")
    if diagnostics.payload[0] != 0:
        raise RuntimeError(f"GET_DIAGNOSTICS refused: {diagnostics.payload.hex()}")
    print(diagnostics.payload[1:].decode("utf-8", errors="replace"))
    port.close()
    del app
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
