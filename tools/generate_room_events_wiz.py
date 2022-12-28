#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import argparse
from io import StringIO

from collections import OrderedDict

from _json_formats import load_mappings_json, Mappings, Name, RoomEvent


# Mapping of parameter types to wiz types
PARAM_TYPES = {
    'u8':                   'u8',
    'u8pos':                'U8Position',
    'gamestate_flag':       'u8',
    'locked_door':          'u8',
    'open_door':            'u8',
    'optional_open_door':   'u8'
}

# Size of each parameter type in bytes
PARAM_SIZE = {
    'u8':                   1,
    'u8pos':                2,
    'gamestate_flag':       1,
    'locked_door':          1,
    'open_door':            1,
    'optional_open_door':   1,
}



def generate_wiz_code(room_events : OrderedDict[Name, RoomEvent]) -> str:

    n_functions = len(room_events)

    with StringIO() as out:
        out.write("""
import "src/memmap";
import "src/room";

namespace room_events {

struct U8Position {
    xPos : u8,
    yPos : u8,
};

in wram7e_roomstate {
""")

        for e in room_events.values():
            if e.parameters:
                i = 0

                out.write(f"namespace {e.name} {{\n")

                for p in e.parameters:
                    if i != 0:
                        out.write('\n')

                    ptype = PARAM_TYPES.get(p.type)
                    if not ptype:
                        raise ValueError(f"Unknown room event parameter type: {p.type}")

                    if p.comment:
                        p_comment = p.comment.replace('\n', '\n  // ')
                        out.write(f"  // { p_comment }\n")
                    out.write(f"  // ({ p.type })\n")
                    out.write(f"  var parameter__{ p.name } @ &room.roomEventParameters[{ i }] : { ptype };\n")

                    i += PARAM_SIZE[p.type]

                out.write('}\n\n')

                if i > 4:
                    raise ValueError(f"Room Event has too many parameters: {e.name}")

        out.write('}\n')
        out.write('}\n')

        return out.getvalue()



def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='wiz output file')
    parser.add_argument('mappings_json_file', action='store',
                        help='mappings.json file')

    args = parser.parse_args()

    return args;



def main() -> None:
    args = parse_arguments()

    mappings = load_mappings_json(args.mappings_json_file)

    out = generate_wiz_code(mappings.room_events)

    with open(args.output, 'w') as fp:
        fp.write(out)



if __name__ == '__main__':
    main()


