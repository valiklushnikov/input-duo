"""Read the USB descriptors out of a built firmware ELF and print them as JSON.

The point is to check what the board will actually enumerate as, not what the
source appears to say. Descriptor bytes are assembled by macros, sized by
`sizeof`, and ordered by an initialiser list; every one of those is a place a
mistake hides in a way that reading the source does not reveal. So the bytes
are taken from the linked image.

    python tools/dump_usb_descriptors.py build/pico-release/firmware/u1_main/duo_u1_main.elf

Nothing here is Pico-specific beyond the ELF itself: the symbols are located
with the ELF symbol table and the bytes are read from the section they live in.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
from dataclasses import dataclass
from pathlib import Path

#: The descriptor symbols every Duo Input firmware defines.
WANTED_SYMBOLS = (
    "desc_device",
    "desc_configuration",
    "desc_hid_keyboard_report",
    "desc_hid_mouse_report",
    "desc_hid_consumer_report",
)


class ElfError(ValueError):
    """The file is not an ELF this tool can read."""


@dataclass(frozen=True)
class Section:
    name: str
    address: int
    offset: int
    size: int
    is_nobits: bool


@dataclass(frozen=True)
class Symbol:
    name: str
    address: int
    size: int


class Elf32:
    """Just enough little-endian 32-bit ELF to find a symbol's bytes."""

    def __init__(self, data: bytes) -> None:
        if data[:4] != b"\x7fELF":
            raise ElfError("not an ELF file")
        if data[4] != 1:
            raise ElfError("only 32-bit ELF is supported")
        if data[5] != 1:
            raise ElfError("only little-endian ELF is supported")
        self._data = data
        self._sections = self._read_sections()

    def _read_sections(self) -> list[Section]:
        e_shoff, = struct.unpack_from("<I", self._data, 0x20)
        e_shentsize, e_shnum, e_shstrndx = struct.unpack_from("<HHH", self._data, 0x2E)

        raw = []
        for index in range(e_shnum):
            base = e_shoff + index * e_shentsize
            name_offset, sh_type, _flags, address, offset, size = struct.unpack_from(
                "<IIIIII", self._data, base
            )
            raw.append((name_offset, sh_type, address, offset, size))

        _, _, _, strtab_offset, strtab_size = raw[e_shstrndx]
        names = self._data[strtab_offset : strtab_offset + strtab_size]

        sections = []
        for name_offset, sh_type, address, offset, size in raw:
            end = names.index(b"\0", name_offset)
            sections.append(
                Section(
                    name=names[name_offset:end].decode("ascii"),
                    address=address,
                    offset=offset,
                    size=size,
                    is_nobits=sh_type == 8,  # SHT_NOBITS
                )
            )
        return sections

    def _section(self, name: str) -> Section | None:
        for section in self._sections:
            if section.name == name:
                return section
        return None

    def symbols(self) -> dict[str, Symbol]:
        symtab = self._section(".symtab")
        strtab = self._section(".strtab")
        if symtab is None or strtab is None:
            raise ElfError("the ELF has no symbol table; it was probably stripped")

        names = self._data[strtab.offset : strtab.offset + strtab.size]
        found: dict[str, Symbol] = {}
        for offset in range(symtab.offset, symtab.offset + symtab.size, 16):
            name_offset, value, size = struct.unpack_from("<III", self._data, offset)
            if name_offset == 0:
                continue
            end = names.index(b"\0", name_offset)
            name = names[name_offset:end].decode("ascii")
            if size:
                found[name] = Symbol(name, value, size)
        return found

    def read(self, symbol: Symbol) -> bytes:
        for section in self._sections:
            if section.is_nobits or not section.size:
                continue
            if section.address <= symbol.address < section.address + section.size:
                start = section.offset + (symbol.address - section.address)
                return self._data[start : start + symbol.size]
        raise ElfError(f"{symbol.name} at 0x{symbol.address:08X} is in no loadable section")


