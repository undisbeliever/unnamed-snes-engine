#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import gzip
import base64
import struct
import argparse
import xml.etree.ElementTree
import posixpath
from collections import namedtuple

from _json_formats import load_entities_json, load_mappings_json


MAP_WIDTH = 16
MAP_HEIGHT = 14

ENTITIES_IN_MAP = 8

TILE_SIZE = 16



# A limited subset of the TMX data format (only supporting one map layer and tileset)::
TmxMap = namedtuple('MapData', ('tileset', 'map', 'entities'))
Entity = namedtuple('Entity', ('x', 'y', 'type', 'parameter'))
TilesetData = namedtuple('TilesetData', ('name', 'firstgid'))



def validate_tag_attr(tag, name, value):
    if tag.attrib[name] != value:
        raise ValueError(f"Invalid attribute: Expected {name}=\"{value}\"")



def parse_tileset_tag(tag):
    # Return basename without extension
    tileset_name = posixpath.splitext(posixpath.basename(tag.attrib['source']))[0]

    firstgid = int(tag.attrib['firstgid'])

    return TilesetData(tileset_name, firstgid)



def parse_layer_tag(tag):
    validate_tag_attr(tag, 'width', str(MAP_WIDTH))
    validate_tag_attr(tag, 'height', str(MAP_HEIGHT))

    if len(tag) != 1 or tag[0].tag != 'data':
        raise ValueError("Unexpected data")

    data_tag = tag[0]

    validate_tag_attr(data_tag, 'compression', 'gzip')

    binary_data = gzip.decompress(base64.b64decode(data_tag.text))

    assert len(binary_data) == MAP_WIDTH * MAP_HEIGHT * 4

    tiles = [ i[0] for i in struct.iter_unpack('<I', binary_data) ]

    return tiles



def parse_objectgroup_tag(tag):
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

            objects.append(Entity(int(child.attrib['x']), int(child.attrib['y']), child.attrib['type'], parameter))


    return objects



def parse_tmx_map(et):
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

    return TmxMap(tileset, tiles, entities)



def get_entity_parameter(e, entities):
    p = entities[e.type].code.parameter

    if p:
        if p.type == 'enum':
            return p.values.index(e.parameter)
        else:
            raise ValueError(f"Unknown parameter type: { p.type }")
    else:
        # no parameter
        if e.parameter is not None:
            raise ValueError(f"Invalid parameter for { e.type } entity: { e.parameter }")

        return 0



def create_room_entities_soa(room_entities, entities):
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



def create_map_data(tmx_map, mapping, entities):
    data = bytearray()

    try:
        data += bytes([ i - tmx_map.tileset.firstgid for i in tmx_map.map ])
    except ValueError:
        raise ValueError("Unknown tile in map.  There must be a maximum of 256 tiles in the tileset and no transparent or flipped tiles in the map.")


    # Tileset byte
    data.append(mapping.tilesets.index(tmx_map.tileset.name))

    data += create_room_entities_soa(tmx_map.entities, entities)


    return data



def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='map output file')
    parser.add_argument('tmx_filename', action='store',
                        help='tmx file input')
    parser.add_argument('mapping_filename', action='store',
                        help='mapping json file input')
    parser.add_argument('entities_json_file', action='store',
                        help='entities JSON file input')

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    with open(args.tmx_filename, 'r') as fp:
        tmx_et = xml.etree.ElementTree.parse(fp)

    entities = load_entities_json(args.entities_json_file)
    mapping = load_mappings_json(args.mapping_filename)

    tmx_map = parse_tmx_map(tmx_et)

    map_data = create_map_data(tmx_map, mapping, entities.entities)

    with open(args.output, 'wb') as fp:
        fp.write(map_data)



if __name__ == '__main__':
    main()

