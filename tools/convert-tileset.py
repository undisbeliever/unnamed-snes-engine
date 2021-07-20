#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import PIL.Image
import argparse
import struct
import array
from collections import namedtuple


TILE_DATA_BPP = 4

DEFAULT_ORDER_BIT = 1


TileMapEntry = namedtuple('TileMapEntry', ('tile_id', 'palette_id'))


def convert_rgb_color(c):
    r, g, b = c

    b = (b >> 3) & 31;
    g = (g >> 3) & 31;
    r = (r >> 3) & 31;

    return (b << 10) | (g << 5) | r;


def convert_snes_tileset_4bpp(tiles):
    out = bytearray()

    for tile in tiles:
        for b in range(0, TILE_DATA_BPP, 2):
            for y in range(0, 8):
                for bi in range(b, min(b+2, TILE_DATA_BPP)):
                    byte = 0
                    mask = 1 << bi
                    for x in range(0, 8):
                        byte <<= 1
                        if tile[x + y * 8] & mask:
                            byte |= 1
                    out.append(byte)
    return out



def extract_tiles(image):
    """ Extracts 8x8px tiles from the image. """

    if image.mode != 'RGB':
        image = image.convert('RGB')


    if image.width != 256 or image.height != 256:
        raise ValueError('Tileset Image MUST BE 256x256 px in size')

    for ty in range(0, 256, 8):
        for tx in range(0, 256, 8):

            tile_data = list()

            for y in range(ty, ty + 8):
                for x in range(tx, tx + 8):
                    tile_data.append(convert_rgb_color(image.getpixel((x, y))))

            yield tile_data



def convert_palette(image):
    if image.mode != 'RGB':
        image = image.convert('RGB')

    if image.width != 16 or image.height != 8:
        raise Value('Palette Image MUST BE 16x8 px in size')

    palette = list()

    for y in range(0, image.height):

        pal_line = list()
        pal_map = dict()

        for x in range(16):
            c = convert_rgb_color(image.getpixel((x, y)))

            pal_line.append(c)
            if c not in pal_map:
                pal_map[c] = x

        palette.append((pal_line, pal_map))

    return palette



def get_palette_id(tile, palettes):
    # Returns a tuple of (palette_id, palette_map)
    for palette_id, _pal in enumerate(palettes):
        pal_map = _pal[1]

        if all([c in pal_map for c in tile]):
            return palette_id, pal_map

    return None, None



def convert_tilemap_and_tileset(tiles, palettes):
    # Returns a tuple(tilemap, tileset)

    invalid_tiles = list()

    tilemap = list()
    tileset = list()

    tileset_map = dict()

    for tile_index, tile in enumerate(tiles):
        palette_id, palette_map = get_palette_id(tile, palettes)

        if palette_map:
            # Must be bytes() here as a dict() key must be immutable
            tile_data = bytes([palette_map[c] for c in tile])

            tile_id = tileset_map.get(tile_data, None)
            if tile_id is None:
                tile_id = len(tileset)
                tileset_map[tile_data] = tile_id
                tileset.append(tile_data)

                # ::TODO add flipped tiles to tileset_map::

            tilemap.append(TileMapEntry(tile_id=tile_id, palette_id=palette_id))
        else:
            invalid_tiles.append(tile_index)

    if invalid_tiles:
        raise ValueError(f"Cannot find palette for tiles {invalid_tiles}")

    return tilemap, tileset



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
                data.append((tm.tile_id >> 8) | (tm.palette_id << 2) | (bool(DEFAULT_ORDER_BIT) << 5))

    return data



def create_tileset_data(palette, metatile_map, tile_data):
    data = bytearray()

    # First Word: tile data size
    tile_data_size = len(tile_data)
    data.append(tile_data_size & 0xff)
    data.append(tile_data_size >> 8)

    # 2048 bytes = metatile map
    assert(len(metatile_map) == 2048)
    data += metatile_map

    # ::TODO add collision data::

    # Next 256 bytes = palette data
    for pal, p_map in palette:
        for c in pal:
            data.append(c & 0xff)
            data.append(c >> 8)
    assert(len(data) == 256 + 2048 + 2)

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

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    with PIL.Image.open(args.palette_filename) as palImage:
        palette = convert_palette(palImage)

        with PIL.Image.open(args.image_filename) as image:
            tilemap, tileset = convert_tilemap_and_tileset(extract_tiles(image), palette)

    metatile_map = create_metatile_map(tilemap)
    tile_data = convert_snes_tileset_4bpp(tileset)

    tileset_data = create_tileset_data(palette, metatile_map, tile_data)

    with open(args.output, 'wb') as fp:
        fp.write(tileset_data)


if __name__ == '__main__':
    main()

