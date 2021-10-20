#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from io import StringIO

from _json_formats import load_mappings_json



def generate_wiz_code(mappings):
    ms_spritesheet_ppu_data = [ f"resources.ms_ppu_data.{ ss }" for ss in mappings.metasprite_spritesheets ]


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

        out.write(f"    let _tilesets = [ { ', '.join(mappings.tilesets) } ];")

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

    mappings = load_mappings_json(args.mappings_json_file)

    out = generate_wiz_code(mappings)

    with open(args.output, 'w') as fp:
        fp.write(out)



if __name__ == '__main__':
    main()

