#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from collections import OrderedDict
from itertools import chain
from io import StringIO

from _json_formats import load_entities_json



def get_metasprite_spritesheets(entities):
    # Python does not have an OrderedSet and I want this function to return a list of consistent order
    ss = OrderedDict()

    for e in entities:
        print(e)
        ms_ss = e.metasprites.split('.')[0]
        ss[ms_ss] = None

    return list(ss)



def generate_wiz_code(entities_input):

    entities = entities_input.entities.values()
    entity_functions = entities_input.entity_functions.values()

    n_entities = len(entities)


    if len(entities) >= 0xff / 2:
        raise ValueError("Too many entity types.")


    with StringIO() as out:

        def write_list(name, type_, values):
            out.write(f"  const { name } : [{ type_ } ; { n_entities }] = [ { ', '.join(values) } ];\n")

        def write_list_u8(name, a_values, b_values):
            interleaved_values = chain.from_iterable(zip(a_values, b_values))
            out.write(f"  const __{ name } : [u8 ; { n_entities * 2}] = [ { ', '.join(interleaved_values) } ];\n")
            out.write(f"  let { name } = far &__{ name } as far *u16;\n\n")


        out.write("""
import "../src/memmap";
import "../src/metasprites";
""")


        for ef in entity_functions:
            out.write(f"""import "../src/entities/{ ef.name.replace('_', '-') }";\n""")

        out.write('\n')

        for ss in get_metasprite_spritesheets(entities):
            out.write(f"""import "metasprites/{ ss }";\n""")


        out.write("""
namespace entity_rom_data {
in rodata0 {
""")

        write_list('init_functions', 'func(u8 in x, u8 in y)',
                    [ f"entities.{ e.code.name }.init" for e in entities ])
        write_list('process_functions', 'func(u8 in x) : bool in carry',
                    [ f"entities.{ e.code.name }.process" for e in entities ])
        write_list('metasprite_framesets', '*const metasprites.MsFramesetFormat',
                    [ f"&ms_framesets.{ e.metasprites }" for e in entities ])

        write_list_u8('vision_ab',
                    [ f"{ e.vision.a if e.vision else '0xff' }" for e in entities ],
                    [ f"{ e.vision.b if e.vision else '0xff' }" for e in entities ])
        write_list_u8('initial_zpos_and_blank',
                    [ f"{ e.zpos }" for e in entities ],
                    [ f"0" for e in entities ])
        write_list_u8('health_and_attack_power',
                    [ f"{ e.health }" for e in entities ],
                    [ f"{ e.attack }" for e in entities ])

        out.write("""
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

