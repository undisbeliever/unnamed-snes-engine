#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import os
import gzip
import base64
import struct
import xml.etree.ElementTree
import posixpath
from collections import OrderedDict
from typing import Final, NamedTuple, Optional, Union

from .json_formats import (
    Mappings,
    EntitiesJson,
    Entity,
    SecondLayerCallback,
    Callback,
    SecondLayerInput,
    DungeonsJson,
    DungeonInput,
    OtherResources,
    Name,
)
from .data_store import EngineData, FixedSizedData
from .errors import SimpleMultilineError
from .callbacks import parse_callback_parameters, ROOM_CALLBACK, SL_ROOM_PARAMETERS, RoomDoors, parse_int


MAP_WIDTH = 16
MAP_HEIGHT = 14

MAP_WIDTH_PX = MAP_WIDTH * 16
MAP_HEIGHT_PX = MAP_HEIGHT * 16

ENTITIES_IN_MAP = 8

TILE_SIZE = 16

GID_FLIP_MASK: Final = 0b1111 << (32 - 4)

BLANK_SL_PARAMETERS: Final = bytes(SL_ROOM_PARAMETERS.parameter_size)


ROOM_DATA_TYPE: Final = 1
WARP_ROOM_DATA_TYPE: Final = 2


WARP_ROOM_MOVE_PLAYER_FLAG: Final = 1 << 7
WARP_ROOM_FADEOUT_FLAG: Final = 1 << 6


class RoomError(SimpleMultilineError):
    pass


# Storing room dependencies in a NamedTuple as I am planning to add more in the future
class RoomDependencies(NamedTuple):
    dungeon: DungeonInput
    second_layer: Optional[SecondLayerInput]
    sl_callback: Optional[SecondLayerCallback]
    mt_tileset: Name


# `TmxMap` is a limited subset of the TMX data format
# (only supporting one map layer, image layer and tileset)


class TmxTileset(NamedTuple):
    basename: str
    firstgid: int


class TmxLayer(NamedTuple):
    name: str
    map: list[int]


class TmxEntity(NamedTuple):
    id: int
    name: Optional[str]
    x: int
    y: int
    type: str
    parameter: Optional[str]


class TmxMap(NamedTuple):
    map_class: str
    tilesets: list[TmxTileset]
    layers: list[TmxLayer]
    entities: list[TmxEntity]
    parameters: OrderedDict[Name, str]


class TmxWarpRoom(NamedTuple):
    warp_string: str


def validate_tag_attr(tag: xml.etree.ElementTree.Element, name: str, value: str, error_list: list[str]) -> None:
    if tag.attrib.get(name) != value:
        error_list.append(f'Invalid attribute: Expected {name}="{value}"')


def read_tag_attr_int(tag: xml.etree.ElementTree.Element, name: str, error_list: list[str]) -> int:
    a = tag.attrib.get(name)
    if not a:
        error_list.append(f"<{ tag.tag } id={ tag.attrib.get('id', '') }>: Missing attribute `{name}`")
        return 0

    try:
        return int(a)
    except:
        error_list.append(f"<{ tag.tag } id={ tag.attrib.get('id', '') } { name }>: Cannot convert attribute to int: { a }")
        return 0


def parse_tileset_tag(tag: xml.etree.ElementTree.Element) -> TmxTileset:
    source = tag.attrib.get("source")

    if source is None:
        raise ValueError("Embedded tilesets are not supported")

    # basename without extension
    basename = posixpath.splitext(posixpath.basename(source))[0]

    firstgid = int(tag.attrib["firstgid"])

    return TmxTileset(basename, firstgid)


def parse_layer_tag(tag: xml.etree.ElementTree.Element) -> TmxLayer:
    layer_width = tag.attrib.get("width")
    layer_height = tag.attrib.get("height")

    if layer_width != str(MAP_WIDTH) or layer_height != str(MAP_HEIGHT):
        raise ValueError(f"Tile Layer must be { MAP_WIDTH } x { MAP_HEIGHT }")

    if len(tag) != 1 or tag[0].tag != "data":
        raise ValueError("Unexpected data")

    data_tag = tag[0]

    compression = data_tag.attrib.get("compression")
    if compression != "gzip":
        raise ValueError("Expected a gzip compressed tile layer")

    if not data_tag.text:
        raise ValueError("No tile layer data")

    binary_data = gzip.decompress(base64.b64decode(data_tag.text))

    expected_binary_data_size = MAP_WIDTH * MAP_HEIGHT * 4
    if len(binary_data) != expected_binary_data_size:
        raise ValueError(f"Tile layer data size mismatch (got { len(binary_data) }, expected { expected_binary_data_size })")

    return TmxLayer(
        tag.attrib.get("name", ""),
        [i[0] for i in struct.iter_unpack("<I", binary_data)],
    )


