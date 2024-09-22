#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import os.path

from collections import OrderedDict
from typing import Callable, Final, Literal, NamedTuple, Optional, TextIO, TypeVar, Union

from .data_store import EngineData, FixedSizedData, DynamicSizedData, DataStore
from .memory_map import MemoryMapMode
from .errors import MultilineError
from .snes import (
    split_large_tile,
    load_image_tile_extractor,
    ImageTileExtractor,
    hflip_tile,
    vflip_tile,
    hflip_large_tile,
    vflip_large_tile,
    convert_snes_tileset,
    SnesColor,
    PaletteMap,
    SmallTileData,
    LargeTileData,
)

from .json_formats import (
    Name,
    ScopedName,
    Filename,
    MsExportOrder,
    MsPattern,
    Aabb,
    TileHitbox,
    MseoDynamicMsFsSettings,
    MsAnimation,
    MsLayout,
    MsFrameset,
    MsPaletteSwap,
    MsSpritesheet,
    MsLayoutOverride,
    AabbOverride,
)


TILE_DATA_BPP = 4
BLANK_SMALL_TILE: Final = bytes(64)

# MUST match `ShadowSize` enum in `src/metasprites.wiz`
SHADOW_SIZES: dict[str, int] = {
    "NONE": 0,
    "SMALL": 1,
    "MEDIUM": 2,
    "LARGE": 3,
}


class TileError(NamedTuple):
    x: int
    y: int
    tile_size: Literal[8, 16]


class AnimationError(MultilineError):
    def __init__(self, name: Name, message: str):
        self.name: Final = name
        self.message: Final = message

    def print_indented(self, fp: TextIO) -> None:
        fp.write(f"    Animation { self.name }: { self.message }\n")


class FrameError(MultilineError):
    def __init__(self, frame_name: Name, message: str, tiles: Optional[list[TileError]] = None):
        self.frame_name: Final = frame_name
        self.message: Final = message
        self.tiles: Final = tiles

    def print_indented(self, fp: TextIO) -> None:
        if not self.tiles:
            fp.write(f"    Frame { self.frame_name }: { self.message }\n")
        if self.tiles:
            fp.write(f"    Frame { self.frame_name }: { self.message }:")
            for t in self.tiles:
                fp.write(f" ({t.x:>3},{t.y:>4})x{t.tile_size:<2}")
            fp.write("\n")


class FramesetError(MultilineError):
    def __init__(self, fs: MsFrameset, errors: Union[str, list[Union[str, FrameError, AnimationError]]]):
        if not isinstance(errors, list):
            errors = [errors]

        self.name: Final = fs.name
        self.image_fn: Final = fs.source
        self.errors: Final = errors

        self.tiles: Final[set[TileError]] = set()
        for e in errors:
            if isinstance(e, FrameError):
                if e.tiles:
                    self.tiles.update(e.tiles)

    def print_indented(self, fp: TextIO) -> None:
        fp.write(f"  Frameset { self.name }: { len(self.errors) } errors\n")
        if self.tiles:
            fp.write(f"    { len(self.tiles) } tile errors in { self.image_fn }\n")
        for e in self.errors:
            if isinstance(e, str):
                fp.write(f"    { e }\n")
            else:
                e.print_indented(fp)


class PaletteSwapError(MultilineError):
    def __init__(self, ps: MsPaletteSwap, message: str):
        self.name: Final = ps.name
        self.message: Final = message

    def print_indented(self, fp: TextIO) -> None:
        fp.write(f"  Palette Swap { self.name }: { self.message }\n")


class SpritesheetError(MultilineError):
    def __init__(self, errors: list[FramesetError | PaletteSwapError], ms_dir: str):
        self.errors: Final = errors
        self.ms_dir: Final = ms_dir

    def print_indented(self, fp: TextIO) -> None:
        fp.write(f"{ len(self.errors) } errors:\n")
        for e in self.errors:
            e.print_indented(fp)


# 16 bit Address
WordAddr = int

SmallOrLargeTileData = bytes

EngineAabb = tuple[int, int, int, int]


class FrameLocation(NamedTuple):
    is_clone: bool
    flip: Optional[str]
    frame_x: int
    frame_y: int
    pattern: MsPattern
    x_offset: Optional[int]
    y_offset: Optional[int]
    hitbox: Optional[Aabb]
    hurtbox: Optional[Aabb]


class ObjectTile(NamedTuple):
    tile_data: SmallOrLargeTileData
    palette_id: int

    def is_large_tile(self) -> bool:
        return len(self.tile_data) == 16 * 16


class FrameData(NamedTuple):
    name: Name
    hitbox: EngineAabb
    hurtbox: EngineAabb
    pattern: MsPattern
    x_offset: int
    y_offset: int
    order: int
    objects: list[ObjectTile]


class FramesetData(NamedTuple):
    name: Name
    frameset: MsFrameset
    ms_export_order: Name
    shadow_size: Name
    tile_hitbox: TileHitbox
    pattern: Optional[Name]
    frames: list[FrameData]
    # engine animation data
    animations: list[bytes]


class PaletteSwapData(NamedTuple):
    name: Name
    fs_data: FramesetData
    palette: int


class TileIdAndFlip(NamedTuple):
    tile_id: int
    hflip: bool
    vflip: bool

    def new_hflip(self) -> "TileIdAndFlip":
        return TileIdAndFlip(self.tile_id, not self.hflip, self.vflip)

    def new_vflip(self) -> "TileIdAndFlip":
        return TileIdAndFlip(self.tile_id, self.hflip, not self.vflip)

    def new_hvflip(self) -> "TileIdAndFlip":
        return TileIdAndFlip(self.tile_id, not self.hflip, not self.vflip)


# MsFramesetFormat and MsFsData intermediate
class MsFsEntry(NamedTuple):
    fullname: ScopedName
    ms_export_order: Name
    header: bytes
    pattern: Optional[Name]

    # `int` is an offset into `bytes` that points to the start of the MsDataFormat data
    #   0 for static tileset frames
    #   2*nTiles for dynamic tile frames
    #
    # (data, offset)
    frames: list[tuple[bytes, int]]

    animations: list[bytes]


