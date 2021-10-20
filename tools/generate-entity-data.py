#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from collections import OrderedDict
from io import StringIO

from _json_formats import load_entities_json


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



def get_metasprite_spritesheets(entities):
    # Python does not have an OrderedSet and I want this function to return a list of consistent order
    ss = OrderedDict()

    for e in entities:
        print(e)
        ms_ss = e.metasprites.split('.')[0]
        ss[ms_ss] = None

    return list(ss)



def optional_value(v, alt):
    if v:
        return v
    else:
        return alt



def generate_wiz_code(entities_input):

    entities = entities_input.entities.values()
    entity_functions = entities_input.entity_functions.values()

    with StringIO() as out:
        def write_list(name, values):
            out.write(f"  const {name} : [u8] = [ { ', '.join(values) } ];\n")


        out.write("""
import "../src/memmap";
import "../src/entities/_variables";
""")


        for ef in entity_functions:
            out.write(f"""import "../src/entities/{ ef.name.replace('_', '-') }";\n""")

        out.write('\n')

        for ss in get_metasprite_spritesheets(entities):
            out.write(f"""import "metasprites/{ ss }";\n""")


        out.write("""

namespace entities {
namespace entity_data {
in rodata0 {

""")

        write_list('init_function_l', [ f"<:&{ e.code.name }.init" for e in entities ])
        write_list('init_function_h', [ f">:&{ e.code.name }.init" for e in entities ])

        write_list('process_function_l', [ f"<:&{ e.code.name }.process" for e in entities ])
        write_list('process_function_h', [ f">:&{ e.code.name }.process" for e in entities ])

        write_list('ms_draw_function_l', [ f"<:&ms.{ e.metasprites }.draw_function" for e in entities ])
        write_list('ms_draw_function_h', [ f">:&ms.{ e.metasprites }.draw_function" for e in entities ])

        write_list('ms_frame_table_l', [ f"<:&ms.{ e.metasprites }.frame_table" for e in entities ])
        write_list('ms_frame_table_h', [ f">:&ms.{ e.metasprites }.frame_table" for e in entities ])

        write_list('shadow_size', [ f"ShadowSize.{ e.shadow_size } as u8" for e in entities ])

        write_list('initial_zpos', [ f"{ e.zpos } as u8" for e in entities ])

        write_list('tile_hitbox_half_width', [ f"{ optional_value(e.half_width, DEFAULT_HALF_WIDTH[e.shadow_size]) }" for e in entities ])
        write_list('tile_hitbox_half_height', [ f"{ optional_value(e.half_height, DEFAULT_HALF_HEIGHT[e.shadow_size]) }" for e in entities ])

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
                        help='entities JSON file input')

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    entities = load_entities_json(args.entities_json_file)

    out = generate_wiz_code(entities)

    with open(args.output, 'w') as fp:
        fp.write(out)



if __name__ == '__main__':
    main()

