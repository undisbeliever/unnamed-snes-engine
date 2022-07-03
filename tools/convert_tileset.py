#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import PIL.Image # type: ignore
import argparse
import xml.etree.ElementTree
from typing import Final, NamedTuple, Optional, TextIO

from _json_formats import load_mappings_json, Filename, Mappings
from _snes import image_to_snes, TileMap, ImageError, InvalidTilesError
from _common import SimpleMultilineError


N_TILES = 256

TILE_DATA_BPP = 4

DEFAULT_PRIORITY = 0


TILE_PROPERTY_SOLID_BIT = 7


class TileProperty(NamedTuple):
    solid       : bool
    type        : Optional[str]
    priority    : int           # integer bitfield (4 bits wide), one priority bit for each 8px tile



class TsxFileError(SimpleMultilineError):
    def __init__(self, errors : list[str]):
        super().__init__('Error reading TSX file', errors)



def create_metatile_map(tilemap : TileMap, tile_properties : list[TileProperty]) -> bytes:
    data = bytearray()

    if tilemap.width != 32 and tilemap.height != 32:
        raise ValueError(f"Invalid tilemap size: { tilemap.width }x{ tilemap.height }")

    priotity_bit = 1 << 4

    assert len(tilemap.grid) == 32 * 32
    for xoffset, yoffset in ((0, 0), (1, 0), (0, 1), (1, 1)):
        priotity_bit >>= 1

        for y in range(yoffset, 32, 2):
            for x in range(xoffset, 32, 2):
                tm = tilemap.get_tile(x, y)
                data.append(tm.tile_id & 0xff)

        for y in range(yoffset, 32, 2):
            for x in range(xoffset, 32, 2):
                tile_id = (y // 2) * 16 + (x // 2)
                priority = tile_properties[tile_id].priority & priotity_bit;

                tm = tilemap.get_tile(x, y)
                # This should never happen
                assert tm.tile_id <= 0x3ff
                assert tm.palette_id <= 7
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
        and childTag.attrib.get('x') == '0'
        and childTag.attrib.get('y') == '0'
        and childTag.attrib.get('width') == '16'
        and childTag.attrib.get('height') == '16')



def read_tile_priority_value(value : Optional[str]) -> int:
    if not isinstance(value, str):
        raise ValueError('Unknown priority value type, expected string')

    if value == '0':
        return 0
    if value == '1':
        return 0xf

    if len(value) != 4:
        raise ValueError(f"Unknown priority value: { value }")

    return int(value, 2)



def read_tile_tag(tile_tag : xml.etree.ElementTree.Element, error_list : list[str]) -> tuple[int, TileProperty]:
    """ Returns (tile_id, TileProperty) """

    try:
        tile_id = int(tile_tag.attrib['id'])
        if tile_id < 0 or tile_id > 255:
            error_list.append(f"Invalid <tile> id: { tile_id }")
    except KeyError:
        error_list.append('<tile> tag with missing id')
        tile_id = -1
    except ValueError:
        error_list.append(f"Invalid <tile> id: { tile_tag.attrib.get('id') }")
        tile_id = -1


    tile_solid = False
    tile_priority = 0

    tile_type = tile_tag.attrib.get('type')

    for tag in tile_tag:
        if tag.tag == 'objectgroup':
            if not check_objectgroup_tag(tag):
                error_list.append(f"Tile {tile_id}: Tile collision MUST cover the whole tile in a single rectangle")
            tile_solid = True

        elif tag.tag == 'properties':
            for ptag in tag:
                if ptag.tag == 'property':
                    p_name = ptag.attrib.get('name')
                    if p_name == 'priority':
                        try:
                            tile_priority = read_tile_priority_value(ptag.attrib.get('value'))
                        except ValueError as e:
                            error_list.append(f"Tile {tile_id}: { e }")

    return tile_id, TileProperty(solid=tile_solid, type=tile_type, priority=tile_priority)



def read_tile_properties(tsx_et : xml.etree.ElementTree.ElementTree, error_list : list[str]) -> list[TileProperty]:
    out = [ TileProperty(solid=False, type=None, priority=DEFAULT_PRIORITY) ] * N_TILES

    read_tiles : set[int] = set()

    for tag in tsx_et.getroot():
        if tag.tag == 'tile':
            tile_id, t = read_tile_tag(tag, error_list)

            if tile_id not in read_tiles:
                out[tile_id] = t;
                read_tiles.add(tile_id)
            else:
                error_list.append(f"Duplicate <tile> id: { tile_id }")

    return out



def create_properties_array(tile_properties : list[TileProperty], interactive_tile_functions : list[str], error_list : list[str]) -> bytes:
    data = bytearray(256)

    for i, tile in enumerate(tile_properties):
        p = 0

        if tile.type:
            try:
                tile_type_id = interactive_tile_functions.index(tile.type) + 1
                p |= tile_type_id << 1
            except ValueError:
                error_list.append(f"Tile { i }: Invalid interactive_tile_function { tile.type }")

        if tile.solid:
            p |= 1 << TILE_PROPERTY_SOLID_BIT

        data[i] = p

    if error_list:
        raise TsxFileError(error_list)

    return data



def create_tileset_data(palette_data : bytes, tile_data : bytes, metatile_map : bytes, properties : bytes) -> bytes:
    data = bytearray()

    # 2048 bytes = metatile map
    assert len(metatile_map) == 2048
    data += metatile_map

    # 256 bytes = properties map
    assert len(properties) == 256
    data += properties

    # Next 256 bytes = palette data
    data += palette_data
    data += bytes(0) * (256 - len(palette_data))

    assert len(data) == 2048 + 256 * 2

    # Next data: tile data
    data += tile_data

    return data



def convert_mt_tileset(tsx_filename : str, image_filename : str, palette_filename : str, mappings : Mappings) -> bytes:

    with PIL.Image.open(palette_filename) as palette_image:
        with PIL.Image.open(image_filename) as image:
            if image.width != 256 or image.height != 256:
                raise ImageError(image_filename, 'Tileset Image MUST BE 256x256 px in size')

            tilemap, tile_data, palette_data = image_to_snes(image, palette_image, TILE_DATA_BPP)

    with open(tsx_filename, 'r') as tsx_fp:
        tsx_et = xml.etree.ElementTree.parse(tsx_fp)

    error_list : list[str] = list()

    tile_properties = read_tile_properties(tsx_et, error_list)

    metatile_map = create_metatile_map(tilemap, tile_properties)
    properties = create_properties_array(tile_properties, mappings.interactive_tile_functions, error_list)

    if error_list:
        raise TsxFileError(error_list)

    return create_tileset_data(palette_data, tile_data, metatile_map, properties)



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

    mappings = load_mappings_json(args.mappings_json_file)

    tileset_data = convert_mt_tileset(args.tsx_filename, args.image_filename, args.palette_filename, mappings)

    with open(args.output, 'wb') as fp:
        fp.write(tileset_data)


if __name__ == '__main__':
    main()

