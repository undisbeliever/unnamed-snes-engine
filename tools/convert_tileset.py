#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import PIL.Image # type: ignore
import argparse
import xml.etree.ElementTree
from typing import NamedTuple, Optional

from _json_formats import load_mappings_json
from _snes import image_to_snes, TileMapEntry


N_TILES = 256

TILE_DATA_BPP = 4

DEFAULT_PRIORITY = 0


TILE_PROPERTY_SOLID_BIT = 7


class TileProperty(NamedTuple):
    solid       : bool
    type        : Optional[str]
    priority    : int           # integer bitfield (4 bits wide), one priority bit for each 8px tile



def create_metatile_map(tilemap : list[TileMapEntry], tile_properties : list[TileProperty]) -> bytes:
    data = bytearray()

    priotity_bit = 1 << 4

    assert(len(tilemap) == 32 * 32)
    for xoffset, yoffset in ((0, 0), (1, 0), (0, 1), (1, 1)):
        priotity_bit >>= 1

        for y in range(yoffset, 32, 2):
            for x in range(xoffset, 32, 2):
                tm = tilemap[x + y * 32]
                data.append(tm.tile_id & 0xff)

        for y in range(yoffset, 32, 2):
            for x in range(xoffset, 32, 2):
                tile_id = (y // 2) * 16 + (x // 2)
                priority = tile_properties[tile_id].priority & priotity_bit;

                tm = tilemap[x + y * 32]
                # This should never happen
                assert(tm.tile_id <= 0x3ff)
                assert(tm.palette_id <= 7)
                data.append((tm.tile_id >> 8) | (tm.palette_id << 2) | (bool(priority) << 5)
                            | (bool(tm.hflip) << 6) | (bool(tm.vflip) << 7))

    return data



def check_objectgroup_tag(tag : xml.etree.ElementTree.Element) -> bool:
    if len(tag) != 1:
        return False

    childTag = tag[0]

    if childTag.tag != 'object':
        return False

    return (childTag.tag == 'object'
        and childTag.attrib['x'] == '0'
        and childTag.attrib['y'] == '0'
        and childTag.attrib['width'] == '16'
        and childTag.attrib['height'] == '16')



def read_tile_priority_value(value : str, tile_id : int) -> int:
    if not isinstance(value, str):
        raise ValueError('Unknown type, expected string')

    if value == '0':
        return 0
    if value == '1':
        return 0xf

    if len(value) != 4:
        raise ValueError(f"Unknown priority value (tile { tile_id }: { value }")

    return int(value, 2)



def read_tile_tag(tile_tag : xml.etree.ElementTree.Element) -> tuple[int, TileProperty]:
    """ Returns (tile_id, TileProperty) """

    tile_id = int(tile_tag.attrib['id'])

    if tile_id > 255:
        raise ValueError("Invalid tileid")

    tile_solid = False
    tile_type = None
    tile_priority = 0

    if 'type' in tile_tag.attrib:
        tile_type = tile_tag.attrib['type']

    for tag in tile_tag:
        if tag.tag == 'objectgroup':
            if not check_objectgroup_tag(tag):
                raise ValueError('Tile collision MUST cover the whole tile in a single rectangle (tile { tile_id }')
            tile_solid = True

        elif tag.tag == 'properties':
            for ptag in tag:
                if ptag.tag == 'property':
                    if ptag.attrib['name'] == 'priority':
                        tile_priority = read_tile_priority_value(ptag.attrib['value'], tile_id)

    return tile_id, TileProperty(solid=tile_solid, type=tile_type, priority=tile_priority)



def read_tile_properties(tsx_et : xml.etree.ElementTree.ElementTree) -> list[TileProperty]:

    out = [ TileProperty(solid=False, type=None, priority=DEFAULT_PRIORITY) ] * N_TILES

    for tag in tsx_et.getroot():
        if tag.tag == 'tile':
            tile_id, t = read_tile_tag(tag)

            out[tile_id] = t;

    return out



def create_properties_array(tile_properties : list[TileProperty], interactive_tile_functions : list[str]) -> bytes:
    data = bytearray(256)

    for i, tile in enumerate(tile_properties):
        p = 0

        if tile.type:
            tile_type_id = interactive_tile_functions.index(tile.type) + 1
            p |= tile_type_id << 1

        if tile.solid:
            p |= 1 << TILE_PROPERTY_SOLID_BIT

        data[i] = p

    return data



def create_tileset_data(palette_data : bytes, tile_data : bytes, metatile_map : bytes, properties : bytes) -> bytes:
    data = bytearray()

    # 2048 bytes = metatile map
    assert(len(metatile_map) == 2048)
    data += metatile_map

    # 256 bytes = properties map
    assert(len(properties) == 256)
    data += properties

    # Next 256 bytes = palette data
    data += palette_data
    data += bytes(0) * (256 - len(palette_data))

    assert(len(data) == 2048 + 256 * 2)

    # Next data: tile data
    data += tile_data

    return data



def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='tileset output file')
    parser.add_argument('mappings_json_file', action='store',
                        help='mappings.json file')
    parser.add_argument('image_filename', action='store',
                        help='Indexed png image')
    parser.add_argument('palette_filename', action='store',
                        help='palette PNG image')
    parser.add_argument('tsx_filename', action='store',
                        help='Tiled tsx file')

    args = parser.parse_args()

    return args;



def main() -> None:
    args = parse_arguments()

    with PIL.Image.open(args.palette_filename) as palette_image:
        with PIL.Image.open(args.image_filename) as image:
            if image.width != 256 or image.height != 256:
                raise ValueError('Tileset Image MUST BE 256x256 px in size')

            tilemap, tile_data, palette_data = image_to_snes(image, palette_image, TILE_DATA_BPP)

    with open(args.tsx_filename, 'r') as tsx_fp:
        tsx_et = xml.etree.ElementTree.parse(tsx_fp)

    tile_properties = read_tile_properties(tsx_et)

    mappings = load_mappings_json(args.mappings_json_file)

    metatile_map = create_metatile_map(tilemap, tile_properties)
    properties = create_properties_array(tile_properties, mappings.interactive_tile_functions)

    tileset_data = create_tileset_data(palette_data, tile_data, metatile_map, properties)

    with open(args.output, 'wb') as fp:
        fp.write(tileset_data)


if __name__ == '__main__':
    main()

