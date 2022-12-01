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
                          Filename, Mappings, EntitiesJson, Entity, RoomEvent, Name
from _common import MultilineError, SimpleMultilineError, print_error

from typing import Final, NamedTuple, Optional, Union


MAP_WIDTH = 16
MAP_HEIGHT = 14

MAP_WIDTH_PX  = MAP_WIDTH * 16
MAP_HEIGHT_PX = MAP_HEIGHT * 16

ENTITIES_IN_MAP = 8

TILE_SIZE = 16


class RoomError(SimpleMultilineError):
    pass


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
    map_class   : str
    tileset     : TmxTileset
    map         : list[int]
    entities    : list[TmxEntity]
    parameters  : OrderedDict[Name, str]



def validate_tag_attr(tag : xml.etree.ElementTree.Element, name : str, value : str, error_list : list[str]) -> None:
    if tag.attrib.get(name) != value:
        error_list.append(f"Invalid attribute: Expected {name}=\"{value}\"")



def read_tag_attr_int(tag : xml.etree.ElementTree.Element, name : str, error_list : list[str]) -> int:
    a = tag.attrib.get(name)
    if not a:
        error_list.append(f"<{ tag.tag } id={ tag.attrib.get('id', '') }>: Missing attribute `{name}`")
        return 0

    try:
        return int(a)
    except:
        error_list.append(f"<{ tag.tag } id={ tag.attrib.get('id', '') } { name }>: Cannot convert attribute to int: { a }")
        return 0



def parse_tileset_tag(tag : xml.etree.ElementTree.Element) -> Optional[TmxTileset]:
    # basename without extension
    tileset_name = posixpath.splitext(posixpath.basename(tag.attrib['source']))[0]

    firstgid = int(tag.attrib['firstgid'])

    return TmxTileset(tileset_name, firstgid)



def parse_layer_tag(tag : xml.etree.ElementTree.Element) -> list[int]:
    layer_width = tag.attrib.get('width')
    layer_height = tag.attrib.get('height')

    if layer_width != str(MAP_WIDTH) or layer_height != str(MAP_HEIGHT):
        raise ValueError(f"Tile Layer must be { MAP_WIDTH } x { MAP_HEIGHT }")

    if len(tag) != 1 or tag[0].tag != 'data':
        raise ValueError("Unexpected data")

    data_tag = tag[0]

    compression = data_tag.attrib.get('compression')
    if compression != 'gzip':
        raise ValueError('Expected a gzip compressed tile layer')

    if not data_tag.text:
        raise ValueError('No tile layer data')

    binary_data = gzip.decompress(base64.b64decode(data_tag.text))

    expected_binary_data_size = MAP_WIDTH * MAP_HEIGHT * 4
    if len(binary_data) != expected_binary_data_size:
        raise ValueError(f"Tile layer data size mismatch (got { len(binary_data) }, expected { expected_binary_data_size })")

    tiles = [ i[0] for i in struct.iter_unpack('<I', binary_data) ]

    return tiles



def parse_objectgroup_tag(tag : xml.etree.ElementTree.Element, error_list : list[str]) -> list[TmxEntity]:
    objects = list()

    if 'offsetx' in tag.attrib or 'offsety' in tag.attrib:
        error_list.append('<objectgroup> tag must not have an offset')

    for child in tag:
        if child.tag == 'object':
            o_id_str   = child.attrib.get('id', '')

            def add_error(msg : str) -> None:
                error_list.append(f"<object id=\"{ o_id_str }\">: { msg }")

            o_id   = read_tag_attr_int(child, 'id', error_list)
            o_name = child.attrib.get('name')
            o_x    = read_tag_attr_int(child, 'x', error_list)
            o_y    = read_tag_attr_int(child, 'y', error_list)
            o_type = child.attrib.get('class', child.attrib.get('type')) # Tiled 1.9 renamed 'type' attribute to 'class'

            if not o_type:
                add_error('Missing type')
                o_type = '__ERROR__'

            parameter = None

            if not any([c.tag == 'point' for c in child]):
                add_error('Object must be a point')

            for c in child:
                if c.tag == 'properties':
                    for p in c:
                        if p.tag == 'property':
                            if parameter is None:
                                p_name = p.attrib.get('name', '')
                                if p_name == 'parameter':
                                    parameter = p.attrib.get('value')
                                    if not parameter:
                                        add_error(f'Missing parameter value')
                                else:
                                    add_error(f'Unknown property: { p_name }')
                            else:
                                add_error('Only one parameter is allowed per entity')

            objects.append(TmxEntity(o_id, o_name, o_x, o_y, o_type, parameter))


    return objects



