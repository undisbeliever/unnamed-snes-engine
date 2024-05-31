#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from collections import OrderedDict
from io import StringIO

from typing import Final, Optional

from unnamed_snes_game.json_formats import (
    load_mappings_json,
    load_entities_json,
    Name,
    Mappings,
    EntitiesJson,
    CallbackDict,
    RoomEvent,
    SecondLayerCallback,
    GameMode,
)


# NOTE: I cannot build these function tables in `gen/entities.wiz` or `gen/enums.wiz` as it causes a circular dependency

MAX_INTERACTIVE_FUNCTIONS: Final = 1 << 6


def next_power_of_two(n: int) -> int:
    i = 1
    while i < n:
        i *= 2
    return i


def function_table(
    out: StringIO, namespace: str, comment: Optional[str], name: Name, func_signature: str, prefix: str, function_list: list[str]
) -> None:
    namespaces = namespace.split(".")

    for ns in namespaces:
        out.write(f"namespace {ns} {{\n")

    if comment:
        out.write(f"// {comment}\n")
    out.write(f"const {name} : [{func_signature} ; {len(function_list)}] = [\n")
    for f in function_list:
        out.write(f"  {prefix}.{f},\n")
    out.write("];\n\n")

    out.write("}\n" * len(namespaces))


def interactive_tiles_table(out: StringIO, interactive_tile_functions: list[str]) -> None:
    # function 0 is null
    n_functions: Final = len(interactive_tile_functions) + 1

    table_size: Final = next_power_of_two(n_functions)
    table_mask: Final = (table_size - 1) * 2

    doorway_index: Final = interactive_tile_functions.index("doorway") + 1

    if table_size > MAX_INTERACTIVE_FUNCTIONS:
        raise ValueError("Too many interactive tile types: { len(interactive_tile_functions) }")

    def generate_table(table_name: str, fn_type: str, fn_name: str) -> None:
        null_line = f"  metatiles.interactive_tiles.null_function as {fn_type},\n"

        out.write(f"const { table_name } : [ { fn_type } ; { table_size } ] = [\n")
        out.write(null_line)

        for it in interactive_tile_functions:
            out.write(f"  metatiles.interactive_tiles.{ it }.{ fn_name },\n")

        for i in range(table_size - n_functions):
            out.write(null_line)

        out.write("];\n\n")

    out.write("namespace metatiles {\n")
    out.write("namespace interactive_tiles {\n\n")

    out.write(f"let DOORWAY_FUNCTION_TABLE_UNALIGNED_INDEX = 0x{ doorway_index * 2 :02x};\n\n")
    out.write(f"let FUNCTION_TABLE_MASK = 0x{ table_mask :02x};\n\n")

    generate_table("player_touches_tile_function_table", "func(u8 in y)", "player_touches_tile")
    generate_table("player_tile_collision_function_table", "func(u8 in y, metatiles.CollisionDirection in a)", "player_tile_collision")

    out.write("}\n}\n\n")


def callback_imports(out: StringIO, callbacks: CallbackDict, src_dir: str) -> None:
    for e in callbacks.values():
        if '"' in e.source:
            raise ValueError(f"Invalid source value for room event: {e.name}")
        if "/" not in e.source:
            out.write(f'import "src/{src_dir}/{e.source}";\n')
        else:
            out.write(f'import "../{e.source}";\n')


def room_events_table(out: StringIO, room_events: OrderedDict[Name, RoomEvent]) -> None:
    n_functions: Final = len(room_events)

    def generate_table(table_name: str, fn_type: str, fn_name: str) -> None:
        out.write(f"const { table_name } : [ { fn_type } ; { n_functions } ] = [\n")
        for e in room_events.values():
            out.write(f"  room_events.{ e.name }.{ fn_name },\n")
        out.write("];\n\n")

    out.write("namespace room_events {\n\n")

    out.write(f"let N_ROOM_EVENT_FUNCTIONS = { n_functions };\n\n")

    out.write("// Called when the room is loaded, in room transition code\n")
    out.write("// This function is allowed to modify the map data.\n")
    out.write("// This function MUST NOT call a MetaTile function.\n")
    generate_table("init_function_table", "func()", "init")

    out.write("// Called once per frame\n")
    generate_table("process_function_table", "func()", "process")

    out.write("}\n\n")


