#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import os.path
import argparse

from _json_formats import load_mappings_json, load_entities_json

from _common import MS_FS_DATA_BANK_OFFSET, ROOM_DATA_BANK_OFFSET

from _entity_data import ENTITY_ROM_DATA_LABEL, validate_entity_rom_data_symbols, \
                         expected_blank_entity_rom_data, create_entity_rom_data
from convert_metasprite import text_to_msfs_entries, build_ms_fs_data


# order MUST match `ResourceType` enum in `src/metasprites.wiz`
# and match `RESOURCE_TYPES` in `tools/generate-resources-wiz.py`
RESOURCE_TYPES = {
    # mappings name
    'mt_tileset'      : 0,
    'ms_spritesheets' : 1,
}


def read_binary_file(path, max_size):
    with open(path, 'rb') as fp:
        out = fp.read(max_size)

        if fp.read(1):
            raise RuntimeError(f"File is too large: maximum file size is { max_size }: { path }")

        return out



def read_symbols_file(symbol_filename):
    regex = re.compile(r'([0-9A-F]{2}):([0-9A-F]{4}) (.+)')

    out = dict()

    with open(symbol_filename, 'r') as fp:
        for line in fp:
            line = line.strip()

            if line == '[labels]':
                continue

            m = regex.match(line)
            addr = (int(m.group(1), 16) << 16) | (int(m.group(2), 16))
            out[m.group(3)] = addr

    return out


def get_largest_rom_address(symbols):
    # assumes max is never a zeropage or low-Ram address
    return max([a for a in symbols.values() if a & 0xfe0000 != 0x7e ])



def hirom_address_to_rom_offset(addr):
    if addr & 0x3f0000 < 0x40 and addr & 0xffff < 0x8000:
        raise ValueError(f"addr is not a ROM address: 0x{addr:06x}")

    if addr >> 16 == 0x7e or addr >> 16 == 0x7f:
        raise ValueError(f"addr is not a ROM address: 0x{addr:06x}")

    return addr & 0x3fffff



class ResourceInserter:
    BANK_END = 0x10000
    BLANK_RESOURCE_ENTRY = bytes(5)


    def __init__(self, sfc_view, symbols, memory_map):
        self.view = sfc_view
        self.symbols = symbols

        # Assume HiRom mapping
        if memory_map.mode == 'hirom':
            self.address_to_rom_offset = hirom_address_to_rom_offset
            self.bank_start = 0
            self.bank_size = 64 * 1024
        else:
            raise ValueError(f"Invalid mapping mode: { mapping.mode }")


        self.bank_offset = memory_map.first_resource_bank
        self.n_resource_banks = memory_map.n_resource_banks
        self.bank_positions = [ self.bank_start ] * memory_map.n_resource_banks


        last_symbol_bank = get_largest_rom_address(symbols) >> 16
        if last_symbol_bank >= memory_map.first_resource_bank:
            raise RuntimeError(f"ERROR: first_resource_bank is not empty.  Found a symbol in bank 0x{last_symbol_bank:02x}")


        expected_size = ((memory_map.first_resource_bank + memory_map.n_resource_banks) & 0x3f) * self.bank_size
        if len(sfc_view) != expected_size:
            raise RuntimeError(f"ERROR:  Expected a sfc file that is { expected_size // 1024 } bytes in size")


    def label_offset(self, label):
        return self.address_to_rom_offset(self.symbols[label])


    def read_u8(self, addr):
        return self.view[self.address_to_rom_offset(addr)]



    def read_u16(self, addr):
        ra = self.address_to_rom_offset(addr)
        return self.view[ra] | (self.view[ra + 1] << 8)



    def insert_blob(self, blob):
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


    def insert_blob_at_label(self, label, blob):
        # NOTE: There is no boundary checking.  This could override data if I am not careful.
        o = self.label_offset(label)
        self.view[o : o + len(blob)] = blob


    def insert_blob_into_start_of_bank(self, bank_id, blob):
        blob_size = len(blob)
        assert blob_size > 0

        u16_addr = self.bank_positions[bank_id]

        if u16_addr != self.bank_start:
            raise RuntimeError("Bank is not empty")

        if blob_size > self.BANK_END:
            raise RuntimeError("Cannot fit blob of size { blob_size } into binary")

        addr = ((self.bank_offset + bank_id) << 16) + u16_addr
        rom_offset = self.address_to_rom_offset(addr)

        self.view[rom_offset : rom_offset+blob_size] = blob

        self.bank_positions[bank_id] += blob_size

        return addr


    def confirm_initial_data_is_correct(self, label, expected_data):
        o = self.label_offset(label)
        if self.view[o:o+len(expected_data)] != expected_data:
            raise RuntimeError(f"ROM data does not match expected data: { label }")


    def resource_table_for_type(self, resource_type):
        resource_type_id = RESOURCE_TYPES[resource_type]

        nrptt_addr   = self.symbols['resources.__NResourcesPerTypeTable']
        retable_addr = self.symbols['resources.__ResourceEntryTable']

        expected_n_resources = self.read_u8(nrptt_addr + resource_type_id)
        resource_table_addr  = self.read_u16(retable_addr + resource_type_id * 2) | (retable_addr & 0xff0000)

        return resource_table_addr, expected_n_resources



    def insert_binary_resources(self, resource_type, resource_names, load_resource):
        table_addr, expected_n_resources = self.resource_table_for_type(resource_type)

        if expected_n_resources != len(resource_names):
            raise RuntimeError(f"NResourcesPerTypeTable mismatch in sfc_file: { resource_type }")

        table_pos = self.address_to_rom_offset(table_addr)

        for name in resource_names:
            data = load_resource(name)

            addr = self.insert_blob(data)
            size = len(data)

            assert self.view[table_pos:table_pos+5] == self.BLANK_RESOURCE_ENTRY

            self.view[table_pos + 0] = addr & 0xff
            self.view[table_pos + 1] = (addr >> 8) & 0xff
            self.view[table_pos + 2] = (addr >> 16)

            self.view[table_pos + 3] = size & 0xff
            self.view[table_pos + 4] = (size >> 8)

            table_pos += 5


    def insert_room_data(self, bank_offset, room_bin):
        ROOM_TABLE_SIZE = 0x100 * 2

        room_view = memoryview(room_bin)

        room_table     = room_view[0:ROOM_TABLE_SIZE]
        room_data_blob = room_view[ROOM_TABLE_SIZE:]

        room_table_offset = self.label_offset('resources.__RoomsTable')

        self.view[room_table_offset:room_table_offset+ROOM_TABLE_SIZE] = room_table

        self.insert_blob_into_start_of_bank(bank_offset, room_data_blob)



