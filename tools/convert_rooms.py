#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import os
import sys
import gzip
import base64
import struct
import argparse
import xml.etree.ElementTree
import posixpath
from collections import OrderedDict

from _json_formats import load_entities_json, load_mappings_json, \
                          Filename, Mappings, EntitiesJson, Entity

from typing import Final, NamedTuple, Optional, Union


MAP_WIDTH = 16
MAP_HEIGHT = 14

MAP_WIDTH_PX  = MAP_WIDTH * 16
MAP_HEIGHT_PX = MAP_HEIGHT * 16

ENTITIES_IN_MAP = 8

TILE_SIZE = 16


class RoomError(Exception):
    def __init__(self, errors : Union[str, list[str]]):
        if not isinstance(errors, list):
            errors = [ errors ]
        self.errors : Final = errors


# `TmxMap` is a limited subset of the TMX data format (only supporting one map layer and tileset)

class TmxTileset(NamedTuple):
    name        : str
    firstgid    : int

class TmxEntity(NamedTuple):
    id          : int
    name        : Optional[str]
    x           : int
    y           : int
    type        : str
    parameter   : Optional[str]

class TmxMap(NamedTuple):
    tileset     : TmxTileset
    map         : list[int]
    entities    : list[TmxEntity]



def validate_tag_attr(tag : xml.etree.ElementTree.Element, name : str, value : str) -> None:
    if tag.attrib[name] != value:
        raise RoomError(f"Invalid attribute: Expected {name}=\"{value}\"")



def parse_tileset_tag(tag : xml.etree.ElementTree.Element) -> TmxTileset:
    # basename without extension
    tileset_name = posixpath.splitext(posixpath.basename(tag.attrib['source']))[0]

    firstgid = int(tag.attrib['firstgid'])

    return TmxTileset(tileset_name, firstgid)



def parse_layer_tag(tag : xml.etree.ElementTree.Element) -> list[int]:
    validate_tag_attr(tag, 'width', str(MAP_WIDTH))
    validate_tag_attr(tag, 'height', str(MAP_HEIGHT))

    if len(tag) != 1 or tag[0].tag != 'data':
        raise RoomError("Unexpected data")

    data_tag = tag[0]

    validate_tag_attr(data_tag, 'compression', 'gzip')

    if not data_tag.text:
        raise RoomError("Expected base64 encoded data")

    binary_data = gzip.decompress(base64.b64decode(data_tag.text))

    assert len(binary_data) == MAP_WIDTH * MAP_HEIGHT * 4

    tiles = [ i[0] for i in struct.iter_unpack('<I', binary_data) ]

    return tiles



def parse_objectgroup_tag(tag : xml.etree.ElementTree.Element) -> list[TmxEntity]:
    objects = list()

    if 'offsetx' in tag.attrib or 'offsety' in tag.attrib:
        raise RoomError('<objectgroup> tag must not have an offset')


    for child in tag:
        if child.tag == 'object':
            parameter = None

            if not any([c.tag == 'point' for c in child]):
                raise RoomError('Object must be a point')

            for c in child:
                if c.tag == 'properties':
                    for p in c:
                        if p.tag == 'property':
                            if parameter is None:
                                if p.attrib['name'] == 'parameter':
                                    parameter = p.attrib['value']
                                else:
                                    raise RoomError(f"Unknown property: { p.attrib['name'] }")
                            else:
                                raise RoomError('Only one parameter is allowed per entity')

            a = child.attrib
            objects.append(TmxEntity(int(a['id']), a.get('name'), int(a['x']), int(a['y']), a['type'], parameter))


    return objects



