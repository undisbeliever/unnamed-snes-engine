#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import json
import sys
import os.path
import PIL.Image # type: ignore
import argparse
from io import StringIO

from typing import overload, Callable, Final, Iterable, Literal, NamedTuple, Optional, TextIO, TypeVar, Union

from _common import RomData, MemoryMapMode, MultilineError, print_error

from _snes import extract_small_tile, extract_large_tile, split_large_tile, \
            hflip_tile, vflip_tile, hflip_large_tile, vflip_large_tile, \
            create_palettes_map, get_palette_id, convert_palette_image, \
            convert_snes_tileset, is_small_tile_not_transparent, \
            SnesColor, SmallTileData, LargeTileData, PaletteMap

from _json_formats import load_ms_export_order_json, load_metasprites_json, \
                          Name, ScopedName, Filename, MsExportOrder, MsPattern, \
                          Aabb, TileHitbox, MsAnimation, MsLayout, MsFrameset, MsSpritesheet, \
                          MsLayoutOverride, AabbOverride


TILE_DATA_BPP = 4

# MUST match `ShadowSize` enum in `src/metasprites.wiz`
SHADOW_SIZES : dict[str, int] = {
    'NONE':   0,
    'SMALL':  1,
    'MEDIUM': 2,
    'LARGE':  3,
}


class TileError(NamedTuple):
    x         : int
    y         : int
    tile_size : Literal[8, 16]


class AnimationError(MultilineError):
    def __init__(self, name : Name, message : str):
        self.name    : Final = name
        self.message : Final = message


    def print_indented(self, fp : TextIO) -> None:
        fp.write(f"    Animation { self.name }: { self.message }\n")


class FrameError(MultilineError):
    def __init__(self, frame_name : Name, message : str, tiles : Optional[list[TileError]] = None):
        self.frame_name : Final = frame_name
        self.message    : Final = message
        self.tiles      : Final = tiles


    def print_indented(self, fp : TextIO) -> None:
        if not self.tiles:
            fp.write(f"    Frame { self.frame_name }: { self.message }\n")
        if self.tiles:
            fp.write(f"    Frame { self.frame_name }: { self.message }:")
            for t in self.tiles:
                fp.write(f" ({t.x:>3},{t.y:>4})x{t.tile_size:<2}")
            fp.write('\n')


class FramesetError(MultilineError):
    def __init__(self, fs_name : Name, errors : Union[str, list[Union[str, FrameError, AnimationError]]]):
        if not isinstance(errors, list):
            errors = [ errors ]

        self.fs_name : Final = fs_name
        self.errors  : Final = errors


    def print_indented(self, fp : TextIO) -> None:
        fp.write(f"  Frameset { self.fs_name }: { len(self.errors) } errors\n")
        for e in self.errors:
            if isinstance(e, str):
                fp.write(f"    { e }\n")
            else:
                e.print_indented(fp)


class SpritesheetError(MultilineError):
    def __init__(self, errors : list[FramesetError]):
        self.errors : Final = errors

    def print_indented(self, fp : TextIO) -> None:
        fp.write(f"{ len(self.errors) } invalid framesets:\n")
        for e in self.errors:
            e.print_indented(fp)



class PatternGrid(NamedTuple):
    tile_count      : int
    width           : int
    height          : int
    data            : list[bool]
    pattern         : Optional[MsPattern]


class FrameLocation(NamedTuple):
    is_clone        : bool
    flip            : Optional[str]
    frame_x         : int
    frame_y         : int
    pattern         : Optional[MsPattern]
    x_offset        : Optional[int]
    y_offset        : Optional[int]
    hitbox          : Optional[Aabb]
    hurtbox         : Optional[Aabb]



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
        assert starting_tile < 512
        assert end_tile <= 512
        assert starting_tile < end_tile

        # starting_tile must start on a VRAM row
        assert starting_tile % 0x10 == 0

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
        assert len(tile_data) == 64

        tile_pos = self._allocate_small_tile()

        self.tiles[tile_pos] = tile_data

        return tile_pos + self.starting_tile


    def add_large_tile(self, tile_data : LargeTileData) -> int:
        assert len(tile_data) == 256

        tile1, tile2, tile3, tile4 = split_large_tile(tile_data)

        tile_pos = self._allocate_large_tile()

        self.tiles[tile_pos] = tile1
        self.tiles[tile_pos + 0x01] = tile2
        self.tiles[tile_pos + 0x10] = tile3
        self.tiles[tile_pos + 0x11] = tile4

        return tile_pos + self.starting_tile


    def add_or_get_small_tile(self, tile_data : SmallTileData) -> tuple[int, bool, bool]:
        assert len(tile_data) == 64

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

    assert frame_width % 8 == 0 and frame_height % 8 == 0

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
            raise ValueError(f"Invalid AABB (x1 cannot be { NO_AABB_VALUE }): { box }")

    else:
        x1 = x2 = y1 = y2 = NO_AABB_VALUE

    data.extend((x1, x2, y1, y2))



