# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import struct
import itertools
import PIL.Image  # type: ignore
from itertools import islice
from abc import ABC, abstractmethod
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


class ConstSmallTileMap:
    def __init__(self, tile_map: dict[SmallTileData, tuple[int, bool, bool]]) -> None:
        self.__tile_map: Final = tile_map

    def get(self, tile_data: SmallTileData) -> Optional[tuple[int, bool, bool]]:
        return self.__tile_map.get(tile_data)


class AbstractTilesetMap(ABC):
    def __init__(self) -> None:
        self._map: Final[dict[SmallTileData, tuple[int, bool, bool]]] = {}

    def const_map(self) -> ConstSmallTileMap:
        return ConstSmallTileMap(self._map)

    def _add_to_map(self, tile_data: SmallTileData, tile_id: int) -> tuple[int, bool, bool]:
        tile_match = tile_id, False, False

        h_tile_data = hflip_tile(tile_data)
        v_tile_data = vflip_tile(tile_data)
        hv_tile_data = vflip_tile(h_tile_data)

        self._map[tile_data] = tile_match
        self._map.setdefault(h_tile_data, (tile_id, True, False))
        self._map.setdefault(v_tile_data, (tile_id, False, True))
        self._map.setdefault(hv_tile_data, (tile_id, True, True))

        return tile_match

    @abstractmethod
    def get_or_insert(self, tile: SmallTileData) -> tuple[int, bool, bool]: ...

    @abstractmethod
    def tiles(self) -> Iterable[SmallTileData]: ...


class SmallTilesetMap(AbstractTilesetMap):
    def __init__(self) -> None:
        super().__init__()
        self._tiles: Final[list[SmallTileData]] = []

    def get_or_insert(self, tile_data: SmallTileData) -> tuple[int, bool, bool]:
        tile_match = self._map.get(tile_data, None)
        if tile_match is not None:
            return tile_match
        else:
            self._tiles.append(tile_data)
            return self._add_to_map(tile_data, len(self._tiles) - 1)

    def tiles(self) -> Iterable[SmallTileData]:
        return self._tiles


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


# NOTE: Does not extract palette from image
def extract_tiles_from_paletted_image(filename: Filename) -> Generator[SmallTileData, None, None]:
    try:
        with PIL.Image.open(filename) as image:
            image.load()
    except Exception as e:
        raise ImageError(filename, str(e))

    if image.mode != "P":
        raise ImageError(image.filename, "Image does not have a palette")

    if image.width % 8 != 0 or image.height % 8 != 0:
        raise ImageError(image.filename, "Image width and height MUST be a multiple of 8")

    assert image.palette

    img_data = image.getdata()

    for ty in range(0, image.height, 8):
        for tx in range(0, image.width, 8):
            yield bytes(image.getpixel((x, y)) for y in range(ty, ty + 8) for x in range(tx, tx + 8))


class PaletteMap(NamedTuple):
    color_maps: list[dict[SnesColor, int]]

    def palette_for_tile(self, tile: Union[SmallColorTile, LargeColorTile]) -> tuple[Optional[int], Optional[dict[SnesColor, int]]]:
        # Returns a tuple of (palette_id, color_map)
        for palette_id, color_map in enumerate(self.color_maps):
            if all([c in color_map for c in tile]):
                return palette_id, color_map

        return None, None