def parse_tmx_map(et : xml.etree.ElementTree.ElementTree) -> TmxMap:
    root = et.getroot()

    if root.tag != 'map':
        raise RoomError("Invalid XML file: Expected a <map> tag")

    validate_tag_attr(root, 'orientation', 'orthogonal')
    validate_tag_attr(root, 'renderorder', 'right-down')
    validate_tag_attr(root, 'width', str(MAP_WIDTH))
    validate_tag_attr(root, 'height', str(MAP_HEIGHT))
    validate_tag_attr(root, 'tilewidth', str(TILE_SIZE))
    validate_tag_attr(root, 'tileheight', str(TILE_SIZE))
    validate_tag_attr(root, 'infinite', '0')

    tileset = None
    tiles = None
    entities = None

    for child in root:
        if child.tag == 'tileset':
            if tileset is not None:
                raise RoomError('Expected only one <tileset> tag')
            tileset = parse_tileset_tag(child)

        elif child.tag == 'layer':
            if tiles is not None:
                raise RoomError('Expected only one <layer> tag')
            tiles = parse_layer_tag(child)

        elif child.tag == 'objectgroup':
            if entities is not None:
                raise RoomError('Expected only one <objectgroup> tag')
            entities = parse_objectgroup_tag(child)

    if entities is None:
        entities = list()

    if tileset is None:
        raise RoomError('Missing <tileset> tag')

    if tiles is None:
        raise RoomError('Missing <layer> tag')

    return TmxMap(tileset, tiles, entities)



class RoomEntity(NamedTuple):
    xpos        : int
    ypos        : int
    type_id     : int
    parameter   : int


def process_room_entities(room_entities : list[TmxEntity], all_entities : OrderedDict[str, Entity]) -> list[RoomEntity]:
    if len(room_entities) > ENTITIES_IN_MAP:
        raise RoomError(f"Too many entities in room ({ len(room_entities) }, max: { ENTITIES_IN_MAP }");

    errors : list[str] = list()

    out = list()

    for tmx_entity in room_entities:
        def add_error(msg : str) -> None:
            if tmx_entity.name:
                errors.append(f"Entity { tmx_entity.id } { tmx_entity.name } ({ tmx_entity.type }): { msg }")
            else:
                errors.append(f"Entity { tmx_entity.id } ({ tmx_entity.type }): { msg }")

        entity_type = all_entities.get(tmx_entity.type)
        if entity_type is None:
            errors.append(f"Entity { tmx_entity.id }: Unknown entity type: { tmx_entity.type }")
            continue

        if tmx_entity.x < 0 or tmx_entity.x >= MAP_WIDTH_PX:
            add_error(f"Invalid x position: { tmx_entity.x }")

        if tmx_entity.y < 0 or tmx_entity.y >= MAP_HEIGHT_PX:
            add_error(f"Invalid x position: { tmx_entity.y }")


        parameter = 0
        if entity_type.code.parameter:
            p = entity_type.code.parameter

            if not tmx_entity.parameter:
                add_error(f"Missing parameter value for { p.type }")
            elif p.type == 'enum':
                try:
                    parameter = p.values.index(tmx_entity.parameter)
                except ValueError:
                    add_error(f"Invalid parameter for { tmx_entity.type } enum: { tmx_entity.parameter }")
            else:
                add_error(f"Unknown parameter type: { p.type }")
        else:
            # no parameter
            if tmx_entity.parameter is not None:
                add_error(f"Entity does not have a parameter")


        out.append(RoomEntity(
            xpos      = tmx_entity.x,
            ypos      = tmx_entity.y,
            type_id   = entity_type.id,
            parameter = parameter
        ))

    if errors:
        raise RoomError(errors)

    return out



def create_room_entities_soa(entities : list[RoomEntity]) -> bytes:
    assert len(entities) <= ENTITIES_IN_MAP

    padding = bytes([ 0xff ] * (ENTITIES_IN_MAP - len(entities)))

    data = bytearray()

    # entity_xPos
    for e in entities:
        data.append(e.xpos)
    data += padding

    # entity_yPos
    for e in entities:
        data.append(e.ypos)
    data += padding

    # entity_type
    for e in entities:
        data.append(e.type_id)
    data += padding

    # entity_parameter
    for e in entities:
        data.append(e.parameter)
    data += padding

    return data



