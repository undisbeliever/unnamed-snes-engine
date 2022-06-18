#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import json
import os.path
import PIL.Image # type: ignore
import argparse
from io import StringIO

from typing import Callable, Iterable, NamedTuple, Optional, Union

from _common import RomData

from _snes import extract_small_tile, extract_large_tile, split_large_tile, \
            hflip_tile, vflip_tile, hflip_large_tile, vflip_large_tile, \
            create_palettes_map, get_palette_id, convert_palette_image, \
            convert_snes_tileset, is_small_tile_not_transparent, \
            SnesColor, SmallTileData, LargeTileData, PaletteMap

from _json_formats import load_ms_export_order_json, load_metasprites_json, \
                          Name, ScopedName, Filename, MsExportOrder, MsPattern, \
                          Aabb, TileHitbox, MsAnimation, MsFrameset, MsSpritesheet


TILE_DATA_BPP = 4

# MUST match `ShadowSize` enum in `src/metasprites.wiz`
SHADOW_SIZES : dict[str, int] = {
    'NONE':   0,
    'SMALL':  1,
    'MEDIUM': 2,
    'LARGE':  3,
}



class PatternGrid(NamedTuple):
    tile_count      : int
    width           : int
    height          : int
    data            : list[bool]
    pattern         : Optional[MsPattern]


# MsFramesetFormat and MsFsData intermediate
class MsFsEntry(NamedTuple):
    fullname            : ScopedName
    ms_export_order     : Name
    header              : bytes
    pattern             : Name
    frames              : list[bytes]
    animations          : list[bytes]



class Tileset:
    def __init__(self, starting_tile : int, end_tile : int):
        assert(starting_tile < 512)
        assert(end_tile <= 512)
        assert(starting_tile < end_tile)

        # starting_tile must start on a VRAM row
        assert(starting_tile % 0x10 == 0)

        self.starting_tile      : int = starting_tile
        self.max_tiles          : int = end_tile - starting_tile

        self.tiles              : list[Optional[LargeTileData]] = [ None ] * 0x20

        self.large_tile_pos     : int = 0
        self.small_tile_pos     : int = 4
        self.small_tile_offset  : int = 0

        self.small_tiles_map    : dict[SmallTileData, tuple[int, bool, bool]] = dict()
        self.large_tiles_map    : dict[LargeTileData, tuple[int, bool, bool]] = dict()


    def get_tiles(self) -> list[LargeTileData]:
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


    def _allocate_large_tile(self) -> int:
        tile_pos = self.large_tile_pos

        self.large_tile_pos += 2
        if self.large_tile_pos & 0x0f == 0:
            self.large_tile_pos += 0x10

            self.tiles += [ None ] * 0x20

        return tile_pos


    def _allocate_small_tile(self) -> int:
        if self.small_tile_pos >= 4:
            self.small_tile_pos = 0
            self.small_tile_offset = self._allocate_large_tile()

        tile_pos = self.small_tile_offset + self._SMALL_TILE_OFFSETS[self.small_tile_pos]
        self.small_tile_pos += 1

        return tile_pos

    _SMALL_TILE_OFFSETS = [ 0x00, 0x01, 0x10, 0x11 ]


    def add_small_tile(self, tile_data : SmallTileData) -> int:
        assert(len(tile_data) == 64)

        tile_pos = self._allocate_small_tile()

        self.tiles[tile_pos] = tile_data

        return tile_pos + self.starting_tile


    def add_large_tile(self, tile_data : LargeTileData) -> int:
        assert(len(tile_data) == 256)

        tile1, tile2, tile3, tile4 = split_large_tile(tile_data)

        tile_pos = self._allocate_large_tile()

        self.tiles[tile_pos] = tile1
        self.tiles[tile_pos + 0x01] = tile2
        self.tiles[tile_pos + 0x10] = tile3
        self.tiles[tile_pos + 0x11] = tile4

        return tile_pos + self.starting_tile


    def add_or_get_small_tile(self, tile_data : SmallTileData) -> tuple[int, bool, bool]:
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


    def add_or_get_large_tile(self, tile_data : LargeTileData) -> tuple[int, bool, bool]:
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



