#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from io import StringIO

from collections import OrderedDict

from _json_formats import load_mappings_json, Mappings, Name, RoomEvent


def generate_wiz_code(room_events: OrderedDict[Name, RoomEvent]) -> str:

    n_functions = len(room_events)

    with StringIO() as out:
        out.write('import "../src/memmap";\n')

        for e in room_events.values():
            if '"' in e.source:
                raise ValueError(f"Invalid source value for room event: {e.name}")
            if "/" not in e.source:
                out.write(f'import "src/room-events/{e.source}";\n')
            else:
                out.write(f'import "../{e.source}";\n')

        out.write(
            """
namespace room_events {

in code {

"""
        )
        out.write(f"let N_ROOM_EVENT_FUNCTIONS = { n_functions };\n\n")

        def generate_table(table_name: str, fn_type: str, fn_name: str) -> None:
            out.write(f"const { table_name } : [ { fn_type } ; { n_functions } ] = [\n")
            for e in room_events.values():
                out.write(f"  room_events.{ e.name }.{ fn_name },\n")
            out.write("];\n\n")

        out.write("// Called when the room is loaded, in room transition code\n")
        out.write("// This function is allowed to modify the map data.\n")
        out.write("// This function MUST NOT call a MetaTile function.\n")
        generate_table("init_function_table", "func()", "init")

        out.write("// Called once per frame\n")
        generate_table("process_function_table", "func()", "process")

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
