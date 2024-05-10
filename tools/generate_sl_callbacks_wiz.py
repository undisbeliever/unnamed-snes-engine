#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from io import StringIO

from collections import OrderedDict

from unnamed_snes_game.json_formats import load_mappings_json, Mappings, Name, SecondLayerCallback
from unnamed_snes_game.callbacks import write_callback_parameters_wiz, SL_CALLBACK_PARAMETERS, SL_ROOM_PARAMETERS


def generate_wiz_code(sl_callbacks: OrderedDict[Name, SecondLayerCallback]) -> str:
    with StringIO() as out:
        out.write(
            """
import "src/memmap";
import "engine/game/second-layer";
import "engine/game/room";

namespace sl_callbacks {

struct U8Position {
    xPos : u8,
    yPos : u8,
};

in lowram {

"""
        )

        write_callback_parameters_wiz(out, sl_callbacks, SL_CALLBACK_PARAMETERS, SL_ROOM_PARAMETERS)

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

    out = generate_wiz_code(mappings.sl_callbacks)

    with open(args.output, "w") as fp:
        fp.write(out)


if __name__ == "__main__":
    main()
