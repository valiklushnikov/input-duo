"""Shared transport codecs for Duo Input protocol frames."""

from .frame import (
    CdcFrame,
    FrameError,
    SpiFrame,
    decode_cdc_frame,
    decode_spi_frame,
    encode_cdc_frame,
    encode_spi_frame,
    is_minor_compatible,
)

__all__ = [
    "CdcFrame",
    "FrameError",
    "SpiFrame",
    "decode_cdc_frame",
    "decode_spi_frame",
    "encode_cdc_frame",
    "encode_spi_frame",
    "is_minor_compatible",
]