def parse_map_parameters_tag(tag : xml.etree.ElementTree.Element, error_list : list[str]) -> OrderedDict[Name, str]:
    out = OrderedDict[Name, str]()

    for child in tag:
        if child.tag == 'property':
            name = child.attrib.get('name')
            value = child.attrib.get('value')

            if name and value:
                out[name] = value
            else:
                if not name:
                    error_list.append("<property>: missing 'name' attribute")
                if not value:
                    error_list.append("<property>: missing 'value' attribute")
        else:
            error_list.append(f"Unknown tag: {child.tag}")


    return out



def parse_tmx_map(et : xml.etree.ElementTree.ElementTree) -> TmxMap:
    error_list : list[str] = list()

    root = et.getroot()

    if root.tag != 'map':
        raise RoomError('Error reading TMX file', ['Expected a <map> tag'])

    validate_tag_attr(root, 'orientation', 'orthogonal', error_list)
    validate_tag_attr(root, 'renderorder', 'right-down', error_list)
    validate_tag_attr(root, 'width', str(MAP_WIDTH), error_list)
    validate_tag_attr(root, 'height', str(MAP_HEIGHT), error_list)
    validate_tag_attr(root, 'tilewidth', str(TILE_SIZE), error_list)
    validate_tag_attr(root, 'tileheight', str(TILE_SIZE), error_list)
    validate_tag_attr(root, 'infinite', '0', error_list)

    map_class = root.attrib.get('class', root.attrib.get('type')) # Tiled 1.9 renamed 'type' attribute to 'class'
    if not map_class:
        error_list.append('<map>: Missing class/type attribute')

    tileset = None
    tiles = None
    entities = None
    parameters = OrderedDict[Name, str]()

    for child in root:
        if child.tag == 'tileset':
            if tileset is not None:
                error_list.append('Expected only one <tileset> tag')
            try:
                tileset = parse_tileset_tag(child)
            except Exception as e:
                error_list.append(f"<tileset>: { e }")

        elif child.tag == 'layer':
            if tiles is not None:
                error_list.append('Expected only one <layer> tag')
            try:
                tiles = parse_layer_tag(child)
            except Exception as e:
                error_list.append(f"<layer>: { e }")

        elif child.tag == 'objectgroup':
            if entities is not None:
                error_list.append('Expected only one <objectgroup> tag')
            entities = parse_objectgroup_tag(child, error_list)

        elif child.tag == 'properties':
            if parameters:
                error_list.append('Expected only one <properties> tag')
            parameters = parse_map_parameters_tag(child, error_list)

    if entities is None:
        entities = list()

    if tileset is None:
        error_list.append('Missing <tileset> tag')

    if tiles is None:
        error_list.append('Missing <layer> tag')

    if error_list:
        raise RoomError('Error reading TMX file', error_list)

    assert map_class
    assert tileset
    assert tiles
    assert entities is not None

    return TmxMap(map_class, tileset, tiles, entities, parameters)



class RoomEntity(NamedTuple):
    xpos        : int
    ypos        : int
    type_id     : int
    parameter   : int


class RoomIntermediate(NamedTuple):
    map_data              : bytes
    tileset_id            : int
    entities              : list[RoomEntity]
    room_event_data       : bytes



def process_room_entities(room_entities : list[TmxEntity], all_entities : OrderedDict[str, Entity], mapping : Mappings, error_list : list[str]) -> list[RoomEntity]:
    if len(room_entities) > ENTITIES_IN_MAP:
        error_list.append(f"Too many entities in room ({ len(room_entities) }, max: { ENTITIES_IN_MAP }");

    out = list()

    for tmx_entity in room_entities:
        def add_error(msg : str) -> None:
            if tmx_entity.name:
                error_list.append(f"Entity { tmx_entity.id } { tmx_entity.name } ({ tmx_entity.type }): { msg }")
            else:
                error_list.append(f"Entity { tmx_entity.id } ({ tmx_entity.type }): { msg }")

        entity_type = all_entities.get(tmx_entity.type)
        if entity_type is None:
            add_error(f"Unknown entity type: { tmx_entity.type }")

        if tmx_entity.x < 0 or tmx_entity.x >= MAP_WIDTH_PX:
            add_error(f"Invalid x position: { tmx_entity.x }")

        if tmx_entity.y < 0 or tmx_entity.y >= MAP_HEIGHT_PX:
            add_error(f"Invalid x position: { tmx_entity.y }")

        entity_type_id = 0
        parameter = 0

        if entity_type:
            entity_type_id = entity_type.id

            if entity_type.code.parameter:
                p = entity_type.code.parameter

                if not tmx_entity.parameter:
                    add_error(f"Missing parameter value for { p.type }")
                elif p.type == 'enum':
                    try:
                        assert p.values
                        parameter = p.values.index(tmx_entity.parameter)
                    except ValueError:
                        add_error(f"Invalid parameter for { tmx_entity.type } enum: { tmx_entity.parameter }")
                elif p.type == 'gamestateflag':
                    try:
                        parameter = mapping.gamestate_flags.index(tmx_entity.parameter)
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
            type_id   = entity_type_id,
            parameter = parameter
        ))

    return out