class DynamicMsSpritesheet(NamedTuple):
    tile_data: bytes
    msfs_entries: list[MsFsEntry]


#
# Static Tileset
# ==============
#

SMALL_TILE_OFFSETS: Final = (0x00, 0x01, 0x10, 0x11)


def validate_start_and_end_tile(starting_tile_id: int, end_tile_id: int) -> None:
    assert starting_tile_id < 512
    assert end_tile_id <= 512
    assert starting_tile_id < end_tile_id

    assert starting_tile_id & 0x01 == 0, "starting_tile is not on an even row"
    assert starting_tile_id & 0x10 == 0, "starting_tile is not on an even column"

    assert end_tile_id & 0x01 == 0, "end_tile is not on an even row"
    assert end_tile_id & 0x10 == 0, "end_tile is not on an even column"


class StaticTileset:
    def __init__(self, starting_tile_id: int, end_tile_id: int):
        validate_start_and_end_tile(starting_tile_id, end_tile_id)
        self.starting_tile_id: Final = starting_tile_id
        self.end_tile_id: Final = end_tile_id

        # Offset between a tile pos and a SNES OAM tile_id
        self.tile_id_offset: Final = starting_tile_id & ~0x1F

        # This list is always incremented 2-rows at a time.
        # This greatly simplifies the `add_small_tile()` code
        self.tiles: list[Optional[LargeTileData]] = [None] * 0x20

        self.large_tile_pos: int = starting_tile_id & 0x1F

        self.small_tile_pos: int = 4
        self.small_tile_offset: int = 0

        self._tile_map: dict[SmallOrLargeTileData, TileIdAndFlip] = dict()

        self.first_large_tile_pos: Final = self.large_tile_pos

    def tile_map(self) -> dict[SmallOrLargeTileData, TileIdAndFlip]:
        return self._tile_map

    def get_tiles(self) -> list[SmallTileData]:
        if self.large_tile_pos == self.first_large_tile_pos:
            raise ValueError("No tiles in tileset")

        large_tile_id = self.large_tile_pos + self.tile_id_offset
        if large_tile_id > self.end_tile_id:
            raise ValueError(f"Too many tiles: current tile_id = { large_tile_id }, end_tile = { self.end_tile_id }")

        # Shrink tiles
        end_tile = 0
        for i, t in enumerate(self.tiles):
            if t is not None:
                end_tile = i
        n_tiles = end_tile + 1

        # Skip tiles before `self.starting_tile_id`
        flt: Final = self.first_large_tile_pos
        if flt > 0:
            tiles = self.tiles[flt:0x10] + self.tiles[flt + 0x10 : n_tiles]
        else:
            tiles = self.tiles

        # Replace unused tiles with blank data
        blank_tile = bytearray(64)
        return [blank_tile if t is None else t for t in tiles]

    def _allocate_large_tile(self) -> int:
        tile_pos = self.large_tile_pos

        self.large_tile_pos += 2
        if self.large_tile_pos & 0x0F == 0:
            self.large_tile_pos += 0x10

            self.tiles += [None] * 0x20

        return tile_pos

    def _allocate_small_tile(self) -> int:
        if self.small_tile_pos >= 4:
            self.small_tile_pos = 0
            self.small_tile_offset = self._allocate_large_tile()

        tile_pos = self.small_tile_offset + SMALL_TILE_OFFSETS[self.small_tile_pos]
        self.small_tile_pos += 1

        return tile_pos

    def _new_small_tile(self, tile_data: SmallTileData) -> int:
        assert len(tile_data) == 64

        tile_pos = self._allocate_small_tile()

        self.tiles[tile_pos] = tile_data

        return tile_pos + self.tile_id_offset

    def _new_large_tile(self, small_tiles: tuple[SmallTileData, SmallTileData, SmallTileData, SmallTileData]) -> int:
        assert all(len(st) == 64 for st in small_tiles)

        tile_pos = self._allocate_large_tile()

        self.tiles[tile_pos] = small_tiles[0]
        self.tiles[tile_pos + 0x01] = small_tiles[1]
        self.tiles[tile_pos + 0x10] = small_tiles[2]
        self.tiles[tile_pos + 0x11] = small_tiles[3]

        return tile_pos + self.tile_id_offset

    def add_small_tile(self, tile_data: SmallTileData) -> None:
        assert len(tile_data) == 64

        if tile_data not in self._tile_map:
            tile_id = self._new_small_tile(tile_data)

            h_tile_data = hflip_tile(tile_data)
            v_tile_data = vflip_tile(tile_data)
            hv_tile_data = vflip_tile(h_tile_data)

            self._tile_map[tile_data] = TileIdAndFlip(tile_id, False, False)
            self._tile_map.setdefault(h_tile_data, TileIdAndFlip(tile_id, True, False))
            self._tile_map.setdefault(v_tile_data, TileIdAndFlip(tile_id, False, True))
            self._tile_map.setdefault(hv_tile_data, TileIdAndFlip(tile_id, True, True))

    def add_large_tile(self, tile_data: LargeTileData) -> None:
        assert len(tile_data) == 256

        if tile_data not in self._tile_map:
            small_tiles = split_large_tile(tile_data)

            tile_id = self._new_large_tile(small_tiles)

            h_tile_data = hflip_large_tile(tile_data)
            v_tile_data = vflip_large_tile(tile_data)
            hv_tile_data = vflip_large_tile(h_tile_data)

            self._tile_map[tile_data] = TileIdAndFlip(tile_id, False, False)
            self._tile_map.setdefault(h_tile_data, TileIdAndFlip(tile_id, True, False))
            self._tile_map.setdefault(v_tile_data, TileIdAndFlip(tile_id, False, True))
            self._tile_map.setdefault(hv_tile_data, TileIdAndFlip(tile_id, True, True))

            for i, st in enumerate(small_tiles):
                small_tile_id = tile_id + SMALL_TILE_OFFSETS[i]

                h_st = hflip_tile(st)
                v_st = vflip_tile(st)
                hv_st = vflip_tile(h_st)

                self._tile_map[st] = TileIdAndFlip(small_tile_id, False, False)
                self._tile_map.setdefault(h_st, TileIdAndFlip(small_tile_id, True, False))
                self._tile_map.setdefault(v_st, TileIdAndFlip(small_tile_id, False, True))
                self._tile_map.setdefault(hv_st, TileIdAndFlip(small_tile_id, True, True))


