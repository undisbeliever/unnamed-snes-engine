#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import PIL.Image  # type: ignore
from collections import OrderedDict
from typing import Any, Callable, Final, Iterable, NamedTuple, Optional

from .common import EngineData, FixedSizedData, DynamicSizedData, print_error
from .palette import PaletteColors
from .snes import (
    AbstractTilesetMap,
    ImageError,
    SmallTileData,
    convert_snes_tileset,
    convert_tilemap_and_tileset,
    create_tilemap_data,
    extract_small_tile_grid,
    hflip_tile,
    vflip_tile,
)

from .json_formats import Name, SecondLayerInput


SECOND_LAYER_BPP: Final = 4

MIN_WIDTH: Final = 256
MIN_HEIGHT: Final = 256

MAX_TILEMAP_CELLS: Final = 6 * 1024


class SecondLayerTilesetMap(AbstractTilesetMap):
    N_BG_TILES: Final = 1024

    def __init__(self) -> None:
        super().__init__()
        self._tiles: Final[list[SmallTileData]] = []

    def get_or_insert(self, tile_data: SmallTileData) -> tuple[int, bool, bool]:
        tile_match = self._map.get(tile_data, None)
        if tile_match is not None:
            return tile_match
        else:
            # Second Layer tiles are added in reverse order (from 1023 down to 0)
            self._tiles.append(tile_data)
            return self._add_to_map(tile_data, self.N_BG_TILES - len(self._tiles))

    def tiles(self) -> Iterable[SmallTileData]:
        return reversed(self._tiles)


def convert_second_layer(sli: SecondLayerInput, palettes: dict[Name, PaletteColors]) -> EngineData:
    image_filename = sli.source

    with PIL.Image.open(image_filename) as image:
        image.load()

    if image.width < MIN_WIDTH or image.height < MIN_HEIGHT:
        raise ImageError(image_filename, f"Image is too small ({image.width} x {image.height} min: {MIN_WIDTH} x {MIN_HEIGHT})")

    if image.width % 8 != 0 or image.height % 8 != 0:
        raise ImageError(image_filename, f"Image is not a multiple of 8 in width or height")

    width = image.width // 8
    height = image.height // 8

    if width * height > MAX_TILEMAP_CELLS:
        raise ImageError(image_filename, f"Image is too large ({width * height} cells, max: {MAX_TILEMAP_CELLS})")

    if width > 0xFF or height > 0xFF:
        raise ImageError(image_filename, f"Image is too large ({width * 8} x {height * 8}, max: 2040 x 2040)")

    pal = palettes.get(sli.palette)
    if pal is None:
        raise RuntimeError(f"Cannot load palette {sli.palette}")

    tileset = SecondLayerTilesetMap()
    tilemap = convert_tilemap_and_tileset(
        extract_small_tile_grid(image), image_filename, tileset, pal.create_map(SECOND_LAYER_BPP), width, height
    )
    tile_data = convert_snes_tileset(tileset.tiles(), SECOND_LAYER_BPP)

    ram_data = bytes(
        [
            width,
            height,
            # ::TODO add callback::
        ]
    ) + create_tilemap_data(tilemap, sli.tile_priority)

    return EngineData(ram_data=DynamicSizedData(ram_data), ppu_data=DynamicSizedData(tile_data))