def parse_objectgroup_tag(tag: xml.etree.ElementTree.Element, error_list: list[str]) -> tuple[list[TmxEntity], Optional[TmxWarpRoom]]:
    objects = list()
    warp_room = None

    if "offsetx" in tag.attrib or "offsety" in tag.attrib:
        error_list.append("<objectgroup> tag must not have an offset")

    for child in tag:
        if child.tag == "object":
            o_id_str = child.attrib.get("id", "")

            def add_error(msg: str) -> None:
                error_list.append(f'<object id="{ o_id_str }">: { msg }')

            if len(child) == 1 and child[0].tag == "text":
                c = child[0]
                if warp_room is None:
                    warp_text = c.text
                    if warp_text:
                        warp_room = TmxWarpRoom(warp_text)
                    else:
                        add_error("No text")
                else:
                    add_error("Only one <text> tag is allowed per room")
            else:
                o_id = read_tag_attr_int(child, "id", error_list)
                o_name = child.attrib.get("name")
                o_x = read_tag_attr_int(child, "x", error_list)
                o_y = read_tag_attr_int(child, "y", error_list)
                o_type = child.attrib.get("class", child.attrib.get("type"))  # Tiled 1.9 renamed 'type' attribute to 'class'

                if not o_type:
                    add_error("Missing type")
                    o_type = "__ERROR__"

                parameter = None

                if not any([c.tag == "point" for c in child]):
                    add_error("Object must be a point")

                for c in child:
                    if c.tag == "properties":
                        for p in c:
                            if p.tag == "property":
                                if parameter is None:
                                    p_name = p.attrib.get("name", "")
                                    if p_name == "parameter":
                                        parameter = p.attrib.get("value")
                                        if not parameter:
                                            add_error("Missing parameter value")
                                    else:
                                        add_error(f"Unknown property: { p_name }")
                                else:
                                    add_error("Only one parameter is allowed per entity")
                objects.append(TmxEntity(o_id, o_name, o_x, o_y, o_type, parameter))

    return objects, warp_room


def parse_map_parameters_tag(tag: xml.etree.ElementTree.Element, error_list: list[str]) -> OrderedDict[Name, str]:
    out = OrderedDict[Name, str]()

    for child in tag:
        if child.tag == "property":
            name = child.attrib.get("name")
            value = child.attrib.get("value")

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


def parse_tmx_map(et: xml.etree.ElementTree.ElementTree) -> Union[TmxMap | TmxWarpRoom]:
    error_list: list[str] = list()

    root = et.getroot()

    if root.tag != "map":
        raise RoomError("Error reading TMX file", ["Expected a <map> tag"])

    validate_tag_attr(root, "orientation", "orthogonal", error_list)
    validate_tag_attr(root, "renderorder", "right-down", error_list)
    validate_tag_attr(root, "width", str(MAP_WIDTH), error_list)
    validate_tag_attr(root, "height", str(MAP_HEIGHT), error_list)
    validate_tag_attr(root, "tilewidth", str(TILE_SIZE), error_list)
    validate_tag_attr(root, "tileheight", str(TILE_SIZE), error_list)
    validate_tag_attr(root, "infinite", "0", error_list)

    tilesets = list()
    layers = list()
    entities = None
    warp_room = None
    parameters = OrderedDict[Name, str]()

    for child in root:
        if child.tag == "tileset":
            try:
                tilesets.append(parse_tileset_tag(child))
            except Exception as e:
                error_list.append(f"<tileset>: { e }")

        elif child.tag == "layer":
            try:
                layers.append(parse_layer_tag(child))
            except Exception as e:
                error_list.append(f"<layer>: { e }")

        elif child.tag == "objectgroup":
            if entities is not None:
                error_list.append("Expected only one <objectgroup> tag")
            entities, warp_room = parse_objectgroup_tag(child, error_list)

        elif child.tag == "properties":
            if parameters:
                error_list.append("Expected only one <properties> tag")
            parameters = parse_map_parameters_tag(child, error_list)

    if warp_room:
        if tilesets:
            error_list.append("<tileset> tag not allowed in a warp room")

        if layers:
            error_list.append("<layer> tag not allowed in a warp room")

        if entities:
            error_list.append("Entities are not allowed in a warp room")

        if error_list:
            raise RoomError("Error reading TMX file", error_list)

        return warp_room

    else:
        map_class = root.attrib.get("class", root.attrib.get("type"))  # Tiled 1.9 renamed 'type' attribute to 'class'
        if not map_class:
            error_list.append("<map>: Missing class/type attribute")

        if entities is None:
            entities = list()

        if not tilesets:
            error_list.append("Missing <tileset> tag")

        if not layers:
            error_list.append("Missing <layer> tag")

        if error_list:
            raise RoomError("Error reading TMX file", error_list)

        assert map_class
        assert entities is not None

        return TmxMap(map_class, tilesets, layers, entities, parameters)


