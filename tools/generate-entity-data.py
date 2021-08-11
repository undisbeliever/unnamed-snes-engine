#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import json
import argparse
from io import StringIO


def code_list(entities):
    seen = set()

    out = list()

    for e in entities:
        code = e['code']
        if code not in seen:
            seen.add(code)
            out.append(code)

    return out


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



def generate_wiz_code(entities_json, mapping_json):
    entities = [entities_json[entity_name] for entity_name in mapping_json['entities']]

    if len(entities) > 254:
        raise ValueError("Too many entities")


    with StringIO() as out:
        def write_list(name, values):
            out.write(f"  const {name} : [u8] = [ { ', '.join(values) } ];\n")

        out.write("""
import "../src/memmap";
import "../src/entities/_variables";
""")

        for c in code_list(entities):
            out.write(f"""import "../src/entities/{ c.replace('_', '-') }";\n""")

        out.write("""

namespace entities {
namespace entity_data {
in rodata0 {

""")

        write_list('init_function_l', [ f"<:&{ e['code'] }.init" for e in entities ])
        write_list('init_function_h', [ f">:&{ e['code'] }.init" for e in entities ])

        write_list('process_function_l', [ f"<:&{ e['code'] }.process" for e in entities ])
        write_list('process_function_h', [ f">:&{ e['code'] }.process" for e in entities ])

        write_list('ms_draw_function_l', [ f"<:&{ e['code'] }.ms_draw_function" for e in entities ])
        write_list('ms_draw_function_h', [ f">:&{ e['code'] }.ms_draw_function" for e in entities ])

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
                        help='entities json file input')
    parser.add_argument('mappings_json_file', action='store',
                        help='mappings json file input')

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