def generate_pattern_grids(ms_export_orders : MsExportOrder) -> list[PatternGrid]:
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



def test_pattern_grid(p_grid : PatternGrid, i_grid : PatternGrid, x_offset : int, y_offset : int) -> tuple[bool, int]:
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



def find_best_pattern(image : None, transparent_color : SnesColor, pattern_grids : list[PatternGrid], x_offset : int, y_offset : int, frame_width : int, frame_height : int) -> tuple[MsPattern, int, int]:
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



def i8_cast(i : int) -> int:
    if i < 0:
        return 0x100 + i
    return i;


NO_AABB_VALUE = 0x80

def add_i8aabb(data : bytearray, box : Optional[Aabb], fs : MsFrameset) -> None:
    if box is not None:
        if box.x < 0 or box.y < 0 or box.width <= 0 or box.height <= 0:
            raise ValueError(f"AABB box is invalid: { box }")
        x1 = box.x
        x2 = box.x + box.width
        y1 = box.y
        y2 = box.y + box.height
        if x2 > fs.frame_width or y2 > fs.frame_height:
            raise ValueError(f"AABB box out of bounds: { box }")
        x1 = i8_cast(x1 - fs.x_origin)
        x2 = i8_cast(x2 - fs.x_origin)
        y1 = i8_cast(y1 - fs.y_origin)
        y2 = i8_cast(y2 - fs.y_origin)

        if x1 == NO_AABB_VALUE:
            raiseValueError(f"Invalid AABB (x1 cannot be { NO_AABB_VALUE }): { box }")

    else:
        x1 = x2 = y1 = y2 = NO_AABB_VALUE

    data.extend((x1, x2, y1, y2))



def extract_frame(image : PIL.Image.Image, pattern : MsPattern, palettes_map : list[PaletteMap], tileset : Tileset, fs : MsFrameset, x : int, y : int, x_offset : int, y_offset : int, hitbox : Optional[Aabb], hurtbox : Optional[Aabb]) -> bytes:
    data = bytearray()

    add_i8aabb(data, hitbox, fs)
    add_i8aabb(data, hurtbox, fs)

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



def animation_delay__distance(d : Union[float, int]) -> int:
    if d < 0.0 or d >= 16.0:
        raise ValueError(f"Invalid animation frame delay (must be between 0 and 16): { d }")
    return round(d * 16)


ANIMATION_DELAY_FUNCTIONS : dict[str, Callable[[Union[float, int]], int]] = {
    'none':         lambda d : 0,
    'frame':        lambda d : int(d),
    'distance_x':   animation_delay__distance,
    'distance_y':   animation_delay__distance,
    'distance_xy':  animation_delay__distance,
}

# NOTE: If you modify this map, also modify the `AnimationProcessFunctions` in `metasprites.wiz`
ANIMATION_DELAY_IDS : dict[str, int] = {
    'none':        0,
    'frame':       2,
    'distance_x':  4,
    'distance_y':  6,
    'distance_xy': 8,
}


END_OF_LOOPING_ANIMATION_BYTE = 0xff
END_OF_ANIMATION_BYTE = 0xfe

MAX_FRAME_ID = 0xfc


def build_animation_data(ani : MsAnimation, get_frame_id : Callable[[Name], int]) -> bytes:
    if ani.delay_type == 'none' and len(ani.frames) != 1:
        raise ValueError("A 'none' delay type can only contain a single animation frame")

    ani_delay_converter = ANIMATION_DELAY_FUNCTIONS[ani.delay_type]

    ani_data = bytearray()
    ani_data.append(ANIMATION_DELAY_IDS[ani.delay_type])

    if ani.fixed_delay is None:
        for f, d in zip(ani.frames, ani.frame_delays):
            ani_data.append(get_frame_id(f))
            ani_data.append(ani_delay_converter(d))
    else:
        d = ani_delay_converter(ani.fixed_delay)
        for f in ani.frames:
            ani_data.append(get_frame_id(f))
            ani_data.append(d)

    if len(ani.frames) == 1 or ani.loop == False:
        ani_data.append(END_OF_ANIMATION_BYTE)
    else:
        ani_data.append(END_OF_LOOPING_ANIMATION_BYTE)

    return ani_data



