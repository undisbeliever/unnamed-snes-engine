# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import itertools
import PIL.Image  # type: ignore
from typing import Generator, Final, Iterable, Literal, NamedTuple, Optional, Sequence, TextIO, Union

from .json_formats import Filename
from .common import MultilineError, FileError


SnesColor = int

# Tiles made up of `SnesColor`s
SmallColorTile = list[SnesColor]  # 64 (8x8) `SnesColor`s
LargeColorTile = list[SnesColor]  # 256 (16x16) `SnesColor`s

# Tiles made up of palette indexes
SmallTileData = bytes  # 64 (8x8) bytes
LargeTileData = bytes  # 256 (16x16) bytes

# Mapping of `SnesColor` to palette index
PaletteMap = dict[SnesColor, int]


class TileMapEntry(NamedTuple):
    tile_id: int
    palette_id: int
    hflip: bool
    vflip: bool


class TileMap(NamedTuple):
    width: int
    height: int
    grid: list[TileMapEntry]

    # NOTE: No bounds checking
    def get_tile(self, x: int, y: int) -> TileMapEntry:
        return self.grid[x + y * self.width]


class ImageError(FileError):
    def __init__(self, filename: Filename, message: str):
        super().__init__(message, (filename,))


class InvalidTilesError(MultilineError):
    def __init__(self, message: str, filename: Filename, invalid_tiles: list[int], tilemap_width: int, tile_size: Literal[8, 16]):
        self.message: Final = message
        self.filename: Final = filename
        self.invalid_tiles: Final = invalid_tiles
        self.tilemap_width: Final = tilemap_width
        self.tile_size: Final = tile_size

    def print_indented(self, fp: TextIO) -> None:
        to_print: Final = min(48, len(self.invalid_tiles))
        per_line: Final = 16

        fp.write(f"{ self.message } for { len(self.invalid_tiles) } { self.tile_size }px tiles in { self.filename }:")
        for i in range(0, to_print, per_line):
            fp.write(f"\n   ")
            fp.write(",".join(f"{t:5}" for t in self.invalid_tiles[i : i + per_line]))
        if len(self.invalid_tiles) > to_print:
            fp.write(" ...")

    def __str__(self) -> str:
        return f"{ self.message } for { len(self.invalid_tiles) } { self.tile_size }px tiles"


def convert_mode7_tileset(tiles: Iterable[SmallTileData]) -> bytes:
    out = bytes().join(tiles)

    if len(out) > 256 * 64:
        raise ValueError("Too many tiles in image")

    return out


def convert_snes_tileset(tiles: Iterable[SmallTileData], bpp: int) -> bytes:
    out = bytearray()

    for tile in tiles:
        for b in range(0, bpp, 2):
            for y in range(0, 8):
                for bi in range(b, min(b + 2, bpp)):
                    byte = 0
                    mask = 1 << bi
                    for x in range(0, 8):
                        byte <<= 1
                        if tile[x + y * 8] & mask:
                            byte |= 1
                    out.append(byte)
    return out


def convert_rgb_color(c: tuple[int, int, int]) -> SnesColor:
    r, g, b = c

    b = (b >> 3) & 31
    g = (g >> 3) & 31
    r = (r >> 3) & 31

    return (b << 10) | (g << 5) | r


def is_small_tile_not_transparent(image: PIL.Image.Image, transparent_color: SnesColor, xpos: int, ypos: int) -> bool:
    """Returns True if the tile contains a non-transparent pixel"""

    if xpos + 8 > image.width or ypos + 8 > image.height:
        raise ValueError(f"position out of bounds: { xpos }, { ypos }")

    return any(
        convert_rgb_color(image.getpixel((x, y))) != transparent_color for y in range(ypos, ypos + 8) for x in range(xpos, xpos + 8)
    )


def extract_small_tile_grid(image: PIL.Image.Image) -> Generator[SmallColorTile, None, None]:
    """Generator that extracts 8px tiles from the image in consecutive order."""

    # Required as `image.convert()` has no `filename` attribute
    image_filename = image.filename

    if image.mode != "RGB":
        image = image.convert("RGB")

    if image.width % 8 != 0 or image.height % 8 != 0:
        raise ImageError(image_filename, "Image width and height MUST be a multiple of 8")

    for ty in range(0, image.height, 8):
        for tx in range(0, image.width, 8):
            yield [convert_rgb_color(image.getpixel((x, y))) for y in range(ty, ty + 8) for x in range(tx, tx + 8)]


