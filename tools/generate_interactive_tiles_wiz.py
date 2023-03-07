#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from io import StringIO
from typing import Final

from unnamed_snes_game.json_formats import load_mappings_json, Mappings


MAX_INTERACTIVE_FUNCTIONS: Final = 1 << 6


def generate_wiz_code(interactive_tile_functions: list[str]) -> str:
    # function 0 is null
    n_functions = len(interactive_tile_functions) + 1

    table_size = 1
    while table_size < n_functions:
        table_size *= 2

    if table_size > MAX_INTERACTIVE_FUNCTIONS:
        raise ValueError("Too many interactive tile types: { len(interactive_tile_functions) }")

    table_mask: Final = (table_size - 1) * 2

    doorway_index = interactive_tile_functions.index("doorway") + 1

    with StringIO() as out:
        out.write(
            """
import "../src/memmap";
import "../src/interactive-tiles";
import "../src/metatiles";
"""
        )

        out.write(
            """
namespace metatiles {
namespace interactive_tiles {

in code {

"""
        )
        out.write(f"let DOORWAY_FUNCTION_TABLE_UNALIGNED_INDEX = 0x{ doorway_index * 2 :02x};\n\n")

        out.write(f"let FUNCTION_TABLE_MASK = 0x{ table_mask :02x};\n\n")

        def generate_table(table_name: str, fn_type: str, fn_name: str) -> None:
            null_line = f"  metatiles.interactive_tiles.null_function as {fn_type},\n"

            out.write(f"const { table_name } : [ { fn_type } ; { table_size } ] = [\n")
            out.write(null_line)

            for it in interactive_tile_functions:
                out.write(f"  metatiles.interactive_tiles.{ it }.{ fn_name },\n")

            for i in range(table_size - n_functions):
                out.write(null_line)

            out.write("];\n\n")

        generate_table("player_touches_tile_function_table", "func(u8 in y)", "player_touches_tile")
        generate_table(
            "player_tile_collision_function_table", "func(u8 in y, metatiles.CollisionDirection in a)", "player_tile_collision"
        )

        out.write(
            """
}
}
}

"""
        )

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

    out = generate_wiz_code(mappings.interactive_tile_functions)

    with open(args.output, "w") as fp:
        fp.write(out)


if __name__ == "__main__":
    main()
