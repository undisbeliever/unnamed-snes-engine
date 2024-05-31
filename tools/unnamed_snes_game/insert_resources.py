#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import os.path
from typing import Callable, Final, NamedTuple, Optional, Union

from .common import (
    MS_FS_DATA_BANK_OFFSET,
    DYNAMIC_SPRITE_TILES_BANK_OFFSET,
    ROOM_DATA_BANK_OFFSET,
    ResourceType,
    USE_RESOURCES_OVER_USB2SNES_LABEL,
)
from .common import EngineData, FixedSizedData, print_error
from .json_formats import Filename, Mappings, MemoryMap
from .entity_data import ENTITY_ROM_DATA_LABEL, validate_entity_rom_data_symbols, expected_blank_entity_rom_data
from .resources_compiler import DataStore, ProjectCompiler, ResourceError, ResourceData

Address = int
RomOffset = int


def read_binary_file(path: Filename, max_size: int) -> bytes:
    with open(path, "rb") as fp:
        out = fp.read(max_size)

        if fp.read(1):
            raise RuntimeError(f"File is too large: maximum file size is { max_size }: { path }")

        return out


def get_largest_rom_address(symbols: dict[str, int]) -> int:
    # assumes max is never a zeropage or low-Ram address
    return max([a for a in symbols.values() if a & 0xFE0000 != 0x7E])


ROM_HEADER_V3_ADDR = 0xFFB0
ROM_HEADER_TITLE_ADDR = 0xFFC0
ROM_HEADER_TITLE_SIZE = 21
ROM_HEADER_TITLE_ENCODING = "Shift-JIS"  # This is supposed to be `JIS X 0201`, but python does not support it.


def convert_title(s: str) -> bytes:
    title = s.encode(ROM_HEADER_TITLE_ENCODING).ljust(ROM_HEADER_TITLE_SIZE, b"\x20")
    if len(title) != ROM_HEADER_TITLE_SIZE:
        raise ValueError(f"Title is too large ({ len(title) }, max: { ROM_HEADER_TITLE_SIZE })")
    return title


def validate_sfc_file(sfc_data: bytes, symbols: dict[str, int], mappings: Mappings) -> None:
    """
    Validates `sfc_data` matches symbols and mappings.
    """

    memory_map = mappings.memory_map
    address_to_rom_offset: Callable[[Address], RomOffset] = memory_map.mode.address_to_rom_offset

    last_symbol_bank = get_largest_rom_address(symbols) >> 16
    if last_symbol_bank >= memory_map.first_resource_bank:
        raise RuntimeError(f"ERROR: first_resource_bank is not empty.  Found a symbol in bank 0x{last_symbol_bank:02x}")

    expected_size = ((memory_map.first_resource_bank + memory_map.n_resource_banks) & 0x3F) * memory_map.mode.bank_size
    if len(sfc_data) != expected_size:
        raise RuntimeError(f"ERROR:  Expected a sfc file that is { expected_size // 1024 } bytes in size")

    # 6 spaces (unlicensed game) + 6 zeros
    # The 6 zeros is the important bit, used by the 'RomUpdateRequired' subsystem of resources-over-usb2snes.
    expected_header_start = (b" " * 6) + bytes(6)
    header_offset = address_to_rom_offset(ROM_HEADER_V3_ADDR)
    header_start_in_sfc_data = sfc_data[header_offset : header_offset + len(expected_header_start)]
    if expected_header_start != header_start_in_sfc_data:
        raise RuntimeError("ERROR: Start of header does not match expected value")

    title_offset = address_to_rom_offset(ROM_HEADER_TITLE_ADDR)

    expected_title = convert_title(mappings.game_title)
    title_in_sfc_data = sfc_data[title_offset : title_offset + ROM_HEADER_TITLE_SIZE]
    if title_in_sfc_data != expected_title:
        decoded_title_in_sfc_data = bytes(title_in_sfc_data).decode(ROM_HEADER_TITLE_ENCODING).strip()
        raise RuntimeError(
            f"ERROR: sfc file header ({ decoded_title_in_sfc_data }) does not match mappings game_title ({ mappings.game_title })"
        )

    if USE_RESOURCES_OVER_USB2SNES_LABEL in symbols:
        o = address_to_rom_offset(symbols[USE_RESOURCES_OVER_USB2SNES_LABEL])
        if sfc_data[o] != 0xFF:
            raise ValueError(f"sfc file contains resource data")