class RoomEntity(NamedTuple):
    xpos: int
    ypos: int
    type_id: int
    parameter: int


class RoomIntermediate(NamedTuple):
    map_data: bytes
    entities: list[RoomEntity]
    room_event: Callback
    room_event_data: bytes
    sl_parameters: bytes
    sl_map: Optional[bytes]


def process_room_entities(
    room_entities: list[TmxEntity], all_entities: OrderedDict[str, Entity], mapping: Mappings, error_list: list[str]
) -> list[RoomEntity]:
    if len(room_entities) > ENTITIES_IN_MAP:
        error_list.append(f"Too many entities in room ({ len(room_entities) }, max: { ENTITIES_IN_MAP }")

    out = list()

    for tmx_entity in room_entities:

        def add_error(msg: str) -> None:
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
                elif p.type == "enum":
                    try:
                        assert p.values
                        parameter = p.values.index(tmx_entity.parameter)
                    except ValueError:
                        add_error(f"Invalid parameter for { tmx_entity.type } enum: { tmx_entity.parameter }")
                elif p.type == "dungeon_flag":
                    try:
                        parameter = mapping.gamestate.dungeon_flags[tmx_entity.parameter].var_index
                    except KeyError:
                        add_error(f"Invalid parameter for { tmx_entity.type } enum: { tmx_entity.parameter }")
                elif p.type == "u8":
                    try:
                        parameter = parse_int(tmx_entity.parameter, 0xFF, error_list)
                    except ValueError:
                        add_error(f"Invalid parameter for { tmx_entity.type } enum: { tmx_entity.parameter }")
                else:
                    add_error(f"Unknown parameter type: { p.type }")
            else:
                # no parameter
                if tmx_entity.parameter is not None:
                    add_error("Entity does not have a parameter")

        out.append(RoomEntity(xpos=tmx_entity.x, ypos=tmx_entity.y, type_id=entity_type_id, parameter=parameter))

    return out


# ::TODO make configurable::
LOCKED_DOOR_TILE_IDS: Final = (0x80, 0xA0, 0xC0, 0xE0)
OPEN_DOOR_TILE_IDS: Final = (0x82, 0xA2, 0xC2, 0xE2)


def process_layer(tilemap: TmxTileset, layer: TmxLayer, error_list: list[str]) -> Optional[bytes]:
    firstgid: Final = tilemap.firstgid
    try:
        return bytes([i - firstgid if i != 0 else 0 for i in layer.map])
    except ValueError:
        # There is a bad cell in the map
        lastgid = firstgid + 256 - 1

        for i, t in enumerate(layer.map):
            if t != 0 and (t < firstgid or t > lastgid):
                x, y = divmod(i, MAP_WIDTH)
                if t & GID_FLIP_MASK != 0:
                    error_list.append(f'<layer name="{layer.name}"> {x}, {y}: flipped tiles are not allowed')
                else:
                    error_list.append(f'<layer name="{layer.name}"> {x}, {y}: invalid tile {t} (wrong tileset?)')

        return None


def is_mt_tileset(tileset: TmxTileset, dependencies: RoomDependencies) -> bool:
    return tileset.basename == dependencies.mt_tileset