def create_map_data(tmx_map : TmxMap, mapping : Mappings, room_entities : list[RoomEntity]) -> bytes:

    try:
        tileset_id = mapping.mt_tilesets.index(tmx_map.tileset.name)
    except ValueError:
        raise RoomError('Unknown MetaTile tileset name: { tmx_map.tileset.name }') from None

    try:
        map_data = bytes([ i - tmx_map.tileset.firstgid for i in tmx_map.map ])
    except ValueError:
        raise RoomError("Unknown tile in map.  There must be a maximum of 256 tiles in the tileset and no transparent or flipped tiles in the map.")


    data = bytearray(map_data)

    # Tileset byte
    data.append(tileset_id)

    data += create_room_entities_soa(room_entities)

    return data



def compile_room(filename : str, entities : EntitiesJson, mapping : Mappings) -> bytes:
    with open(filename, 'r') as fp:
        tmx_et = xml.etree.ElementTree.parse(fp)

    tmx_map = parse_tmx_map(tmx_et)

    room_entities = process_room_entities(tmx_map.entities, entities.entities)

    # ::TODO compress room data with lz4::

    return create_map_data(tmx_map, mapping, room_entities)



def get_list_of_tmx_files(directory : str) -> list[str]:
    tmx_files = list()

    for e in os.scandir(directory):
        if e.is_file() and e.name.startswith('_') is False:
            ext = os.path.splitext(e.name)[1]
            if ext == '.tmx':
                tmx_files.append(e.name)

    tmx_files.sort()

    return tmx_files



ROOM_LOCATION_REGEX = re.compile(r'(\d+)-(\d+)-.+.tmx$')

def extract_room_id(basename : str) -> int:
    m = ROOM_LOCATION_REGEX.match(basename)
    if not m:
        raise RoomError("Invalid room filename")

    return int(m.group(1), 10) + 16 * int(m.group(2), 10)



def compile_rooms(rooms_directory : str, entities : EntitiesJson, mapping : Mappings) -> bytes:
    errors : list[tuple[Filename, Exception]] = list()

    tmx_files = get_list_of_tmx_files(rooms_directory)

    # First 256 words: index of each room in the binary
    # If a value is 0xffff, then there is no room that location
    out = bytearray([0xff, 0xff]) * 256

    room_id_set = set()

    room_addr = mapping.memory_map.mode.bank_start

    for basename in tmx_files:
        try:
            room_data = compile_room(os.path.join(rooms_directory, basename), entities, mapping)

            room_id = extract_room_id(basename)

            if room_id in room_id_set:
                raise RoomError(f"Two rooms have the same location: { room_id }")
            room_id_set.add(room_id)

            out[room_id * 2 + 0] = room_addr & 0xff
            out[room_id * 2 + 1] = room_addr >> 8
            out += room_data

            room_addr += len(room_data)

        except Exception as ex:
            errors.append( (basename, ex) )


    if room_addr > 0x10000:
        raise RuntimeError("Output too large.  Maximum rooms binary is 64KiB.")


    if errors:
        # Print errors
        sys.stderr.write('ROOM ERRORS:\n')
        for basename, e in errors:
            sys.stderr.write(f"{ basename }\n")
            if isinstance(e, RoomError):
                for m in e.errors:
                    sys.stderr.write(f"    { m }\n")
                sys.stderr.write('\n')
            else:
                sys.stderr.write(f"    { type(e).__name__ }: { e }")

        sys.exit(f"{ len(errors) } rooms contain errors")

    return out



def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='output file')
    parser.add_argument('mapping_filename', action='store',
                        help='mapping json file input')
    parser.add_argument('entities_json_file', action='store',
                        help='entities JSON file input')
    parser.add_argument('rooms_directory', action='store',
                        help='rooms directory (containing tmx files)')

    args = parser.parse_args()

    return args;



def main() -> None:
    args = parse_arguments()

    entities = load_entities_json(args.entities_json_file)
    mapping = load_mappings_json(args.mapping_filename)

    rooms_bin = compile_rooms(args.rooms_directory, entities, mapping)

    with open(args.output, 'wb') as fp:
        fp.write(rooms_bin)



if __name__ == '__main__':
    main()

