#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from io import StringIO

from unnamed_snes_game.json_formats import load_entities_json, load_ms_export_order_json, EntitiesJson, MsExportOrder


def generate_wiz_code(entities_input: EntitiesJson, ms_export_orders: MsExportOrder) -> str:

    entity_functions = entities_input.entity_functions.values()
    entities = entities_input.entities.values()

    with StringIO() as out:
        out.write("namespace entities {\n\n")

        out.write(f"let N_ENTITY_TYPES = { len(entities) };\n\n")

        out.write("enum EntityTypes : u8 {\n")
        for e in entities:
            out.write(f"  { e.name },\n")
        out.write("};\n\n")

        for ef in entity_functions:
            out.write(f"namespace { ef.name } {{\n")

            if ef.ms_export_order:
                out.write(f"// ms-export-order = { ef.ms_export_order }\n")
                out.write("namespace ms_animations {\n")

                mseo = ms_export_orders.animation_lists[ef.ms_export_order]
                for i, a in enumerate(mseo.animations):
                    out.write(f"  let { a } = { i };\n")

                out.write("}\n")

            p = ef.parameter
            if p:
                if p.type == "enum":
                    assert p.values and len(p.values) > 0

                    out.write("enum init_parameter : u8 {\n")
                    for v in p.values:
                        out.write(f"  { v },\n")
                    out.write("};\n")
                else:
                    ValueError(f"Unknown entity_function parameter type: { p.type }")

            out.write("}\n\n")

        out.write("}\n\n")

        return out.getvalue()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", required=True, help="wiz output file")
    parser.add_argument("entities_json_file", action="store", help="entities  JSON  file input")
    parser.add_argument("ms_export_order_json_file", action="store", help="metasprite export order  JSON  file input")

    args = parser.parse_args()

    return args


def main() -> None:
    args = parse_arguments()

    entities = load_entities_json(args.entities_json_file)
    ms_export_orders = load_ms_export_order_json(args.ms_export_order_json_file)

    out = generate_wiz_code(entities, ms_export_orders)

    with open(args.output, "w") as fp:
        fp.write(out)


if __name__ == "__main__":
    main()
