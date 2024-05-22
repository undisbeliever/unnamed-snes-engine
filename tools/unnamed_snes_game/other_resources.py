#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import PIL.Image  # type: ignore
import sys
from collections import OrderedDict
from typing import Any, Callable, Final, Iterable, NamedTuple, Optional

from .common import EngineData, FixedSizedData, DynamicSizedData, print_error
from .palette import PaletteResource

from .snes import (
    load_image_tile_extractor,
    extract_tiles_from_paletted_image,
    convert_mode7_tileset,
    convert_snes_tileset,
    image_and_palette_map_to_snes,
    create_tilemap_data,
    SmallTileData,
)

from .json_formats import (
    load_mappings_json,
    load_other_resources_json,
    Name,
    Filename,
    Mappings,
    TilesInput,
    BackgroundImageInput,
    OtherResources,
)


# ::TODO add palettes::


#
# Tiles
# =====
#

TILE_FORMATS: dict[str, Callable[[Iterable[SmallTileData]], bytes]] = {
    "m7": convert_mode7_tileset,
    "mode7": convert_mode7_tileset,
    "1bpp": lambda tiles: convert_snes_tileset(tiles, 1),
    "2bpp": lambda tiles: convert_snes_tileset(tiles, 2),
    "3bpp": lambda tiles: convert_snes_tileset(tiles, 3),
    "4bpp": lambda tiles: convert_snes_tileset(tiles, 4),
    "8bpp": lambda tiles: convert_snes_tileset(tiles, 8),
}


def convert_tiles(t: TilesInput) -> EngineData:
    tile_converter = TILE_FORMATS[t.format]

    with PIL.Image.open(t.source) as image:
        image.load()

    tile_data = tile_converter(extract_tiles_from_paletted_image(image))

    return EngineData(
        ram_data=None,
        ppu_data=DynamicSizedData(tile_data),
    )


#
# Background Images
# =================
#

BI_BPP_FORMATS: dict[str, int] = {
    "2bpp": 2,
    "4bpp": 4,
    "8bpp": 8,
}

NAMETABLE_SIZE_BYTES: Final = 32 * 32 * 2
VALID_BGI_HEADER_TM_SIZES: Final = (1, 2, 4)


def convert_bg_image(bgi: BackgroundImageInput, palettes: dict[Name, PaletteResource]) -> EngineData:
    bpp = BI_BPP_FORMATS[bgi.format]

    image = load_image_tile_extractor(bgi.source)

    pal = palettes.get(bgi.palette)
    if pal is None:
        raise RuntimeError(f"Cannot load palette {bgi.palette}")

    tilemap, tile_data = image_and_palette_map_to_snes(image, pal.create_map(bpp), bpp)

    tilemap_data = create_tilemap_data(tilemap, bgi.tile_priority)

    tm_size = len(tilemap_data) // NAMETABLE_SIZE_BYTES

    if tm_size not in VALID_BGI_HEADER_TM_SIZES:
        raise ValueError(f"Invalid number of nametables, expected { VALID_BGI_HEADER_TM_SIZES }, got { tm_size }.")

    header = bytes(
        [
            tm_size << 3,
        ]
    )

    ppu_data = bytearray()
    ppu_data += tilemap_data
    ppu_data += tile_data

    return EngineData(
        ram_data=FixedSizedData(header),
        ppu_data=DynamicSizedData(ppu_data),
    )
