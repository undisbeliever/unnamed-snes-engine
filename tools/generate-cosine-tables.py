#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import math
import argparse

from io import StringIO


N_ANGLES = 32


COSINE_TABLES = (
    ('PlayerHurtVelocity',  0x0200),
    ('CalcAngleTest',       0x2000),
)


def build_cosine_table(amplitude):
    out = list()

    for i in range(N_ANGLES):
        out.append(int(math.cos(math.tau / N_ANGLES * i) * amplitude))

    return out



def generate_wiz_code():

    with StringIO() as out:

        out.write("""
import "../src/memmap";

namespace CosineTables {

in rodata0 {
""")
        out.write(f"  let N_COSINE_ANGLES = { N_ANGLES };\n")
        out.write(f"  let TABLE_MASK = 0x{ (N_ANGLES - 1) * 2 :02x};\n")
        out.write(f"  let SINE_OFFSET = 0x{ (N_ANGLES // 4) * 2 :02x};\n")

        for name, a in COSINE_TABLES:
            table = build_cosine_table(a)
            out.write(f"  const _{name} : [ i16 ; { N_ANGLES } ] = { table };\n")
            out.write(f"  let {name} = far &_{name} as far *const i16;")

        out.write("""
}
}

""")

        return out.getvalue()




def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='wiz output file')

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    out = generate_wiz_code()

    with open(args.output, 'w') as fp:
        fp.write(out)



if __name__ == '__main__':
    main()

