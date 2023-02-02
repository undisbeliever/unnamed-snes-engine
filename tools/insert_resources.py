#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import argparse
from unnamed_snes_game.insert_resources import insert_resources_into_binary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", required=True, help="sfc output file")
    parser.add_argument("-j", "--processes", required=False, type=int, default=None, help="Number of processors to use (default=all)")
    parser.add_argument("resources_directory", help="resources directory")
    parser.add_argument("symbols_file", help="symbols input file")
    parser.add_argument("sfc_input", help="sfc input file (unmodified)")

    args = parser.parse_args()

    sfc_data = insert_resources_into_binary(args.resources_directory, args.symbols_file, args.sfc_input, args.processes)

    with open(args.output, "wb") as fp:
        fp.write(sfc_data)


if __name__ == "__main__":
    main()