def build_static_tileset(
    framesets: OrderedDict[Name, FramesetData], ms_input: MsSpritesheet
) -> tuple[list[SmallTileData], dict[SmallOrLargeTileData, TileIdAndFlip]]:
    tileset = StaticTileset(ms_input.first_tile, ms_input.end_tile)

    # Process the small tiles after the large tiles have been added to the tileset.
    # This will deduplicate the small tiles that exist in large tiles.
    small_tiles = list()

    for fs in framesets.values():
        for f in fs.frames:
            for t in f.objects:
                if t.is_large_tile():
                    tileset.add_large_tile(t.tile_data)
                else:
                    small_tiles.append(t.tile_data)

    for st in small_tiles:
        tileset.add_small_tile(st)

    return (tileset.get_tiles(), tileset.tile_map())


#
# Dynamic Tiles
# =============
#


# Validated dynamic MetaSprite frameset tile settings
class DynamicFsTileSettings(NamedTuple):
    first_tile_id: int
    n_large_tiles: int


def build_dynamic_tile_settings(dtl: MseoDynamicMsFsSettings) -> DynamicFsTileSettings:
    end_tile_id = dtl.first_tile_id + dtl.n_large_tiles * 2

    if dtl.first_tile_id // 16 != end_tile_id // 16:
        raise RuntimeError("Only a single row of dynamic MetaSprite tiles is supported")

    validate_start_and_end_tile(dtl.first_tile_id, end_tile_id)

    return DynamicFsTileSettings(
        first_tile_id=dtl.first_tile_id,
        n_large_tiles=dtl.n_large_tiles,
    )


# The dynamic MetaSprite tiles stores in the ROM (separate from the tiles used in a frame)
# Tiles are stored in the start of a memory bank and only use 16 bit addresses.
class DynamicTileStore:
    def __init__(self, map_mode: MemoryMapMode):
        self.start_addr: Final = map_mode.bank_start
        self.max_tiles: Final = map_mode.bank_size / 128

        # Tiles are stored in tile16 format (top-left, top-right, bottom-left, bottom-right) order.
        self._tiles: Final[list[SmallTileData]] = list()

        # Tile map is (tile_addr, hflip, vflip)
        self._tile_addr_map: Final[dict[LargeTileData, tuple[WordAddr, bool, bool]]] = dict()

    def tile_data(self) -> list[SmallTileData]:
        assert len(self._tiles) % 4 == 0

        if len(self._tiles) > self.max_tiles:
            raise ValueError(f"Too many dynamic metasprite tiles ({len(self._tiles)}, max {self.max_tiles})")

        return self._tiles

    def tile_map(self) -> dict[LargeTileData, tuple[WordAddr, bool, bool]]:
        return self._tile_addr_map

    def get_or_add_large_tile(self, large_tile: LargeTileData) -> tuple[WordAddr, bool, bool]:
        assert len(large_tile) == 256

        out = self._tile_addr_map.get(large_tile)
        if out is None:
            tile_addr: Final = self.start_addr + len(self._tiles) * 32
            small_tiles: Final = split_large_tile(large_tile)

            assert len(self._tiles) % 4 == 0
            self._tiles.extend(small_tiles)

            h_tile_data = hflip_large_tile(large_tile)
            v_tile_data = vflip_large_tile(large_tile)
            hv_tile_data = vflip_large_tile(h_tile_data)

            out = (tile_addr, False, False)
            self._tile_addr_map[large_tile] = out
            self._tile_addr_map.setdefault(h_tile_data, (tile_addr, True, False))
            self._tile_addr_map.setdefault(v_tile_data, (tile_addr, False, True))
            self._tile_addr_map.setdefault(hv_tile_data, (tile_addr, True, True))
        return out

    def add_small_tiles(self, small_tiles: list[SmallTileData]) -> WordAddr:
        tile_addr: Final = self.start_addr + len(self._tiles) * 32

        assert len(small_tiles) == 4
        self._tiles.extend(small_tiles)

        return tile_addr