def insert_metasprite_data(ri, mappings):
    spritesheets = list()

    for ss_name in mappings.ms_spritesheets:
        with open(f"gen/metasprites/{ ss_name }.txt", 'r') as fp:
            spritesheets.append(text_to_msfs_entries(fp))

    ms_fs_data, metasprite_map = build_ms_fs_data(spritesheets, ri.symbols, ri.bank_start, ri.bank_size)

    ri.insert_blob_into_start_of_bank(MS_FS_DATA_BANK_OFFSET, ms_fs_data.data())

    return metasprite_map



def insert_entity_rom_data(ri, entities_input, symbols, metasprite_map):
    n_entities = len(entities_input.entities)

    validate_entity_rom_data_symbols(symbols, n_entities)
    ri.confirm_initial_data_is_correct(ENTITY_ROM_DATA_LABEL,
                                       expected_blank_entity_rom_data(symbols, n_entities))

    entity_rom_data = create_entity_rom_data(entities_input.entities, entities_input.entity_functions, symbols, metasprite_map)

    ri.insert_blob_at_label(ENTITY_ROM_DATA_LABEL, entity_rom_data)



def insert_resources(sfc_view, symbols, mappings, entities):
    # sfc_view is a memoryview of a bytearray containing the SFC file

    # ::TODO confirm sfc_view is the correct file::

    ri = ResourceInserter(sfc_view, symbols, mappings.memory_map)


    metasprite_map = insert_metasprite_data(ri, mappings)


    insert_entity_rom_data(ri, entities, symbols, metasprite_map)


    ri.insert_room_data(ROOM_DATA_BANK_OFFSET,
                        read_binary_file('gen/rooms.bin', ri.bank_size))

    ri.insert_binary_resources('mt_tileset', mappings.mt_tilesets,
                               lambda resource_name : read_binary_file(f"gen/metatiles/{ resource_name }.bin", ri.bank_size)
    )

    ri.insert_binary_resources('ms_spritesheets', mappings.ms_spritesheets,
                               lambda resource_name : read_binary_file(f"gen/metasprites/{ resource_name }.bin", ri.bank_size)
    )



def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='sfc output file')
    parser.add_argument('mappings_json_file',
                        help='mappings json file input')
    parser.add_argument('entities_json_file',
                        help='entities  JSON  file input')
    parser.add_argument('symbols_file',
                        help='symbols input file')
    parser.add_argument('sfc_input',
                        help='sfc input file (unmodified)')

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    mappings = load_mappings_json(args.mappings_json_file)
    entities = load_entities_json(args.entities_json_file)
    symbols = read_symbols_file(args.symbols_file)
    sfc_data = bytearray(read_binary_file(args.sfc_input, 4 * 1024 * 1024))

    out = insert_resources(memoryview(sfc_data), symbols, mappings, entities)

    with open(args.output, 'wb') as fp:
        fp.write(sfc_data)



if __name__ == '__main__':
    main()

