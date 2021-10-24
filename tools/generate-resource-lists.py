#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from io import StringIO

from _json_formats import load_mappings_json



def _write_byte_array(out, name, prefix, items):
    out.write(f"const { name } : [ u8 ] = [ ")

    for i in items:
        out.write(f"{ prefix }{ i }, ")

    out.write("];\n")



def write_resource_lists(out, name, prefix, items):
    _write_byte_array(out, name + '_l', '<:&' + prefix, items)
    _write_byte_array(out, name + '_h', '>:&' + prefix, items)
    _write_byte_array(out, name + '_b', '#:far &' + prefix, items)



def generate_wiz_code(mappings):

    with StringIO() as out:
        out.write("""
import "../src/memmap";
import "../src/resources";
""")

        out.write("""
in rodata0 {

namespace resources {
namespace metatile_tilesets {

""")

        write_resource_lists(out, '_tileset_list', '', mappings.tilesets)

        out.write("""
}

namespace metasprites {
""")


        write_resource_lists(out, '_spritesheet_list', 'ms_ppu_data.', mappings.metasprite_spritesheets)

        out.write("""
}
}
}

""")

        return out.getvalue()



def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='wiz output file')
    parser.add_argument('mappings_json_file', action='store',
                        help='mappings json file input')

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    mappings = load_mappings_json(args.mappings_json_file)

    out = generate_wiz_code(mappings)

    with open(args.output, 'w') as fp:
        fp.write(out)



if __name__ == '__main__':
    main()