class DynamicFrameTiles:
    def __init__(self, settings: DynamicFsTileSettings, tile_store: DynamicTileStore):
        self.settings: Final = settings

        self._tile_store: Final = tile_store

        self._pending_small_tiles: Final[list[SmallTileData]] = list()
        self._n_small_tiles = 0

        self._tile16_addresses: Final[list[WordAddr]] = list()
        self._tile_map: Final[dict[SmallOrLargeTileData, TileIdAndFlip]] = dict()

    def tile_map(self) -> dict[SmallOrLargeTileData, TileIdAndFlip]:
        return self._tile_map

    def tile_addresses(self) -> list[WordAddr]:
        if len(self._tile16_addresses) > self.settings.n_large_tiles:
            raise RuntimeError(f"Too many tile16 tiles: {len(self._tile16_addresses)}, max {self.settings.n_large_tiles}")

        return self._tile16_addresses

    def add_large_tile(self, large_tile: LargeTileData) -> None:
        assert self._n_small_tiles == 0

        if large_tile not in self._tile_map:
            tile_id: Final = self.settings.first_tile_id + len(self._tile16_addresses) * 2

            tile_addr, hflip, vflip = self._tile_store.get_or_add_large_tile(large_tile)
            self._tile16_addresses.append(tile_addr)

            h_large_tile = hflip_large_tile(large_tile)
            v_large_tile = vflip_large_tile(large_tile)
            hv_large_tile = vflip_large_tile(h_large_tile)

            out = TileIdAndFlip(tile_id, hflip, vflip)
            self._tile_map[large_tile] = out
            self._tile_map.setdefault(h_large_tile, out.new_hflip())
            self._tile_map.setdefault(v_large_tile, out.new_vflip())
            self._tile_map.setdefault(hv_large_tile, out.new_hvflip())

            small_tiles = split_large_tile(large_tile)
            for i, st in enumerate(small_tiles):
                small_tile_id = tile_id + SMALL_TILE_OFFSETS[i]

                h_st = hflip_tile(st)
                v_st = vflip_tile(st)
                hv_st = vflip_tile(h_st)

                st_out = TileIdAndFlip(small_tile_id, hflip, vflip)
                self._tile_map.setdefault(st, st_out)
                self._tile_map.setdefault(h_st, st_out.new_hflip())
                self._tile_map.setdefault(v_st, st_out.new_vflip())
                self._tile_map.setdefault(hv_st, st_out.new_hvflip())

    # This function MUST be called after all of the large tiles have been added
    def add_small_tile(self, small_tile: SmallTileData) -> None:
        if small_tile not in self._tile_map:
            self._n_small_tiles += 1

            tile_id: Final = (
                self.settings.first_tile_id + len(self._tile16_addresses) * 2 + SMALL_TILE_OFFSETS[len(self._pending_small_tiles)]
            )

            self._pending_small_tiles.append(small_tile)
            if len(self._pending_small_tiles) == 4:
                self.commit_pending_small_tiles()

            h_small_tile = hflip_tile(small_tile)
            v_small_tile = vflip_tile(small_tile)
            hv_small_tile = vflip_tile(h_small_tile)

            self._tile_map[small_tile] = TileIdAndFlip(tile_id, False, False)
            self._tile_map.setdefault(h_small_tile, TileIdAndFlip(tile_id, True, False))
            self._tile_map.setdefault(v_small_tile, TileIdAndFlip(tile_id, False, True))
            self._tile_map.setdefault(hv_small_tile, TileIdAndFlip(tile_id, True, True))

    def commit_pending_small_tiles(self) -> None:
        if len(self._pending_small_tiles) != 0:
            while len(self._pending_small_tiles) < 4:
                self._pending_small_tiles.append(BLANK_SMALL_TILE)

            tile16_addr: Final = self._tile_store.add_small_tiles(self._pending_small_tiles)
            self._tile16_addresses.append(tile16_addr)
            self._pending_small_tiles.clear()


def dynamic_tiles_for_frame(frame: FrameData, settings: DynamicFsTileSettings, tile_store: DynamicTileStore) -> DynamicFrameTiles:
    dft: Final = DynamicFrameTiles(settings, tile_store)

    # Process the small tiles after the large tiles
    # NOTE: small tiles are only deduplicated if they were previously used in the frame.
    # (Optimize towards decreased VRAM usage over ROM)
    small_tiles = list()

    for o in frame.objects:
        if o.is_large_tile():
            dft.add_large_tile(o.tile_data)
        else:
            small_tiles.append(o.tile_data)

    for st in small_tiles:
        dft.add_small_tile(st)

    dft.commit_pending_small_tiles()

    return dft


#
# RomData
# =======
#


class RomData:
    def __init__(self, addr: int, max_size: int) -> None:
        self._out: bytearray = bytearray(max_size)

        self._view: memoryview = memoryview(self._out)

        self._pos: int = 0
        self._addr: int = addr

    def data(self) -> memoryview:
        return self._view[0 : self._pos]

    def allocate(self, size: int) -> tuple[memoryview, int]:
        a = self._addr
        v = self._view[self._pos : self._pos + size]

        self._pos += size
        self._addr += size

        return v, a

    def insert_data(self, data: bytes) -> int:
        # ::TODO deduplicate data::
        size = len(data)

        a = self._addr
        self._view[self._pos : self._pos + size] = data

        self._pos += size
        self._addr += size

        return a

    def insert_data_addr_table(self, data_list: list[bytes]) -> int:
        table_size = len(data_list) * 2
        table, table_addr = self.allocate(table_size)

        i = 0
        for d in data_list:
            addr = self.insert_data(d) & 0xFFFF

            table[i] = addr & 0xFF
            table[i + 1] = addr >> 8

            i += 2

        assert i == table_size

        return table_addr

    # Dynamic Metasprite data stores tile addresses before the frame data but the frame table must point to the frame data.
    def insert_ms_frame_addr_table(self, data_list: list[tuple[bytes, int]]) -> int:
        table_size = len(data_list) * 2
        table, table_addr = self.allocate(table_size)

        i = 0
        for data, offset in data_list:
            addr = (self.insert_data(data) + offset) & 0xFFFF

            table[i] = addr & 0xFF
            table[i + 1] = addr >> 8

            i += 2

        assert i == table_size

        return table_addr


#
# FramesetData and FrameData
# ==========================
#


def i8_cast(i: int) -> int:
    if i < 0:
        return 0x100 + i
    return i


NO_AABB_VALUE = 0x80


def i8aabb(box: Optional[Aabb], fs: MsFrameset) -> EngineAabb:
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

    return (x1, x2, y1, y2)


