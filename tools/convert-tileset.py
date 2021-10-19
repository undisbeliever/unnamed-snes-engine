#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import PIL.Image
import argparse
import xml.etree.ElementTree


from _snes import image_to_snes


TILE_DATA_BPP = 4

DEFAULT_ORDER_BIT = 0


TILE_PROPERTY_SOLID_BIT = 7



def create_metatile_map(tilemap):
    data = bytearray()

    assert(len(tilemap) == 32 * 32)
    for xoffset, yoffset in ((0, 0), (1, 0), (0, 1), (1, 1)):
        for y in range(yoffset, 32, 2):
            for x in range(xoffset, 32, 2):
                tm = tilemap[x + y * 32]
                data.append(tm.tile_id & 0xff)

        for y in range(yoffset, 32, 2):
            for x in range(xoffset, 32, 2):
                tm = tilemap[x + y * 32]
                # This should never happen
                assert(tm.tile_id <= 0x3ff)
                assert(tm.palette_id <= 7)
                data.append((tm.tile_id >> 8) | (tm.palette_id << 2) | (bool(DEFAULT_ORDER_BIT) << 5)
                            | (bool(tm.hflip) << 6) | (bool(tm.vflip) << 7))

    return data



def check_objectgroup_tag(tag):
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



def read_tile_tag(tile_tag):
    """ Returns (tile_id, properties_value) """

    tile_id = int(tile_tag.attrib['id']);

    if tile_id > 255:
        raise ValueError("Invalid tileid")

    properties = 0;

    for tag in tile_tag:
        if tag.tag == 'objectgroup':
            if not check_objectgroup_tag(tag):
                raise ValueError('Tile collision MUST cover the whole tile in a single rectangle')
            properties |= 1 << TILE_PROPERTY_SOLID_BIT

    return tile_id, properties



def create_properties_array(tsx_et):
    data = bytearray(256)

    for tag in tsx_et.getroot():
        if tag.tag == 'tile':
            tile_id, p = read_tile_tag(tag)
            data[tile_id] = p

    return data



def create_tileset_data(palette_data, tile_data, metatile_map, properties):
    data = bytearray()

    # First Word: tile data size
    tile_data_size = len(tile_data)
    data.append(tile_data_size & 0xff)
    data.append(tile_data_size >> 8)

    # 2048 bytes = metatile map
    assert(len(metatile_map) == 2048)
    data += metatile_map

    # 256 bytes = properties map
    assert(len(properties) == 256)
    data += properties

    # Next 256 bytes = palette data
    data += palette_data
    data += bytes(0) * (256 - len(palette_data))

    assert(len(data) == 2 + 2048 + 256 * 2)

    # Next data: tile data
    data += tile_data

    return data



def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='tileset output file')
    parser.add_argument('image_filename', action='store',
                        help='Indexed png image')
    parser.add_argument('palette_filename', action='store',
                        help='palette PNG image')
    parser.add_argument('tsx_filename', action='store',
                        help='Tiled tsx file')

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    with PIL.Image.open(args.palette_filename) as palette_image:
        with PIL.Image.open(args.image_filename) as image:
            if image.width != 256 or image.height != 256:
                raise ValueError('Tileset Image MUST BE 256x256 px in size')

            tilemap, tile_data, palette_data = image_to_snes(image, palette_image, TILE_DATA_BPP)

    with open(args.tsx_filename, 'r') as tsx_fp:
        tsx_et = xml.etree.ElementTree.parse(tsx_fp)

    metatile_map = create_metatile_map(tilemap)
    properties = create_properties_array(tsx_et)

    tileset_data = create_tileset_data(palette_data, tile_data, metatile_map, properties)

    with open(args.output, 'wb') as fp:
        fp.write(tileset_data)


if __name__ == '__main__':
    main()