def build_frameset(fs : MsFrameset, ms_export_orders : MsExportOrder, ms_dir : Filename, tiles : Tileset, palettes_map : list[PaletteMap], transparent_color : SnesColor, pattern_grids : list[PatternGrid], spritesheet_name : Name) -> MsFsEntry:
    frames      : dict[Name, bytes] = dict()
    animations  : dict[Name, bytes] = dict()

    exported_frames    : list[bytes]     = list()
    exported_frame_ids : dict[Name, int] = dict()

    if fs.pattern:
        base_pattern = ms_export_orders.patterns[fs.pattern]
    else:
        base_pattern = None


    ms_export_orders.shadow_sizes[fs.shadow_size]
    shadow_size = fs.shadow_size

    tile_hitbox = fs.tilehitbox


    image = load_image(ms_dir, fs.source)

    image_hflip : Optional[PIL.Image.Image] = None
    image_vflip : Optional[PIL.Image.Image] = None
    image_hvflip : Optional[PIL.Image.Image] = None

    block_pattern : Optional[MsPattern] = None


    if image.width % fs.frame_width != 0 or image.height % fs.frame_height != 0:
        raise ValueError(f"Source image is not a multiple of frame size: { fs.name }")

    frames_per_row = image.width // fs.frame_width


    all_blocks_use_the_same_pattern = base_pattern is not None


    for block in fs.blocks:
        # ::TODO somehow handle clone blocks::

        if block.pattern:
            block_pattern = ms_export_orders.patterns[block.pattern]

            if block.pattern != fs.pattern:
                all_blocks_use_the_same_pattern = False
        else:
            block_pattern = base_pattern


        if block.flip is None:
            block_image = image

        elif block.flip == 'hflip':
            if image_hflip is None:
                image_hflip = image.transpose(PIL.Image.Transpose.FLIP_LEFT_RIGHT)
            block_image = image_hflip

        elif block.flip == 'vflip':
            if image_vflip is None:
                image_vflip = image.transpose(PIL.Image.Transpose.FLIP_TOP_BOTTOM)
            block_image = image_vflip

        elif block.flip == 'hvflip':
            if image_hvflip is None:
                image_hvflip = image.transpose(PIL.Image.Transpose.ROTATE_180)
            block_image = image_hvflip

        else:
            raise ValueError(f"Unknown flip: { block.flip}")


        for i, frame_name in enumerate(block.frames):
            if frame_name in frames:
                raise ValueError(f"Duplicate frame: { frame_name }")

            frame_number = block.start + i
            x = (frame_number % frames_per_row) * fs.frame_width
            y = (frame_number // frames_per_row) * fs.frame_height


            if block.flip == 'hflip' or block.flip == 'hvflip':
                x = block_image.width - x - fs.frame_width
            elif block.flip == 'vflip' or block.flip == 'hvflip':
                y = block_image.width - y - fs.frame_height


            try:
                if block_pattern is None:
                    pattern, pattern_x, pattern_y = find_best_pattern(block_image, transparent_color, pattern_grids, x, y, fs.frame_width, fs.frame_height)

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

                hitbox = fs.hitbox_overrides.get(frame_name, block.default_hitbox)
                hurtbox = fs.hurtbox_overrides.get(frame_name, block.default_hurtbox)

                frames[frame_name] = extract_frame(block_image, pattern, palettes_map, tiles, fs, x, y, x_offset, y_offset, hitbox, hurtbox)
            except Exception as e:
                raise Exception(f"Error with { fs.name }, frame { frame_name }: { e }")


    def get_frame_id(frame_name : Name) -> int:
        if frame_name in exported_frame_ids:
            return exported_frame_ids[frame_name]
        else:
            if frame_name not in frames:
                raise ValueError(f"Cannot find frame: { frame_name }")

            fid = len(exported_frames)
            if fid > MAX_FRAME_ID:
                raise ValueError('Cannot add frame to animation, too many frames')

            exported_frame_ids[frame_name] = fid
            exported_frames.append(frames[frame_name])
            return fid


    for ani in fs.animations.values():
        assert ani.name not in animations

        try:
            animations[ani.name] = build_animation_data(ani, get_frame_id)
        except Exception as e:
            raise Exception(f"Error with { fs.name }, animation { ani.name }: { e }")


    try:
        export_order = ms_export_orders.animation_lists[fs.ms_export_order]
        eo_animations = [ animations[a] for a in export_order.animations ]
    except Exception as e:
        raise Exception(f"Error with { fs.name }: { e }")


    if all_blocks_use_the_same_pattern:
        pattern_name = fs.pattern
    else:
        pattern_name = "dynamic_pattern"


    unused_frames = frames.keys() - exported_frame_ids.keys()
    if unused_frames:
        print(f"WARNING: Unused MetaSprite frames in { fs.name }: { unused_frames }")

    unused_animations = fs.animations.keys() - export_order.animations
    if unused_animations:
        print(f"WARNING: Unused MetaSprite animations in { fs.name }: { unused_animations }")


    return build_msfs_entry(spritesheet_name, fs.name, fs.ms_export_order, shadow_size, tile_hitbox, pattern_name, exported_frames, eo_animations)



#
# MsFsData
# ========
#



def build_msfs_entry(spritesheet_name : Name, fs_name : Name, ms_export_order : Name, shadow_size : Name, tile_hitbox : TileHitbox, pattern : Name, frames : list[bytes], animations : list[bytes]) -> MsFsEntry:

    header = bytearray().zfill(3)

    header[0] = SHADOW_SIZES[shadow_size]
    header[1] = tile_hitbox[0]
    header[2] = tile_hitbox[1]

    return MsFsEntry(
            fullname = f"{ spritesheet_name }.{ fs_name }",
            ms_export_order = ms_export_order,
            header = header,
            pattern = pattern,
            frames = frames,
            animations = animations
    )



def msfs_entries_to_text(msfs_entries : list[MsFsEntry]) -> str:
    with StringIO() as out:
        for entry in msfs_entries:
            frames = ','.join([ f.hex() for f in entry.frames ])
            animations = ','.join([ a.hex() for a in entry.animations ])

            out.write(f"{entry.fullname} {entry.ms_export_order} {entry.header.hex()} {entry.pattern} {frames} {animations}\n")

        return out.getvalue()



def text_to_msfs_entries(line_iterator : Iterable[str]) -> list[MsFsEntry]:
    out = list()

    for line in line_iterator:
        sep = line.split(' ')

        if len(sep) != 6:
            raise ValueError("Invalid MsFsEntry text format")

        out.append(MsFsEntry(
                fullname = sep[0],
                ms_export_order = sep[1],
                header = bytes.fromhex(sep[2]),
                pattern = sep[3],
                frames = [ bytes.fromhex(i) for i in sep[4].split(',') ],
                animations = [ bytes.fromhex(i) for i in sep[5].split(',') ]
        ))

    return out



def build_ms_fs_data(spritesheets : list[list[MsFsEntry]], symbols : dict[str, int], bank_addr : int, bank_size : int) -> tuple[RomData, dict[ScopedName, tuple[int, Name]]]:
    # Return: tuple(rom_data, dict fs_fullname -> tuple(addr, export_order))

    MS_FRAMESET_FORMAT_SIZE = 9

    rom_data = RomData(bank_addr, bank_size)

    fs_map = dict()

    n_framesets = sum([ len(i) for i in spritesheets ])
    assert(n_framesets > 0)

    fs_table, fs_table_addr = rom_data.allocate(n_framesets * MS_FRAMESET_FORMAT_SIZE)
    fs_pos = 0

    for framesets in spritesheets:
        for fs in framesets:
            fs_addr = fs_table_addr + fs_pos

            frame_table_addr = rom_data.insert_data_addr_table(fs.frames)
            animation_table_addr = rom_data.insert_data_addr_table(fs.animations)

            drawing_function = symbols[f"metasprites.drawing_functions.{ fs.pattern }"] & 0xffff


            fs_table[fs_pos : fs_pos+3] = fs.header

            fs_table[fs_pos + 3] = drawing_function & 0xff
            fs_table[fs_pos + 4] = drawing_function >> 8

            fs_table[fs_pos + 5] = frame_table_addr & 0xff
            fs_table[fs_pos + 6] = frame_table_addr >> 8

            fs_table[fs_pos + 7] = animation_table_addr & 0xff
            fs_table[fs_pos + 8] = animation_table_addr >> 8

            fs_pos += MS_FRAMESET_FORMAT_SIZE

            fs_map[fs.fullname] = (fs_addr, fs.ms_export_order)

    assert(fs_pos == n_framesets * MS_FRAMESET_FORMAT_SIZE)


    # Ensure player data is the first item
    if fs_map['common.Player'][0] != bank_addr:
        raise RuntimeError("The first MetaSprite FrameSet MUST be the player")


    return rom_data, fs_map



#
# Image loaders
# =============
#


def load_palette(ms_dir : Filename, palette_filename : Filename) -> tuple[list[PaletteMap], bytes]:
    image = load_image(ms_dir, palette_filename)

    if image.width != 16 or image.height != 8:
        raise ValueError('Palette Image MUST BE 16x8 px in size')

    palettes_map = create_palettes_map(image, TILE_DATA_BPP)
    palette_data = convert_palette_image(image)

    return palettes_map, palette_data



def get_transparent_color(palette_data : bytes) -> SnesColor:
    # Hack to reconstruct the first color from palette_data bytes
    return palette_data[0] | (palette_data[1] << 8)



def load_image(ms_dir : Filename, filename : Filename) -> PIL.Image.Image:
    image_filename = os.path.join(ms_dir, filename)

    with PIL.Image.open(image_filename) as image:
        image.load()

    if image.mode == 'RGB':
        return image
    else:
        return image.convert('RGB')



#
# =========================
#



def generate_ppu_data(ms_input : MsSpritesheet, tileset : list[SmallTileData], palette_data : bytes) -> bytes:
    tile_data = convert_snes_tileset(tileset, TILE_DATA_BPP)


    data = bytearray()

    # first_tile
    data.append(ms_input.first_tile & 0xff)
    data.append(ms_input.first_tile >> 8)

    # palette_data
    data += palette_data

    # tile_data
    data += tile_data

    return data



def convert_spritesheet(ms_input : MsSpritesheet, ms_export_orders : MsExportOrder, pattern_grids : list[PatternGrid], ms_dir : Filename) -> tuple[bytes, list[MsFsEntry]]:
    # Returns tuple (binary_data, msfs_entries)

    palettes_map, palette_data = load_palette(ms_dir, ms_input.palette)
    transparent_color = get_transparent_color(palette_data)

    tileset = Tileset(ms_input.first_tile, ms_input.end_tile)

    msfs_entries = list()

    for fs in ms_input.framesets.values():
        msfs_entries.append(
                build_frameset(fs, ms_export_orders, ms_dir, tileset, palettes_map, transparent_color, pattern_grids, ms_input.name)
        )

    bin_data = generate_ppu_data(ms_input, tileset.get_tiles(), palette_data)

    return bin_data, msfs_entries



#
# =========================
#


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('--bin-output', required=True,
                        help='binary (PPU) data output file')
    parser.add_argument('--msfs-output', required=True,
                        help='msfs text output file')
    parser.add_argument('json_filename', action='store',
                        help='Sprite map JSON file')
    parser.add_argument('ms_export_order_json_file', action='store',
                        help='metasprite export order map JSON file')

    args = parser.parse_args()

    return args;



def main() -> None:
    args = parse_arguments()

    ms_dir = os.path.dirname(args.json_filename)

    ms_input = load_metasprites_json(args.json_filename)
    ms_export_orders = load_ms_export_order_json(args.ms_export_order_json_file)

    pattern_grids = generate_pattern_grids(ms_export_orders)

    bin_data, msfs_entries = convert_spritesheet(ms_input, ms_export_orders, pattern_grids, ms_dir)

    msfs_text = msfs_entries_to_text(msfs_entries)


    with open(args.bin_output, 'wb') as fp:
        fp.write(bin_data)

    with open(args.msfs_output, 'w') as fp:
        fp.write(msfs_text)



if __name__ == '__main__':
    main()