class ResourceUsage(NamedTuple):
    memory_map: MemoryMap
    resource_bank_end: list[int]

    def summary(self) -> str:
        n_banks: Final = len(self.resource_bank_end)
        bank_start: Final = self.memory_map.mode.bank_start
        bank_size: Final = self.memory_map.mode.bank_size

        total_size: Final = n_banks * bank_size
        total_used: Final = sum(self.resource_bank_end) - bank_start * n_banks
        total_remaining: Final = total_size - total_used
        percent_used: Final = total_used / total_size * 100

        return f"{total_used} bytes used, {total_remaining} bytes free ({percent_used:0.1f}% full)"


class ResourceInserter:
    BANK_END = 0x10000
    BLANK_RESOURCE_ENTRY = bytes(3)

    def __init__(self, sfc_view: memoryview, symbols: dict[str, int], mappings: Mappings):
        memory_map = mappings.memory_map

        self.view: memoryview = sfc_view
        self.symbols: dict[str, int] = symbols

        self.memory_map: Final = memory_map

        self.address_to_rom_offset: Callable[[Address], RomOffset] = memory_map.mode.address_to_rom_offset
        self.bank_start: int = memory_map.mode.bank_start
        self.bank_size: int = memory_map.mode.bank_size

        self.bank_offset: int = memory_map.first_resource_bank
        self.n_resource_banks: int = memory_map.n_resource_banks

        self.bank_positions: list[int] = [self.bank_start] * memory_map.n_resource_banks

        validate_sfc_file(sfc_view, symbols, mappings)

    def usage_table(self) -> ResourceUsage:
        return ResourceUsage(self.memory_map, self.bank_positions.copy())

    def label_offset(self, label: str) -> RomOffset:
        return self.address_to_rom_offset(self.symbols[label])

    def read_u8(self, addr: Address) -> int:
        return self.view[self.address_to_rom_offset(addr)]

    def read_u16(self, addr: Address) -> int:
        ra = self.address_to_rom_offset(addr)
        return self.view[ra] | (self.view[ra + 1] << 8)

    def subview_addr(self, addr: Address, size: int) -> memoryview:
        o = self.address_to_rom_offset(addr)
        return self.view[o : o + size]

    def insert_engine_data(self, engine_data: EngineData) -> Address:
        assert isinstance(engine_data, EngineData)

        data_size = engine_data.size()
        assert data_size > 0 and data_size <= self.bank_size

        for i in range(len(self.bank_positions)):
            if self.bank_positions[i] + data_size <= self.BANK_END:
                addr = ((self.bank_offset + i) << 16) + self.bank_positions[i]

                rom_offset = self.address_to_rom_offset(addr)
                rom_offset_end = rom_offset + data_size

                def write_data(d: bytes) -> None:
                    nonlocal rom_offset
                    self.view[rom_offset : rom_offset + len(d)] = d
                    rom_offset += len(d)

                if engine_data.ram_data is not None:
                    write_data(engine_data.ram_data.data())
                if engine_data.ppu_data is not None:
                    write_data(engine_data.ppu_data.data())

                assert rom_offset == rom_offset_end

                self.bank_positions[i] += data_size

                return addr

        raise RuntimeError(f"Cannot fit blob of size { data_size } into binary")

    def insert_blob_at_label(self, label: str, blob: bytes) -> None:
        # NOTE: There is no boundary checking.  This could override data if I am not careful.
        o = self.label_offset(label)
        self.view[o : o + len(blob)] = blob

    def insert_blob_into_start_of_bank(self, bank_id: int, blob: bytes) -> Address:
        blob_size = len(blob)
        assert blob_size > 0

        u16_addr = self.bank_positions[bank_id]

        if u16_addr != self.bank_start:
            raise RuntimeError("Bank is not empty")

        if blob_size > self.BANK_END:
            raise RuntimeError("Cannot fit blob of size { blob_size } into binary")

        addr: Address = ((self.bank_offset + bank_id) << 16) + u16_addr
        rom_offset = self.address_to_rom_offset(addr)

        self.view[rom_offset : rom_offset + blob_size] = blob

        self.bank_positions[bank_id] += blob_size

        return addr

    def confirm_initial_data_is_correct(self, label: str, expected_data: bytes) -> None:
        o = self.label_offset(label)
        if self.view[o : o + len(expected_data)] != expected_data:
            raise RuntimeError(f"ROM data does not match expected data: { label }")

    def resource_table_for_type(self, resource_type: ResourceType) -> tuple[Address, int]:
        resource_type_id = resource_type.value

        nrptt_addr = self.symbols["resources.__NResourcesPerTypeTable"]
        retable_addr = self.symbols["resources.__ResourceEntryTable"]

        expected_n_resources = self.read_u8(nrptt_addr + resource_type_id)
        resource_table_addr = self.read_u16(retable_addr + resource_type_id * 2) | (retable_addr & 0xFF0000)

        return resource_table_addr, expected_n_resources

    def insert_resources(self, resource_type: ResourceType, resource_data: list[EngineData]) -> None:
        table_addr, expected_n_resources = self.resource_table_for_type(resource_type)

        if len(resource_data) != expected_n_resources:
            raise RuntimeError(f"NResourcesPerTypeTable mismatch in sfc_file: { resource_type }")

        table_pos = self.address_to_rom_offset(table_addr)

        for data in resource_data:
            addr = self.insert_engine_data(data)

            assert self.view[table_pos : table_pos + 3] == self.BLANK_RESOURCE_ENTRY

            self.view[table_pos + 0] = addr & 0xFF
            self.view[table_pos + 1] = (addr >> 8) & 0xFF
            self.view[table_pos + 2] = addr >> 16

            table_pos += 3

    def insert_room_data(self, bank_offset: int, rooms: list[Optional[EngineData]]) -> None:
        assert len(rooms) == 256
        ROOM_TABLE_SIZE: Final = 0x100 * 2

        room_table = bytearray([0xFF]) * ROOM_TABLE_SIZE
        room_data_blob = bytearray()

        room_addr = self.bank_start + len(room_data_blob)

        for room_id, room_data in enumerate(rooms):
            if room_data:
                assert isinstance(room_data.ram_data, FixedSizedData)
                assert room_data.ppu_data is None

                rd = room_data.ram_data.data()

                room_table[room_id * 2 + 0] = room_addr & 0xFF
                room_table[room_id * 2 + 1] = room_addr >> 8
                room_data_blob += rd
                room_addr += len(rd)

        room_table_offset = self.label_offset("resources.__RoomsTable")
        self.view[room_table_offset : room_table_offset + ROOM_TABLE_SIZE] = room_table

        self.insert_blob_into_start_of_bank(bank_offset, room_data_blob)


