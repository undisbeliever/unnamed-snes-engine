#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import os.path
import argparse

from io import StringIO
from collections import OrderedDict

from _json_formats import load_mappings_json



def extract_locations(room_filenames):
    room_regex = re.compile(r'((\d+)-(\d+)-.+).bin$')

    for f in room_filenames:
        bn = os.path.basename(f)
        if not bn.startswith('_'):
            m = room_regex.match(bn)
            yield m.group(1), int(m.group(2), 10), int(m.group(3), 10)



def build_room_table(room_filenames, mappings):
    table = list()
    for y in range(16):
        table.append([ None ] * 16 )


    for name, x, y in extract_locations(room_filenames):
        if table[y][x] is not None:
            raise RuntimeError(f"Overlapping room: cannot place { name } at position ({ x }, { y }), it is occupied by { table[y][x] }")

        table[y][x] = name

    return table



def build_room_mapping(room_filenames):
    rooms = OrderedDict()

    for i, fn in enumerate(room_filenames):
        r = os.path.splitext(os.path.basename(fn))[0]

        if not r.startswith('_'):
            rooms[r] = len(rooms) + 1

    return rooms



def convert_room_table(rooms, table):
    out = list()

    for row in table:
        for r in row:
            if r is None:
                out.append(0)
            else:
                out.append(rooms[r])

    return out



def find_starting_room(rooms, table, mappings):
    return table.index(rooms[mappings.starting_room])



def generate_wiz_code(rooms, table, starting_room):

    with StringIO() as out:
        out.write("""
import "../src/memmap";

in rodata1 {

namespace resources {
namespace rooms {
  const _rooms = [
""")

        for r in rooms:
            out.write(f"    embed \"rooms/{ r }.bin\",\n")

        out.write("  ];\n\n")

        assert len(table) == 256
        out.write(f"  const _room_table : [ u8 ; 256 ] = { table };\n\n")

        out.write(f"  let _STARTING_ROOM = { starting_room };")
        out.write("""
}
}

}

""")

        return out.getvalue()




def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='map output file')
    parser.add_argument('mapping_filename', action='store',
                        help='mapping json file input')
    parser.add_argument('rooms', action='store', nargs='+',
                        help='rooms in dungeon')

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    mappings = load_mappings_json(args.mapping_filename)

    rooms = build_room_mapping(args.rooms)
    table_str = build_room_table(args.rooms, mappings)

    table = convert_room_table(rooms, table_str)

    starting_room = find_starting_room(rooms, table, mappings)

    out = generate_wiz_code(rooms, table, starting_room)

    with open(args.output, 'w') as fp:
        fp.write(out)



if __name__ == '__main__':
    main()