def parse_int(value : str, max_value : int, error_list : list[str]) -> int:
    try:
        int_value = int(value)
    except ValueError as e:
        error_list.append(str(e))
        return 0

    if int_value > max_value:
        error_list.append(f"Parameter is too large: got { int_value }, max: { max_value }")
        return 0

    return int_value




def process_room_event_data(tmx_map : TmxMap, mapping : Mappings, error_list : list[str]) -> bytes:

    out = bytearray()

    room_event = mapping.room_events.get(tmx_map.map_class)
    if not room_event:
        error_list.append("Unknown room event: { tmx_map.map_class }")
        return out

    out.append(room_event.id * 2)

    for p in room_event.parameters:
        value = tmx_map.parameters.get(p.name, p.default_value)

        if not value:
            error_list.append(f"Room Event {room_event.name}: Missing parameter {p.name}")
        else:
            if p.type == 'u8':
                out.append(parse_int(value, 0xff, error_list))
            else:
                error_list.append(f"Unknown Room Event parameter type: {p.type}")

    if len(out) < 5:
        out += bytes(5 - len(out))

    if len(out) > 5:
        error_list.append("CRITICAL ERROR: Room event data is too large")
        return bytes(5)

    return out



def process_room(tmx_map : TmxMap, mapping : Mappings, all_entities : OrderedDict[str, Entity]) -> RoomIntermediate:
    error_list : list[str] = list()

    try:
        tileset_id = mapping.mt_tilesets.index(tmx_map.tileset.name)
    except ValueError:
        error_list.append('Unknown MetaTile tileset name: { tmx_map.tileset.name }')

    try:
        map_data = bytes([ i - tmx_map.tileset.firstgid for i in tmx_map.map ])
    except ValueError:
        error_list.append("Unknown tile in map.  There must be a maximum of 256 tiles in the tileset and no transparent or flipped tiles in the map.")

    room_entities = process_room_entities(tmx_map.entities, all_entities, mapping, error_list)

    room_event_data = process_room_event_data(tmx_map, mapping, error_list)

    if error_list:
        raise RoomError('Error compiling room', error_list)

    return RoomIntermediate(map_data, tileset_id, room_entities, room_event_data)



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



def create_map_data(room : RoomIntermediate) -> bytes:

    data = bytearray(room.map_data)

    # Tileset byte
    data.append(room.tileset_id)

    # Room event
    assert len(room.room_event_data) == 5
    data += room.room_event_data

    data += create_room_entities_soa(room.entities)

    return data



def compile_room(filename : str, entities : EntitiesJson, mapping : Mappings) -> bytes:
    with open(filename, 'r') as fp:
        tmx_et = xml.etree.ElementTree.parse(fp)

    tmx_map = parse_tmx_map(tmx_et)

    room = process_room(tmx_map, mapping, entities.entities)

    # ::TODO compress room data with lz4::

    return create_map_data(room)



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
        raise RuntimeError("Invalid room filename")

    return int(m.group(1), 10) + 16 * int(m.group(2), 10)



def compile_rooms(rooms_directory : str, entities : EntitiesJson, mapping : Mappings) -> Optional[bytes]:
    valid = True

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
                raise RuntimeError(f"Two rooms have the same location: { room_id }")
            room_id_set.add(room_id)

            out[room_id * 2 + 0] = room_addr & 0xff
            out[room_id * 2 + 1] = room_addr >> 8
            out += room_data

            room_addr += len(room_data)

        except Exception as ex:
            print_error(f"ERROR compiling { basename }", ex)
            valid = False


    if room_addr > 0x10000:
        raise RuntimeError("Output too large.  Maximum rooms binary is 64KiB.")


    if not valid:
        return None

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

    if not rooms_bin:
        sys.exit('Error compiling rooms')

    with open(args.output, 'wb') as fp:
        fp.write(rooms_bin)



if __name__ == '__main__':
    main()

