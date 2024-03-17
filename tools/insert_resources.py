#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import argparse
from unnamed_snes_game.insert_resources import compile_data, print_resource_sizes, insert_resources_into_binary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", required=True, help="sfc output file")
    parser.add_argument("-j", "--processes", required=False, type=int, default=None, help="Number of processors to use (default=all)")
    parser.add_argument("-p", "--print-usage", action="store_true", help="Print resource usage")
    parser.add_argument("-r", "--resource-sizes", action="store_true", help="Print size of individual resource data")
    parser.add_argument("resources_directory", help="resources directory")
    parser.add_argument("symbols_file", help="symbols input file")
    parser.add_argument("sfc_input", help="sfc input file (unmodified)")

    args = parser.parse_args()

    data_store = compile_data(args.resources_directory, args.symbols_file, args.processes)
    if data_store is None:
        raise RuntimeError("Error compiling resources")

    if args.resource_sizes:
        print_resource_sizes(data_store)

    sfc_data, usage = insert_resources_into_binary(data_store, args.sfc_input)

    if args.print_usage or args.resource_sizes:
        print(usage.summary())

    with open(args.output, "wb") as fp:
        fp.write(sfc_data)


if __name__ == "__main__":
    main()
