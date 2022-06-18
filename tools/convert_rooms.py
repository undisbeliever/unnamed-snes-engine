#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import os

import gzip
import base64
import struct
import argparse
import xml.etree.ElementTree
import posixpath
from collections import OrderedDict

from _json_formats import load_entities_json, load_mappings_json, \
                          Mappings, EntitiesJson, Entity

from typing import NamedTuple, Optional


MAP_WIDTH = 16
MAP_HEIGHT = 14

ENTITIES_IN_MAP = 8

TILE_SIZE = 16



# `TmxMap` is a limited subset of the TMX data format (only supporting one map layer and tileset)

class TmxTileset(NamedTuple):
    name        : str
    firstgid    : int

class TmxEntity(NamedTuple):
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
        raise ValueError(f"Invalid attribute: Expected {name}=\"{value}\"")



def parse_tileset_tag(tag : xml.etree.ElementTree.Element) -> TmxTileset:
    # basename without extension
    tileset_name = posixpath.splitext(posixpath.basename(tag.attrib['source']))[0]

    firstgid = int(tag.attrib['firstgid'])

    return TmxTileset(tileset_name, firstgid)



def parse_layer_tag(tag : xml.etree.ElementTree.Element) -> list[int]:
    validate_tag_attr(tag, 'width', str(MAP_WIDTH))
    validate_tag_attr(tag, 'height', str(MAP_HEIGHT))

    if len(tag) != 1 or tag[0].tag != 'data':
        raise ValueError("Unexpected data")

    data_tag = tag[0]

    validate_tag_attr(data_tag, 'compression', 'gzip')

    if not data_tag.text:
        raise ValueError("Expected base64 encoded data")

    binary_data = gzip.decompress(base64.b64decode(data_tag.text))

    assert len(binary_data) == MAP_WIDTH * MAP_HEIGHT * 4

    tiles = [ i[0] for i in struct.iter_unpack('<I', binary_data) ]

    return tiles



def parse_objectgroup_tag(tag : xml.etree.ElementTree.Element) -> list[TmxEntity]:
    objects = list()

    if 'offsetx' in tag.attrib or 'offsety' in tag.attrib:
        raise ValueError('<objectgroup> tag must not have an offset')


    for child in tag:
        if child.tag == 'object':
            parameter = None

            if not any([c.tag == 'point' for c in child]):
                raise ValueError('Object must be a point')

            for c in child:
                if c.tag == 'properties':
                    for p in c:
                        if p.tag == 'property':
                            if parameter is None:
                                if p.attrib['name'] == 'parameter':
                                    parameter = p.attrib['value']
                                else:
                                    raise ValueError(f"Unknown property: { p.attrib['name'] }")
                            else:
                                raise ValueError('Only one parameter is allowed per entity')

            objects.append(TmxEntity(int(child.attrib['x']), int(child.attrib['y']), child.attrib['type'], parameter))


    return objects



def parse_tmx_map(et : xml.etree.ElementTree.ElementTree) -> TmxMap:
    root = et.getroot()

    if root.tag != 'map':
        raise ValueError("Invalid XML file: Expected a <map> tag")

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
                raise ValueError('Expected only one <tileset> tag')
            tileset = parse_tileset_tag(child)

        elif child.tag == 'layer':
            if tiles is not None:
                raise ValueError('Expected only one <layer> tag')
            tiles = parse_layer_tag(child)

        elif child.tag == 'objectgroup':
            if entities is not None:
                raise ValueError('Expected only one <objectgroup> tag')
            entities = parse_objectgroup_tag(child)

    if entities is None:
        entities = list()

    if tileset is None:
        raise ValueError('Missing <tileset> tag')

    if tiles is None:
        raise ValueError('Missing <layer> tag')

    return TmxMap(tileset, tiles, entities)



def get_entity_parameter(e : TmxEntity, entities : OrderedDict[str, Entity]) -> int:
    p = entities[e.type].code.parameter

    if p:
        if p.type == 'enum':
            if not e.parameter:
                raise ValueError(f"Missing parameter value for type: { p.type }")
            return p.values.index(e.parameter)
        else:
            raise ValueError(f"Unknown parameter type: { p.type }")
    else:
        # no parameter
        if e.parameter is not None:
            raise ValueError(f"Invalid parameter for { e.type } entity: { e.parameter }")

        return 0



def create_room_entities_soa(room_entities : list[TmxEntity], entities : OrderedDict[str, Entity]) -> bytes:
    if len(room_entities) > ENTITIES_IN_MAP:
        raise ValueError(f"Too many entities in room ({ len(room_entities) }, max: { ENTITIES_IN_MAP }");

    padding = bytes([ 0xff ] * (ENTITIES_IN_MAP - len(room_entities)))

    data = bytearray()

    # entity_xPos
    for e in room_entities:
        data.append(e.x)
    data += padding

    # entity_yPos
    for e in room_entities:
        data.append(e.y)
    data += padding

    # entity_type
    for e in room_entities:
        data.append(entities[e.type].id)
    data += padding

    # entity_parameter
    for e in room_entities:
        data.append(get_entity_parameter(e, entities))
    data += padding

    return data



def create_map_data(tmx_map : TmxMap, mapping : Mappings, entities : OrderedDict[str, Entity]) -> bytes:
    data = bytearray()

    try:
        data += bytes([ i - tmx_map.tileset.firstgid for i in tmx_map.map ])
    except ValueError:
        raise ValueError("Unknown tile in map.  There must be a maximum of 256 tiles in the tileset and no transparent or flipped tiles in the map.")


    # Tileset byte
    data.append(mapping.mt_tilesets.index(tmx_map.tileset.name))

    data += create_room_entities_soa(tmx_map.entities, entities)


    return data



def compile_room(filename : str, entities : EntitiesJson, mapping : Mappings) -> bytes:
    with open(filename, 'r') as fp:
        tmx_et = xml.etree.ElementTree.parse(fp)

    tmx_map = parse_tmx_map(tmx_et)

    # ::TODO compress room data with lz4::

    return create_map_data(tmx_map, mapping, entities.entities)



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
        raise ValueError("Invalid room filename")

    return int(m.group(1), 10) + 16 * int(m.group(2), 10)



def compile_rooms(rooms_directory : str, entities : EntitiesJson, mapping : Mappings) -> bytes:

    tmx_files = get_list_of_tmx_files(rooms_directory)

    # First 256 words: index of each room in the binary
    # If a value is 0xffff, then there is no room that location
    out = bytearray([0xff, 0xff]) * 256

    room_id_set = set()

    if mapping.memory_map.mode == 'hirom':
        room_addr = 0
    elif mapping.memory_map.mode == 'lorom':
        room_addr = 0x8000
    else:
        raise RuntimeError(f"Unknown memory map mode")

    for basename in tmx_files:
        room_data = compile_room(os.path.join(rooms_directory, basename), entities, mapping)

        room_id = extract_room_id(basename)

        if room_id in room_id_set:
            raise RuntimeError(f"Two rooms have the same location: { basename }")
        room_id_set.add(room_id)

        if room_addr >= 0x10000:
            raise RuntimeError("Output too large.  Maximum rooms binary is 64KiB.")

        out[room_id * 2 + 0] = room_addr & 0xff
        out[room_id * 2 + 1] = room_addr >> 8
        out += room_data

        room_addr += len(room_data)

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