def process_layers_and_tilesets(
    tmx_map: TmxMap, dependencies: RoomDependencies, error_list: list[str]
) -> tuple[Optional[bytes], Optional[bytes]]:

    if dependencies.second_layer and dependencies.second_layer.part_of_room:
        if len(tmx_map.tilesets) == 2 and len(tmx_map.layers) == 2:
            if dependencies.second_layer.above_metatiles:
                mt_map, sl_map = tmx_map.layers[0], tmx_map.layers[1]
            else:
                mt_map, sl_map = tmx_map.layers[1], tmx_map.layers[0]

            if is_mt_tileset(tmx_map.tilesets[0], dependencies):
                mt_tileset, sl_tileset = tmx_map.tilesets[0], tmx_map.tilesets[1]
            elif is_mt_tileset(tmx_map.tilesets[1], dependencies):
                mt_tileset, sl_tileset = tmx_map.tilesets[1], tmx_map.tilesets[0]
            else:
                error_list.append(f'Cannot find dungeon metatile tileset "{dependencies.mt_tileset}"')
                return None, None

            if sl_tileset.basename != dependencies.second_layer.name:
                error_list.append(f'<tileset {sl_tileset.basename}> does not match second-layer "{dependencies.second_layer.name}"')

            return process_layer(mt_tileset, mt_map, error_list), process_layer(sl_tileset, sl_map, error_list)
        else:
            if len(tmx_map.tilesets) != 2:
                error_list.append("Expected two <tileset> tags.  Second-Layer is part_of_room.")
            if len(tmx_map.layers) != 2:
                error_list.append("Expected two <layer> tags.  Second-Layer is part_of_room.")
            return None, None

    else:
        if len(tmx_map.tilesets) == 1 and len(tmx_map.layers) == 1:
            mt_tileset, mt_map = tmx_map.tilesets[0], tmx_map.layers[0]

            if not is_mt_tileset(mt_tileset, dependencies):
                error_list.append("<tileset> does not match dungeon metatile tileset")

            return process_layer(mt_tileset, mt_map, error_list), None
        else:
            if len(tmx_map.layers) != 1:
                error_list.append("Expected one <tileset> tag.  There is only one room layer")
            if len(tmx_map.layers) != 1:
                error_list.append("Expected one <layer> tag.  There is only one room layer")
            return None, None


def process_room(
    tmx_map: TmxMap, dependencies: RoomDependencies, mapping: Mappings, all_entities: OrderedDict[str, Entity]
) -> RoomIntermediate:
    error_list: list[str] = list()

    mt_map, sl_map = process_layers_and_tilesets(tmx_map, dependencies, error_list)

    room_entities = process_room_entities(tmx_map.entities, all_entities, mapping, error_list)

    room_event = mapping.room_events.get(tmx_map.map_class)
    if not room_event:
        error_list.append("Unknown room event: { tmx_map.map_class }")

    if error_list:
        raise RoomError("Error compiling room", error_list)

    assert mt_map is not None
    assert room_event is not None

    assert len(mt_map) == MAP_WIDTH * MAP_HEIGHT
    room_doors = RoomDoors(
        map_data=mt_map,
        locked_door_tiles=LOCKED_DOOR_TILE_IDS,
        open_door_tiles=OPEN_DOOR_TILE_IDS,
    )

    room_event_data = parse_callback_parameters(ROOM_CALLBACK, room_event, tmx_map.parameters, mapping, room_doors, error_list)

    if dependencies.sl_callback:
        sl_parameters = parse_callback_parameters(
            SL_ROOM_PARAMETERS, dependencies.sl_callback, tmx_map.parameters, mapping, None, error_list
        )
    else:
        sl_parameters = BLANK_SL_PARAMETERS

    if error_list:
        raise RoomError("Error compiling room", error_list)

    return RoomIntermediate(mt_map, room_entities, room_event, room_event_data, sl_parameters, sl_map)


def create_room_entities_soa(entities: list[RoomEntity]) -> bytes:
    assert len(entities) <= ENTITIES_IN_MAP

    padding = bytes([0xFF] * (ENTITIES_IN_MAP - len(entities)))

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


def create_room_data(room: RoomIntermediate) -> bytes:
    data = bytearray()

    data.append(ROOM_DATA_TYPE)

    data += room.map_data

    # Room event
    assert len(room.room_event_data) == 4
    data.append(room.room_event.id * 2)
    data += room.room_event_data

    assert len(room.sl_parameters) == 2
    data += room.sl_parameters

    data += create_room_entities_soa(room.entities)

    if room.sl_map:
        data += room.sl_map

    return data