def extract_tiles_from_paletted_image(image: PIL.Image.Image) -> Generator[SmallTileData, None, None]:
    if image.width % 8 != 0 or image.height % 8 != 0:
        raise ImageError(image.filename, "Image width and height MUST be a multiple of 8")

    if not image.palette:
        raise ImageError(image.filename, "Image does not have a palette")

    img_data = image.getdata()

    for ty in range(0, image.height, 8):
        for tx in range(0, image.width, 8):
            yield bytes(image.getpixel((x, y)) for y in range(ty, ty + 8) for x in range(tx, tx + 8))


def extract_small_tile(image: PIL.Image.Image, xpos: int, ypos: int) -> SmallColorTile:
    # Assumes image.mode == 'RGB'

    if xpos + 8 > image.width or ypos + 8 > image.height:
        raise ValueError(f"position out of bounds: { xpos }, { ypos }")

    return [convert_rgb_color(image.getpixel((x, y))) for y in range(ypos, ypos + 8) for x in range(xpos, xpos + 8)]


def extract_large_tile(image: PIL.Image.Image, xpos: int, ypos: int) -> LargeColorTile:
    # Assumes image.mode == 'RGB'

    if xpos + 16 > image.width or ypos + 16 > image.height:
        raise ValueError(f"position out of bounds: { xpos }, { ypos }")

    return [convert_rgb_color(image.getpixel((x, y))) for y in range(ypos, ypos + 16) for x in range(xpos, xpos + 16)]


