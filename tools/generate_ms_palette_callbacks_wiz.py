#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from io import StringIO

from collections import OrderedDict

from unnamed_snes_game.json_formats import load_mappings_json, Name, MsPaletteCallback
from unnamed_snes_game.callbacks import write_callback_parameters_wiz, MS_PALETTE_CALLBACK_PARAMETERS


def generate_wiz_code(callbacks: OrderedDict[Name, MsPaletteCallback]) -> str:
    with StringIO() as out:
        out.write(
            """
import "src/memmap";
import "engine/ms-palette-api";

namespace ms_palette_callbacks {

in lowram {

"""
        )

        write_callback_parameters_wiz(out, callbacks, MS_PALETTE_CALLBACK_PARAMETERS)

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

    out = generate_wiz_code(mappings.ms_palette_callbacks)

    with open(args.output, "w") as fp:
        fp.write(out)


if __name__ == "__main__":
    main()