def insert_resources(sfc_view: memoryview, data_store: DataStore) -> ResourceUsage:
    # sfc_view is a memoryview of a bytearray containing the SFC file

    # ::TODO confirm sfc_view is the correct file::

    mappings, symbols, n_entities = data_store.get_mappings_symbols_and_n_entities()
    msfs_entity_data: Final = data_store.get_msfs_and_entity_data()
    dynamic_ms_data: Final = data_store.get_dynamic_ms_data()

    assert msfs_entity_data and msfs_entity_data.msfs_data and msfs_entity_data.entity_rom_data and dynamic_ms_data

    validate_entity_rom_data_symbols(symbols, n_entities)

    ri = ResourceInserter(sfc_view, symbols, mappings)
    ri.confirm_initial_data_is_correct(ENTITY_ROM_DATA_LABEL, expected_blank_entity_rom_data(symbols, n_entities))

    ri.insert_room_data(ROOM_DATA_BANK_OFFSET, data_store.get_data_for_all_rooms())

    ri.insert_blob_into_start_of_bank(MS_FS_DATA_BANK_OFFSET, msfs_entity_data.msfs_data)
    ri.insert_blob_into_start_of_bank(DYNAMIC_SPRITE_TILES_BANK_OFFSET, dynamic_ms_data.tile_data)
    ri.insert_blob_at_label(ENTITY_ROM_DATA_LABEL, msfs_entity_data.entity_rom_data)

    for r_type in ResourceType:
        ri.insert_resources(r_type, data_store.get_all_data_for_type(r_type))

    # Disable resources-over-usb2snes
    if USE_RESOURCES_OVER_USB2SNES_LABEL in symbols:
        ri.insert_blob_at_label(USE_RESOURCES_OVER_USB2SNES_LABEL, bytes(1))

    return ri.usage_table()