WARP_REGEX: Final = re.compile(r"^warp\s+(\w+\s+)?(\d+)-(\d+)(-[^\s]*)?(\s+\d+\s*,\s*\d+)?(\s+fadeout)?$")
WARP_FORMAT: Final = "warp [dungeon] <room> [playerY,playerY] [fadeout]"


def process_warp_room(warp_room: TmxWarpRoom, dependencies: RoomDependencies, dungeons: DungeonsJson) -> bytes:
    error_list: list[str] = list()

    m = WARP_REGEX.match(warp_room.warp_string)

    if not m:
        raise RoomError("error compiling warp room", [f'Unknown warp string, expected "{WARP_FORMAT}"'])

    flags = 0

    dungeon_name = m.group(1)
    room_x: Final = int(m.group(2))
    room_y: Final = int(m.group(3))
    player_pos: Final = m.group(5)
    fadeout: Final = m.group(6)

    if dungeon_name:
        dungeon_name = dungeon_name.strip()
        dungeon = dungeons.dungeons.get(dungeon_name)
        if dungeon is None:
            error_list.append(f"Unknown dungeon: {dungeon_name}")
            dungeon = dependencies.dungeon
    else:
        dungeon = dependencies.dungeon

    if room_x < 0 or room_x >= dungeon.width or room_y < 0 or room_y >= dungeon.height:
        error_list.append(f"room location out of bounds: {room_x}, {room_y} (dungeon is {dungeon.width}x{dungeon.height})")

    if player_pos:
        flags |= WARP_ROOM_MOVE_PLAYER_FLAG
        _pos = player_pos.split(",")
        player_x = int(_pos[0].strip())
        player_y = int(_pos[1].strip())

        if player_x < 0 or player_x >= MAP_WIDTH_PX or player_y < 0 or player_y >= MAP_HEIGHT_PX:
            error_list.append(f"player position outside the room: {player_x}, {player_y}")
    else:
        player_x = MAP_WIDTH_PX // 2
        player_y = MAP_HEIGHT_PX // 2

    if fadeout:
        flags |= WARP_ROOM_FADEOUT_FLAG

    if error_list:
        raise RoomError("Error compiling warp room", error_list)

    return bytes((WARP_ROOM_DATA_TYPE, flags, dungeon.id, room_x, room_y, player_x, player_y))


def compile_room(
    filename: str, dependencies: RoomDependencies, entities: EntitiesJson, mapping: Mappings, dungeons: DungeonsJson
) -> EngineData:
    with open(filename, "r") as fp:
        tmx_et = xml.etree.ElementTree.parse(fp)

    tmx_map = parse_tmx_map(tmx_et)

    if isinstance(tmx_map, TmxMap):
        room = process_room(tmx_map, dependencies, mapping, entities.entities)
        map_data = create_room_data(room)
    else:
        assert isinstance(tmx_map, TmxWarpRoom)
        map_data = process_warp_room(tmx_map, dependencies, dungeons)

    # ::TODO compress room data with lz4::
    return EngineData(
        ram_data=FixedSizedData(map_data),
        ppu_data=None,
    )


# Must not raise an exception
# Does not preform error checking, that will be done in the dungeon compiler.
def build_room_dependencies__noexcept(
    dungeon: DungeonInput, mappings: Mappings, other_resources: OtherResources
) -> Optional[RoomDependencies]:
    try:
        if dungeon.second_layer:
            second_layer = other_resources.second_layers.get(dungeon.second_layer)
        else:
            second_layer = None

        if second_layer and second_layer.callback:
            sl_callback = mappings.sl_callbacks[second_layer.callback]
        else:
            sl_callback = None

        return RoomDependencies(
            dungeon=dungeon,
            second_layer=second_layer,
            sl_callback=sl_callback,
            mt_tileset=dungeon.tileset,
        )
    except Exception:
        return None


def get_list_of_tmx_files(directory: str) -> list[str]:
    tmx_files = list()

    for e in os.scandir(directory):
        if e.is_file() and e.name.startswith("_") is False:
            ext = os.path.splitext(e.name)[1]
            if ext == ".tmx":
                tmx_files.append(e.name)

    tmx_files.sort()

    return tmx_files


ROOM_LOCATION_REGEX = re.compile(r"(\d+)-(\d+)-.+.tmx$")


def extract_room_position(basename: str) -> tuple[int, int]:
    m = ROOM_LOCATION_REGEX.match(basename)
    if not m:
        raise RuntimeError("Invalid room filename")

    return int(m.group(1), 10), int(m.group(2), 10)