class Palette(NamedTuple):
    colors: list[SnesColor]

    def create_map(self, bpp: int) -> PaletteMap:
        if bpp < 1 or bpp > 8:
            raise ValueError("Invalid bpp")

        colors: Final = self.colors
        colors_per_palette: Final = 1 << bpp
        n_palettes: Final = min(len(self.colors) // colors_per_palette, 8)

        out = list()

        for p in range(n_palettes):
            color_map = dict()
            pi = p * colors_per_palette
            for i, c in enumerate(colors[pi : pi + colors_per_palette]):
                if c not in color_map:
                    color_map[c] = i
            out.append(color_map)

        return PaletteMap(out)

    def snes_data(self) -> bytes:
        return struct.pack(f"<{len(self.colors)}H", *self.colors)


PALETTE_IMAGE_WIDTH: Final = 16


def load_palette_image(filename: Filename, max_colors: int) -> Palette:
    try:
        with PIL.Image.open(filename) as image:
            image.load()
    except Exception as e:
        raise ImageError(filename, str(e))

    if image.width != PALETTE_IMAGE_WIDTH:
        raise ImageError(filename, f"Palette Image MUST BE {PALETTE_IMAGE_WIDTH} px wide")

    if image.height > max_colors // 16:
        raise ImageError(filename, "Palette Image contains too many colors")

    if image.mode != "RGB":
        image = image.convert("RGB")

    return Palette([convert_rgb_color(c) for c in image.getdata()])


class ImageTileExtractor(ABC):
    def __init__(self, filename: Filename, width_px: int, height_px: int):
        if width_px % 8 != 0 or height_px % 8 != 0:
            raise ImageError(filename, "Image width and height MUST be a multiple of 8")

        self.filename: Final = filename
        self.width_px: Final = width_px
        self.height_px: Final = height_px

    @abstractmethod
    def small_tile(self, xpos: int, ypos: int) -> SmallColorTile:
        pass

    @abstractmethod
    def large_tile(self, xpos: int, ypos: int) -> LargeColorTile:
        pass

    def extract_small_tiles(self) -> Generator[SmallColorTile, None, None]:
        """Generator that extracts 8px tiles from the image in consecutive order."""

        for ty in range(0, self.height_px, 8):
            for tx in range(0, self.width_px, 8):
                yield self.small_tile(tx, ty)

    @abstractmethod
    def hflip_image(self) -> "ImageTileExtractor":
        pass

    @abstractmethod
    def vflip_image(self) -> "ImageTileExtractor":
        pass

    @abstractmethod
    def hvflip_image(self) -> "ImageTileExtractor":
        pass


class RgbImageTileExtractor(ImageTileExtractor):
    def __init__(self, filename: Filename, image: PIL.Image.Image):
        assert image.mode == "RGB", "wrong image mode"
        super().__init__(filename, image.width, image.height)
        self.__image: Final = image

    def hflip_image(self) -> "RgbImageTileExtractor":
        return RgbImageTileExtractor(self.filename + " [hflip]", self.__image.transpose(PIL.Image.Transpose.FLIP_LEFT_RIGHT))

    def vflip_image(self) -> "RgbImageTileExtractor":
        return RgbImageTileExtractor(self.filename + " [vflip]", self.__image.transpose(PIL.Image.Transpose.FLIP_TOP_BOTTOM))

    def hvflip_image(self) -> "RgbImageTileExtractor":
        return RgbImageTileExtractor(self.filename + " [hvflip]", self.__image.transpose(PIL.Image.Transpose.ROTATE_180))

    def small_tile(self, xpos: int, ypos: int) -> SmallColorTile:
        if xpos + 8 > self.width_px or ypos + 8 > self.height_px:
            raise ImageError(self.filename, f"position out of bounds: { xpos }, { ypos }")

        return [
            convert_rgb_color(self.__image.getpixel((x, y)))  # type: ignore
            for y in range(ypos, ypos + 8)
            for x in range(xpos, xpos + 8)
        ]

    def large_tile(self, xpos: int, ypos: int) -> LargeColorTile:
        if xpos + 16 > self.width_px or ypos + 16 > self.height_px:
            raise ImageError(self.filename, f"position out of bounds: { xpos }, { ypos }")

        return [
            convert_rgb_color(self.__image.getpixel((x, y)))  # type: ignore
            for y in range(ypos, ypos + 16)
            for x in range(xpos, xpos + 16)
        ]


class IndexedImageTileExtractor(ImageTileExtractor):
    def __init__(self, filename: Filename, image: PIL.Image.Image, palette: Optional[list[SnesColor]] = None):
        im_palette = image.getpalette("RGB")
        if im_palette is None:
            raise ImageError(self.filename, "image does not have a palette")

        if palette is None:
            palette = list()
            it = iter(im_palette)
            while c := tuple(islice(it, 3)):
                palette.append(convert_rgb_color(c))  # type: ignore

        super().__init__(filename, image.width, image.height)
        self.__palette: Final = palette
        self.__image: Final = image

    def hflip_image(self) -> "IndexedImageTileExtractor":
        return IndexedImageTileExtractor(
            self.filename + " [hflip]", self.__image.transpose(PIL.Image.Transpose.FLIP_LEFT_RIGHT), self.__palette
        )

    def vflip_image(self) -> "IndexedImageTileExtractor":
        return IndexedImageTileExtractor(
            self.filename + " [vflip]", self.__image.transpose(PIL.Image.Transpose.FLIP_TOP_BOTTOM), self.__palette
        )

    def hvflip_image(self) -> "IndexedImageTileExtractor":
        return IndexedImageTileExtractor(
            self.filename + " [hvflip]", self.__image.transpose(PIL.Image.Transpose.ROTATE_180), self.__palette
        )

    def small_tile(self, xpos: int, ypos: int) -> SmallColorTile:
        if xpos + 8 > self.width_px or ypos + 8 > self.height_px:
            raise ImageError(self.filename, f"position out of bounds: { xpos }, { ypos }")

        return [
            self.__palette[self.__image.getpixel((x, y))] for y in range(ypos, ypos + 8) for x in range(xpos, xpos + 8)  # type: ignore
        ]

    def large_tile(self, xpos: int, ypos: int) -> LargeColorTile:
        if xpos + 16 > self.width_px or ypos + 16 > self.height_px:
            raise ImageError(self.filename, f"position out of bounds: { xpos }, { ypos }")

        return [
            self.__palette[self.__image.getpixel((x, y))]  # type: ignore
            for y in range(ypos, ypos + 16)
            for x in range(xpos, xpos + 16)
        ]


def load_image_tile_extractor(filename: Filename) -> ImageTileExtractor:
    try:
        with PIL.Image.open(filename) as image:
            image.load()
    except Exception as e:
        raise ImageError(filename, str(e))

    if image.mode == "RGB":
        return RgbImageTileExtractor(filename, image)
    elif image.mode == "P":
        return IndexedImageTileExtractor(filename, image)
    else:
        return RgbImageTileExtractor(filename, image.convert("RGB"))


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


def extract_tiles_and_build_tilemap(
    image: ImageTileExtractor,
    tileset: AbstractTilesetMap,
    palette_map: PaletteMap,
) -> TileMap:
    assert len(palette_map.color_maps) <= 8

    map_width: Final = image.width_px // 8
    map_height: Final = image.height_px // 8

    invalid_tiles = list()

    tilemap: list[TileMapEntry] = list()

    for tile_index, tile in enumerate(image.extract_small_tiles()):
        palette_id, color_map = palette_map.palette_for_tile(tile)

        if color_map:
            assert palette_id is not None

            # Must be bytes() here as a dict() key must be immutable
            tile_data = bytes([color_map[c] for c in tile])
            tile_id, hflip, vflip = tileset.get_or_insert(tile_data)

            tilemap.append(TileMapEntry(tile_id, palette_id, hflip, vflip))
        else:
            invalid_tiles.append(tile_index)

    if invalid_tiles:
        raise InvalidTilesError("Cannot find palette", image.filename, invalid_tiles, map_width, 8)

    assert len(tilemap) == map_width * map_height

    return TileMap(width=map_width, height=map_height, grid=tilemap)


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


def image_to_snes(image: ImageTileExtractor, palette: Palette, bpp: int) -> tuple[TileMap, bytes, bytes]:
    # Return (tilemap, tile_data, palette_data)

    palette_map = palette.create_map(bpp)
    palette_data = palette.snes_data()

    tileset = SmallTilesetMap()
    tilemap = extract_tiles_and_build_tilemap(image, tileset, palette_map)

    tile_data = convert_snes_tileset(tileset.tiles(), bpp)

    return tilemap, tile_data, palette_data
