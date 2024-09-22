# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from .data_store import DataStore, EngineData, FixedSizedData, BaseResourceData, DungeonResourceData
from .errors import SimpleMultilineError
from .audio import BLANK_SONG_NAME
from .json_formats import DungeonInput, Mappings, OtherResources, MsPalettesJson, AudioProject, Name

import re
from typing import Any, Final, NamedTuple, Optional


MAX_WIDTH: Final = 16
MAX_HEIGHT: Final = 16

MAX_BG_TILES: Final = 1024

INFINITE_FLAG: Final = 1 << 7


class DungeonError(SimpleMultilineError):
    pass


class DungeonIntermediate(NamedTuple):
    width: int
    height: int
    default_room: tuple[int, int]


def get_resource_id(name: Name, name_list: list[Name], m_type: str, error_list: list[str]) -> int:
    try:
        return name_list.index(name)
    except ValueError:
        error_list.append(f"Cannot find {m_type} resource_id: {name}")
        return 0


def get_optional_resource_id(name: Optional[Name], name_list: list[Name], m_type: str, error_list: list[str]) -> int:
    if name is None:
        return 0xFF
    else:
        return get_resource_id(name, name_list, m_type, error_list)


def get_song_id(name: Optional[Name], audio_project: AudioProject, error_list: list[str]) -> int:
    if name is None:
        return 0xFF

    if name == BLANK_SONG_NAME:
        return 0

    song = audio_project.songs.get(name)
    if song is None:
        error_list.append(f"Cannot find song song: {name}")
        return 0

    return song.id


ROOM_LOCATION_REGEX = re.compile(r"(\d+)-(\d+)(-.*)+")


def extract_room_position(room_name: str) -> tuple[int, int]:
    m = ROOM_LOCATION_REGEX.match(room_name)
    if not m:
        raise RuntimeError("Invalid room filename")

    return int(m.group(1), 10), int(m.group(2), 10)


def get_n_tiles(dungeon: DungeonInput, data_store: DataStore, error_list: list[str]) -> int:
    n_tiles = 0

    mt_tileset = data_store.get_mt_tileset(dungeon.tileset)
    if mt_tileset:
        n_tiles += mt_tileset.tile_map.n_tiles()
    else:
        error_list.append(f"Dependency error: cannot load second_layer {dungeon.second_layer}")

    if dungeon.second_layer:
        sl = data_store.get_second_layer(dungeon.second_layer)
        if sl:
            n_tiles += sl.n_tiles
        else:
            error_list.append(f"Dependency error: cannot load second_layer {dungeon.second_layer}")

    return n_tiles


def compile_dungeon_header(
    dungeon: DungeonInput,
    mappings: Mappings,
    other_resources: OtherResources,
    ms_palettes: MsPalettesJson,
    audio_project: AudioProject,
    data_store: DataStore,
) -> tuple[EngineData, DungeonIntermediate]:
    error_list = list()

    if dungeon.width < 0 or dungeon.width > MAX_WIDTH or dungeon.height < 0 or dungeon.height > MAX_HEIGHT:
        error_list.append(f"Invalid dungeon size ({dungeon.width}x{dungeon.height}, max {MAX_WIDTH}x{MAX_HEIGHT})")

    palette_id = get_resource_id(dungeon.palette, mappings.palettes, "palette", error_list)
    tileset_id = get_resource_id(dungeon.tileset, mappings.mt_tilesets, "tileset", error_list)
    second_layer_id = get_optional_resource_id(dungeon.second_layer, mappings.second_layers, "second_layer", error_list)
    ms_spritesheet_id = get_resource_id(dungeon.ms_spritesheet, mappings.ms_spritesheets, "ms_spritesheet", error_list)

    ms_palette_id = 0
    if ms_palette := ms_palettes.ms_palettes.get(dungeon.ms_palette):
        ms_palette_id = ms_palette.id
    else:
        error_list.append(f"Cannot find ms_palette: {dungeon.ms_palette}")

    if room_m := ROOM_LOCATION_REGEX.match(dungeon.default_room):
        default_room_x = int(room_m.group(1))
        default_room_y = int(room_m.group(2))
    else:
        error_list.append(f"Invalid room name: {dungeon.default_room}")
        default_room_x = 0
        default_room_y = 0

    flags = 0
    if dungeon.infinite:
        flags |= INFINITE_FLAG

    song_id = get_song_id(dungeon.song, audio_project, error_list)

    second_layer = None
    sl_callback = None
    if dungeon.second_layer is not None:
        second_layer = other_resources.second_layers.get(dungeon.second_layer)
        if second_layer is None:
            error_list.append(f"Cannot find second_layer: {dungeon.second_layer}")

    if second_layer and second_layer.callback:
        sl_callback = mappings.sl_callbacks.get(second_layer.callback)
        if sl_callback is None:
            error_list.append(f"Unknown second-layer callback: {second_layer.callback}")

    n_tiles = get_n_tiles(dungeon, data_store, error_list)
    if n_tiles > MAX_BG_TILES:
        error_list.append(f"mt_tileset and second_layer tiles cannot fit in VRAM ({n_tiles} tiles required, {MAX_BG_TILES} max)")

    if error_list:
        raise DungeonError("Error compiling dungeon", error_list)

    ram_data = bytes(
        [
            flags,
            dungeon.width,
            dungeon.height,
            default_room_x,
            default_room_y,
            palette_id,
            tileset_id,
            second_layer_id,
            ms_palette_id,
            ms_spritesheet_id,
            song_id,
        ]
    )

    return (
        EngineData(
            ram_data=FixedSizedData(ram_data),
            ppu_data=None,
        ),
        DungeonIntermediate(
            width=dungeon.width,
            height=dungeon.height,
            default_room=(default_room_x, default_room_y),
        ),
    )


# ::TODO change Any to correct typing::
def combine_dungeon_and_room_data(dungeon: BaseResourceData, rooms: dict[tuple[int, int], Any]) -> EngineData:
    if not isinstance(dungeon, DungeonResourceData):
        raise RuntimeError("Not a DungeonResourceData")

    assert dungeon.includes_room_data is False
    assert isinstance(dungeon.data.ram_data, FixedSizedData)

    errors = list()

    dungeon_name: Final = dungeon.resource_name
    dungeon_header_data: Final = dungeon.data.ram_data.data()
    width: Final = dungeon.header.width
    height: Final = dungeon.header.height

    room_table: Final = bytearray(width * height * 2)
    room_data = bytearray()

    rd_offset: Final = len(room_table)

    # Must sort the rooms to create a reproducible binary
    for (x, y), r in sorted(rooms.items()):
        if x >= 0 and x < width and y >= 0 and y < height:
            i = (y * width + x) * 2
            o = rd_offset + len(room_data)
            room_table[i] = o & 0xFF
            room_table[i + 1] = o >> 8

            room_data += r.data.ram_data.data()
        else:
            errors.append(f"Invalid room position: {x}, {y}")

    # ::TODO check tiles surrounding the default spawn location are not solid::
    if dungeon.header.default_room not in rooms:
        errors.append(f"Cannot load default room: {dungeon.header.default_room}")

    if errors:
        raise DungeonError(f"Cannot add rooms to dungeon '{dungeon_name}'", errors)

    return EngineData(
        ram_data=FixedSizedData(dungeon_header_data + room_table + room_data),
        ppu_data=None,
    )
