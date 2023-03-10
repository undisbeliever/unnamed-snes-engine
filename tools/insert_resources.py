#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import os.path
import argparse

from typing import Callable, Final

from _json_formats import load_mappings_json, load_entities_json, \
                          Name, Filename, MemoryMap, Mappings, EntitiesJson

from _common import MS_FS_DATA_BANK_OFFSET, ROOM_DATA_BANK_OFFSET, ResourceType, \
                    USE_RESOURCES_OVER_USB2SNES_LABEL

from _entity_data import ENTITY_ROM_DATA_LABEL, validate_entity_rom_data_symbols, \
                         expected_blank_entity_rom_data, create_entity_rom_data
from convert_metasprite import text_to_msfs_entries, build_ms_fs_data
from convert_other_resources import load_other_resources_data_from_file, ResourceEntry, OtherResourcesData



Address = int
RomOffset = int



def read_binary_file(path : Filename, max_size : int) -> bytes:
    with open(path, 'rb') as fp:
        out = fp.read(max_size)

        if fp.read(1):
            raise RuntimeError(f"File is too large: maximum file size is { max_size }: { path }")

        return out



def read_symbols_file(symbol_filename : Filename) -> dict[str, int]:
    regex = re.compile(r'([0-9A-F]{2}):([0-9A-F]{4}) (.+)')

    out = dict()

    with open(symbol_filename, 'r') as fp:
        for line in fp:
            line = line.strip()

            if line == '[labels]':
                continue

            m = regex.match(line)
            if not m:
                raise ValueError('Cannot read symbol file: invalid line')
            addr = (int(m.group(1), 16) << 16) | (int(m.group(2), 16))
            out[m.group(3)] = addr

    return out


def get_largest_rom_address(symbols : dict[str, int]) -> int:
    # assumes max is never a zeropage or low-Ram address
    return max([a for a in symbols.values() if a & 0xfe0000 != 0x7e ])



ROM_HEADER_V3_ADDR = 0xffb0
ROM_HEADER_TITLE_ADDR = 0xFFC0
ROM_HEADER_TITLE_SIZE = 21
ROM_HEADER_TITLE_ENCODING = 'Shift-JIS' # This is supposed to be `JIS X 0201`, but python does not support it.


def convert_title(s : str) -> bytes:
    title = s.encode(ROM_HEADER_TITLE_ENCODING).ljust(ROM_HEADER_TITLE_SIZE, b'\x20')
    if len(title) != ROM_HEADER_TITLE_SIZE:
        raise ValueError(f"Title is too large ({ len(title) }, max: { ROM_HEADER_TITLE_SIZE })")
    return title



def validate_sfc_file(sfc_data : bytes, symbols : dict[str, int], mappings : Mappings) -> None:
    """
    Validates `sfc_data` matches symbols and mappings.
    """

    memory_map = mappings.memory_map
    address_to_rom_offset : Callable[[Address], RomOffset] = memory_map.mode.address_to_rom_offset

    last_symbol_bank = get_largest_rom_address(symbols) >> 16
    if last_symbol_bank >= memory_map.first_resource_bank:
        raise RuntimeError(f"ERROR: first_resource_bank is not empty.  Found a symbol in bank 0x{last_symbol_bank:02x}")


    expected_size = ((memory_map.first_resource_bank + memory_map.n_resource_banks) & 0x3f) * memory_map.mode.bank_size
    if len(sfc_data) != expected_size:
        raise RuntimeError(f"ERROR:  Expected a sfc file that is { expected_size // 1024 } bytes in size")


    # 6 spaces (unlicensed game) + 6 zeros
    # The 6 zeros is the important bit, used by the 'RomUpdateRequired' subsystem of resources-over-usb2snes.
    expected_header_start = (b' ' * 6) + bytes(6)
    header_offset = address_to_rom_offset(ROM_HEADER_V3_ADDR)
    header_start_in_sfc_data = sfc_data[header_offset : header_offset + len(expected_header_start)]
    if expected_header_start != header_start_in_sfc_data:
        raise RuntimeError('ERROR: Start of header does not match expected value')


    title_offset = address_to_rom_offset(ROM_HEADER_TITLE_ADDR)

    expected_title = convert_title(mappings.game_title)
    title_in_sfc_data = sfc_data[title_offset : title_offset + ROM_HEADER_TITLE_SIZE]
    if title_in_sfc_data != expected_title:
        decoded_title_in_sfc_data = bytes(title_in_sfc_data).decode(ROM_HEADER_TITLE_ENCODING).strip()
        raise RuntimeError(f"ERROR: sfc file header ({ decoded_title_in_sfc_data }) does not match mappings game_title ({ mappings.game_title })")


    if USE_RESOURCES_OVER_USB2SNES_LABEL in symbols:
        o = address_to_rom_offset(symbols[USE_RESOURCES_OVER_USB2SNES_LABEL])
        if sfc_data[o] != 0xff:
            raise ValueError(f"sfc file contains resource data")



