#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import PIL.Image # type: ignore
import sys
import argparse

from collections import OrderedDict
from typing import Any, Callable, Final, Iterable, NamedTuple, Optional

from _common import print_error

from _snes import extract_tiles_from_paletted_image, convert_mode7_tileset, convert_snes_tileset, \
                  image_to_snes, create_tilemap_data, SmallTileData

from _json_formats import load_mappings_json, load_other_resources_json, \
                          Name, Filename, Mappings, TilesInput, BackgroundImageInput, OtherResources


# ::TODO add images::
# ::TODO add palettes::


# OtherResourcesData
# ==================

class ResourceEntry(NamedTuple):
    name : Name
    data : bytes


class OtherResourcesData(NamedTuple):
    tiles       : list[ResourceEntry]
    bg_images   : list[ResourceEntry]



# Change the header and footer when the data format changes
OTHER_RESOURCES_DATA_FILE_HEADER = b'he4Iez-ResourceDataFile-muVei0'
OTHER_RESOURCES_DATA_FILE_FOOTER = b'eeTei8-END-aiLix8'


def save_other_resources_data_to_file(filename : Filename, data : OtherResourcesData) -> None:
    assert isinstance(data, OtherResourcesData)

    with open(filename, 'wb') as fp:
        def write_int(i : int) -> None:
            assert isinstance(i, int)

            if i < 0 or i > (1 << 30):
                raise ValueError("Integer too large")
            fp.write(i.to_bytes(4, byteorder='little'))

        def write_data(b : bytes) -> None:
            assert isinstance(b, bytes) or isinstance(b, bytearray)

            write_int(len(b))
            if len(b) > 0:
                fp.write(b)

        def write_string(s : str) -> None:
            assert isinstance(s, str)
            write_data(s.encode('utf-8'))

        fp.write(OTHER_RESOURCES_DATA_FILE_HEADER)

        for re_list in data:
            assert isinstance(re_list, list)

            write_int(len(re_list))
            for e in re_list:
                assert isinstance(e, ResourceEntry)

                fp.write(b'RE')
                write_string(e.name)
                write_data(e.data)

        fp.write(OTHER_RESOURCES_DATA_FILE_FOOTER)



def load_other_resources_data_from_file(filename : Filename) -> OtherResourcesData:
    out : list[list[ResourceEntry]] = list()

    with open(filename, 'rb') as fp:
        def read_int() -> int:
            return int.from_bytes(fp.read(4), byteorder='little')

        def read_data() -> bytes:
            l = read_int()
            return fp.read(l)

        def read_string() -> str:
            return read_data().decode('utf-8')

        def read_and_confirm_fixed_value(expected : bytes) -> None:
            d = fp.read(len(expected))
            if d != expected:
                raise RuntimeError('Not a valid OtherResourcesData file')


        read_and_confirm_fixed_value(OTHER_RESOURCES_DATA_FILE_HEADER)

        out = list()

        for field in OtherResourcesData._fields:
            field_list = list()
            n_elements = read_int()
            for i in range(n_elements):
                read_and_confirm_fixed_value(b'RE')

                r_name = read_string()
                r_data = read_data()

                field_list.append(ResourceEntry(name=r_name, data=r_data))
            out.append(field_list)

        read_and_confirm_fixed_value(OTHER_RESOURCES_DATA_FILE_FOOTER)

        if fp.read(1):
            raise RuntimeError('Expected end of file')

    assert len(out) == len(OtherResourcesData._fields)

    return OtherResourcesData(*out)



#
# Tiles
# =====
#

TILE_FORMATS : dict[str, Callable[[Iterable[SmallTileData]], bytes]] = {
    'm7'    : convert_mode7_tileset,
    'mode7' : convert_mode7_tileset,
    '1bpp'  : lambda tiles : convert_snes_tileset(tiles, 1),
    '2bpp'  : lambda tiles : convert_snes_tileset(tiles, 2),
    '3bpp'  : lambda tiles : convert_snes_tileset(tiles, 3),
    '4bpp'  : lambda tiles : convert_snes_tileset(tiles, 4),
    '8bpp'  : lambda tiles : convert_snes_tileset(tiles, 8),
}


def convert_tiles(t : TilesInput) -> bytes:
    tile_converter = TILE_FORMATS[t.format]

    with PIL.Image.open(t.source) as image:
        image.load()

    return tile_converter(extract_tiles_from_paletted_image(image))


#
# Background Images
# =================
#

BI_BPP_FORMATS : dict[str, int] = {
    '2bpp': 2,
    '4bpp': 4,
    '8bpp': 8,
}

NAMETABLE_SIZE_BYTES : Final = 32 * 32 * 2
VALID_BGI_HEADER_TM_SIZES : Final = (1, 2, 4)

def convert_bg_image(bgi : BackgroundImageInput ) -> bytes:

    bpp = BI_BPP_FORMATS[bgi.format]

    with PIL.Image.open(bgi.source) as image:
        image.load()

    with PIL.Image.open(bgi.palette) as pal_image:
        pal_image.load()

    tilemap, tile_data, palette_data = image_to_snes(image, pal_image, bpp)

    tilemap_data = create_tilemap_data(tilemap, bgi.tile_priority)


    n_palette_rows = len(palette_data) // 32
    assert len(palette_data) % 32 == 0
    assert 1 < n_palette_rows < 8

    tm_size = len(tilemap_data) // NAMETABLE_SIZE_BYTES

    if tm_size not in VALID_BGI_HEADER_TM_SIZES:
        raise ValueError(f"Invalid number of nametables, expected { VALID_BGI_HEADER_TM_SIZES }, got { tm_size }.")

    header_byte = (n_palette_rows << 4) | (tm_size << 2)


    out = bytearray()
    out.append(header_byte)
    out += palette_data
    out += tilemap_data
    out += tile_data

    return out




#
# convert_resources
# =================
#

def compile_list(typename : Name, mapping : list[Name], inputs : dict[str, Any], func : Callable[[Any], bytes]) -> Optional[list[ResourceEntry]]:
    valid = True

    out = list()

    for resource_name in mapping:
        i = inputs.get(resource_name)
        if not i:
            raise RuntimeError(f"Cannot find { typename } resource: {resource_name}")

        try:
            out.append(ResourceEntry(resource_name, func(i)))
        except Exception as e:
            print_error("ERROR compiling { typename } { resource_name }", e)
            valid = False


    if not valid:
        return None

    return out



def build_other_resources(mapping : Mappings, other_resources : OtherResources) -> Optional[OtherResourcesData]:
    tiles = compile_list('tiles', mapping.tiles, other_resources.tiles, convert_tiles)
    bg_images = compile_list('bg_images', mapping.bg_images, other_resources.bg_images, convert_bg_image)

    if tiles is None or bg_images is None:
        return None

    return OtherResourcesData(
            tiles = tiles,
            bg_images = bg_images,
    )



def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='output file')
    parser.add_argument('mapping_filename', action='store',
                        help='mapping json file input')
    parser.add_argument('other_resources_json_file', action='store',
                        help='other resources JSON file input')

    args = parser.parse_args()

    return args;



def main() -> None:
    args = parse_arguments()

    mapping = load_mappings_json(args.mapping_filename)
    other_resources = load_other_resources_json(args.other_resources_json_file)

    data = build_other_resources(mapping, other_resources)

    if not data:
        sys.exit('Error compiling other resources')

    save_other_resources_data_to_file(args.output, data)



if __name__ == '__main__':
    main()


