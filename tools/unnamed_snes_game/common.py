# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import struct
from enum import IntEnum, Enum, unique
from typing import Callable, Final, Optional, Union


# Offset between the first_resource_bank and the named data banks
MS_FS_DATA_BANK_OFFSET = 0
DYNAMIC_SPRITE_TILES_BANK_OFFSET = 1
RESOURCE_ADDR_TABLE_BANK_OFFSET = 2

# Resource table data for resources_over_usb2snes response data
USB2SNES_DATA_BANK_OFFSET = RESOURCE_ADDR_TABLE_BANK_OFFSET


USE_RESOURCES_OVER_USB2SNES_LABEL = "resources.UseResourcesOverUsb2Snes"


# order MUST match `ResourceType` enum in `src/metasprites.wiz`
#
# enum fields MUST be plural
@unique
class ResourceType(IntEnum):
    palettes = 0
    mt_tilesets = 1
    second_layers = 2
    ms_spritesheets = 3
    tiles = 4
    bg_images = 5
    audio_data = 6
    dungeons = 7


class FixedSizedData:
    "Engine Data with a fixed size"

    def __init__(self, data: bytes):
        if len(data) > 0xFFFF:
            raise RuntimeError("data is too large")
        self._data: Final = data

    def size(self) -> int:
        return len(self._data)

    def data(self) -> bytes:
        return self._data


# ::TODO compress dynamic data::
class DynamicSizedData:
    "Engine data with an unknown size"

    def __init__(self, data: bytes):
        if len(data) > 0xFFFF:
            raise RuntimeError("data is too large")
        self._data: Final = struct.pack("<H", len(data)) + data

    def size(self) -> int:
        return len(self._data)

    def data(self) -> bytes:
        return self._data


class EngineData:
    def __init__(self, ram_data: Optional[Union[FixedSizedData, DynamicSizedData]], ppu_data: Optional[DynamicSizedData]):
        self.ram_data: Final = ram_data
        self.ppu_data: Final = ppu_data

        if self.size() > 0xFFFF:
            raise RuntimeError("data is too large")

    def size(self) -> int:
        size = 0
        if self.ram_data is not None:
            size += self.ram_data.size()
        if self.ppu_data is not None:
            size += self.ppu_data.size()
        return size

    def to_rou2s_data(self) -> bytes:
        if self.ram_data and self.ppu_data:
            return self.ram_data.data() + self.ppu_data.data()
        elif self.ram_data:
            return self.ram_data.data()
        elif self.ppu_data:
            return self.ppu_data.data()
        else:
            raise RuntimeError("No data")

    def ram_and_ppu_size(self) -> tuple[int, int]:
        if self.ram_data is not None and self.ppu_data is not None:
            return self.ram_data.size(), self.ppu_data.size()
        elif self.ram_data is not None:
            return self.ram_data.size(), 0
        elif self.ppu_data is not None:
            return 0, self.ppu_data.size()
        else:
            raise RuntimeError("No data")


def lorom_address_to_rom_offset(addr: int) -> int:
    if addr & 0x3F_0000 < 0x40_0000 and addr & 0xFFFF < 0x8000:
        raise ValueError(f"addr is not a ROM address: 0x{addr:06x}")

    if addr >> 16 == 0x7E or addr >> 16 == 0x7F:
        raise ValueError(f"addr is not a ROM address: 0x{addr:06x}")

    return ((addr & 0x3F0000) >> 1) | (addr & 0x7FFF)


def hirom_address_to_rom_offset(addr: int) -> int:
    if addr & 0x7F_0000 < 0x40_0000 and addr & 0xFFFF < 0x8000:
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