class ResourceInserter:
    BANK_END = 0x10000
    BLANK_RESOURCE_ENTRY = bytes(5)

    def __init__(self, sfc_view : memoryview, symbols : dict[str, int], mappings : Mappings):
        memory_map = mappings.memory_map

        self.view    : memoryview = sfc_view
        self.symbols : dict[str, int] = symbols

        # Assume HiRom mapping
        self.address_to_rom_offset : Callable[[Address], RomOffset] = memory_map.mode.address_to_rom_offset
        self.bank_start : int = memory_map.mode.bank_start
        self.bank_size  : int = memory_map.mode.bank_size

        self.bank_offset      : int = memory_map.first_resource_bank
        self.n_resource_banks : int = memory_map.n_resource_banks

        self.bank_positions   : list[int] = [ self.bank_start ] * memory_map.n_resource_banks

        validate_sfc_file(sfc_view, symbols, mappings)


    def label_offset(self, label : str) -> RomOffset:
        return self.address_to_rom_offset(self.symbols[label])


    def read_u8(self, addr : Address) -> int:
        return self.view[self.address_to_rom_offset(addr)]


    def read_u16(self, addr : Address) -> int:
        ra = self.address_to_rom_offset(addr)
        return self.view[ra] | (self.view[ra + 1] << 8)


    def subview_addr(self, addr : Address, size : int) -> memoryview:
        o = self.address_to_rom_offset(addr)
        return self.view[o:o+size]


    def insert_blob(self, blob : bytes) -> Address:
        assert isinstance(blob, bytes) or isinstance(blob, bytearray)

        blob_size = len(blob)
        assert blob_size > 0 and blob_size <= self.bank_size

        for i in range(len(self.bank_positions)):
            if self.bank_positions[i] + blob_size <= self.BANK_END:
                addr = ((self.bank_offset + i) << 16) + self.bank_positions[i]

                rom_offset = self.address_to_rom_offset(addr)

                self.view[rom_offset : rom_offset+blob_size] = blob

                self.bank_positions[i] += blob_size

                return addr

        raise RuntimeError(f"Cannot fit blob of size { blob_size } into binary")


    def insert_blob_at_label(self, label : str, blob : bytes) -> None:
        # NOTE: There is no boundary checking.  This could override data if I am not careful.
        o = self.label_offset(label)
        self.view[o : o + len(blob)] = blob


    def insert_blob_into_start_of_bank(self, bank_id : int, blob : bytes) -> Address:
        blob_size = len(blob)
        assert blob_size > 0

        u16_addr = self.bank_positions[bank_id]

        if u16_addr != self.bank_start:
            raise RuntimeError("Bank is not empty")

        if blob_size > self.BANK_END:
            raise RuntimeError("Cannot fit blob of size { blob_size } into binary")

        addr : Address = ((self.bank_offset + bank_id) << 16) + u16_addr
        rom_offset = self.address_to_rom_offset(addr)

        self.view[rom_offset : rom_offset+blob_size] = blob

        self.bank_positions[bank_id] += blob_size

        return addr


    def confirm_initial_data_is_correct(self, label : str, expected_data : bytes) -> None:
        o = self.label_offset(label)
        if self.view[o:o+len(expected_data)] != expected_data:
            raise RuntimeError(f"ROM data does not match expected data: { label }")


    def resource_table_for_type(self, resource_type : ResourceType) -> tuple[Address, int]:
        resource_type_id = resource_type.value

        nrptt_addr   = self.symbols['resources.__NResourcesPerTypeTable']
        retable_addr = self.symbols['resources.__ResourceEntryTable']

        expected_n_resources = self.read_u8(nrptt_addr + resource_type_id)
        resource_table_addr  = self.read_u16(retable_addr + resource_type_id * 2) | (retable_addr & 0xff0000)

        return resource_table_addr, expected_n_resources


    def _insert_binary_resources(self, resource_type : ResourceType, n_resources : int, func : Callable[[int], bytes]) -> None:
        table_addr, expected_n_resources = self.resource_table_for_type(resource_type)

        if n_resources != expected_n_resources:
            raise RuntimeError(f"NResourcesPerTypeTable mismatch in sfc_file: { resource_type }")

        table_pos = self.address_to_rom_offset(table_addr)

        for i in range(n_resources):
            data = func(i)

            addr = self.insert_blob(data)
            size = len(data)

            assert self.view[table_pos:table_pos+5] == self.BLANK_RESOURCE_ENTRY

            self.view[table_pos + 0] = addr & 0xff
            self.view[table_pos + 1] = (addr >> 8) & 0xff
            self.view[table_pos + 2] = (addr >> 16)

            self.view[table_pos + 3] = size & 0xff
            self.view[table_pos + 4] = (size >> 8)

            table_pos += 5



    def insert_binary_file_resources(self, resource_type : ResourceType, resource_names : list[Name], fmt : str) -> None:
        self._insert_binary_resources(
                resource_type, len(resource_names),
                lambda i : read_binary_file(fmt.format(resource_names[i]), self.bank_size)
        )


    def insert_other_resources(self, other_resources : OtherResourcesData, mapping : Mappings) -> None:
        for resource_type_name in other_resources._fields:
            mapping_names    : list[Name]          = getattr(mapping, resource_type_name)
            resource_entries : list[ResourceEntry] = getattr(other_resources, resource_type_name)
            resource_type = ResourceType[resource_type_name]

            if len(mapping_names) != len(resource_entries):
                raise RuntimeError(f"OtherResourcesData file does not match mappings.json: { resource_type }")

            for i in range(len(resource_entries)):
                if mapping_names[i] != resource_entries[i].name:
                    raise RuntimeError(f"OtherResourcesData file does not match mappings.json: { resource_type }: { mapping_names[i] }, { resource_entries[i] }")

            self._insert_binary_resources(
                    resource_type, len(mapping_names),
                    lambda i : resource_entries[i].data
            )


    def insert_room_data(self, bank_offset : int, room_bin : bytes) -> None:
        ROOM_TABLE_SIZE = 0x100 * 2

        room_view = memoryview(room_bin)

        room_table     = room_view[0:ROOM_TABLE_SIZE]
        room_data_blob = room_view[ROOM_TABLE_SIZE:]

        room_table_offset = self.label_offset('resources.__RoomsTable')

        self.view[room_table_offset:room_table_offset+ROOM_TABLE_SIZE] = room_table

        self.insert_blob_into_start_of_bank(bank_offset, room_data_blob)



