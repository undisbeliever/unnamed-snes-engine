#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import re
import os.path
import argparse
from io import StringIO

from unnamed_snes_game.json_formats import load_mappings_json

from typing import Callable, Final, TextIO, NamedTuple


AUDIO_MEMORY_TYPE: Final = "SpcRam"
SNES_PRG_MEMORY_TYPE: Final = "SnesPrgRom"
WORK_RAM_MEMORY_TYPE: Final = "SnesWorkRam"


class Symbol(NamedTuple):
    memory_type: str
    addr: int
    name: str


SYM_REGEX: Final = re.compile(r"([0-9A-Fa-f]{2}):([0-9A-Fa-f]{4}) (.+)")


def parse_audio_sym_file(fp: TextIO) -> list[Symbol]:
    out = list()

    for line in fp:
        if m := SYM_REGEX.match(line):
            out.append(
                Symbol(
                    memory_type=AUDIO_MEMORY_TYPE,
                    addr=(int(m.group(1), 16) << 16) | int(m.group(2), 16),
                    name=m.group(3).replace(".", "_").strip(),
                )
            )

    return out


def parse_snes_sym_file(fp: TextIO, addr_to_rom_offset: Callable[[int], int]) -> list[Symbol]:
    out = list()

    for line in fp:
        if m := SYM_REGEX.match(line):
            bank = int(m.group(1), 16)
            cpu_addr = bank << 16 | int(m.group(2), 16)

            if bank == 0x7E or bank == 0x7F:
                memory_type = WORK_RAM_MEMORY_TYPE
                addr = cpu_addr & 0x01FFFF

            elif (bank & 0x7F) < 0x40 and (cpu_addr & 0xFFFF) <= 0x2000:
                memory_type = WORK_RAM_MEMORY_TYPE
                addr = cpu_addr & 0x1FFF

            else:
                memory_type = SNES_PRG_MEMORY_TYPE
                addr = addr_to_rom_offset(cpu_addr)

            out.append(Symbol(memory_type=memory_type, addr=addr, name=m.group(3).replace(".", "_").strip()))

    return out


def create_mlb_file(symbols: list[Symbol]) -> str:
    with StringIO() as out:
        for s in symbols:
            out.write(f"{s.memory_type}:{s.addr:04X}:{s.name}\n")

        return out.getvalue()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", required=True, help="wiz output file")
    parser.add_argument("-m", "--mappings", required=True, help="mappings JSON file (used to determine mapping)")

    parser.add_argument("sym_files", nargs="+", help="symbol files")

    args = parser.parse_args()

    return args


def main() -> None:
    args = parse_arguments()

    mappings = load_mappings_json(args.mappings)

    addr_to_rom_offset = mappings.memory_map.mode.address_to_rom_offset

    symbols = list()
    for sym_fn in args.sym_files:
        with open(sym_fn, "r") as fp:
            if os.path.basename(sym_fn).startswith("audio-"):
                symbols += parse_audio_sym_file(fp)
            else:
                symbols += parse_snes_sym_file(fp, addr_to_rom_offset)

    out = create_mlb_file(symbols)

    with open(args.output, "w") as fp:
        fp.write(out)


if __name__ == "__main__":
    main()
