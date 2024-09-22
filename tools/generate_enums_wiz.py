#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import argparse
from io import StringIO
from typing import Final, OrderedDict, TextIO, Sequence

from unnamed_snes_game.json_formats import (
    RoomName,
    load_mappings_json,
    load_audio_project,
    Name,
    Mappings,
    AudioProject,
    SfxExportOrder,
    MemoryMap,
    GameMode,
    GameState,
    GameStateVar,
)
from unnamed_snes_game.memory_map import (
    MS_FS_DATA_BANK_OFFSET,
    DYNAMIC_SPRITE_TILES_BANK_OFFSET,
    RESOURCE_ADDR_TABLE_BANK_OFFSET,
    USB2SNES_DATA_BANK_OFFSET,
)
from unnamed_snes_game.gamestate import gamestate_data_size, validate_gamestate_cart_size
from unnamed_snes_game.enums import ResourceType
from unnamed_snes_game.audio import BLANK_SONG_NAME


def room_pos_for_name(room_name: RoomName) -> tuple[int, int]:
    m = re.match(r"(\d+)-(\d+)-.+$", room_name)

    if not m:
        raise ValueError("Invalid room name")

    return int(m.group(1), 10), int(m.group(2), 10)


def resources_over_usb2snes_data_addr(memory_map: MemoryMap) -> int:
    return ((memory_map.first_resource_bank + USB2SNES_DATA_BANK_OFFSET) << 16) | memory_map.mode.bank_start


def write_enum(out: TextIO, name: Name, name_list: Sequence[Name]) -> None:
    out.write(f"enum { name } : u8 {{\n")

    for n in name_list:
        out.write(f"  { n },\n")

    out.write("};\n\n")


def write_enum_inc_by_2(out: TextIO, name: Name, name_list: list[Name]) -> None:
    out.write(f"enum { name } : u8 {{\n")

    for i, n in enumerate(name_list):
        out.write(f"  { n } = {i * 2},\n")

    out.write("};\n\n")


def write_songs_enum(out: TextIO, audio_project: AudioProject) -> None:
    out.write("enum songs : u8 {\n")
    out.write(f"  {BLANK_SONG_NAME},\n")
    for s in audio_project.songs.values():
        out.write(f"  { s.name },\n")
    out.write("};\n")
    out.write(f"let N_SONGS = { len(audio_project.songs) + 1};\n\n")


def write_sound_effects(out: TextIO, sfx_eo: SfxExportOrder) -> None:
    out.write("enum sound_effects : u8 {\n")

    for i, n in enumerate(sfx_eo.export_order):
        if i == sfx_eo.first_low_priority_sfx:
            out.write("  // low priority sound effects\n")
        elif i == sfx_eo.n_high_priority_sfx:
            out.write("  // normal priority sound effects\n")
        elif i == 0:
            out.write("  // high priority sound effects\n")
        out.write(f"  { n },\n")

    out.write("};\n")
    out.write(f"let N_SOUND_EFFECTS = { len(sfx_eo.export_order) };\n\n")


def write_gamemodes_enum(out: TextIO, gamemodes: list[GameMode]) -> None:
    out.write("enum GameModes : u8 {\n")

    for gn in gamemodes:
        out.write(f"  {gn.name.upper()},\n")

    out.write("};\n\n")


def write_gamestate_enum(out: TextIO, name: Name, values: OrderedDict[Name, GameStateVar], element_size: int) -> None:
    out.write(f"enum { name } : u8 {{\n")

    assert element_size.bit_count() == 1, "element_size not a power of 2"
    mask: Final = 0xFF & ~(element_size - 1)

    for name, v in values.items():
        if v.comment:
            c = v.comment.replace("\n", "\n  // ")
            out.write(f"  // { c }\n")

        assert v.var_index & mask == v.var_index
        out.write(f"  { name } = { v.var_index },\n")

    out.write("};\n\n")


def write_gamestate(out: TextIO, gs: GameState) -> None:
    validate_gamestate_cart_size(gs)

    out.write("namespace gs {\n\n")

    assert len(gs.identifier) == 4
    out.write(f'let IDENTIFIER = "{gs.identifier}";\n')
    out.write(f"let VERSION = {gs.version};\n")
    out.write("\n")

    out.write(f"// gamestate data size is { gamestate_data_size(gs) } bytes\n")
    out.write(f"let CART_RAM_SIZE = {gs.cart_ram_size};\n")
    out.write(f"let N_SAVE_SLOTS = {gs.n_save_slots};\n")
    out.write(f"let N_SAVE_COPIES = {gs.n_save_copies};\n")
    out.write("\n")

    out.write(f"let N_U8_VARS = {gs.u8_array_len};\n")
    out.write(f"let N_U16_VARS = {gs.u16_array_len};\n")
    out.write("\n")

    out.write("// Global Flags\n")
    write_gamestate_enum(out, "gf", gs.global_flags, 1)

    write_gamestate_enum(out, "var8", gs.u8_vars, 1)
    write_gamestate_enum(out, "var16", gs.u16_vars, 2)

    out.write("}\n\n")


def generate_wiz_code(mappings: Mappings, audio_project: AudioProject) -> str:
    with StringIO() as out:
        out.write("namespace resources {\n\n")

        out.write(f"let MS_FS_DATA_BANK = 0x{mappings.memory_map.first_resource_bank + MS_FS_DATA_BANK_OFFSET:02x};\n")
        out.write(
            f"let DYNAMIC_SPRITE_TILES_DATA_BANK = 0x{mappings.memory_map.first_resource_bank + DYNAMIC_SPRITE_TILES_BANK_OFFSET:02x};\n"
        )
        out.write(
            f"let RESOURCE_ADDR_TABLE_BANK = 0x{mappings.memory_map.first_resource_bank + RESOURCE_ADDR_TABLE_BANK_OFFSET:02x};\n\n"
        )
        out.write(f"let N_RESOURCE_TYPES = { len(ResourceType) };\n\n")

        out.write(f"let _USB2SNES_DATA_ADDR = 0x{resources_over_usb2snes_data_addr(mappings.memory_map):06x};\n")
        out.write(f"let _BANK_SIZE = { mappings.memory_map.mode.bank_size };\n\n")

        out.write(f"let N_SECOND_LAYERS = { len(mappings.second_layers) };\n\n")

        write_enum(out, "palettes", mappings.palettes)
        write_enum(out, "mt_tilesets", mappings.mt_tilesets)
        write_enum(out, "second_layers", mappings.second_layers)
        write_enum(out, "ms_spritesheets", mappings.ms_spritesheets)
        write_enum(out, "tiles", mappings.tiles)
        write_enum(out, "bg_images", mappings.bg_images)

        write_songs_enum(out, audio_project)

        out.write("}\n\n")

        write_sound_effects(out, audio_project.sound_effects)

        write_gamemodes_enum(out, mappings.gamemodes)

        write_enum_inc_by_2(out, "RoomTransitions", mappings.room_transitions)

        write_gamestate(out, mappings.gamestate)

        return out.getvalue()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", required=True, help="wiz output file")
    parser.add_argument("mappings_json_file", action="store", help="mappings json file input")
    parser.add_argument("audio_project_file", action="store", help="terrific audio project file")

    args = parser.parse_args()

    return args


def main() -> None:
    args = parse_arguments()

    mappings = load_mappings_json(args.mappings_json_file)
    audio_project = load_audio_project(args.audio_project_file)

    out = generate_wiz_code(mappings, audio_project)

    with open(args.output, "w") as fp:
        fp.write(out)


if __name__ == "__main__":
    main()
