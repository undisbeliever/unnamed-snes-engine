#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from io import StringIO

from unnamed_snes_game.json_formats import load_entities_json, EntitiesJson


# NOTE: I cannot put this data in `gen/entities.wiz` as it causes a circular dependency


def generate_wiz_code(entities_json: EntitiesJson) -> str:
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
import "../src/memmap";
import "../src/entities/_death_functions";

namespace entities {

in code {

// Death function returns true if the entity is still active
const DeathFunctionsTable : [func(entityId : u8 in y) : bool in carry] = [
"""
        )
        for df in death_functions:
            out.write(f"    entities.death_functions.{df},\n")

        out.write("];\n\n")

        out.write(f"let N_DEATH_FUNCTIONS = { len(death_functions) };\n")

        out.write(
            """
}

}
"""
        )

        return out.getvalue()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", required=True, help="wiz output file")
    parser.add_argument("entities_json_file", action="store", help="entities.json file")

    args = parser.parse_args()

    return args


def main() -> None:
    args = parse_arguments()

    entities = load_entities_json(args.entities_json_file)

    out = generate_wiz_code(entities)

    with open(args.output, "w") as fp:
        fp.write(out)


if __name__ == "__main__":
    main()
