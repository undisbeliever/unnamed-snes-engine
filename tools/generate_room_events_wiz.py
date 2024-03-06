#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from io import StringIO

from collections import OrderedDict

from unnamed_snes_game.json_formats import load_mappings_json, Mappings, Name, EngineHookFunction
from unnamed_snes_game.engine_hooks import write_hook_parameters_wiz, ROOM_EVENT_HOOK


def generate_wiz_code(room_events: OrderedDict[Name, EngineHookFunction]) -> str:
    n_functions = len(room_events)

    with StringIO() as out:
        out.write(
            """
import "src/memmap";
import "engine/game/room";

namespace room_events {

struct U8Position {
    xPos : u8,
    yPos : u8,
};

in wram7e_roomstate {
"""
        )

        write_hook_parameters_wiz(out, room_events, ROOM_EVENT_HOOK)

        out.write("}\n")
        out.write("}\n")

        return out.getvalue()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", required=True, help="wiz output file")
    parser.add_argument("mappings_json_file", action="store", help="mappings.json file")

    args = parser.parse_args()

    return args


def main() -> None:
    args = parse_arguments()

    mappings = load_mappings_json(args.mappings_json_file)

    out = generate_wiz_code(mappings.room_events)

    with open(args.output, "w") as fp:
        fp.write(out)


if __name__ == "__main__":
    main()
