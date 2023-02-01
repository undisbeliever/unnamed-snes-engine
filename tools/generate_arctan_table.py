#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import math
import argparse

from io import StringIO


N_ARCTAN_ANGLES = 32
MAX_ANGLE = (N_ARCTAN_ANGLES // 4) - 1

FIXED_POINT_BITS = 5


def build_arctan_32x2_table() -> list[int]:
    FIXED_POINT_SCALE = 1 << FIXED_POINT_BITS

    out = list()

    for i in range(256 * FIXED_POINT_SCALE):
        a = math.atan(i / FIXED_POINT_SCALE)
        a = int(a / math.tau * N_ARCTAN_ANGLES) % N_ARCTAN_ANGLES
        if a >= MAX_ANGLE:
            break

        out.append(a * 2)

    return out


def generate_wiz_code(arctan_32x2_table: list[int]) -> str:

    assert len(arctan_32x2_table) < 0xFF

    with StringIO() as out:
        out.write(
            """
import "../src/memmap";

namespace math {

in rodata0 {
"""
        )

        out.write(f"  let N_ARCTAN_ANGLES = { N_ARCTAN_ANGLES };\n")
        out.write(f"  let N_ARCTAN_FIXED_POINT_BITS = { FIXED_POINT_BITS };\n")
        out.write(f"  let ARCTAN_32x2_TABLE_SIZE = { len(arctan_32x2_table) };\n")
        out.write(f"  let ARCTAN_32x2_OVERFLOW_VALUE = { MAX_ANGLE * 2 };\n")
        out.write("\n")

        out.write(f"  // Table mapping of 0:8:{ FIXED_POINT_BITS } fixed point tangents to angles multiplied by 2\n")
        out.write(f"  const _Arctan_32x2_Table : [ u8 ; { len(arctan_32x2_table) } ] = { arctan_32x2_table };\n")
        out.write(f"  let Arctan_32x2_Table = far &_Arctan_32x2_Table as far *const u8;\n")

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

    arctan_32x2_table = build_arctan_32x2_table()

    out = generate_wiz_code(arctan_32x2_table)

    with open(args.output, "w") as fp:
        fp.write(out)


if __name__ == "__main__":
    main()