def insert_metasprite_data(ri : ResourceInserter, mappings : Mappings) -> dict[str, tuple[int, Name]]:
    spritesheets = list()

    for ss_name in mappings.ms_spritesheets:
        with open(f"gen/metasprites/{ ss_name }.txt", 'r') as fp:
            spritesheets.append(text_to_msfs_entries(fp))

    ms_fs_data, metasprite_map = build_ms_fs_data(spritesheets, ri.symbols, mappings.memory_map.mode)

    ri.insert_blob_into_start_of_bank(MS_FS_DATA_BANK_OFFSET, ms_fs_data.data())

    return metasprite_map



def insert_entity_rom_data(ri : ResourceInserter, entities_input : EntitiesJson, symbols : dict[str, int], metasprite_map : dict[str, tuple[int, Name]]) -> None:
    n_entities = len(entities_input.entities)

    validate_entity_rom_data_symbols(symbols, n_entities)
    ri.confirm_initial_data_is_correct(ENTITY_ROM_DATA_LABEL,
                                       expected_blank_entity_rom_data(symbols, n_entities))

    entity_rom_data = create_entity_rom_data(entities_input, symbols, metasprite_map)

    ri.insert_blob_at_label(ENTITY_ROM_DATA_LABEL, entity_rom_data)



def insert_resources(sfc_view : memoryview, symbols : dict[str, Address], mappings : Mappings, entities : EntitiesJson, other_resources_data : OtherResourcesData) -> None:
    # sfc_view is a memoryview of a bytearray containing the SFC file

    # ::TODO confirm sfc_view is the correct file::

    ri = ResourceInserter(sfc_view, symbols, mappings)


    metasprite_map = insert_metasprite_data(ri, mappings)


    insert_entity_rom_data(ri, entities, symbols, metasprite_map)


    ri.insert_room_data(ROOM_DATA_BANK_OFFSET,
                        read_binary_file('gen/rooms.bin', ri.bank_size))

    ri.insert_binary_file_resources(ResourceType.mt_tilesets, mappings.mt_tilesets, "gen/metatiles/{}.bin")

    ri.insert_binary_file_resources(ResourceType.ms_spritesheets, mappings.ms_spritesheets, "gen/metasprites/{}.bin")


    ri.insert_other_resources(other_resources_data, mappings)


    if USE_RESOURCES_OVER_USB2SNES_LABEL in symbols:
        ri.insert_blob_at_label(USE_RESOURCES_OVER_USB2SNES_LABEL, bytes(1))



