#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import json
import os.path
import PIL.Image
import argparse
from collections import namedtuple
from io import StringIO


from _snes import extract_small_tile, extract_large_tile, split_large_tile, \
            hflip_tile, vflip_tile, hflip_large_tile, vflip_large_tile, \
            create_palettes_map, get_palette_id, convert_palette_image, \
            convert_snes_tileset

from _json_formats import load_ms_export_order_json, load_metasprites_json


TILE_DATA_BPP = 4

ROM_BANK = 'rodata0'



FrameSet = namedtuple('FrameSet', ('name', 'pattern', 'ms_export_order', 'frames'))



class Tileset:
    def __init__(self, starting_tile, end_tile):
        assert(starting_tile < 512)
        assert(end_tile <= 512)
        assert(starting_tile < end_tile)

        # starting_tile must start on a VRAM row
        assert(starting_tile % 0x10 == 0)

        self.starting_tile = starting_tile
        self.max_tiles = end_tile - starting_tile

        self.tiles = [ None ] * 0x20
        self.large_tile_pos = 0
        self.small_tile_pos = 4
        self.small_tile_offset = 0

        self.small_tiles_map = dict()
        self.large_tiles_map = dict()


    def get_tiles(self):
        # Replace unused tiles with blank data
        blank_tile = bytearray(64)
        tiles = [ blank_tile if t is None else t for t in self.tiles ]

        # Shrink tiles
        end_tile = 0
        for i, t in enumerate(self.tiles):
            if t is not None:
                end_tile = i
        n_tiles = end_tile + 1

        if n_tiles > self.max_tiles:
            raise ValueError(f"Too many tiles: { n_tiles }, max { self.max_tiles }")

        return tiles[:n_tiles]


    def _allocate_large_tile(self):
        tile_pos = self.large_tile_pos

        self.large_tile_pos += 2
        if self.large_tile_pos & 0x0f == 0:
            self.large_tile_pos += 0x10

            self.tiles += [ None ] * 0x20

        return tile_pos


    def _allocate_small_tile(self):
        if self.small_tile_pos >= 4:
            self.small_tile_pos = 0
            self.small_tile_offset = self._allocate_large_tile()

        tile_pos = self.small_tile_offset + self._SMALL_TILE_OFFSETS[self.small_tile_pos]
        self.small_tile_pos += 1

        return tile_pos

    _SMALL_TILE_OFFSETS = [ 0x00, 0x01, 0x10, 0x11 ]


    def add_small_tile(self, tile_data):
        assert(len(tile_data) == 64)

        tile_pos = self._allocate_small_tile()

        self.tiles[tile_pos] = tile_data

        return tile_pos + self.starting_tile


    def add_large_tile(self, tile_data):
        assert(len(tile_data) == 256)

        tile1, tile2, tile3, tile4 = split_large_tile(tile_data)

        tile_pos = self._allocate_large_tile()

        self.tiles[tile_pos] = tile1
        self.tiles[tile_pos + 0x01] = tile2
        self.tiles[tile_pos + 0x10] = tile3
        self.tiles[tile_pos + 0x11] = tile4

        return tile_pos + self.starting_tile


    def add_or_get_small_tile(self, tile_data):
        assert(len(tile_data) == 64)

        match = self.small_tiles_map.get(tile_data)
        if match is None:
            tile_id = self.add_small_tile(tile_data)

            match = (tile_id, False, False)

            h_tile_data = hflip_tile(tile_data)
            v_tile_data = vflip_tile(tile_data)
            hv_tile_data = vflip_tile(h_tile_data)

            self.small_tiles_map[tile_data] = match
            self.small_tiles_map.setdefault(h_tile_data, (tile_id, True, False))
            self.small_tiles_map.setdefault(v_tile_data, (tile_id, False, True))
            self.small_tiles_map.setdefault(hv_tile_data, (tile_id, True, True))

        return match


    def add_or_get_large_tile(self, tile_data):
        match = self.large_tiles_map.get(tile_data)
        if match is None:
            tile_id = self.add_large_tile(tile_data)

            match = (tile_id, False, False)

            h_tile_data = hflip_large_tile(tile_data)
            v_tile_data = vflip_large_tile(tile_data)
            hv_tile_data = vflip_large_tile(h_tile_data)

            self.large_tiles_map[tile_data] = match
            self.large_tiles_map.setdefault(h_tile_data, (tile_id, True, False))
            self.large_tiles_map.setdefault(v_tile_data, (tile_id, False, True))
            self.large_tiles_map.setdefault(hv_tile_data, (tile_id, True, True))

        return match



def extract_frame(image, pattern, palettes_map, tileset, fs, block, x, y):
    data = bytearray()

    data.append(pattern.id)
    data.append(block.x_offset)
    data.append(block.y_offset)

    for o in pattern.objects:
        if o.size == 8:
            tile = extract_small_tile(image, x + o.xpos, y + o.ypos)
            palette_id, pal_map = get_palette_id(tile, palettes_map)
            tile_data = bytes([pal_map[c] for c in tile])
            tile_id, hflip, vflip = tileset.add_or_get_small_tile(tile_data)
        else:
            tile = extract_large_tile(image, x + o.xpos, y + o.ypos)
            palette_id, pal_map = get_palette_id(tile, palettes_map)
            tile_data = bytes([pal_map[c] for c in tile])
            tile_id, hflip, vflip = tileset.add_or_get_large_tile(tile_data)

        assert(tile_id < 512)
        data.append(tile_id & 0xff)
        data.append((tile_id >> 8)
                    | ((palette_id & 7) << 1)
                    | ((fs.order & 3) << 4)
                    | (bool(hflip) << 6)
                    | (bool(vflip) << 7)
        )

    return data