def extract_frame(fl : FrameLocation, frame_name : Name, image : PIL.Image.Image, palettes_map : list[PaletteMap], tileset : Tileset, fs : MsFrameset) -> bytes:
    assert fl.x_offset is not None and fl.y_offset is not None

    pattern : Final = fl.pattern
    assert(pattern)

    image_x : Final = fl.frame_x + fl.x_offset
    image_y : Final = fl.frame_y + fl.y_offset

    x_offset : Final = fs.x_origin - fl.x_offset
    y_offset : Final = fs.y_origin - fl.y_offset

    if x_offset < 0 or x_offset >= fs.frame_width or y_offset < 0 or y_offset >= fs.frame_height:
        raise FrameError(frame_name, f"offset is outside frame: { x_offset }, { y_offset }")

    objects_outside_frame = list()
    tiles_with_no_palettes = list()

    data = bytearray()

    add_i8aabb(data, fl.hitbox, fs)
    add_i8aabb(data, fl.hurtbox, fs)

    data.append(pattern.id)
    data.append(x_offset)
    data.append(y_offset)

    for o in pattern.objects:
        tile_id, hflip, vflip = 0, False, False

        x = image_x + o.xpos
        y = image_y + o.ypos

        if o.xpos < 0 or o.xpos > fs.frame_width or o.ypos < 0 or o.ypos > fs.frame_height:
            objects_outside_frame.append(TileError(x, y, o.size))
            continue

        if o.size == 8:
            tile = extract_small_tile(image, x, y)
            palette_id, pal_map = get_palette_id(tile, palettes_map)
            if pal_map:
                tile_data = bytes([pal_map[c] for c in tile])
                tile_id, hflip, vflip = tileset.add_or_get_small_tile(tile_data)
            else:
                tiles_with_no_palettes.append(TileError(x, y, 8))
                palette_id = 0
        else:
            tile = extract_large_tile(image, x, y)
            palette_id, pal_map = get_palette_id(tile, palettes_map)
            if pal_map:
                tile_data = bytes([pal_map[c] for c in tile])
                tile_id, hflip, vflip = tileset.add_or_get_large_tile(tile_data)
            else:
                tiles_with_no_palettes.append(TileError(x, y, 16))
                palette_id = 0

        assert tile_id < 512
        assert palette_id is not None

        data.append(tile_id & 0xff)
        data.append((tile_id >> 8)
                    | ((palette_id & 7) << 1)
                    | ((fs.order & 3) << 4)
                    | (bool(hflip) << 6)
                    | (bool(vflip) << 7)
        )

    if objects_outside_frame:
        raise FrameError(frame_name, 'Objects outside frame', objects_outside_frame)

    if tiles_with_no_palettes:
        raise FrameError(frame_name, 'Cannot find palette for object tiles', tiles_with_no_palettes)

    return data



def build_frame_data(frame_locations : dict[Name, FrameLocation], fs : MsFrameset, image : PIL.Image.Image, tiles : Tileset, palettes_map : list[PaletteMap], transparent_color : SnesColor, pattern_grids : list[PatternGrid]) -> tuple[dict[Name, bytes], set[Name]]:
    errors : list[Union[str, FrameError, AnimationError]] = list()

    image_hflip : Optional[PIL.Image.Image] = None
    image_vflip : Optional[PIL.Image.Image] = None
    image_hvflip : Optional[PIL.Image.Image] = None

    frames      : dict[Name, bytes] = dict()
    patterns_used : set[Name] = set()

    for frame_name, fl in frame_locations.items():
        frame_image = None
        if not fl.flip:
            frame_image = image
        elif fl.flip == 'hflip':
            if image_hflip is None:
                image_hflip = image.transpose(PIL.Image.Transpose.FLIP_LEFT_RIGHT)
            frame_image = image_hflip

        elif fl.flip == 'vflip':
            if image_vflip is None:
                image_vflip = image.transpose(PIL.Image.Transpose.FLIP_TOP_BOTTOM)
            frame_image = image_vflip

        elif fl.flip == 'hvflip':
            if image_hvflip is None:
                image_hvflip = image.transpose(PIL.Image.Transpose.ROTATE_180)
            frame_image = image_hvflip
        else:
            errors.append(f"Unknown flip { fl.flip }")
            continue

        try:
            if fl.pattern is None:
                pattern, px, py = find_best_pattern(frame_image, transparent_color, pattern_grids, fl.frame_x, fl.frame_y, fs.frame_width, fs.frame_height)
                fl = fl._replace(pattern=pattern, x_offset=px, y_offset=py)

            assert fl.pattern
            patterns_used.add(fl.pattern.name)

            frames[frame_name] = extract_frame(fl, frame_name, frame_image, palettes_map, tiles, fs)

        except FrameError as e:
            errors.append(e)
        except ValueError as e:
            errors.append(FrameError(frame_name, str(e)))

    if errors:
        raise FramesetError(fs.name, errors)

    return frames, patterns_used



