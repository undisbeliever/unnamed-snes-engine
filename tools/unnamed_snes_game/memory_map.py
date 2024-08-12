# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from enum import Enum

from typing import Callable


# Offset between the first_resource_bank and the named data banks
MS_FS_DATA_BANK_OFFSET = 0
DYNAMIC_SPRITE_TILES_BANK_OFFSET = 1
RESOURCE_ADDR_TABLE_BANK_OFFSET = 2

# Resource table data for resources_over_usb2snes response data
USB2SNES_DATA_BANK_OFFSET = RESOURCE_ADDR_TABLE_BANK_OFFSET


USE_RESOURCES_OVER_USB2SNES_LABEL = "resources.UseResourcesOverUsb2Snes"


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
