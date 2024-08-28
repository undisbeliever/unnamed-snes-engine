#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


from enum import IntFlag
from typing import Final, Iterable, NamedTuple, Optional

from .data_store import DataStore, EngineData, DynamicSizedData
from .errors import SimpleMultilineError
from .palette import PaletteResource
from .snes import (
    AbstractTilesetMap,
    load_image_tile_extractor,
    ImageTileExtractor,
    ImageError,
    SmallTileData,
    ConstSmallTileMap,
    TileMapEntry,
    convert_snes_tileset,
    extract_tiles_and_build_tilemap,
    create_tilemap_data,
)
from .callbacks import parse_callback_parameters, SL_CALLBACK_PARAMETERS
from .json_formats import SecondLayerInput, Mappings


SECOND_LAYER_BPP: Final = 4

MIN_WIDTH: Final = 256
MIN_HEIGHT: Final = 256

MT_TILE_PX: Final = 16
N_SL_METATILES: Final = 256

MAX_SL_CELLS: Final = 10 * 1024

BLANK_CALLBACK_PARAMETERS: Final = bytes(SL_CALLBACK_PARAMETERS.parameter_size)


class SlFlags(IntFlag):
    PART_OF_ROOM = 0x80
    ABOVE_METATILES = 0x40


class SecondLayerTilesetMap(AbstractTilesetMap):
    N_BG_TILES: Final = 1024

    def __init__(self, mt_tiles: Optional[ConstSmallTileMap]) -> None:
        super().__init__()
        self._tiles: Final[list[SmallTileData]] = []
        self.__mt_tiles: Final = mt_tiles

    def get_or_insert(self, tile_data: SmallTileData) -> tuple[int, bool, bool]:
        if self.__mt_tiles:
            tile_match = self.__mt_tiles.get(tile_data)
            if tile_match is not None:
                return tile_match

        tile_match = self._map.get(tile_data, None)
        if tile_match is not None:
            return tile_match
        else:
            # Second Layer tiles are added in reverse order (from 1023 down to 0)
            self._tiles.append(tile_data)
            return self._add_to_map(tile_data, self.N_BG_TILES - len(self._tiles))

    def tiles(self) -> Iterable[SmallTileData]:
        return reversed(self._tiles)


class SecondLayerImage(NamedTuple):
    width: int
    height: int
    metatiles: bytes
    sl_map: Optional[bytes]
    tile_data: bytes


def convert_sl_image(
    image: ImageTileExtractor,
    tile_priority: bool,
    palette: PaletteResource,
    mt_tiles: Optional[ConstSmallTileMap],
) -> SecondLayerImage:
    if image.width_px % MT_TILE_PX != 0 or image.height_px % MT_TILE_PX != 0:
        raise ImageError(image.filename, f"Image is not a multiple of {MT_TILE_PX} in width or height")

    width: Final = image.width_px // MT_TILE_PX
    height: Final = image.height_px // MT_TILE_PX

    width8: Final = width * 2
    height8: Final = height * 2

    tileset: Final = SecondLayerTilesetMap(mt_tiles)
    tilemap8: Final = extract_tiles_and_build_tilemap(image, tileset, palette.create_map(SECOND_LAYER_BPP))

    mt_map: dict[tuple[TileMapEntry, ...], int] = dict()
    mt_top_left = list()
    mt_top_right = list()
    mt_bottom_left = list()
    mt_bottom_right = list()

    sl_map = bytearray()

    for mty in range(0, height8, 2):
        for mtx in range(0, width8, 2):
            gi = mty * width8 + mtx
            mt = (
                tilemap8.grid[gi],
                tilemap8.grid[gi + 1],
                tilemap8.grid[gi + width8],
                tilemap8.grid[gi + width8 + 1],
            )

            mt_index = mt_map.get(mt)
            if mt_index is None:
                mt_index = len(mt_map) % N_SL_METATILES
                mt_map[mt] = mt_index
                mt_top_left.append(mt[0])
                mt_top_right.append(mt[1])
                mt_bottom_left.append(mt[2])
                mt_bottom_right.append(mt[3])

            sl_map.append(mt_index)

    assert len(mt_map) == len(mt_top_left) == len(mt_bottom_left) == len(mt_bottom_right)

    if len(mt_map) > N_SL_METATILES:
        raise ImageError(image.filename, f"Too many metatiles in image ({len(mt_map)}, max: {N_SL_METATILES})")

    mt_padding: Final = bytes(2 * (N_SL_METATILES - len(mt_map)))

    return SecondLayerImage(
        width=width,
        height=height,
        metatiles=(
            create_tilemap_data(mt_top_left, tile_priority)
            + mt_padding
            + create_tilemap_data(mt_top_right, tile_priority)
            + mt_padding
            + create_tilemap_data(mt_bottom_left, tile_priority)
            + mt_padding
            + create_tilemap_data(mt_bottom_right, tile_priority)
            + mt_padding
        ),
        sl_map=sl_map,
        tile_data=convert_snes_tileset(tileset.tiles(), SECOND_LAYER_BPP),
    )