def extract_frame(
    fl: FrameLocation, frame_name: Name, image: ImageTileExtractor, palette_map: PaletteMap, fs: MsFrameset
) -> FrameData:
    assert fl.x_offset is not None and fl.y_offset is not None

    pattern: Final = fl.pattern
    assert pattern

    # ::TODO continue if this function causes an error::
    hitbox = i8aabb(fl.hitbox, fs)
    hurtbox = i8aabb(fl.hurtbox, fs)

    image_x: Final = fl.frame_x + fl.x_offset
    image_y: Final = fl.frame_y + fl.y_offset

    x_offset: Final = fs.x_origin - fl.x_offset
    y_offset: Final = fs.y_origin - fl.y_offset

    if x_offset < 0 or x_offset >= fs.frame_width or y_offset < 0 or y_offset >= fs.frame_height:
        raise FrameError(frame_name, f"offset is outside frame: { x_offset }, { y_offset }")

    objects_outside_frame = list()
    tiles_with_no_palettes = list()

    objects = list()

    for o in pattern.objects:
        x = image_x + o.xpos
        y = image_y + o.ypos

        if o.xpos < 0 or o.xpos > fs.frame_width or o.ypos < 0 or o.ypos > fs.frame_height:
            objects_outside_frame.append(TileError(x, y, o.size))
            continue

        if o.size == 8:
            tile = image.small_tile(x, y)
            palette_id, color_map = palette_map.palette_for_tile(tile)
            if color_map:
                assert palette_id is not None
                tile_data = bytes([color_map[c] for c in tile])
                objects.append(ObjectTile(tile_data, palette_id))
            else:
                tiles_with_no_palettes.append(TileError(x, y, 8))
        else:
            tile = image.large_tile(x, y)
            palette_id, color_map = palette_map.palette_for_tile(tile)
            if color_map:
                assert palette_id is not None
                tile_data = bytes([color_map[c] for c in tile])
                objects.append(ObjectTile(tile_data, palette_id))
            else:
                tiles_with_no_palettes.append(TileError(x, y, 16))

    if objects_outside_frame:
        raise FrameError(frame_name, "Objects outside frame", objects_outside_frame)

    if tiles_with_no_palettes:
        raise FrameError(frame_name, "Cannot find palette for object tiles", tiles_with_no_palettes)

    assert len(objects) == len(pattern.objects)

    return FrameData(
        name=frame_name,
        hitbox=hitbox,
        hurtbox=hurtbox,
        pattern=pattern,
        x_offset=x_offset,
        y_offset=y_offset,
        order=fs.order,
        objects=objects,
    )


def build_frameset_data(
    frame_locations: dict[Name, FrameLocation],
    fs: MsFrameset,
    image: ImageTileExtractor,
    palette_map: PaletteMap,
    transparent_color: SnesColor,
) -> tuple[dict[Name, FrameData], set[Name]]:
    errors: list[Union[str, FrameError, AnimationError]] = list()

    image_hflip: Optional[ImageTileExtractor] = None
    image_vflip: Optional[ImageTileExtractor] = None
    image_hvflip: Optional[ImageTileExtractor] = None

    frames: dict[Name, FrameData] = dict()
    patterns_used: set[Name] = set()

    for frame_name, fl in frame_locations.items():
        frame_image = None
        if not fl.flip:
            frame_image = image
        elif fl.flip == "hflip":
            if image_hflip is None:
                image_hflip = image.hflip_image()
            frame_image = image_hflip

        elif fl.flip == "vflip":
            if image_vflip is None:
                image_vflip = image.vflip_image()
            frame_image = image_vflip

        elif fl.flip == "hvflip":
            if image_hvflip is None:
                image_hvflip = image.hvflip_image()
            frame_image = image_hvflip
        else:
            errors.append(f"Unknown flip { fl.flip }")
            continue

        try:
            patterns_used.add(fl.pattern.name)

            frames[frame_name] = extract_frame(fl, frame_name, frame_image, palette_map, fs)

        except FrameError as e:
            errors.append(e)
        except ValueError as e:
            errors.append(FrameError(frame_name, str(e)))

    if errors:
        raise FramesetError(fs, errors)

    return frames, patterns_used


def flip_optional_aabb(aabb: Optional[Aabb], flip: Optional[str], fs: MsFrameset) -> Optional[Aabb]:
    if not aabb or not flip:
        return None

    x = aabb.x
    y = aabb.y

    if flip == "hflip" or flip == "hvflip":
        x = 2 * fs.x_origin - aabb.x - aabb.width
    if flip == "vflip" or flip == "hvflip":
        y = 2 * fs.y_origin - aabb.y - aabb.height

    return Aabb(x, y, aabb.width, aabb.height)


def clone_frame_location(fl: FrameLocation, flip: Optional[str], fs: MsFrameset, image_width: int, image_height: int) -> FrameLocation:
    if not flip:
        return fl

    assert fl.flip is None

    frame_x = fl.frame_x
    frame_y = fl.frame_y

    if flip == "hflip" or flip == "hvflip":
        frame_x = image_width - frame_x - fs.frame_width
    if flip == "vflip" or flip == "hvflip":
        frame_y = image_height - frame_y - fs.frame_height

    # ::TODO test if pattern is symmetrical::
    # ::TODO update x_offset/y_offset::
    return FrameLocation(
        is_clone=True,
        flip=flip,
        frame_x=frame_x,
        frame_y=frame_y,
        pattern=fl.pattern,
        x_offset=fl.x_offset,
        y_offset=fl.y_offset,
        hitbox=flip_optional_aabb(fl.hitbox, flip, fs),
        hurtbox=flip_optional_aabb(fl.hurtbox, flip, fs),
    )


T = TypeVar("T", Aabb, MsLayout)


def build_override_table(
    olist: Union[list[AabbOverride], list[MsLayoutOverride]],
    default_value: Optional[T],
    fs: MsFrameset,
    errors: list[Union[str, FrameError, AnimationError]],
) -> list[Optional[T]]:
    out = [default_value] * len(fs.frames)

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


