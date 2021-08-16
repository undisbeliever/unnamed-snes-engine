#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import PIL.Image
import argparse
import struct
from collections import namedtuple



TileMapEntry = namedtuple('TileMapEntry', ('tile_id', 'palette_id', 'hflip', 'vflip'))

VALID_FORMATS = {
    '2bpp'  : 2,
    '4bpp'  : 4,
    '8bpp'  : 8,
}



def convert_snes_tileset(tiles, bpp):
    out = bytearray()

    for tile in tiles:
        for b in range(0, bpp, 2):
            for y in range(0, 8):
                for bi in range(b, min(b+2, bpp)):
                    byte = 0
                    mask = 1 << bi
                    for x in range(0, 8):
                        byte <<= 1
                        if tile[x + y * 8] & mask:
                            byte |= 1
                    out.append(byte)
    return out



def convert_rgb_color(c):
    r, g, b = c

    b = (b >> 3) & 31;
    g = (g >> 3) & 31;
    r = (r >> 3) & 31;

    return (b << 10) | (g << 5) | r;



def create_palette_maps(image, bpp):
    if image.mode != 'RGB':
        image = image.convert('RGB')


    colors_per_palette = 1 << bpp

    if image.width != 16:
        raise ValueError('Palette Image MUST BE 16 px in width')

    if image.width * image.height > 256:
        raise ValueError('Palette Image has too many colours (max 256)')

    if image.width * image.height > colors_per_palette * 8:
        raise ValueError(f'Palette Image has too many colours (max { colors_per_palette * 8 })')

    image_data = image.getdata()


    palettes = list()


    for p in range(len(image_data) // colors_per_palette):
        pal_map = dict()

        for x in range(colors_per_palette):
            c = convert_rgb_color(image_data[p * colors_per_palette + x])
            if c not in pal_map:
                pal_map[c] = x

        palettes.append(pal_map)

    return palettes



def convert_palette(image, max_colors=256):
    # Assumes image is valid

    if image.mode != 'RGB':
        image = image.convert('RGB')

    out = bytearray()

    for c in image.getdata():
        u16 = convert_rgb_color(c)

        out.append(u16 & 0xff)
        out.append(u16 >> 8)


    return out



def extract_tiles(image):
    if image.mode != 'RGB':
        image = image.convert('RGB')

    if image.width % 256 != 0:
        raise ValueError('Image width MUST BE a multiple of 256')

    if image.height % 256 != 0:
        raise ValueError('Image height MUST BE a multiple of 256')

    if image.width > 512 or image.height > 512:
        raise ValueError('Maximum image size is 512x512 pixels')


    t_width = image.width // 8
    t_height = image.height // 8

    img_data = image.getdata()

    for screen_y in range(t_height // 32):
        for screen_x in range(t_width // 32):
            for ty in range(32):
                ty = screen_y * 256 + ty * 8
                for tx in range(32):
                    tx = screen_x * 256 + tx * 8

                    tile_data = list()

                    for y in range(ty, ty + 8):
                        for x in range(tx, tx + 8):
                            tile_data.append(convert_rgb_color(image.getpixel((x, y))))

                    yield tile_data



def get_palette_id(tile, palettes):
    # Returns a tuple of (palette_id, palette_map)
    for palette_id, pal_map in enumerate(palettes):
        if all([c in pal_map for c in tile]):
            return palette_id, pal_map

    return None, None



def convert_tilemap_and_tileset(image, palettes):
    # Returns a tuple(tilemap, tileset)

    invalid_tiles = list()

    tilemap = list()
    tileset = list()

    tileset_map = dict()

    for tile_index, tile in enumerate(extract_tiles(image)):
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

            tilemap.append(TileMapEntry(tile_id=tile_id, palette_id=palette_id, hflip=False, vflip=False))
        else:
            invalid_tiles.append(tile_index)

    if invalid_tiles:
        raise ValueError(f"Cannot find palette for tiles {invalid_tiles}")

    return tilemap, tileset



def create_tilemap_data(tilemap, default_order):
    data = bytearray()

    assert(len(tilemap) % 32 * 32 == 0)

    for t in tilemap:
        data.append(t.tile_id & 0xff)
        data.append(((t.tile_id & 0x3ff) >> 8) | ((t.palette_id & 7) << 2)
                    | (bool(default_order) << 5) | (bool(t.hflip) << 6) | (bool(t.vflip) << 7))

    return data



def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-f', '--format', required=True,
                        choices=VALID_FORMATS.keys(),
                        help='tile format')
    parser.add_argument('-t', '--tileset-output', required=True,
                        help='tileset output file')
    parser.add_argument('-m', '--tilemap-output', required=True,
                        help='tilemap output file')
    parser.add_argument('-p', '--palette-output', required=True,
                        help='palette output file')
    parser.add_argument('--order', required=False, action='store_true',
                        help='increase tilemap priority')
    parser.add_argument('image_filename', action='store',
                        help='Indexed png image')
    parser.add_argument('palette_image', action='store',
                        help='Palette png image')

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    bpp = VALID_FORMATS[args.format]

    image = PIL.Image.open(args.image_filename)
    palette_image = PIL.Image.open(args.palette_image)

    palettes_map = create_palette_maps(palette_image, bpp)
    tilemap, tileset = convert_tilemap_and_tileset(image, palettes_map)

    tileset_data = convert_snes_tileset(tileset, bpp)
    tilemap_data = create_tilemap_data(tilemap, args.order)
    palette_data = convert_palette(palette_image)

    with open(args.tileset_output, 'wb') as fp:
        fp.write(tileset_data)

    with open(args.tilemap_output, 'wb') as fp:
        fp.write(tilemap_data)

    with open(args.palette_output, 'wb') as fp:
        fp.write(palette_data)



if __name__ == '__main__':
    main()

