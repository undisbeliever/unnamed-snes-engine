#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import json
import argparse
from io import StringIO



def validate_mapping_json(mapping):
    regex = re.compile('[a-zA-Z0-9_]+$')

    def test_list(l):
        for i in l:
            if not regex.match(i):
                raise ValueError(f"Invalid name: {i}")

    test_list(mapping['tilesets'])
    test_list(mapping['entities'])
    test_list(mapping['metasprite_spritesheets'])



def generate_wiz_code(mapping):
    ms_spritesheet_ppu_data = [ f"ms.{ ss }.ppu_data" for ss in mapping['metasprite_spritesheets'] ]


    with StringIO() as out:
        out.write("""
import "../src/memmap";
import "../src/resources";
""")
        for ss in mapping['metasprite_spritesheets']:
            out.write(f"import \"metasprites/{ ss }\";\n")

        out.write("""
in rodata0 {

namespace resources {
  namespace metatile_tilesets {
""")

        out.write(f"    let _tilesets = [ { ', '.join(mapping['tilesets']) } ];")

        out.write("""
  }

""")

        out.write(f"  let _ms_spritesheets = [ { ', '.join(ms_spritesheet_ppu_data) } ];")

        out.write("""
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

    with open(args.mappings_json_file, 'r') as fp:
        mapping = json.load(fp)

    validate_mapping_json(mapping)

    out = generate_wiz_code(mapping)

    with open(args.output, 'w') as fp:
        fp.write(out)



if __name__ == '__main__':
    main()