def update_checksum(sfc_view : memoryview, memory_map : MemoryMap) -> None:
    """
    Update the SFC header checksum in `sfc_view` (in place).
    """

    mm_mode : Final = memory_map.mode
    cs_header_offset : Final = mm_mode.address_to_rom_offset(0x00ffdc)


    if len(sfc_view) % mm_mode.bank_size != 0:
        raise RuntimeError(f"sfc file has an invalid size (expected a multiple of { mm_mode.bank_size })")

    if len(sfc_view).bit_count() != 1:
        # ::TODO handle non-power of two ROM sizes::
        raise RuntimeError(f"Invalid sfc file size (must be a power of two in size)")

    checksum = sum(sfc_view)

    # Remove the old checksum/complement
    checksum -= sum(sfc_view[cs_header_offset : cs_header_offset + 4])

    # Add the expected `checksum + complement` value to checksum
    checksum += 0xff + 0xff

    checksum = checksum & 0xffff
    complement = checksum ^ 0xffff

    # Write checksum to `sfc_view`
    sfc_view[cs_header_offset + 0] = complement & 0xff
    sfc_view[cs_header_offset + 1] = complement >> 8
    sfc_view[cs_header_offset + 2] = checksum & 0xff
    sfc_view[cs_header_offset + 3] = checksum >> 8



def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='sfc output file')
    parser.add_argument('mappings_json_file',
                        help='mappings json file input')
    parser.add_argument('entities_json_file',
                        help='entities  JSON  file input')
    parser.add_argument('other_resources_bin_file',
                        help='other resources data binary file')
    parser.add_argument('symbols_file',
                        help='symbols input file')
    parser.add_argument('sfc_input',
                        help='sfc input file (unmodified)')

    args = parser.parse_args()

    return args;



def main() -> None:
    args = parse_arguments()

    mappings = load_mappings_json(args.mappings_json_file)
    entities = load_entities_json(args.entities_json_file)
    symbols = read_symbols_file(args.symbols_file)
    other_resources_data = load_other_resources_data_from_file(args.other_resources_bin_file)

    sfc_data = bytearray(read_binary_file(args.sfc_input, 4 * 1024 * 1024))
    sfc_memoryview = memoryview(sfc_data)

    insert_resources(sfc_memoryview, symbols, mappings, entities, other_resources_data)

    update_checksum(sfc_memoryview, mappings.memory_map)

    with open(args.output, 'wb') as fp:
        fp.write(sfc_data)



if __name__ == '__main__':
    main()

