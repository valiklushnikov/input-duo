"""Consistent Overhead Byte Stuffing codec."""


def _as_bytes(data: bytes | bytearray | memoryview) -> bytes:
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise TypeError("data must be bytes-like")
    return bytes(data)


def cobs_encode(data: bytes | bytearray | memoryview) -> bytes:
    """Encode bytes into a COBS frame without a delimiter."""
    source = _as_bytes(data)
    encoded = bytearray()
    code_index = 0
    encoded.append(0)
    code = 1

    for byte in source:
        if byte == 0:
            encoded[code_index] = code
            code_index = len(encoded)
            encoded.append(0)
            code = 1
        else:
            encoded.append(byte)
            code += 1
            if code == 0xFF:
                encoded[code_index] = code
                code_index = len(encoded)
                encoded.append(0)
                code = 1

    encoded[code_index] = code
    return bytes(encoded)


def cobs_decode(data: bytes | bytearray | memoryview) -> bytes:
    """Decode a delimiter-free COBS frame."""
    encoded = _as_bytes(data)
    if not encoded:
        raise ValueError("invalid COBS frame")

    decoded = bytearray()
    read_index = 0
    encoded_size = len(encoded)
    while read_index < encoded_size:
        code = encoded[read_index]
        read_index += 1
        if code == 0:
            raise ValueError("invalid COBS frame")

        block_size = code - 1
        if block_size > encoded_size - read_index:
            raise ValueError("invalid COBS frame")
        decoded.extend(encoded[read_index : read_index + block_size])
        read_index += block_size
        if read_index < encoded_size and code != 0xFF:
            decoded.append(0)

    return bytes(decoded)