def flip_optional_aabb(aabb : Optional[Aabb], flip : Optional[str], fs : MsFrameset) -> Optional[Aabb]:
    if not aabb or not flip:
        return None

    x = aabb.x
    y = aabb.y

    if flip == 'hflip' or flip == 'hvflip':
        x = 2 * fs.x_origin - aabb.x - aabb.width
    if flip == 'vflip' or flip == 'hvflip':
        y = 2 * fs.y_origin - aabb.y - aabb.height

    return Aabb(x, y, aabb.width, aabb.height)



def clone_frame_location(fl : FrameLocation, flip : Optional[str], fs : MsFrameset, image_width : int, image_height : int) -> FrameLocation:
    if not flip:
        return fl

    assert fl.flip is None

    frame_x = fl.frame_x
    frame_y = fl.frame_y

    if flip == 'hflip' or flip == 'hvflip':
        frame_x = image_width - frame_x - fs.frame_width
    if flip == 'vflip' or flip == 'hvflip':
        frame_y = image_height - frame_y - fs.frame_height

    # ::TODO test if pattern is symmetrical::
    # ::TODO update x_offset/y_offset::
    return FrameLocation(
            is_clone = True,
            flip = flip,
            frame_x = frame_x,
            frame_y = frame_y,
            pattern = fl.pattern,
            x_offset = fl.x_offset,
            y_offset = fl.y_offset,
            hitbox = flip_optional_aabb(fl.hitbox, flip, fs),
            hurtbox = flip_optional_aabb(fl.hurtbox, flip, fs),
    )


T = TypeVar('T', Aabb, MsLayout)

def build_override_table(olist : Union[list[AabbOverride], list[MsLayoutOverride]], default_value : Optional[T], fs : MsFrameset, errors : list[Union[str, FrameError, AnimationError]]) -> list[Optional[T]]:
    out = [ default_value ] * len(fs.frames)

    for o in olist:
        if o.end:
            try:
                start = fs.frames.index(o.start)
                end = fs.frames.index(o.end)
                for i in range(start, end + 1):
                    out[i] = o.value  # type: ignore
            except ValueError:
                errors.append(f"Cannot find frames in override range: {o.start} - {o.end}")
        else:
            try:
                i = fs.frames.index(o.start)
                out[i] = o.value  # type: ignore
            except ValueError:
                errors.append(f"Cannot find frame: {o.start}")

    return out


