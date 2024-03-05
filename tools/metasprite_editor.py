#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import os.path
import argparse
from unnamed_snes_game import resources_compiler
from unnamed_snes_game.json_formats import load_ms_export_order_json
from unnamed_snes_game.gui.metasprite_editor import EditorWindow

from typing import Final


MS_EXPORT_ORDER_FILENAME: Final = "resources/" + resources_compiler.MS_EXPORT_ORDER_FILENAME

MS_JSON_FILENAME: Final = "_metasprites.json"
DYNAMIC_METASPRITES_DIR: Final = "resources/dynamic-metasprites"
METASPRITES_DIRECTORY_DIR: Final = "resources/metasprites"


def find_json_file(arg: str) -> str:
    if arg == "dynamic-metasprites":
        arg = os.path.join(DYNAMIC_METASPRITES_DIR, MS_JSON_FILENAME)

    if os.path.isfile(arg):
        return arg

    arg_json = os.path.join(arg, MS_JSON_FILENAME)
    if os.path.isfile(arg_json):
        return arg_json

    arg_json = os.path.join(METASPRITES_DIRECTORY_DIR, arg_json)
    if os.path.isfile(arg_json):
        return arg_json

    raise RuntimeError(f"Cannot find {MS_JSON_FILENAME}")


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("metasprite_frameset", action="store", help="MetaSprite Frameset to edit")

    args = parser.parse_args()

    return args


def main() -> None:
    args = parse_arguments()

    json_file = find_json_file(args.metasprite_frameset)
    ms_export_orders = load_ms_export_order_json(MS_EXPORT_ORDER_FILENAME)

    editor = EditorWindow(json_file, ms_export_orders)
    editor.mainloop()


if __name__ == "__main__":
    main()