def parse_device_descriptor(raw: bytes) -> dict:
    """The fields anyone would check by hand, named."""
    if len(raw) < 18:
        raise ValueError("device descriptor is shorter than 18 bytes")
    (
        length, descriptor_type, bcd_usb, device_class, device_subclass, device_protocol,
        max_packet_size, vendor_id, product_id, bcd_device, manufacturer, product,
        serial_number, configurations,
    ) = struct.unpack_from("<BBHBBBBHHHBBBB", raw, 0)
    return {
        "bLength": length,
        "bDescriptorType": descriptor_type,
        "bcdUSB": bcd_usb,
        "bDeviceClass": device_class,
        "bDeviceSubClass": device_subclass,
        "bDeviceProtocol": device_protocol,
        "bMaxPacketSize0": max_packet_size,
        "idVendor": vendor_id,
        "idProduct": product_id,
        "bcdDevice": bcd_device,
        "iManufacturer": manufacturer,
        "iProduct": product,
        "iSerialNumber": serial_number,
        "bNumConfigurations": configurations,
    }


def parse_configuration(raw: bytes) -> dict:
    """Walk the configuration and list its interfaces and endpoints."""
    if len(raw) < 9:
        raise ValueError("configuration descriptor is shorter than 9 bytes")
    total_length, interface_count, _value, _index, attributes, max_power = struct.unpack_from(
        "<HBBBBB", raw, 2
    )

    interfaces: list[dict] = []
    endpoints: list[dict] = []
    cursor = 0
    while cursor + 2 <= len(raw):
        length = raw[cursor]
        kind = raw[cursor + 1]
        if length == 0:
            break
        if kind == 0x04 and cursor + 9 <= len(raw):  # INTERFACE
            number, alternate, endpoint_count, klass, subclass, protocol, name = (
                struct.unpack_from("<BBBBBBB", raw, cursor + 2)
            )
            interfaces.append(
                {
                    "bInterfaceNumber": number,
                    "bAlternateSetting": alternate,
                    "bNumEndpoints": endpoint_count,
                    "bInterfaceClass": klass,
                    "bInterfaceSubClass": subclass,
                    "bInterfaceProtocol": protocol,
                    "iInterface": name,
                }
            )
        elif kind == 0x05 and cursor + 7 <= len(raw):  # ENDPOINT
            address, attributes_byte, packet_size, interval = struct.unpack_from(
                "<BBHB", raw, cursor + 2
            )
            endpoints.append(
                {
                    "bEndpointAddress": address,
                    "bmAttributes": attributes_byte,
                    "wMaxPacketSize": packet_size,
                    "bInterval": interval,
                }
            )
        cursor += length

    return {
        "wTotalLength": total_length,
        "bNumInterfaces": interface_count,
        "bmAttributes": attributes,
        "bMaxPower": max_power,
        "declared_length": len(raw),
        "interfaces": interfaces,
        "endpoints": endpoints,
    }


def dump(elf_path: str | Path) -> dict:
    """Everything the contract test needs, from one ELF."""
    elf = Elf32(Path(elf_path).read_bytes())
    symbols = elf.symbols()

    missing = [name for name in WANTED_SYMBOLS if name not in symbols]
    if missing:
        raise ElfError(f"{Path(elf_path).name} is missing {', '.join(missing)}")

    raw = {name: elf.read(symbols[name]) for name in WANTED_SYMBOLS}
    return {
        "image": Path(elf_path).name,
        "device": parse_device_descriptor(raw["desc_device"]),
        "configuration": parse_configuration(raw["desc_configuration"]),
        "report_descriptors": {
            "keyboard": raw["desc_hid_keyboard_report"].hex(),
            "mouse": raw["desc_hid_mouse_report"].hex(),
            "consumer": raw["desc_hid_consumer_report"].hex(),
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("elf", type=Path, help="firmware ELF to read")
    parser.add_argument("--output", type=Path, help="write the JSON here instead of stdout")
    arguments = parser.parse_args(argv)

    try:
        document = dump(arguments.elf)
    except (ElfError, OSError, ValueError) as error:
        print(f"{arguments.elf}: {error}", file=sys.stderr)
        return 1

    text = json.dumps(document, indent=2, sort_keys=True)
    if arguments.output:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
