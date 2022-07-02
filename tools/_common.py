# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from enum import IntEnum, Enum, unique

from typing import Callable, TextIO
from abc import abstractmethod


# Offset between the first_resource_bank and the named data banks
MS_FS_DATA_BANK_OFFSET = 0
ROOM_DATA_BANK_OFFSET = 1

# Reuse Room Data Bank for resources_over_usb2snes response data
USB2SNES_DATA_BANK_OFFSET = ROOM_DATA_BANK_OFFSET


USE_RESOURCES_OVER_USB2SNES_LABEL = 'resources.UseResourcesOverUsb2Snes'


# order MUST match `ResourceType` enum in `src/metasprites.wiz`
#
# enum fields MUST be plural
@unique
class ResourceType(IntEnum):
    mt_tilesets     = 0
    ms_spritesheets = 1
    tiles           = 2



def lorom_address_to_rom_offset(addr : int) -> int:
    if addr & 0x3f0000 < 0x40 and addr & 0xffff < 0x8000:
        raise ValueError(f"addr is not a ROM address: 0x{addr:06x}")

    if addr >> 16 == 0x7e or addr >> 16 == 0x7f:
        raise ValueError(f"addr is not a ROM address: 0x{addr:06x}")

    return ((addr & 0x3f0000) >> 1) | (addr & 0x7fff)



def hirom_address_to_rom_offset(addr : int) -> int:
    if addr & 0x3f0000 < 0x40 and addr & 0xffff < 0x8000:
        raise ValueError(f"addr is not a ROM address: 0x{addr:06x}")

    if addr >> 16 == 0x7e or addr >> 16 == 0x7f:
        raise ValueError(f"addr is not a ROM address: 0x{addr:06x}")

    return addr & 0x3fffff



class MemoryMapMode(Enum):
    LOROM = 0x8000,  0x8000, lorom_address_to_rom_offset
    HIROM = 0x0000, 0x10000, hirom_address_to_rom_offset

    def __init__(self, bank_start : int, bank_size : int, addr_to_offset : Callable[[int], int]):
        self.bank_start : int = bank_start
        self.bank_size  : int = bank_size
        self.address_to_rom_offset : Callable[[int], int] = addr_to_offset



class RomData:
    def __init__(self, addr : int, max_size : int) -> None:
        self._out   : bytearray = bytearray(max_size)

        self._view  : memoryview = memoryview(self._out)

        self._pos   : int = 0
        self._addr  : int = addr


    def data(self) -> memoryview:
        return self._view[0:self._pos]


    def allocate(self, size : int) -> tuple[memoryview, int]:
        a = self._addr
        v = self._view[self._pos : self._pos + size]

        self._pos += size
        self._addr += size

        return v, a


    def insert_data(self, data : bytes) -> int:
        # ::TODO deduplicate data::
        size = len(data)

        a = self._addr
        self._view[self._pos : self._pos + size] = data

        self._pos += size
        self._addr += size

        return a


    def insert_data_addr_table(self, data_list : list[bytes]) -> int:
        table_size = len(data_list) * 2
        table, table_addr = self.allocate(table_size)

        i = 0
        for d in data_list:
            addr = self.insert_data(d) & 0xffff

            table[i]   = addr & 0xff
            table[i+1] = addr >> 8

            i += 2

        assert i == table_size

        return table_addr



class MultilineError(Exception):
    @abstractmethod
    def print_indented(self, fp : TextIO) -> None: pass


