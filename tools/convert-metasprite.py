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
            convert_snes_tileset, is_small_tile_not_transparent

from _json_formats import load_ms_export_order_json, load_metasprites_json


TILE_DATA_BPP = 4

ROM_BANK = 'rodata0'



PatternGrid = namedtuple('PatternGrid', ('tile_count', 'width', 'height', 'data', 'pattern'))

FrameSet = namedtuple('FrameSet', ('name', 'shadow_size', 'tile_hitbox', 'pattern', 'ms_export_order', 'frames'))



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



def generate_pattern_grids(ms_export_orders):
    """
    Convert `ms_export_orders.patterns' to a list of `PatternGrid`.
    """

    pattern_grids = list()

    for p in ms_export_orders.patterns.values():
        obj_min_x = min(o.xpos for o in p.objects)
        obj_min_y = min(o.ypos for o in p.objects)
        obj_max_x = max(o.xpos + o.size for o in p.objects)
        obj_max_y = max(o.ypos + o.size for o in p.objects)

        if obj_min_x % 8 != 0 or obj_min_y % 8 != 0 or obj_max_x % 8 != 0 or obj_max_y % 8 != 0:
            continue

        width = (obj_max_x - obj_min_x) // 8
        height = (obj_max_y - obj_min_y) // 8

        data = [ False ] * (width * height)

        tile_count = 0
        for o in p.objects:
            tile_pos = (o.xpos // 8) + (o.ypos // 8 * width)
            for y in range(o.size // 8):
                for x in range(o.size // 8):
                    data[tile_pos + x + y * width] = True
                    tile_count += 1

        pattern_grids.append(
            PatternGrid(
                tile_count = tile_count,
                width = width,
                height = height,
                data = data,
                pattern = p
            )
        )

    pattern_grids.sort(key=lambda pg: pg.tile_count)

    return pattern_grids



def test_pattern_grid(p_grid, i_grid, x_offset, y_offset):
    """
    Test if a PatternGrid can be used on an Image Grid at a given location.

    Returns tuple (valid (bool), number of unused tiles in PatternGrid)
    """

    n_matches = 0
    n_unused_tiles = 0

    for y in range(p_grid.height):
        for x in range(p_grid.width):
            p_tile = p_grid.data[y * p_grid.width + x]
            i_tile = i_grid.data[(y + y_offset) * i_grid.width + (x + x_offset)]

            if i_tile:
                if not p_tile:
                    # Non-transparent tile in image grid is not in pattern grid
                    return False, -1
                n_matches += 1
            else:
                if p_tile:
                    n_unused_tiles += 1

    return (n_matches == i_grid.tile_count), n_unused_tiles



def find_best_pattern(image, transparent_color, pattern_grids, x_offset, y_offset, frame_width, frame_height):
    """
    Search through the `pattern_grids` and find the best pattern for a given frame image.

    Returns tuple (pattern, xpos, ypos)
    """

    if frame_width % 8 != 0 or frame_height % 8 != 0:
        raise ValueError("find_best_pattern only works with frames that are a multiple of 8 in width and height")


    # Convert frame image into a grid of booleans (True if tile is not 100% transparent)
    i_grid_data = [ is_small_tile_not_transparent(image, transparent_color, x, y)
                    for y in range(y_offset, y_offset + frame_height, 8) for x in range(x_offset, x_offset + frame_width, 8) ]
    i_grid = PatternGrid(
                tile_count = sum(i_grid_data),
                width = frame_width // 8,
                height = frame_height // 8,
                data = i_grid_data,
                pattern = None
    )


    best_pattern = None
    best_n_unused_tiles = 0xffff
    best_x = 0
    best_y = 0

    for p_grid in pattern_grids:
        if p_grid.tile_count >= i_grid.tile_count and p_grid.width <= i_grid.width and p_grid.height <= i_grid.height:
            for y in range(0, i_grid.height - p_grid.height + 1):
                for x in range(0, i_grid.width - p_grid.width + 1):
                    valid, n_unused_tiles = test_pattern_grid(p_grid, i_grid, x, y)
                    if valid:
                        if n_unused_tiles < best_n_unused_tiles:
                            best_n_unused_tiles = n_unused_tiles
                            best_pattern = p_grid.pattern
                            best_x = x * 8
                            best_y = y * 8

    if best_pattern is None:
        # No patterns found
        raise ValueError(f"Cannot find pattern for frame at ({ x_offset }, { y_offset }).  (NOTE: Only the first colour in the palette image is considered transparent)")

    return best_pattern, best_x, best_y



def extract_frame(image, pattern, palettes_map, tileset, fs, x, y, x_offset, y_offset):
    data = bytearray()

    data.append(pattern.id)
    data.append(x_offset)
    data.append(y_offset)

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



def build_frameset(fs, ms_export_orders, ms_dir, tiles, palettes_map, transparent_color, pattern_grids):
    frames = dict()

    if fs.pattern:
        base_pattern = ms_export_orders.patterns[fs.pattern]
    else:
        base_pattern = None


    ms_export_orders.shadow_sizes[fs.shadow_size]
    shadow_size = fs.shadow_size

    tile_hitbox = fs.tilehitbox


    image = load_image(ms_dir, fs.source)

    if image.width % fs.frame_width != 0 or image.height % fs.frame_height != 0:
        raise ValueError(f"Source image is not a multiple of frame size: { fs.name }")

    frames_per_row = image.width // fs.frame_width


    all_blocks_use_the_same_pattern = base_pattern is not None


    for block in fs.blocks:
        # ::TODO somehow handle clone blocks::
        # ::TODO somehow handle flipped blocks::

        if block.pattern:
            block_pattern = ms_export_orders.patterns[block.pattern]

            if block.pattern != fs.pattern:
                all_blocks_use_the_same_pattern = False
        else:
            block_pattern = base_pattern


        for i, f in enumerate(block.frames):
            if f in frames:
                raise ValueError(f"Duplicate frame: { f }")

            frame_number = block.start + i
            x = (frame_number % frames_per_row) * fs.frame_width
            y = (frame_number // frames_per_row) * fs.frame_height

            try:
                if block_pattern is None:
                    pattern, pattern_x, pattern_y = find_best_pattern(image, transparent_color, pattern_grids, x, y, fs.frame_width, fs.frame_height)

                    x += pattern_x
                    y += pattern_y
                    x_offset = fs.x_origin - pattern_x
                    y_offset = fs.y_origin - pattern_y
                else:
                    pattern = block_pattern

                    x += block.x
                    y += block.y

                    x_offset = fs.x_origin - block.x
                    y_offset = fs.y_origin - block.y

                frames[f] = extract_frame(image, pattern, palettes_map, tiles, fs, x, y, x_offset, y_offset)
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


    return FrameSet(fs.name, shadow_size, tile_hitbox, pattern_name, fs.ms_export_order, eo_frames)



def build_spritesheet(ms_input, ms_export_orders, ms_dir, palettes_map, transparent_color, pattern_grids):

    tiles = Tileset(ms_input.first_tile, ms_input.end_tile)

    spritesheet = list()

    for fs in ms_input.framesets.values():
        spritesheet.append(
            build_frameset(fs, ms_export_orders, ms_dir, tiles, palettes_map, transparent_color, pattern_grids))

    return spritesheet, tiles.get_tiles()



def load_palette(ms_dir, palette_filename):
    image = load_image(ms_dir, palette_filename)

    if image.width != 16 or image.height != 8:
        raise ValueError('Palette Image MUST BE 16x8 px in size')

    palettes_map = create_palettes_map(image, TILE_DATA_BPP)
    palette_data = convert_palette_image(image)

    return palettes_map, palette_data



def get_transparent_color(palette_data):
    # Hack to reconstruct the first color from palette_data bytes
    return palette_data[0] | (palette_data[1] << 8)



def to_csv(l):
    return ', '.join(map(str, l))



def generate_wiz_data(spritesheet, spritesheet_name, binary_data_path):
    with StringIO() as out:
        out.write("""
import "../../src/memmap";
import "../../src/metasprites";
""")
        out.write(f"in { ROM_BANK } {{")
        out.write("""

namespace ms_framesets {
""")
        out.write(f"namespace { spritesheet_name } {{\n")

        for fs in spritesheet:
            n_frames = len(fs.frames)

            out.write('\n')
            if fs.name != 'Player':
                out.write(f"  const { fs.name } = metasprites.MsFramesetFormat{{\n")

                out.write(f"    shadowSize = metasprites.ShadowSize.{ fs.shadow_size },\n")
                out.write(f"    tileHitbox = [ { to_csv(fs.tile_hitbox) } ],\n")
                out.write(f"    drawFunction = metasprites.drawing_functions.{ fs.pattern } as func,\n")
                out.write(f"    frameTable = @[\n")

                for frame_data in fs.frames:
                    out.write(f"      @[ { frame_data[0] }u8, { to_csv(frame_data[1:]) } ],\n")

                out.write( '    ]\n')
                out.write( '  };\n')

            else:
                # Player frameset
                out.write(f"  namespace { fs.name } {{\n")

                out.write(f"    let shadowSize = metasprites.ShadowSize.{ fs.shadow_size };\n")
                out.write(f"    let tileHitbox = [ { to_csv(fs.tile_hitbox) } ];\n")
                out.write(f"    let drawFunction = metasprites.drawing_functions.{ fs.pattern };\n")
                out.write(f"    const frameTable : [*const u8] = [\n")

                for frame_data in fs.frames:
                    out.write(f"      @[ { frame_data[0] }u8, { to_csv(frame_data[1:]) } ],\n")

                out.write( '    ];\n')
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


    pattern_grids = generate_pattern_grids(ms_export_orders)

    palettes_map, palette_data = load_palette(ms_dir, ms_input.palette)

    transparent_color = get_transparent_color(palette_data)

    spritesheet, tileset = build_spritesheet(ms_input, ms_export_orders, ms_dir, palettes_map, transparent_color, pattern_grids)


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


