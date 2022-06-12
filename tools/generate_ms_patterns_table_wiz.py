#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from io import StringIO

from _json_formats import load_ms_export_order_json


def generate_wiz_code(ms_patterns):

    table_size = 1
    while table_size < len(ms_patterns):
        table_size *= 2


    with StringIO() as out:
        out.write("""
import "../src/memmap";
import "../src/metasprites";
""")

        out.write("""
namespace metasprites {
in code {

""")

        out.write(f"let MS_PATTERNS_TABLE_MASK = 0x{ (table_size - 1) * 2 :02x};\n\n")

        out.write(f"const ms_patterns_table : [ func(u16 in yy, u16 in metasprites.xPos, u16 in metasprites.yPos) ; { table_size } ] = [")

        for p in ms_patterns.values():
            out.write(f"\n  metasprites.drawing_functions.{ p.name },")

        for i in range(table_size - len(ms_patterns)):
            out.write("\n  metasprites.drawing_functions.null_function,")

        out.write("""
];

}
}

""")

        return out.getvalue()



def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='wiz output file')
    parser.add_argument('ms_export_order_json_file', action='store',
                        help='metasprite export order map JSON file')

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    ms_export_orders = load_ms_export_order_json(args.ms_export_order_json_file)

    out = generate_wiz_code(ms_export_orders.patterns)

    with open(args.output, 'w') as fp:
        fp.write(out)



if __name__ == '__main__':
    main()


