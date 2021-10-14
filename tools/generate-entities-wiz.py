#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import json
import argparse
from io import StringIO


def validate_name(s):
    if re.match(r'[a-zA-Z0-9_]+$', s) is None:
        raise ValueError(f"Invalid name: {s}")



def generate_wiz_code(entities_json, ms_export_orders_json):

    with StringIO() as out:
        out.write("namespace entities {\n\n")

        for ef in entities_json["entity_functions"]:
            validate_name(ef['name'])

            ef_ms_export_order = ef['ms-export-order']
            if ef_ms_export_order:
                out.write(f"namespace { ef['name'] } {{\n")
                out.write(f"// ms-export-order = { ef_ms_export_order }\n")
                out.write("namespace ms_frames {\n")

                for i, ms_frame in enumerate(ms_export_orders_json[ef_ms_export_order]['frames']):
                    validate_name(ms_frame)
                    out.write(f"  let { ms_frame } = { i };\n")

                out.write("}\n")
                out.write("}\n")

            p = ef.get('parameter')
            if p:
                if p['type'] == 'enum':
                    out.write('\nenum init_parameter : u8 {\n')
                    for v in p['values']:
                        validate_name(v)
                        out.write(f"  { v },\n")
                    out.write('};\n\n')
                else:
                    ValueError(f"Unknown entity_function parameter type: { p['type'] }")

        out.write("}\n\n")


        return out.getvalue()



def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='wiz output file')
    parser.add_argument('entities_json_file', action='store',
                        help='entities  JSON  file input')
    parser.add_argument('ms_export_order_json_file', action='store',
                        help='metasprite export order  JSON  file input')

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    with open(args.entities_json_file, 'r') as fp:
        entities = json.load(fp)

    with open(args.ms_export_order_json_file, 'r') as fp:
        ms_export_orders = json.load(fp)

    out = generate_wiz_code(entities, ms_export_orders)

    with open(args.output, 'w') as fp:
        fp.write(out)



if __name__ == '__main__':
    main()


