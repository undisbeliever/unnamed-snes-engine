#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import math
import argparse

from io import StringIO


N_ANGLES = 32


COSINE_TABLES = (
    ("PlayerHurtVelocity", 0x0200),
    ("CalcAngleTest", 0x2000),
    ("SwordStrike_Slower", 0x0100),
    ("SwordStrike_Slow", 0x0180),
    ("SwordStrike_Medium", 0x0200),
    ("SwordStrike_Fast", 0x0280),
    ("SwordStrike_ExtraFast", 0x0300),
    ("SwordStrike_FastFastFast", 0x0380),
    ("Boss_Fastest", 0x0400),
    ("Boss_Fast", 0x0240),
    ("Boss_Slow", 0x0180),  # Should be the same speed or faster then the player
    ("BossBombVelocity", 0x0260),
)


def build_cosine_table(amplitude: int) -> list[int]:
    out = list()

    for i in range(N_ANGLES):
        out.append(int(math.cos(math.tau / N_ANGLES * i) * amplitude))

    return out


def generate_wiz_code() -> str:
    with StringIO() as out:
        out.write(
            """
import "../src/memmap";

namespace CosineTables {

in rodata0 {
"""
        )
        out.write(f"  let N_COSINE_ANGLES = { N_ANGLES };\n")
        out.write(f"  let TABLE_MASK = 0x{ (N_ANGLES - 1) * 2 :02x};\n")
        out.write(f"  let SINE_OFFSET = 0x{ (N_ANGLES // 4) * 2 :02x};\n")

        for name, a in COSINE_TABLES:
            table = build_cosine_table(a)
            out.write(f"  const _{name} : [ i16 ; { N_ANGLES } ] = { table };\n")
            out.write(f"  let {name} = far &_{name} as far *const i16;")

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

    args = parser.parse_args()

    return args


def main() -> None:
    args = parse_arguments()

    out = generate_wiz_code()

    with open(args.output, "w") as fp:
        fp.write(out)


if __name__ == "__main__":
    main()
