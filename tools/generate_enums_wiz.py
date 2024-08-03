#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import argparse
from io import StringIO
from typing import TextIO, Sequence

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
)
from unnamed_snes_game.common import (
    MS_FS_DATA_BANK_OFFSET,
    ROOM_DATA_BANK_OFFSET,
    DYNAMIC_SPRITE_TILES_BANK_OFFSET,
    USB2SNES_DATA_BANK_OFFSET,
    ResourceType,
)


def room_id_for_name(room_name: RoomName) -> int:
    m = re.match(r"(\d+)-(\d+)-.+$", room_name)

    if not m:
        raise ValueError("Invalid room name")

    return int(m.group(1), 10) + 16 * int(m.group(2), 10)


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

    out.write("};\n\n")


def write_gamemodes_enum(out: TextIO, gamemodes: list[GameMode]) -> None:
    out.write("enum GameModes : u8 {\n")

    for gn in gamemodes:
        out.write(f"  {gn.name.upper()},\n")

    out.write("};\n\n")


def generate_wiz_code(mappings: Mappings, audio_project: AudioProject) -> str:
    with StringIO() as out:
        out.write("namespace resources {\n\n")

        out.write(f"let MS_FS_DATA_BANK = { mappings.memory_map.first_resource_bank + MS_FS_DATA_BANK_OFFSET };\n")
        out.write(
            f"let DYNAMIC_SPRITE_TILES_DATA_BANK = { mappings.memory_map.first_resource_bank + DYNAMIC_SPRITE_TILES_BANK_OFFSET };\n"
        )
        out.write(f"let ROOM_DATA_BANK = { mappings.memory_map.first_resource_bank + ROOM_DATA_BANK_OFFSET };\n\n")

        out.write(f"let _STARTING_ROOM = { room_id_for_name(mappings.starting_room) };\n\n")

        out.write(f"let _USB2SNES_DATA_ADDR = { resources_over_usb2snes_data_addr(mappings.memory_map) };\n")
        out.write(f"let _BANK_SIZE = { mappings.memory_map.mode.bank_size };\n\n")

        out.write(f"let N_SECOND_LAYERS = { len(mappings.second_layers) };\n\n")
        out.write(f"let N_SONGS = { len(mappings.songs) };\n\n")

        out.write("let n_resources_per_type = [")
        for rt in ResourceType:
            l = len(getattr(mappings, rt.name))
            out.write(f"{ l }, ")
        out.write("];\n\n")

        for rt in ResourceType:
            write_enum(out, rt.name, getattr(mappings, rt.name))

        out.write("}\n\n")

        write_sound_effects(out, audio_project.sound_effects)
        out.write(f"let N_SOUND_EFFECTS = { len(audio_project.sound_effects) };\n\n")

        write_gamemodes_enum(out, mappings.gamemodes)

        write_enum_inc_by_2(out, "RoomTransitions", mappings.room_transitions)

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