# Using `image_width` and `image_height` instead of an `image` argument so I
# can call this function with either a ImageTileExtractor and a `tk.PhotoImage` image.
def extract_frame_locations(
    fs: MsFrameset, ms_export_orders: MsExportOrder, image_width: int, image_height: int
) -> dict[Name, FrameLocation]:
    errors: list[Union[str, FrameError, AnimationError]] = list()

    frame_locations = dict[Name, FrameLocation]()

    if fs.frame_width < 0 or fs.frame_height < 0:
        errors.append(f"Invalid frame size: { fs.frame_width } x { fs.frame_height }")

    if fs.frame_width >= 256 or fs.frame_height >= 256:
        errors.append(f"Frame size is too large: { fs.frame_width } x { fs.frame_height }")

    if image_width % fs.frame_width != 0 or image_height % fs.frame_height != 0:
        errors.append("Source image is not a multiple of frame size")

    if fs.x_origin < 0 or fs.x_origin >= fs.frame_width or fs.y_origin < 0 or fs.y_origin >= fs.frame_height:
        errors.append(f"Origin is outside frame: { fs.x_origin }, { fs.y_origin }")

    layouts = build_override_table(fs.layout_overrides, fs.default_layout, fs, errors)
    hitboxes = build_override_table(fs.hitbox_overrides, fs.default_hitbox, fs, errors)
    hurtboxes = build_override_table(fs.hurtbox_overrides, fs.default_hurtbox, fs, errors)

    if errors:
        raise FramesetError(fs, errors)

    frames_per_row: Final = image_width // fs.frame_width

    for frame_number, frame_name in enumerate(fs.frames):
        if frame_name in frame_locations:
            errors.append(f"Duplicate frame name: { frame_name }")

        frame_x = (frame_number % frames_per_row) * fs.frame_width
        frame_y = (frame_number // frames_per_row) * fs.frame_height

        layout = layouts[frame_number]
        if layout:
            pattern = ms_export_orders.patterns.get(layout.pattern)
            if pattern is not None:
                frame_locations[frame_name] = FrameLocation(
                    is_clone=False,
                    flip=None,
                    frame_x=frame_x,
                    frame_y=frame_y,
                    pattern=pattern,
                    x_offset=layout.x_offset,
                    y_offset=layout.y_offset,
                    hitbox=hitboxes[frame_number],
                    hurtbox=hurtboxes[frame_number],
                )
            else:
                errors.append(f"Unknown pattern for { frame_name }: { layout.pattern }")
        else:
            errors.append(f"Missing layout for frame: { frame_name }")

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
        raise FramesetError(fs, errors)

    return frame_locations


def animation_delay__distance(d: Union[float, int]) -> int:
    if d < 0.0 or d >= 16.0:
        raise ValueError(f"Invalid animation frame delay (must be between 0 and 16): { d }")
    return round(d * 16)


ANIMATION_DELAY_FUNCTIONS: dict[str, Callable[[Union[float, int]], int]] = {
    "none": lambda d: 0,
    "frame": lambda d: int(d),
    "distance_x": animation_delay__distance,
    "distance_y": animation_delay__distance,
    "distance_xy": animation_delay__distance,
}

# NOTE: If you modify this map, also modify the `AnimationProcessFunctions` in `metasprites.wiz`
LOOPING_ANIMATION_DELAY_IDS: Final[dict[str, int]] = {
    "none": 0,
    "frame": 2,
    "distance_x": 4,
    "distance_y": 6,
    "distance_xy": 8,
}

# NOTE: If you modify this map, also modify the `AnimationProcessFunctions` in `metasprites.wiz`
NON_LOOPING_ANIMATION_DELAY_IDS: Final[dict[str, int]] = {
    "none": 0,
    "frame": 24,
    "distance_x": 26,
    "distance_y": 28,
    "distance_xy": 30,
}


END_OF_ANIMATION_BYTE = 0xFF

MAX_FRAME_ID = 0xFC
MAX_N_FRAMES = MAX_FRAME_ID + 1

MAX_N_ANIMATIONS = 0xFF


def build_animation_data(ani: MsAnimation, get_frame_id: Callable[[Name], int]) -> bytes:
    if ani.delay_type == "none" and len(ani.frames) != 1:
        raise ValueError("A 'none' delay type can only contain a single animation frame")

    ani_delay_converter = ANIMATION_DELAY_FUNCTIONS[ani.delay_type]

    if ani.loop:
        if len(ani.frames) == 1:
            # Do not process looping animations that have a single frame
            process_function = LOOPING_ANIMATION_DELAY_IDS["none"]
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


def build_frameset(
    fs: MsFrameset,
    ms_export_orders: MsExportOrder,
    ms_dir: Filename,
    palette_map: PaletteMap,
    transparent_color: SnesColor,
    spritesheet_name: Name,
) -> FramesetData:
    errors: list[Union[str, FrameError, AnimationError]] = list()

    image = load_image_tile_extractor(os.path.join(ms_dir, fs.source))

    frame_locations = extract_frame_locations(fs, ms_export_orders, image.width_px, image.height_px)

    frames, patterns_used = build_frameset_data(frame_locations, fs, image, palette_map, transparent_color)
    animations: dict[Name, bytes] = dict()

    exported_frames: list[FrameData] = list()
    exported_frame_ids: dict[Name, int] = dict()

    ms_export_orders.shadow_sizes[fs.shadow_size]
    shadow_size = fs.shadow_size

    tile_hitbox = fs.tilehitbox
    if tile_hitbox.half_width >= 128 or tile_hitbox.half_height >= 128:
        errors.append(f"Tile hitbox is too large: { tile_hitbox.half_width }, { tile_hitbox.half_height }")

    export_order = ms_export_orders.animation_lists.get(fs.ms_export_order)
    if export_order is None:
        errors.append(f"Unknown export order: { fs.ms_export_order }")

    if errors:
        raise FramesetError(fs, errors)

    # Confirm all frames have been processed
    assert len(frames) == len(fs.frames) + len(fs.clones)

    for ani in fs.animations.values():
        assert ani.name not in animations

        def get_frame_id(frame_name: Name) -> int:
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
        raise FramesetError(fs, errors)

    if len(exported_frames) > MAX_N_FRAMES:
        errors.append(f"Too many frames ({ len(exported_frames) }, max: { MAX_N_FRAMES })")

    if len(animations) > MAX_N_ANIMATIONS:
        errors.append(f"Too many animations ({ len(exported_frames) }, max: { MAX_N_ANIMATIONS})")

    assert export_order

    eo_animations: list[bytes] = list()
    for ea_name in export_order.animations:
        a = animations.get(ea_name)
        if a:
            eo_animations.append(a)
        else:
            errors.append(f"Cannot find animation: { ea_name }")

    if errors:
        raise FramesetError(fs, errors)

    if len(patterns_used) == 1:
        pattern_name = next(iter(patterns_used))
    else:
        pattern_name = None

    unused_frames = frames.keys() - exported_frame_ids.keys()
    if unused_frames:
        # ::TODO do something about this (not thread safe)::
        print(f"WARNING: Unused MetaSprite frames in { fs.name }: { unused_frames }")

    unused_animations = fs.animations.keys() - export_order.animations
    if unused_animations:
        # ::TODO do something about this (not thread safe)::
        print(f"WARNING: Unused MetaSprite animations in { fs.name }: { unused_animations }")

    assert not errors

    return FramesetData(fs.name, fs, fs.ms_export_order, shadow_size, tile_hitbox, pattern_name, exported_frames, eo_animations)


#
# MsFsData
# ========
#


def build_engine_frame_data(
    frame: FrameData, tile_map: dict[SmallOrLargeTileData, TileIdAndFlip], dynamic_tiles: Optional[list[WordAddr]]
) -> tuple[bytes, int]:
    data = bytearray()

    if dynamic_tiles is not None:
        assert len(dynamic_tiles) > 0

        for addr in reversed(dynamic_tiles):
            data.append(addr & 0xFF)
            data.append(addr >> 8)

    offset: Final = len(data)

    data.extend(frame.hitbox)
    data.extend(frame.hurtbox)

    data.append(frame.pattern.id)
    data.append(frame.x_offset)
    data.append(frame.y_offset)

    for o in frame.objects:
        t = tile_map[o.tile_data]
        assert 0 <= t.tile_id <= 511

        data.append(t.tile_id & 0xFF)
        data.append(
            (t.tile_id >> 8) | ((o.palette_id & 7) << 1) | ((frame.order & 3) << 4) | (bool(t.hflip) << 6) | (bool(t.vflip) << 7)
        )

    return data, offset


def build_palette_swapped_engine_frame_data(
    frame: FrameData, tile_map: dict[SmallOrLargeTileData, TileIdAndFlip], palette_id: int
) -> tuple[bytes, int]:
    data = bytearray()

    data.extend(frame.hitbox)
    data.extend(frame.hurtbox)

    data.append(frame.pattern.id)
    data.append(frame.x_offset)
    data.append(frame.y_offset)

    for o in frame.objects:
        t = tile_map[o.tile_data]
        assert 0 <= t.tile_id <= 511

        data.append(t.tile_id & 0xFF)
        data.append(
            (t.tile_id >> 8) | ((palette_id & 7) << 1) | ((frame.order & 3) << 4) | (bool(t.hflip) << 6) | (bool(t.vflip) << 7)
        )

    return data, 0


def build_msfs_entry(name: Name, fs: FramesetData, frames: list[tuple[bytes, int]], spritesheet_name: Name) -> MsFsEntry:
    header = bytearray().zfill(3)

    header[0] = SHADOW_SIZES[fs.shadow_size]
    header[1] = fs.tile_hitbox[0]
    header[2] = fs.tile_hitbox[1]

    return MsFsEntry(
        fullname=f"{ spritesheet_name }.{ name }",
        ms_export_order=fs.ms_export_order,
        header=header,
        pattern=fs.pattern,
        frames=frames,
        animations=fs.animations,
    )


def build_static_msfs_entries(
    framesets: OrderedDict[Name, FramesetData],
    palette_swaps: list[PaletteSwapData],
    ms_input: MsSpritesheet,
    tile_map: dict[SmallOrLargeTileData, TileIdAndFlip],
) -> list[MsFsEntry]:
    spritesheet_name: Final = ms_input.name

    return [
        build_msfs_entry(
            fs.name,
            fs,
            [build_engine_frame_data(f, tile_map, None) for f in fs.frames],
            spritesheet_name,
        )
        for fs in framesets.values()
    ] + [
        build_msfs_entry(
            ps.name,
            ps.fs_data,
            [build_palette_swapped_engine_frame_data(f, tile_map, ps.palette) for f in ps.fs_data.frames],
            spritesheet_name,
        )
        for ps in palette_swaps
    ]


def build_dynamic_msfs_entry(
    frameset: FramesetData, tile_store: DynamicTileStore, ms_input: MsSpritesheet, ms_export_orders: MsExportOrder
) -> MsFsEntry:
    frames: list[tuple[bytes, int]] = list()

    # ::TODO check max number of tiles in the frameset::

    tile_locations = ms_export_orders.dynamic_metasprites.get(frameset.name)
    if tile_locations is None:
        raise FramesetError(
            frameset.frameset,
            f"Cannot build dynamic metasprite tiles: `{frameset.name}` not found in `ms_export_orders.dynamic_metasprites`",
        )

    try:
        tile_settings = build_dynamic_tile_settings(tile_locations)
    except Exception as e:
        raise FramesetError(frameset.frameset, f"Cannot build dynamic metasprite tiles: {e}")

    errors: list[Union[str, FrameError, AnimationError]] = list()

    for f in frameset.frames:
        try:
            dft = dynamic_tiles_for_frame(f, tile_settings, tile_store)
            frame_data = build_engine_frame_data(f, dft.tile_map(), dft.tile_addresses())
            frames.append(frame_data)
        except Exception as e:
            errors.append(FrameError(f.name, str(e)))

    if errors:
        raise FramesetError(frameset.frameset, errors)

    return build_msfs_entry(frameset.name, frameset, frames, ms_input.name)


def build_ms_fs_data(
    dynamic_spritesheet: DynamicMsSpritesheet,
    static_spritesheets: list[list[MsFsEntry]],
    ms: MsExportOrder,
    mapmode: MemoryMapMode,
) -> tuple[RomData, dict[ScopedName, tuple[int, Name]]]:
    # Return: tuple(rom_data, dict fs_fullname -> tuple(addr, export_order))

    spritesheets: Final = [dynamic_spritesheet.msfs_entries] + static_spritesheets

    MS_FRAMESET_FORMAT_SIZE = 8

    rom_data = RomData(mapmode.bank_start, mapmode.bank_size)

    fs_map = dict()

    n_framesets = sum([len(i) for i in spritesheets])
    assert n_framesets > 0

    fs_table, fs_table_addr = rom_data.allocate(n_framesets * MS_FRAMESET_FORMAT_SIZE)
    fs_pos = 0

    for framesets in spritesheets:
        for fs in framesets:
            fs_addr = fs_table_addr + fs_pos

            frame_table_addr = rom_data.insert_ms_frame_addr_table(fs.frames)
            animation_table_addr = rom_data.insert_data_addr_table(fs.animations)

            if fs.pattern:
                drawing_function = ms.patterns[fs.pattern].id
            else:
                drawing_function = ms.dynamic_pattern_id

            fs_table[fs_pos : fs_pos + 3] = fs.header

            fs_table[fs_pos + 3] = drawing_function

            fs_table[fs_pos + 4] = frame_table_addr & 0xFF
            fs_table[fs_pos + 5] = frame_table_addr >> 8

            fs_table[fs_pos + 6] = animation_table_addr & 0xFF
            fs_table[fs_pos + 7] = animation_table_addr >> 8

            fs_pos += MS_FRAMESET_FORMAT_SIZE

            fs_map[fs.fullname] = (fs_addr, fs.ms_export_order)

    assert fs_pos == n_framesets * MS_FRAMESET_FORMAT_SIZE

    # Ensure player data is the first item
    if fs_map["dynamic.Player"][0] != mapmode.bank_start:
        raise RuntimeError("The first MetaSprite FrameSet MUST be the player")

    return rom_data, fs_map


#
# =========================
#


def generate_ppu_data(ms_input: MsSpritesheet, tileset: list[SmallTileData]) -> EngineData:
    tile_data = convert_snes_tileset(tileset, TILE_DATA_BPP)

    header = bytearray()

    # first_tile
    header.append(ms_input.first_tile & 0xFF)
    header.append(ms_input.first_tile >> 8)

    ppu_data = tile_data

    return EngineData(
        ram_data=FixedSizedData(header),
        ppu_data=DynamicSizedData(ppu_data),
    )


def _extract_frameset_data(
    ms_input: MsSpritesheet, ms_export_orders: MsExportOrder, ms_dir: Filename, data_store: DataStore
) -> tuple[OrderedDict[Name, FramesetData], list[PaletteSwapData]]:
    pal_data = data_store.get_ms_palette(ms_input.palette)
    if pal_data is None:
        raise RuntimeError(f"Cannot load palette: {ms_input.palette}")

    palette_map = pal_data.palette.palette_map
    transparent_color = pal_data.palette.transparent_color

    framesets = OrderedDict()

    errors: list[FramesetError | PaletteSwapError] = list()

    for fs in ms_input.framesets.values():
        try:
            framesets[fs.name] = build_frameset(fs, ms_export_orders, ms_dir, palette_map, transparent_color, ms_input.name)
        except FramesetError as e:
            errors.append(e)
        except Exception as e:
            errors.append(FramesetError(fs, f"{ type(e).__name__ }({ e })"))

    palette_swaps = list()
    for ps in ms_input.palette_swaps.values():
        if ps.name in framesets:
            errors.append(PaletteSwapError(ps, f"Palette swap name already exists: {ps.name}"))
        fs_data = framesets.get(ps.copies)
        if fs_data is None:
            errors.append(PaletteSwapError(ps, f"Cannot find frameset: {ps.copies}"))

        if ps.palette < 0 or ps.palette > 7:
            errors.append(PaletteSwapError(ps, f"invalid palette: {ps.palette}"))

        if fs_data:
            palette_swaps.append(
                PaletteSwapData(
                    name=ps.name,
                    fs_data=fs_data,
                    palette=ps.palette,
                )
            )

    if errors:
        raise SpritesheetError(errors, ms_dir)

    return framesets, palette_swaps


def convert_static_spritesheet(
    ms_input: MsSpritesheet, ms_export_orders: MsExportOrder, ms_dir: Filename, data_store: DataStore
) -> tuple[EngineData, list[MsFsEntry]]:
    framesets, palette_swaps = _extract_frameset_data(ms_input, ms_export_orders, ms_dir, data_store)

    tileset_data, tile_map = build_static_tileset(framesets, ms_input)

    msfs_entries = build_static_msfs_entries(framesets, palette_swaps, ms_input, tile_map)

    ppu_data = generate_ppu_data(ms_input, tileset_data)

    return ppu_data, msfs_entries


def convert_dynamic_spritesheet(
    ms_input: MsSpritesheet, ms_export_orders: MsExportOrder, ms_dir: Filename, map_mode: MemoryMapMode, data_store: DataStore
) -> DynamicMsSpritesheet:
    if ms_input.palette_swaps:
        raise RuntimeError("Dynamic metasprite spritesheet does not support palette swaps")

    framesets, palette_swaps = _extract_frameset_data(ms_input, ms_export_orders, ms_dir, data_store)

    assert len(palette_swaps) == 0

    tile_store = DynamicTileStore(map_mode)

    msfs_entries = list()
    errors: list[FramesetError | PaletteSwapError] = list()

    for fs in framesets.values():
        try:
            msfs_entries.append(build_dynamic_msfs_entry(fs, tile_store, ms_input, ms_export_orders))
        except FramesetError as e:
            errors.append(e)
        except Exception as e:
            errors.append(FramesetError(fs.frameset, f"{ type(e).__name__ }({ e })"))

    tile_data = convert_snes_tileset(tile_store.tile_data(), TILE_DATA_BPP)

    if errors:
        raise SpritesheetError(errors, ms_dir)

    return DynamicMsSpritesheet(tile_data, msfs_entries)
