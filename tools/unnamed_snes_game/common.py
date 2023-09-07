# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import sys
from io import StringIO
from enum import IntEnum, Enum, unique
from typing import Callable, Final, Optional, TextIO, Union
from abc import abstractmethod

from .ansi_color import NoAnsiColors, AnsiColors


# Offset between the first_resource_bank and the named data banks
MS_FS_DATA_BANK_OFFSET = 0
DYNAMIC_SPRITE_TILES_BANK_OFFSET = 1
ROOM_DATA_BANK_OFFSET = 2

# Reuse Room Data Bank for resources_over_usb2snes response data
USB2SNES_DATA_BANK_OFFSET = ROOM_DATA_BANK_OFFSET


USE_RESOURCES_OVER_USB2SNES_LABEL = "resources.UseResourcesOverUsb2Snes"


# order MUST match `ResourceType` enum in `src/metasprites.wiz`
#
# enum fields MUST be plural
@unique
class ResourceType(IntEnum):
    mt_tilesets = 0
    ms_spritesheets = 1
    tiles = 2
    bg_images = 3
    songs = 4


def lorom_address_to_rom_offset(addr: int) -> int:
    if addr & 0x3F0000 < 0x40 and addr & 0xFFFF < 0x8000:
        raise ValueError(f"addr is not a ROM address: 0x{addr:06x}")

    if addr >> 16 == 0x7E or addr >> 16 == 0x7F:
        raise ValueError(f"addr is not a ROM address: 0x{addr:06x}")

    return ((addr & 0x3F0000) >> 1) | (addr & 0x7FFF)


def hirom_address_to_rom_offset(addr: int) -> int:
    if addr & 0x3F0000 < 0x40 and addr & 0xFFFF < 0x8000:
        raise ValueError(f"addr is not a ROM address: 0x{addr:06x}")

    if addr >> 16 == 0x7E or addr >> 16 == 0x7F:
        raise ValueError(f"addr is not a ROM address: 0x{addr:06x}")

    return addr & 0x3FFFFF


class MemoryMapMode(Enum):
    LOROM = 0x8000, 0x8000, lorom_address_to_rom_offset
    HIROM = 0x0000, 0x10000, hirom_address_to_rom_offset

    def __init__(self, bank_start: int, bank_size: int, addr_to_offset: Callable[[int], int]):
        self.bank_start: int = bank_start
        self.bank_size: int = bank_size
        self.address_to_rom_offset: Callable[[int], int] = addr_to_offset


class RomData:
    def __init__(self, addr: int, max_size: int) -> None:
        self._out: bytearray = bytearray(max_size)

        self._view: memoryview = memoryview(self._out)

        self._pos: int = 0
        self._addr: int = addr

    def data(self) -> memoryview:
        return self._view[0 : self._pos]

    def allocate(self, size: int) -> tuple[memoryview, int]:
        a = self._addr
        v = self._view[self._pos : self._pos + size]

        self._pos += size
        self._addr += size

        return v, a

    def insert_data(self, data: bytes) -> int:
        # ::TODO deduplicate data::
        size = len(data)

        a = self._addr
        self._view[self._pos : self._pos + size] = data

        self._pos += size
        self._addr += size

        return a

    def insert_data_addr_table(self, data_list: list[bytes]) -> int:
        table_size = len(data_list) * 2
        table, table_addr = self.allocate(table_size)

        i = 0
        for d in data_list:
            addr = self.insert_data(d) & 0xFFFF

            table[i] = addr & 0xFF
            table[i + 1] = addr >> 8

            i += 2

        assert i == table_size

        return table_addr

    # Dynamic Metasprite data stores tile addresses before the frame data but the frame table must point to the frame data.
    def insert_ms_frame_addr_table(self, data_list: list[tuple[bytes, int]]) -> int:
        table_size = len(data_list) * 2
        table, table_addr = self.allocate(table_size)

        i = 0
        for data, offset in data_list:
            addr = (self.insert_data(data) + offset) & 0xFFFF

            table[i] = addr & 0xFF
            table[i + 1] = addr >> 8

            i += 2

        assert i == table_size

        return table_addr


class MultilineError(Exception):
    @abstractmethod
    def print_indented(self, fp: TextIO) -> None:
        pass

    def string_indented(self) -> str:
        with StringIO() as f:
            self.print_indented(f)
            return f.getvalue()


class SimpleMultilineError(MultilineError):
    def __init__(self, short_message: str, errors: list[str]):
        self.short_message: Final = short_message
        self.errors: Final = errors

    def print_indented(self, fp: TextIO) -> None:
        if len(self.errors) == 1:
            fp.write(f"{ self.short_message }: { self.errors[0] }")
        else:
            fp.write(f"{ self.short_message }:")
            for e in self.errors:
                fp.write(f"\n    { e }")


class FileError(Exception):
    def __init__(self, message: str, path: tuple[str, ...]):
        self.message: Final = message
        self.path: Final = path

    def __str__(self) -> str:
        return f"{ ' '.join(self.path) }: { self.message }"


def print_error(msg: str, e: Optional[Union[str, Exception]] = None, fp: Optional[TextIO] = None) -> None:
    if fp is None:
        fp = sys.stderr

    ac = AnsiColors if fp.isatty() else NoAnsiColors

    fp.write(ac.BOLD + ac.BRIGHT_RED)
    fp.write(msg)
    if e:
        fp.write(": ")
        fp.write(ac.NORMAL)
        if isinstance(e, str):
            fp.write(e)
        elif isinstance(e, ValueError) or isinstance(e, RuntimeError):
            fp.write(str(e))
        elif isinstance(e, FileError):
            if e.path:
                fp.write(ac.BOLD + ac.BRIGHT_WHITE)
                fp.write(e.path[0])
                fp.write(ac.NORMAL)
                if len(e.path) > 1:
                    fp.write(f" { ': '.join(e.path[1:]) }: ")
                else:
                    fp.write(": ")
            fp.write(ac.BRIGHT_RED)
            fp.write(e.message)
        elif isinstance(e, MultilineError):
            e.print_indented(fp)
        else:
            fp.write(f"{ type(e).__name__ }({ e })")
    fp.write(ac.RESET + "\n")