def update_checksum(sfc_view: memoryview, memory_map: MemoryMap) -> None:
    """
    Update the SFC header checksum in `sfc_view` (in place).
    """

    mm_mode: Final = memory_map.mode
    cs_header_offset: Final = mm_mode.address_to_rom_offset(0x00FFDC)

    if len(sfc_view) % mm_mode.bank_size != 0:
        raise RuntimeError(f"sfc file has an invalid size (expected a multiple of { mm_mode.bank_size })")

    if len(sfc_view).bit_count() != 1:
        # ::TODO handle non-power of two ROM sizes::
        raise RuntimeError(f"Invalid sfc file size (must be a power of two in size)")

    checksum = sum(sfc_view)

    # Remove the old checksum/complement
    checksum -= sum(sfc_view[cs_header_offset : cs_header_offset + 4])

    # Add the expected `checksum + complement` value to checksum
    checksum += 0xFF + 0xFF

    checksum = checksum & 0xFFFF
    complement = checksum ^ 0xFFFF

    # Write checksum to `sfc_view`
    sfc_view[cs_header_offset + 0] = complement & 0xFF
    sfc_view[cs_header_offset + 1] = complement >> 8
    sfc_view[cs_header_offset + 2] = checksum & 0xFF
    sfc_view[cs_header_offset + 3] = checksum >> 8


def null_print_function(message: str) -> None:
    pass


def compile_data(resources_directory: Filename, symbols_file: Filename, n_processes: Optional[int]) -> Optional[DataStore]:
    valid = True

    def print_resource_error(e: Union[ResourceError, Exception, str]) -> None:
        nonlocal valid
        valid = False
        if isinstance(e, ResourceError):
            print_error(f"ERROR: { e.res_string() }", e.error)
        else:
            print_error("ERROR: ", e)

    cwd: Final = os.getcwd()
    symbols_file_relpath = os.path.relpath(symbols_file, resources_directory)

    os.chdir(resources_directory)

    data_store: Final = DataStore()
    compiler: Final = ProjectCompiler(data_store, symbols_file_relpath, n_processes, print_resource_error, null_print_function)

    compiler.compile_everything()

    os.chdir(cwd)

    if valid:
        return data_store
    else:
        return None


def print_resource_sizes(data_store: DataStore) -> None:
    total_line = "-" * 70
    mappings = data_store.get_mappings()

    dynamic_ms_data = data_store.get_dynamic_ms_data()
    if dynamic_ms_data is not None:
        print(f"Dynamic MS Tiles:{len(dynamic_ms_data.tile_data): 6} bytes")

    msfs_and_entity_data = data_store.get_msfs_and_entity_data()
    if msfs_and_entity_data is not None:
        if msfs_and_entity_data.msfs_data is not None:
            print(f"MetaSprite Data: {len(msfs_and_entity_data.msfs_data): 6} bytes")
        if msfs_and_entity_data.entity_rom_data is not None:
            print(f"Entity ROM Data: {len(msfs_and_entity_data.entity_rom_data): 6} bytes")

    print()

    for rt in ResourceType:
        print(f"{rt.name}:")
        n_resources = len(getattr(mappings, rt.name))
        total_ram_size = 0
        total_ppu_size = 0

        for i in range(n_resources):
            d = data_store.get_resource_data(rt, i)
            if isinstance(d, ResourceData):
                ram_size, ppu_size = d.data.ram_and_ppu_size()
                total_ram_size += ram_size
                total_ppu_size += ppu_size
                print(f"{d.resource_id: 5} {d.resource_name:30} {ram_size: 6} + {ppu_size: 6} = {ram_size + ppu_size: 8} bytes")
            elif d is not None:
                print(f"{d.resource_id: 5} {d.resource_name:30} NO DATA")
            else:
                print(f"{i: 5} ERROR")
        print(total_line)
        print(f"{total_ram_size: 43} + {total_ppu_size:6} = {total_ram_size + total_ppu_size: 8} bytes")
        print()


def insert_resources_into_binary(data_store: DataStore, sfc_input: Filename) -> tuple[bytes, ResourceUsage]:
    sfc_data = bytearray(read_binary_file(sfc_input, 4 * 1024 * 1024))
    sfc_memoryview = memoryview(sfc_data)

    usage = insert_resources(sfc_memoryview, data_store)

    update_checksum(sfc_memoryview, data_store.get_mappings().memory_map)

    return sfc_data, usage