def create_palettes_map(image: PIL.Image.Image, bpp: int) -> list[PaletteMap]:
    # Returns palettes_map

    if image.mode != "RGB":
        image = image.convert("RGB")

    colors_per_palette = 1 << bpp
    max_colors = min(colors_per_palette * 8, 256)

    if image.width != 16:
        raise ImageError(image.filename, "Palette Image must be 16 pixels in width")

    if image.width * image.height > max_colors:
        raise ImageError(image.filename, f"Palette Image has too many colours (max { max_colors })")

    image_data = image.getdata()

    palettes_map = list()

    for p in range(len(image_data) // colors_per_palette):
        pal_map = dict()

        for x in range(colors_per_palette):
            c = convert_rgb_color(image_data[p * colors_per_palette + x])
            if c not in pal_map:
                pal_map[c] = x

        palettes_map.append(pal_map)

    return palettes_map


def convert_palette_image(image: PIL.Image.Image) -> bytes:
    palette_data = bytearray()

    for c in image.getdata():
        u16 = convert_rgb_color(c)

        palette_data.append(u16 & 0xFF)
        palette_data.append(u16 >> 8)

    return palette_data


def get_palette_id(
    tile: Union[SmallColorTile, LargeColorTile], palettes_map: list[PaletteMap]
) -> tuple[Optional[int], Optional[PaletteMap]]:
    # Returns a tuple of (palette_id, palette_map)
    for palette_id, pal_map in enumerate(palettes_map):
        if all([c in pal_map for c in tile]):
            return palette_id, pal_map

    return None, None


_H_FLIP_ORDER_SMALL = [(y * 8 + x) for y, x in itertools.product(range(8), reversed(range(8)))]
_V_FLIP_ORDER_SMALL = [(y * 8 + x) for y, x in itertools.product(reversed(range(8)), range(8))]


def hflip_tile(tile: SmallTileData) -> SmallTileData:
    return bytes([tile[i] for i in _H_FLIP_ORDER_SMALL])


def vflip_tile(tile: SmallTileData) -> SmallTileData:
    return bytes([tile[i] for i in _V_FLIP_ORDER_SMALL])


_H_FLIP_ORDER_LARGE = [(y * 16 + x) for y, x in itertools.product(range(16), reversed(range(16)))]
_V_FLIP_ORDER_LARGE = [(y * 16 + x) for y, x in itertools.product(reversed(range(16)), range(16))]


def hflip_large_tile(tile: LargeTileData) -> LargeTileData:
    return bytes([tile[i] for i in _H_FLIP_ORDER_LARGE])


def vflip_large_tile(tile: LargeTileData) -> LargeTileData:
    return bytes([tile[i] for i in _V_FLIP_ORDER_LARGE])


def split_large_tile(tile: LargeTileData) -> tuple[SmallTileData, SmallTileData, SmallTileData, SmallTileData]:
    return (
        bytes(tile[y * 16 + x] for y in range(0, 8) for x in range(0, 8)),
        bytes(tile[y * 16 + x] for y in range(0, 8) for x in range(8, 16)),
        bytes(tile[y * 16 + x] for y in range(8, 16) for x in range(0, 8)),
        bytes(tile[y * 16 + x] for y in range(8, 16) for x in range(8, 16)),
    )


def convert_tilemap_and_tileset(
    tiles: Generator[SmallColorTile, None, None], filename: Filename, palettes_map: list[PaletteMap], map_width: int, map_height: int
) -> tuple[TileMap, list[SmallTileData]]:
    # Returns a tuple(tilemap, tileset)

    assert len(palettes_map) <= 8

    invalid_tiles = list()

    tilemap: list[TileMapEntry] = list()
    tileset: list[SmallTileData] = list()

    tileset_map: dict[SmallTileData, tuple[int, bool, bool]] = dict()

    for tile_index, tile in enumerate(tiles):
        palette_id, pal_map = get_palette_id(tile, palettes_map)

        if pal_map:
            assert palette_id is not None

            # Must be bytes() here as a dict() key must be immutable
            tile_data = bytes([pal_map[c] for c in tile])

            tile_match = tileset_map.get(tile_data, None)
            if tile_match is None:
                tile_id = len(tileset)
                tile_match = tile_id, False, False

                tileset.append(tile_data)

                h_tile_data = hflip_tile(tile_data)
                v_tile_data = vflip_tile(tile_data)
                hv_tile_data = vflip_tile(h_tile_data)

                tileset_map[tile_data] = tile_match
                tileset_map.setdefault(h_tile_data, (tile_id, True, False))
                tileset_map.setdefault(v_tile_data, (tile_id, False, True))
                tileset_map.setdefault(hv_tile_data, (tile_id, True, True))

            tilemap.append(TileMapEntry(tile_id=tile_match[0], palette_id=palette_id, hflip=tile_match[1], vflip=tile_match[2]))
        else:
            invalid_tiles.append(tile_index)

    if invalid_tiles:
        raise InvalidTilesError("Cannot find palette", filename, invalid_tiles, map_width, 8)

    assert len(tilemap) == map_width * map_height

    return TileMap(width=map_width, height=map_height, grid=tilemap), tileset


# ::TODO add a reorder_tilemap function that will reorder a TileMap into the snes nametable order (with padding)::


def create_tilemap_data(tilemap: Union[TileMap, Sequence[TileMapEntry]], default_order: bool) -> bytes:
    if isinstance(tilemap, TileMap):
        tilemap = tilemap.grid

    data = bytearray()

    for t in tilemap:
        data.append(t.tile_id & 0xFF)
        data.append(
            ((t.tile_id & 0x3FF) >> 8)
            | ((t.palette_id & 7) << 2)
            | (bool(default_order) << 5)
            | (bool(t.hflip) << 6)
            | (bool(t.vflip) << 7)
        )

    return data


def create_tilemap_data_low(tilemap: Union[TileMap, Sequence[TileMapEntry]]) -> bytes:
    if isinstance(tilemap, TileMap):
        tilemap = tilemap.grid

    data = bytearray()

    for t in tilemap:
        data.append(t.tile_id & 0xFF)

    return data


def create_tilemap_data_high(tilemap: Union[TileMap, Sequence[TileMapEntry]], default_order: bool) -> bytes:
    if isinstance(tilemap, TileMap):
        tilemap = tilemap.grid

    data = bytearray()

    for t in tilemap:
        data.append(t.tile_id & 0xFF)
        data.append(
            ((t.tile_id & 0x3FF) >> 8)
            | ((t.palette_id & 7) << 2)
            | (bool(default_order) << 5)
            | (bool(t.hflip) << 6)
            | (bool(t.vflip) << 7)
        )

    return data


def image_and_palette_map_to_snes(
    image: PIL.Image.Image, image_filename: Filename, palettes_map: list[PaletteMap], bpp: int
) -> tuple[TileMap, bytes]:
    # Return (tilemap, tile_data)

    tilemap, tileset = convert_tilemap_and_tileset(
        extract_small_tile_grid(image), image_filename, palettes_map, image.width // 8, image.height // 8
    )

    tile_data = convert_snes_tileset(tileset, bpp)

    return tilemap, tile_data


def image_to_snes(
    image: PIL.Image.Image, image_filename: Filename, palette_image: PIL.Image.Image, bpp: int
) -> tuple[TileMap, bytes, bytes]:
    # Return (tilemap, tile_data, palette_data)

    tilemap, tileset = convert_tilemap_and_tileset(
        extract_small_tile_grid(image), image_filename, create_palettes_map(palette_image, bpp), image.width // 8, image.height // 8
    )

    tile_data = convert_snes_tileset(tileset, bpp)

    palette_data = convert_palette_image(palette_image)

    return tilemap, tile_data, palette_data
