# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from .data_store import EngineData, FixedSizedData
from .errors import SimpleMultilineError
from .audio import BLANK_SONG_NAME
from .json_formats import DungeonInput, Mappings, OtherResources, AudioProject, Name

from typing import Any, Final, Optional


MAX_WIDTH: Final = 16
MAX_HEIGHT: Final = 16

INFINITE_FLAG: Final = 1 << 7


class DungeonError(SimpleMultilineError):
    pass


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


def compile_dungeon_header(
    dungeon: DungeonInput, mappings: Mappings, other_resources: OtherResources, audio_project: AudioProject
) -> EngineData:
    error_list = list()

    if dungeon.width < 0 or dungeon.width > MAX_WIDTH or dungeon.height < 0 or dungeon.height > MAX_HEIGHT:
        error_list.append(f"Invalid dungeon size ({dungeon.width}x{dungeon.height}, max {MAX_WIDTH}x{MAX_HEIGHT})")

    tileset_id = get_resource_id(dungeon.tileset, mappings.mt_tilesets, "tileset", error_list)
    second_layer_id = get_optional_resource_id(dungeon.second_layer, mappings.second_layers, "second_layer", error_list)
    ms_spritesheet_id = get_resource_id(dungeon.ms_spritesheet, mappings.ms_spritesheets, "ms_spritesheet", error_list)

    flags = 0
    if dungeon.infinite:
        flags |= INFINITE_FLAG

    song_id = get_song_id(dungeon.song, audio_project, error_list)

    # ::TODO verify tileset/second-layer will fit in VRAM::

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

    if error_list:
        raise DungeonError("Error compiling dungeon", error_list)

    ram_data = bytes(
        [
            flags,
            dungeon.width,
            dungeon.height,
            tileset_id,
            second_layer_id,
            ms_spritesheet_id,
            song_id,
        ]
    )

    return EngineData(
        ram_data=FixedSizedData(ram_data),
        ppu_data=None,
    )


# ::TODO change Any to correct typing::
def combine_dungeon_and_room_data(dungeon_name: Name, dungeon: EngineData, rooms: dict[tuple[int, int], Any]) -> EngineData:
    assert isinstance(dungeon.ram_data, FixedSizedData)

    errors = list()

    header: Final = dungeon.ram_data.data()
    width: Final = header[1]
    height: Final = header[2]

    room_table: Final = bytearray(width * height * 2)
    room_data = bytearray()

    rd_offset: Final = len(room_table)

    for (x, y), r in rooms.items():
        if x < 0 or x >= width or y < 0 or y >= height:
            errors.append(f"Invalid room position: {x}, {y}")

        i = (y * width + x) * 2
        o = rd_offset + len(room_data)
        room_table[i] = o & 0xFF
        room_table[i + 1] = o >> 8

        room_data += r.data.ram_data.data()

    if errors:
        raise DungeonError(f"Cannot add rooms to dungeon '{dungeon_name}'", errors)

    return EngineData(
        ram_data=FixedSizedData(header + room_table + room_data),
        ppu_data=None,
    )