def convert_sl_part_of_room(
    image: ImageTileExtractor,
    tile_priority: bool,
    palette: PaletteResource,
    mt_tiles: Optional[ConstSmallTileMap],
) -> SecondLayerImage:
    if image.width_px != 256 or image.height_px != 256:
        raise ImageError(
            image.filename, f"PART_OF_ROOM second layer must be 256x256px in size (image is {image.width_px} x {image.height_px})"
        )

    width: Final = image.width_px // MT_TILE_PX
    height: Final = image.height_px // MT_TILE_PX

    tileset: Final = SecondLayerTilesetMap(mt_tiles)
    tilemap8: Final = extract_tiles_and_build_tilemap(image, tileset, palette.create_map(SECOND_LAYER_BPP))

    mt = list()

    # ::TODO create a function that builds the tilemap in metatile order::
    assert len(tilemap8.grid) == 32 * 32
    for xoffset, yoffset in ((0, 0), (1, 0), (0, 1), (1, 1)):
        for y in range(yoffset, 32, 2):
            for x in range(xoffset, 32, 2):
                mt.append(tilemap8.get_tile(x, y))

    return SecondLayerImage(
        width=width,
        height=height,
        metatiles=create_tilemap_data(mt, tile_priority),
        sl_map=None,
        tile_data=convert_snes_tileset(tileset.tiles(), SECOND_LAYER_BPP),
    )


def convert_second_layer(sli: SecondLayerInput, mapping: Mappings, data_store: DataStore) -> EngineData:
    image_filename = sli.source

    pal_r = data_store.get_palette(sli.palette)
    if pal_r is None:
        raise RuntimeError(f"Cannot load palette {sli.palette}")
    pal: Final = pal_r.palette

    mt_tiles = None
    if sli.mt_tileset:
        # ::TODO validate second-layer is loaded with the mt_tileset::
        mt_tiles_r = data_store.get_mt_tileset(sli.mt_tileset)
        if mt_tiles_r is None:
            raise RuntimeError(f"Cannot load MetaTile tileset {sli.mt_tileset}")
        mt_tiles = mt_tiles_r.tile_map

    image = load_image_tile_extractor(image_filename)

    flags = SlFlags(0)

    if sli.part_of_room:
        flags |= SlFlags.PART_OF_ROOM
        sl = convert_sl_part_of_room(image, sli.tile_priority, pal, mt_tiles)
    else:
        sl = convert_sl_image(image, sli.tile_priority, pal, mt_tiles)

    if sli.above_metatiles:
        flags |= SlFlags.ABOVE_METATILES

    if sl.width * sl.height > MAX_SL_CELLS:
        raise ImageError(image_filename, f"Image is too large ({sl.width * sl.height} cells, max: {MAX_SL_CELLS})")

    if sl.width > 0xFF or sl.height > 0xFF:
        raise ImageError(
            image_filename,
            f"Image is too large ({sl.width * MT_TILE_PX} x {sl.height * MT_TILE_PX}, max: {0xff * MT_TILE_PX} x {0xff * MT_TILE_PX}",
        )

    error_list: list[str] = list()

    if sli.callback:
        sl_callback = mapping.sl_callbacks.get(sli.callback)
        if sl_callback is None:
            raise RuntimeError(f"Unknown sl_callback: {sli.callback}")

        sl_callback_id = sl_callback.id
        callback_parameters = parse_callback_parameters(
            SL_CALLBACK_PARAMETERS, sl_callback, sli.parameters or {}, mapping, None, error_list
        )
    else:
        sl_callback_id = 0
        callback_parameters = BLANK_CALLBACK_PARAMETERS

    if error_list:
        raise SimpleMultilineError("Error compiling second layer", error_list)

    if len(sl.metatiles) != N_SL_METATILES * 4 * 2:
        raise RuntimeError("Invalid sl.metatiles size")

    ram_data = (
        bytes(
            [
                sl.width,
                sl.height,
                flags,
                sl_callback_id * 2,
            ]
        )
        + callback_parameters
        + sl.metatiles
    )
    assert len(ram_data) == 12 + 2048

    if flags & SlFlags.PART_OF_ROOM:
        assert sl.sl_map is None
    else:
        assert sl.sl_map
        if len(sl.sl_map) > MAX_SL_CELLS:
            raise RuntimeError("sl_map is too large")
        ram_data += sl.sl_map

    return EngineData(ram_data=DynamicSizedData(ram_data), ppu_data=DynamicSizedData(sl.tile_data))
