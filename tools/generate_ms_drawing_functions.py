#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from io import StringIO

from collections import OrderedDict

from unnamed_snes_game.json_formats import load_ms_export_order_json, Name, MsPattern


def generate_pattern_code(out: StringIO, pattern: MsPattern) -> None:
    # Kept `square_four_32` in `src/metasprites.wiz` as an example
    if pattern.name == "square_four_32":
        return

    out.write(
        f"""
// DB = 0x7e
#[mem8, idx16]
func {pattern.name}(msFrame : u16 in yy, xPos : u16 in xPos, yPos : u16 in yPos) {{
    xx = yy;

    _subtract_ms_offset_from_position(xx, xPos, yPos);

    yy = bufferPos;
    return if negative || yy < {(len(pattern.objects)-1) * 4};

"""
    )

    # xPos
    if any(o.xpos == 0 for o in pattern.objects):
        out.write("    a = <:xPos;\n")
        for i, o in enumerate(pattern.objects):
            if o.xpos == 0:
                out.write(f"    (&buffer[{-i}].xPos)[unaligned yy] = a;\n")

    for i, o in enumerate(pattern.objects):
        out.write("\n")
        if o.size == 8:
            out.write("    _update_hi_table_size_small();\n")
        else:
            out.write("    _update_hi_table_size_large();\n")

        if o.xpos == 0:
            # buffer.xPos already written
            out.write(f"    _update_hi_table_x8_xPos();\n")
        else:
            out.write(f"    (&buffer[{-i}].xPos)[unaligned yy] = a = <:xPos + {o.xpos};\n")
            out.write(f"    a = (>:xPos +# 0) >>> 1;\n")
            out.write(f"    _update_hi_table_x8(carry);\n")

    out.write("\n")
    out.write("    mem16();\n")
    out.write("    #[mem16] {\n")

    # yPos
    y_pos_written = [False] * len(pattern.objects)

    for i, o in enumerate(pattern.objects):
        if not y_pos_written[i]:
            if o.ypos == 0:
                out.write("        aa = yPos;\n")
            else:
                out.write(f"        aa = yPos + {o.ypos};\n")

            out.write("        if aa >= DISPLAY_HEIGHT && aa < -16 as u16 { aa = -16 as u16; }\n")

            for j, jo in enumerate(pattern.objects):
                if jo.ypos == o.ypos:
                    out.write(f"        (&buffer[{-j}].yPos as *u16)[unaligned yy] = aa;\n")
                    y_pos_written[j] = True
            out.write("\n")

    # charAttr
    for i in range(len(pattern.objects)):
        out.write(f"        (&buffer[{-i}].char as *u16)[unaligned yy] = aa = MsDataFormat.charAttr{i}[unaligned xx];\n")

    out.write(
        f"""
        bufferPos = aa = yy - {4*len(pattern.objects)};
    }}
    mem8();
}}
"""
    )


def generate_ms_patterns_table(out: StringIO, ms_patterns: OrderedDict[Name, MsPattern]) -> None:
    table_size = 1
    while table_size < len(ms_patterns):
        table_size *= 2

    out.write(f"let MS_PATTERNS_TABLE_MASK = 0x{ (table_size - 1) * 2 :02x};\n\n")

    out.write(
        f"const ms_patterns_table : [ func(u16 in yy, u16 in metasprites.xPos, u16 in metasprites.yPos) ; { table_size } ] = [\n"
    )

    for p in ms_patterns.values():
        out.write(f"  metasprites.drawing_functions.{ p.name },\n")

    for i in range(table_size - len(ms_patterns)):
        out.write("  metasprites.drawing_functions.null_function,\n")

    out.write("];\n")


def generate_wiz_code(ms_patterns: OrderedDict[Name, MsPattern]) -> str:
    with StringIO() as out:
        out.write(
            """
// This file was auto-generated by `tools/generate_ms_drawing_functions.py`.

import "src/memmap";
import "src/game/metasprites";

namespace metasprites {
namespace drawing_functions {

in code {

"""
        )

        for pattern in ms_patterns.values():
            generate_pattern_code(out, pattern)

        generate_ms_patterns_table(out, ms_patterns)

        out.write(
            """
}

}
}
"""
        )

        return out.getvalue()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", required=True, help="wiz output file")
    parser.add_argument("ms_export_order_json_file", action="store", help="metasprite export order  JSON  file input")

    args = parser.parse_args()

    return args


def main() -> None:
    args = parse_arguments()

    ms_export_orders = load_ms_export_order_json(args.ms_export_order_json_file)

    out = generate_wiz_code(ms_export_orders.patterns)

    with open(args.output, "w") as fp:
        fp.write(out)


if __name__ == "__main__":
    main()
