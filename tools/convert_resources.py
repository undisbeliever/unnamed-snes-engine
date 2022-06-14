#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import PIL.Image
import argparse

from collections import namedtuple

from _snes import extract_tiles_from_paletted_image, convert_mode7_tileset, convert_snes_tileset
from _json_formats import load_mappings_json, load_resources_json


# ::TODO add images::
# ::TODO add palettes::


# ResourceData
# ============

ResourceEntry = namedtuple('ResourceEntry', ('name', 'data'))

ResourceData = namedtuple('ResourceData', ('tiles', ))


# Change the header and footer when the data format changes
RESOURCES_DATA_FILE_HEADER = b'G9Ww60-ResourceDataFile-y5JWpM'
RESOURCES_DATA_FILE_FOOTER = b'6eMLlE-END-6QuVUa'


def save_resource_data_to_file(filename, resource_data):
    assert isinstance(resource_data, ResourceData)

    with open(filename, 'wb') as fp:
        def write_int(i):
            assert isinstance(i, int)

            if i < 0 or i > (1 << 30):
                raise ValueError("Integer too large")
            fp.write(i.to_bytes(4, byteorder='little'))

        def write_data(b):
            assert isinstance(b, bytes) or isinstance(b, bytearray)

            write_int(len(b))
            if len(b) > 0:
                fp.write(b)

        def write_string(s):
            assert isinstance(s, str)
            write_data(s.encode('utf-8'))

        fp.write(RESOURCES_DATA_FILE_HEADER)

        for re_list in resource_data:
            assert isinstance(re_list, list)

            write_int(len(re_list))
            for e in re_list:
                assert isinstance(e, ResourceEntry)

                fp.write(b'RE')
                write_string(e.name)
                write_data(e.data)

        fp.write(RESOURCES_DATA_FILE_FOOTER)



def load_resource_data_from_file(filename):
    out = list()

    with open(filename, 'rb') as fp:
        def read_int():
            return int.from_bytes(fp.read(4), byteorder='little')

        def read_data():
            l = read_int()
            return fp.read(l)

        def read_string():
            return read_data().decode('utf-8')

        def read_and_confirm_fixed_value(expected):
            d = fp.read(len(expected))
            if d != expected:
                raise RuntimeError('Not a valid ResourceData file')


        read_and_confirm_fixed_value(RESOURCES_DATA_FILE_HEADER)

        out = list()

        for field in ResourceData._fields:
            field_list = list()
            n_elements = read_int()
            for i in range(n_elements):
                read_and_confirm_fixed_value(b'RE')

                r_name = read_string()
                r_data = read_data()

                field_list.append(ResourceEntry(name=r_name, data=r_data))
            out.append(field_list)

        read_and_confirm_fixed_value(RESOURCES_DATA_FILE_FOOTER)

        if fp.read(1):
            raise RuntimeError('Expected end of file')

    assert len(out) == len(ResourceData._fields)

    return ResourceData(*out)



#
# Tiles
# =====
#

TILE_FORMATS = {
    'm7'    : convert_mode7_tileset,
    'mode7' : convert_mode7_tileset,
    '1bpp'  : lambda tiles : convert_snes_tileset(tiles, 1),
    '2bpp'  : lambda tiles : convert_snes_tileset(tiles, 2),
    '3bpp'  : lambda tiles : convert_snes_tileset(tiles, 3),
    '4bpp'  : lambda tiles : convert_snes_tileset(tiles, 4),
    '8bpp'  : lambda tiles : convert_snes_tileset(tiles, 8),
}


def convert_tiles(t):
    tile_converter = TILE_FORMATS[t.format]

    with PIL.Image.open(t.source) as image:
        image.load()

    tile_data = tile_converter(extract_tiles_from_paletted_image(image))

    l = list(extract_tiles_from_paletted_image(image))

    return ResourceEntry(t.name, tile_data)



def compile_list(typename, mapping, inputs, func):
    out = list()

    for resource_name in mapping:
        i = inputs.get(resource_name)
        if not i:
            raise RuntimeError(f"Cannot find { typename } resource: {resource_name}")

        out.append(func(i))

    return out



#
# convert_resources
# =================
#



def build_resources(mapping, resources):
    return ResourceData(
            tiles = compile_list('tiles', mapping.tiles, resources.tiles, convert_tiles)
    )



def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='output file')
    parser.add_argument('mapping_filename', action='store',
                        help='mapping json file input')
    parser.add_argument('resources_json_file', action='store',
                        help='resources JSON file input')

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    mapping = load_mappings_json(args.mapping_filename)
    resources = load_resources_json(args.resources_json_file)

    resource_data = build_resources(mapping, resources)

    save_resource_data_to_file(args.output, resource_data)



if __name__ == '__main__':
    main()


