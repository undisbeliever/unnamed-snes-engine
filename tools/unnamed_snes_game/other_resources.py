#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import PIL.Image  # type: ignore
import sys
from collections import OrderedDict
from typing import Any, Callable, Final, Iterable, NamedTuple, Optional

from .common import print_error

from .snes import (
    extract_tiles_from_paletted_image,
    convert_mode7_tileset,
    convert_snes_tileset,
    image_to_snes,
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


def convert_tiles(t: TilesInput) -> bytes:
    tile_converter = TILE_FORMATS[t.format]

    with PIL.Image.open(t.source) as image:
        image.load()

    return tile_converter(extract_tiles_from_paletted_image(image))


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


def convert_bg_image(bgi: BackgroundImageInput) -> bytes:

    bpp = BI_BPP_FORMATS[bgi.format]

    with PIL.Image.open(bgi.source) as image:
        image.load()

    with PIL.Image.open(bgi.palette) as pal_image:
        pal_image.load()

    tilemap, tile_data, palette_data = image_to_snes(image, bgi.source, pal_image, bpp)

    tilemap_data = create_tilemap_data(tilemap, bgi.tile_priority)

    n_palette_rows = len(palette_data) // 32
    assert len(palette_data) % 32 == 0
    assert 1 < n_palette_rows < 8

    tm_size = len(tilemap_data) // NAMETABLE_SIZE_BYTES

    if tm_size not in VALID_BGI_HEADER_TM_SIZES:
        raise ValueError(f"Invalid number of nametables, expected { VALID_BGI_HEADER_TM_SIZES }, got { tm_size }.")

    header_byte = (n_palette_rows << 4) | (tm_size << 2)

    out = bytearray()
    out.append(header_byte)
    out += palette_data
    out += tilemap_data
    out += tile_data

    return out
