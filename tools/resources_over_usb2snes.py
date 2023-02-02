#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import os
import argparse
from unnamed_snes_game.resources_over_usb2snes import resources_over_usb2snes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--address", required=False, default="ws://localhost:8080", help="Websocket address")
    parser.add_argument("-j", "--processes", required=False, type=int, default=None, help="Number of processors to use (default=all)")
    parser.add_argument("resources_directory", action="store", help="resources directory")
    parser.add_argument("sfc_file", action="store", help="sfc file (without resources)")

    args = parser.parse_args()

    sfc_file_relpath = os.path.relpath(args.sfc_file, args.resources_directory)
    os.chdir(args.resources_directory)
    resources_over_usb2snes(sfc_file_relpath, args.address, args.processes)


if __name__ == "__main__":
    main()
