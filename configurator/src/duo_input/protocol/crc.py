"""CRC algorithms used by the Duo Input transport."""


def _as_bytes(data: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes-like")
    return bytes(data)


def crc16_ccitt(data: bytes | bytearray | memoryview) -> int:
    """Return CRC-16/CCITT-FALSE for *data*."""
    crc = 0xFFFF
    for byte in _as_bytes(data):
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def crc32_ieee(data: bytes | bytearray | memoryview) -> int:
    """Return the reflected CRC-32/IEEE checksum for *data*."""
    crc = 0xFFFFFFFF
    for byte in _as_bytes(data):
        crc ^= byte
        for _ in range(8):
            crc = (crc >> 1) ^ 0xEDB88320 if crc & 1 else crc >> 1
    return crc ^ 0xFFFFFFFF
