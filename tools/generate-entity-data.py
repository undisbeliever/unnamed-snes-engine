#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import json
import argparse
from collections import OrderedDict
from io import StringIO


DEFAULT_HALF_WIDTH = {
    'SMALL': 3,
    'MEDIUM': 6,
    'LARGE': 8,
}

DEFAULT_HALF_HEIGHT = {
    'SMALL': 2,
    'MEDIUM': 3,
    'LARGE': 4,
}



def validate_name(s):
    if re.match(r'[a-zA-Z0-9_]+$', s) is None:
        raise ValueError(f"Invalid name: {s}")



def get_metasprite_spritesheets(entities):
    # Python does not have an OrderedSet and I want this function to return a list of consistent order
    ss = OrderedDict()

    for e in entities.values():
        ms_ss = e['metasprites'].split('.')[0]
        ss[ms_ss] = None

    return list(ss)



def generate_wiz_code(entities_json, mapping_json):
    if len(mapping_json['entities']) > 254:
        raise ValueError("Too many entities")

    for e in mapping_json['entities']:
        validate_name(e)


    entities = [entities_json["entities"][entity_name] for entity_name in mapping_json['entities']]


    with StringIO() as out:
        def write_list(name, values):
            out.write(f"  const {name} : [u8] = [ { ', '.join(values) } ];\n")


        out.write("""
import "../src/memmap";
import "../src/entities/_variables";
""")


        for ef in entities_json["entity_functions"]:
            validate_name(ef['name'])
            out.write(f"""import "../src/entities/{ ef['name'].replace('_', '-') }";\n""")

        out.write('\n')

        for ss in get_metasprite_spritesheets(entities_json["entities"]):
            out.write(f"""import "metasprites/{ ss }";\n""")


        out.write("""

namespace entities {
namespace entity_data {
in rodata0 {

""")

        write_list('init_function_l', [ f"<:&{ e['code'] }.init" for e in entities ])
        write_list('init_function_h', [ f">:&{ e['code'] }.init" for e in entities ])

        write_list('process_function_l', [ f"<:&{ e['code'] }.process" for e in entities ])
        write_list('process_function_h', [ f">:&{ e['code'] }.process" for e in entities ])

        write_list('ms_draw_function_l', [ f"<:&ms.{ e['metasprites'] }.draw_function" for e in entities ])
        write_list('ms_draw_function_h', [ f">:&ms.{ e['metasprites'] }.draw_function" for e in entities ])

        write_list('ms_frame_table_l', [ f"<:&ms.{ e['metasprites'] }.frame_table" for e in entities ])
        write_list('ms_frame_table_h', [ f">:&ms.{ e['metasprites'] }.frame_table" for e in entities ])

        write_list('shadow_size', [ f"ShadowSize.{ e['shadowSize'] } as u8" for e in entities ])

        write_list('tile_hitbox_half_width', [ f"{ int(e.get('halfWidth', DEFAULT_HALF_WIDTH[e['shadowSize']])) }" for e in entities ])
        write_list('tile_hitbox_half_height', [ f"{ int(e.get('halfHeight', DEFAULT_HALF_HEIGHT[e['shadowSize']])) }" for e in entities ])

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
    parser.add_argument('entities_json_file', action='store',
                        help='entities  JSON  file input')
    parser.add_argument('mappings_json_file', action='store',
                        help='mappings  JSON  file input')

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    with open(args.entities_json_file, 'r') as fp:
        entities = json.load(fp)

    with open(args.mappings_json_file, 'r') as fp:
        mapping = json.load(fp)

    out = generate_wiz_code(entities, mapping)

    with open(args.output, 'w') as fp:
        fp.write(out)



if __name__ == '__main__':
    main()

