#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import json
import argparse
from io import StringIO



def validate_mapping_json(mapping):
    regex = re.compile('[a-zA-Z0-9_]+')

    def test_list(l):
        for i in l:
            if not regex.match(i):
                raise ValueError(f"Invalid name: {i}")

    test_list(mapping['tilesets'])
    test_list(mapping['entities'])



def generate_wiz_code(mapping):
    with StringIO() as out:
        out.write("""
import "../src/memmap";
import "../src/resources";

namespace resources {

namespace metatile_tilesets {
in rodata0 {
""")

        out.write(f"let _tilesets = [ { ', '.join(mapping['tilesets']) } ];")

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

    with open(args.mappings_json_file, 'r') as fp:
        mapping = json.load(fp)

    validate_mapping_json(mapping)

    out = generate_wiz_code(mapping)

    with open(args.output, 'w') as fp:
        fp.write(out)



if __name__ == '__main__':
    main()