def second_layers_table(out: StringIO, sl_callbacks: OrderedDict[Name, SecondLayerCallback]) -> None:
    n_functions: Final = len(sl_callbacks) + 1

    def generate_table(table_name: str, fn_type: str, fn_name: str) -> None:
        out.write(f"const { table_name } : [ { fn_type } ; { n_functions } ] = [\n")
        out.write("  sl_callbacks.null_function,\n")
        for i, e in enumerate(sl_callbacks.values(), 1):
            assert e.id == i
            out.write(f"  sl_callbacks.{ e.name }.{ fn_name },\n")
        out.write("];\n\n")

    out.write("namespace sl_callbacks {\n\n")

    out.write(f"let N_SECOND_LAYER_FUNCTIONS = { n_functions };\n\n")

    out.write("// Called when the second layer is loaded, before the tilemap is transferred to VRAM.\n")
    out.write("// This callback is allowed to setup HDMA effects.\n")
    out.write("// DB = 0x7e\n")
    out.write("#[mem8, idx8]\n")
    generate_table("init_function_table", "func()", "init")

    out.write("// Called once per frame\n")
    out.write("// DB = 0x7e\n")
    out.write("#[mem8, idx8]\n")
    generate_table("process_function_table", "func()", "process")

    out.write("}\n\n")


def gamemodes_imports(out: StringIO, gamemodes: list[GameMode]) -> None:
    for gm in gamemodes:
        if '"' in gm.source:
            raise ValueError(f"Invalid source value for game mode: {gm.name}")
        if "/" not in gm.source:
            out.write(f'import "src/gamemodes/{gm.source}";\n')
        else:
            out.write(f'import "../{gm.source}";\n')


def gamemodes_table(out: StringIO, gamemodes: list[GameMode]) -> None:
    n_functions: Final = len(gamemodes)

    def generate_table(table_name: str, fn_type: str, fn_name: str) -> None:
        out.write(f"const { table_name } : [ { fn_type } ; { n_functions } ] = [\n")
        for gm in gamemodes:
            out.write(f"  gamemodes.{ gm.name }.{ fn_name },\n")
        out.write("];\n\n")

    out.write("namespace gamemodes {\n\n")

    out.write(f"let N_GAME_MODES = { n_functions };\n\n")

    generate_table("exec_function_table", "func()", "exec")

    out.write("}\n\n")


def generate_wiz_code(mappings: Mappings, entities_json: EntitiesJson) -> str:
    death_functions = entities_json.death_functions
    if not death_functions:
        raise ValueError("No death functions")

    if len(death_functions) >= 256 / 2:
        raise ValueError("Too many death functions")

    if death_functions[0] != "none":
        raise ValueError("The first death functions must be `none`")

    with StringIO() as out:
        out.write(
            """
import "src/memmap";

import "src/entities/_death_functions";
import "src/interactive-tiles";
import "src/gamemodes/room-transition.wiz";
import "engine/game/metatiles";
"""
        )
        callback_imports(out, mappings.room_events, "room-events")
        callback_imports(out, mappings.sl_callbacks, "sl-callbacks")
        gamemodes_imports(out, mappings.gamemodes)

        out.write("\n")
        out.write("in code {\n\n")

        function_table(
            out,
            "entities",
            "Death function returns true if the entity is still active",
            "DeathFunctionsTable",
            "func(entityId : u8 in y) : bool in carry",
            "entities.death_functions",
            entities_json.death_functions,
        )
        interactive_tiles_table(out, mappings.interactive_tile_functions)
        room_events_table(out, mappings.room_events)
        second_layers_table(out, mappings.sl_callbacks)
        gamemodes_table(out, mappings.gamemodes)

        function_table(
            out,
            "gamemodes.room_transition",
            "Room transition functions load the next room and switch to the gameloop",
            "RoomTransitionsTable",
            "func()",
            "gamemodes.room_transition",
            mappings.room_transitions,
        )

        out.write("}\n")

        return out.getvalue()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", required=True, help="wiz output file")
    parser.add_argument("mappings_json_file", action="store", help="mappings.json file")
    parser.add_argument("entities_json_file", action="store", help="entities.json file")

    args = parser.parse_args()

    return args


def main() -> None:
    args = parse_arguments()

    mappings = load_mappings_json(args.mappings_json_file)
    entities = load_entities_json(args.entities_json_file)

    out = generate_wiz_code(mappings, entities)

    with open(args.output, "w") as fp:
        fp.write(out)


if __name__ == "__main__":
    main()
