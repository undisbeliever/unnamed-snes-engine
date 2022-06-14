#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import argparse
from io import StringIO

from _json_formats import load_mappings_json
from _common import MS_FS_DATA_BANK_OFFSET, ROOM_DATA_BANK_OFFSET


# order MUST match `ResourceType` enum in `src/metasprites.wiz`
#        and match `RESOURCE_TYPES` in `tools/insert-resources.py`
RESOURCE_TYPES = (
    'mt_tilesets',
    'ms_spritesheets',
)



def room_id_for_name(room_name):
    m = re.match(r'(\d+)-(\d+)-.+$', room_name)

    if not m:
        raise ValueError("Invalid room name")

    return int(m.group(1), 10) + 16 * int(m.group(2), 10)



def generate_wiz_code(mappings):

    with StringIO() as out:
        out.write('namespace resources {\n\n')

        out.write(f"let MS_FS_DATA_BANK = { mappings.memory_map.first_resource_bank + MS_FS_DATA_BANK_OFFSET };\n")
        out.write(f"let ROOM_DATA_BANK = { mappings.memory_map.first_resource_bank + ROOM_DATA_BANK_OFFSET };\n\n")

        out.write(f"let _STARTING_ROOM = { room_id_for_name(mappings.starting_room) };\n\n")

        out.write('let n_resources_per_type = [')
        for enum_name in RESOURCE_TYPES:
            l = len(getattr(mappings, enum_name))
            out.write(f"{ l }, ")
        out.write('];\n\n')


        for enum_name in RESOURCE_TYPES:
            out.write(f"enum { enum_name } : u8 {{\n")

            for i in getattr(mappings, enum_name):
                out.write(f"  { i },\n")

            out.write('};\n\n')

        out.write('}')

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