def build_frameset(fs, ms_export_orders, ms_dir, tiles, palettes_map):
    frames = dict()

    base_pattern = ms_export_orders.patterns[fs.pattern]

    image = load_image(ms_dir, fs.source)

    if image.width % fs.frame_width != 0 or image.height % fs.frame_height != 0:
        raise ValueError(f"Source image is not a multiple of frame size: { fs.name }")

    frames_per_row = image.width // fs.frame_width


    all_blocks_use_the_same_pattern = True

    for block in fs.blocks:
        # ::TODO somehow handle clone blocks::
        # ::TODO somehow handle flipped blocks::

        if block.pattern:
            pattern = ms_export_orders.patterns[block.pattern]

            if block.pattern != fs.pattern:
                all_blocks_use_the_same_pattern = False
        else:
            pattern = base_pattern

        for i, f in enumerate(block.frames):
            if f in frames:
                raise ValueError(f"Duplicate frame: { f }")

            frame_number = block.start + i
            x = (frame_number % frames_per_row) * fs.frame_width + block.x
            y = (frame_number // frames_per_row) * fs.frame_height + block.y

            try:
                frames[f] = extract_frame(image, pattern, palettes_map, tiles, fs, block, x, y)
            except Exception as e:
                raise Exception(f"Error with { fs.name }, { f }: { e }")

    try:
        export_order = ms_export_orders.frame_lists[fs.ms_export_order]
        eo_frames = [ frames[f] for f in export_order.frames ]
    except Exception as e:
        raise Exception(f"Error with { fs.name }: { e }")


    if all_blocks_use_the_same_pattern:
        pattern_name = fs.pattern
    else:
        pattern_name = "dynamic_pattern"


    return FrameSet(fs.name, pattern_name, fs.ms_export_order, eo_frames)



def build_spritesheet(ms_input, ms_export_orders, ms_dir, palettes_map):

    tiles = Tileset(ms_input.first_tile, ms_input.end_tile)

    spritesheet = list()

    for fs in ms_input.framesets.values():
        spritesheet.append(
            build_frameset(fs, ms_export_orders, ms_dir, tiles, palettes_map))

    return spritesheet, tiles.get_tiles()



def load_palette(ms_dir, palette_filename):
    image = load_image(ms_dir, palette_filename)

    if image.width != 16 or image.height != 8:
        raise ValueError('Palette Image MUST BE 16x8 px in size')

    palettes_map = create_palettes_map(image, TILE_DATA_BPP)
    palette_data = convert_palette_image(image)

    return palettes_map, palette_data



def generate_wiz_data(spritesheet, spritesheet_name, binary_data_path):
    with StringIO() as out:
        out.write("""
import "../../src/memmap";
import "../../src/metasprites";
""")
        out.write(f"in { ROM_BANK } {{")
        out.write("""

namespace ms {
""")
        out.write(f"namespace { spritesheet_name } {{\n")

        for fs in spritesheet:
            n_frames = len(fs.frames)

            out.write('\n')
            out.write(f"  namespace { fs.name } {{\n")

            out.write(f"    // ms_export_order = { fs.ms_export_order }\n")
            out.write(f"    let draw_function = metasprites.drawing_functions.{ fs.pattern };\n\n")

            out.write(f"    const frame_table : [*const u8 ; { n_frames }] = [\n")

            for frame_data in fs.frames:
                out.write(f"      @[ { frame_data[0] }u8, { ', '.join(map(str, frame_data[1:])) } ],\n")

            out.write( '    ];\n\n')

            out.write( '  }\n')

        out.write("""
}
}

}

""")

        return out.getvalue()



#
# =========================
#



def generate_ppu_data(ms_input, tileset, palette_data):
    tile_data = convert_snes_tileset(tileset, TILE_DATA_BPP)


    data = bytearray()

    # first_tile
    data.append(ms_input.first_tile & 0xff)
    data.append(ms_input.first_tile >> 8)

    # tile_data_size
    data.append(len(tile_data) & 0xff)
    data.append(len(tile_data) >> 8)

    # tile_data
    data += tile_data

    # palette_data
    data += palette_data

    return data



def load_image(ms_dir, filename):
    image_filename = os.path.join(ms_dir, filename)

    with PIL.Image.open(image_filename) as image:
        image.load()

    if image.mode == 'RGB':
        return image
    else:
        return image.convert('RGB')



def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ppu-output', required=True,
                        help='ppu data output file')
    parser.add_argument('--wiz-output', required=True,
                        help='sprite output file')
    parser.add_argument('json_filename', action='store',
                        help='Sprite map JSON file')
    parser.add_argument('ms_export_order_json_file', action='store',
                        help='metasprite export order map JSON file')

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    ms_dir = os.path.dirname(args.json_filename)

    ms_input = load_metasprites_json(args.json_filename)
    ms_export_orders = load_ms_export_order_json(args.ms_export_order_json_file)


    palettes_map, palette_data = load_palette(ms_dir, ms_input.palette)

    spritesheet, tileset = build_spritesheet(ms_input, ms_export_orders, ms_dir, palettes_map)


    ppu_data = generate_ppu_data(ms_input, tileset, palette_data)
    wiz_data = generate_wiz_data(spritesheet,
                                 os.path.splitext(os.path.basename(args.wiz_output))[0],
                                 os.path.relpath(args.ppu_output, os.path.dirname(args.wiz_output)))

    with open(args.ppu_output, 'wb') as fp:
        fp.write(ppu_data)

    with open(args.wiz_output, 'w') as fp:
        fp.write(wiz_data)



if __name__ == '__main__':
    main()