# Using `image_width` and `image_height` instead of an `PIL.Image.Image` argument so I
# can call this function with either a PIL image and a `tk.PhotoImage` image.
def extract_frame_locations(fs : MsFrameset, ms_export_orders : MsExportOrder, image_width : int, image_height : int) -> dict[Name, FrameLocation]:
    errors : list[Union[str, FrameError, AnimationError]] = list()

    frame_locations = dict[Name, FrameLocation]()

    if fs.frame_width < 0 or fs.frame_height < 0:
        errors.append(f"Invalid frame size: { fs.frame_width } x { fs.frame_height }")

    if fs.frame_width >= 256 or fs.frame_height >= 256:
        errors.append(f"Frame size is too large: { fs.frame_width } x { fs.frame_height }")

    if image_width % fs.frame_width != 0 or image_height % fs.frame_height != 0:
        errors.append('Source image is not a multiple of frame size')

    if fs.frame_width % 8 != 0 or fs.frame_height % 8 != 0:
        errors.append("find_best_pattern only works with frames that are a multiple of 8 in width and height")

    if fs.x_origin < 0 or fs.x_origin >= fs.frame_width or fs.y_origin < 0 or fs.y_origin >= fs.frame_height:
        errors.append(f"Origin is outside frame: { fs.x_origin }, { fs.y_origin }")

    layouts = build_override_table(fs.layout_overrides, fs.default_layout, fs, errors)
    hitboxes = build_override_table(fs.hitbox_overrides, fs.default_hitbox, fs, errors)
    hurtboxes = build_override_table(fs.hurtbox_overrides, fs.default_hurtbox, fs, errors)

    if errors:
        raise FramesetError(fs.name, errors)

    frames_per_row : Final = image_width // fs.frame_width

    for frame_number, frame_name in enumerate(fs.frames):
        if frame_name in frame_locations:
            errors.append(f"Duplicate frame name: { frame_name }")

        frame_x = (frame_number % frames_per_row) * fs.frame_width
        frame_y = (frame_number // frames_per_row) * fs.frame_height

        pattern = None
        x_offset = None
        y_offset = None
        layout = layouts[frame_number]
        if layout:
            pattern = ms_export_orders.patterns[layout.pattern]
            x_offset = layout.x_offset
            y_offset = layout.y_offset

        frame_locations[frame_name] = FrameLocation(
                is_clone = False,
                flip = None,
                frame_x = frame_x,
                frame_y = frame_y,
                pattern = pattern,
                x_offset = x_offset,
                y_offset = y_offset,
                hitbox = hitboxes[frame_number],
                hurtbox = hurtboxes[frame_number],
        )


    #
    # Process cloned frames
    for c in fs.clones:
        if c.name in frame_locations:
            errors.append(f"Duplicate cloned frame name: { c.name }")
        else:
            source = frame_locations.get(c.source)
            if not source:
                errors.append(f"Cannot clone frame: { c.name } { c.source }")
            elif source.flip is not None:
                errors.append(f"Cannot clone a flipped frame: { c.name } { c.source }")
            else:
                frame_locations[c.name] = clone_frame_location(source, c.flip, fs, image_width, image_height)

    if errors:
        raise FramesetError(fs.name, errors)

    return frame_locations


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
LOOPING_ANIMATION_DELAY_IDS : Final[dict[str, int]] = {
    'none':        0,
    'frame':       2,
    'distance_x':  4,
    'distance_y':  6,
    'distance_xy': 8,
}

# NOTE: If you modify this map, also modify the `AnimationProcessFunctions` in `metasprites.wiz`
NON_LOOPING_ANIMATION_DELAY_IDS : Final[dict[str, int]] = {
    'none':         0,
    'frame':       24,
    'distance_x':  26,
    'distance_y':  28,
    'distance_xy': 30,
}


END_OF_ANIMATION_BYTE = 0xff

MAX_FRAME_ID = 0xfc
MAX_N_FRAMES = MAX_FRAME_ID + 1

MAX_N_ANIMATIONS = 0xff


def build_animation_data(ani : MsAnimation, get_frame_id : Callable[[Name], int]) -> bytes:
    if ani.delay_type == 'none' and len(ani.frames) != 1:
        raise ValueError('A \'none\' delay type can only contain a single animation frame')

    ani_delay_converter = ANIMATION_DELAY_FUNCTIONS[ani.delay_type]

    if ani.loop:
        if len(ani.frames) == 1:
            # Do not process looping animations that have a single frame
            process_function = LOOPING_ANIMATION_DELAY_IDS['none']
        else:
            process_function = LOOPING_ANIMATION_DELAY_IDS[ani.delay_type]
    else:
        # Non-Looping animations can be used as timers.  Do not test for 1-frame animations.
        process_function = NON_LOOPING_ANIMATION_DELAY_IDS[ani.delay_type]


    ani_data = bytearray()
    ani_data.append(process_function)

    if ani.fixed_delay is None:
        assert ani.frame_delays is not None
        assert len(ani.frame_delays) == len(ani.frames)

        for f, d in zip(ani.frames, ani.frame_delays):
            ani_data.append(get_frame_id(f))
            ani_data.append(ani_delay_converter(d))
    else:
        d = ani_delay_converter(ani.fixed_delay)
        for f in ani.frames:
            ani_data.append(get_frame_id(f))
            ani_data.append(d)

    ani_data.append(END_OF_ANIMATION_BYTE)

    return ani_data



def build_frameset(fs : MsFrameset, ms_export_orders : MsExportOrder, ms_dir : Filename, tiles : Tileset, palettes_map : list[PaletteMap], transparent_color : SnesColor, pattern_grids : list[PatternGrid], spritesheet_name : Name) -> MsFsEntry:
    errors : list[Union[str,FrameError,AnimationError]] = list()

    image = load_image(ms_dir, fs.source)

    frame_locations = extract_frame_locations(fs, ms_export_orders, image.width, image.height)

    frames, patterns_used = build_frame_data(frame_locations, fs, image, tiles, palettes_map, transparent_color, pattern_grids)
    animations  : dict[Name, bytes] = dict()

    exported_frames    : list[bytes]     = list()
    exported_frame_ids : dict[Name, int] = dict()


    ms_export_orders.shadow_sizes[fs.shadow_size]
    shadow_size = fs.shadow_size

    tile_hitbox = fs.tilehitbox
    if tile_hitbox.half_width >= 128 or tile_hitbox.half_height >= 128:
        errors.append(f"Tile hitbox is too large: { tile_hitbox.half_width }, { tile_hitbox.half_height }")

    export_order = ms_export_orders.animation_lists.get(fs.ms_export_order)
    if export_order is None:
        errors.append(f"Unknown export order: { fs.ms_export_order }")

    if errors:
        raise FramesetError(fs.name, errors)


    # Confirm all frames have been processed
    assert len(frames) == len(fs.frames) + len(fs.clones)

    for ani in fs.animations.values():
        assert ani.name not in animations

        def get_frame_id(frame_name : Name) -> int:
            if frame_name in exported_frame_ids:
                return exported_frame_ids[frame_name]
            else:
                if frame_name not in frames:
                    errors.append(AnimationError(ani.name, f"Cannot find frame: { frame_name }"))

                fid = len(exported_frames)
                if fid > MAX_FRAME_ID:
                    # MAX_FRAME_ID exception will be raised after all the frames have been processed
                    return 0

                exported_frame_ids[frame_name] = fid
                exported_frames.append(frames[frame_name])
                return fid

        try:
            animations[ani.name] = build_animation_data(ani, get_frame_id)
        except ValueError as e:
            errors.append(AnimationError(ani.name, str(e)))

    if errors:
        raise FramesetError(fs.name, errors)


    if len(exported_frames) > MAX_N_FRAMES:
        errors.append(f"Too many frames ({ len(exported_frames) }, max: { MAX_N_FRAMES })")

    if len(animations) > MAX_N_ANIMATIONS:
        errors.append(f"Too many animations ({ len(exported_frames) }, max: { MAX_N_ANIMATIONS})")


    assert export_order

    eo_animations : list[bytes] = list()
    for ea_name in export_order.animations:
        a = animations.get(ea_name)
        if a:
            eo_animations.append(a)
        else:
            errors.append(f"Cannot find animation: { ea_name }")

    if errors:
        raise FramesetError(fs.name, errors)


    if len(patterns_used) == 1:
        pattern_name = next(iter(patterns_used))
    else:
        pattern_name = "dynamic_pattern"


    unused_frames = frames.keys() - exported_frame_ids.keys()
    if unused_frames:
        # ::TODO do something about this (not thread safe)::
        print(f"WARNING: Unused MetaSprite frames in { fs.name }: { unused_frames }")

    unused_animations = fs.animations.keys() - export_order.animations
    if unused_animations:
        # ::TODO do something about this (not thread safe)::
        print(f"WARNING: Unused MetaSprite animations in { fs.name }: { unused_animations }")


    assert not errors

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



def build_ms_fs_data(spritesheets : list[list[MsFsEntry]], symbols : dict[str, int], mapmode : MemoryMapMode) -> tuple[RomData, dict[ScopedName, tuple[int, Name]]]:
    # Return: tuple(rom_data, dict fs_fullname -> tuple(addr, export_order))

    MS_FRAMESET_FORMAT_SIZE = 9

    rom_data = RomData(mapmode.bank_start, mapmode.bank_size)

    fs_map = dict()

    n_framesets = sum([ len(i) for i in spritesheets ])
    assert n_framesets > 0

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

    assert fs_pos == n_framesets * MS_FRAMESET_FORMAT_SIZE


    # Ensure player data is the first item
    if fs_map['common.Player'][0] != mapmode.bank_start:
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
    errors : list[FramesetError] = list()

    for fs in ms_input.framesets.values():
        try:
            msfs_entries.append(
                    build_frameset(fs, ms_export_orders, ms_dir, tileset, palettes_map, transparent_color, pattern_grids, ms_input.name)
            )
        except FramesetError as e:
            errors.append(e)
        except Exception as e:
            errors.append(FramesetError(fs.name, f"{ type(e).__name__ }({ e })"))

    if errors:
        raise SpritesheetError(errors)

    bin_data = generate_ppu_data(ms_input, tileset.get_tiles(), palette_data)

    assert not errors

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
    try:
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

    except Exception as e:
        print_error('ERROR', e)
        sys.exit("Error compiling MetaSprite spritesheet")


if __name__ == '__main__':
    main()


