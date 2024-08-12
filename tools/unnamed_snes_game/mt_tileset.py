#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import os.path
import xml.etree.ElementTree
from typing import Final, NamedTuple, Optional

from .json_formats import Filename, Mappings, Name
from .palette import PaletteResource
from .snes import (
    TileMap,
    ConstSmallTileMap,
    SmallTilesetMap,
    ImageError,
    load_image_tile_extractor,
    extract_tiles_and_build_tilemap,
    convert_snes_tileset,
)
from .common import FixedSizedData, DynamicSizedData, EngineData
from .errors import SimpleMultilineError


N_TILES = 256

TILE_DATA_BPP = 4

DEFAULT_PRIORITY = 0


TILE_PROPERTY_SOLID_MASK: Final = 0b10000000
TILE_PROPERTY_PROJECTILE_SOLID_MASK: Final = 0b01000000
TILE_PROPERTY_INTERACTIVE_TILES_MASK: Final = 0b00111111


class TileProperty(NamedTuple):
    solid: bool
    projectile_solid: bool
    type: Optional[str]
    priority: int  # integer bitfield (4 bits wide), one priority bit for each 8px tile


class TsxFile(NamedTuple):
    name: str
    image_filename: Filename
    palette: str
    tile_properties: list[TileProperty]


class TsxFileError(SimpleMultilineError):
    pass


def create_metatile_map(tilemap: TileMap, tile_properties: list[TileProperty]) -> bytes:
    data = bytearray()

    if tilemap.width != 32 and tilemap.height != 32:
        raise ValueError(f"Invalid tilemap size: { tilemap.width }x{ tilemap.height }")

    priotity_bit = 1 << 4

    assert len(tilemap.grid) == 32 * 32
    for xoffset, yoffset in ((0, 0), (1, 0), (0, 1), (1, 1)):
        priotity_bit >>= 1

        for y in range(yoffset, 32, 2):
            for x in range(xoffset, 32, 2):
                tm = tilemap.get_tile(x, y)
                data.append(tm.tile_id & 0xFF)

        for y in range(yoffset, 32, 2):
            for x in range(xoffset, 32, 2):
                tile_id = (y // 2) * 16 + (x // 2)
                priority = tile_properties[tile_id].priority & priotity_bit

                tm = tilemap.get_tile(x, y)
                # This should never happen
                assert tm.tile_id <= 0x3FF
                assert tm.palette_id <= 7
                data.append(
                    (tm.tile_id >> 8) | (tm.palette_id << 2) | (bool(priority) << 5) | (bool(tm.hflip) << 6) | (bool(tm.vflip) << 7)
                )

    return data


def check_objectgroup_tag(tag: xml.etree.ElementTree.Element) -> bool:
    if len(tag) != 1:
        return False

    childTag = tag[0]

    if childTag.tag != "object":
        return False

    return (
        childTag.tag == "object"
        and childTag.attrib.get("x") == "0"
        and childTag.attrib.get("y") == "0"
        and childTag.attrib.get("width") == "16"
        and childTag.attrib.get("height") == "16"
    )


def read_tile_priority_value(value: Optional[str]) -> int:
    if not isinstance(value, str):
        raise ValueError("Unknown priority value type, expected string")

    if value == "0":
        return 0
    if value == "1":
        return 0xF

    if len(value) != 4:
        raise ValueError(f"Unknown priority value: { value }")

    return int(value, 2)


def read_tile_tag(tile_tag: xml.etree.ElementTree.Element, error_list: list[str]) -> tuple[int, TileProperty]:
    """Returns (tile_id, TileProperty)"""

    try:
        tile_id = int(tile_tag.attrib["id"])
        if tile_id < 0 or tile_id > 255:
            error_list.append(f"Invalid <tile> id: { tile_id }")
    except KeyError:
        error_list.append("<tile> tag with missing id")
        tile_id = -1
    except ValueError:
        error_list.append(f"Invalid <tile> id: { tile_tag.attrib.get('id') }")
        tile_id = -1

    tile_solid: bool = False
    projectile_solid: Optional[bool] = None
    tile_priority: int = 0

    tile_type = tile_tag.attrib.get("class", tile_tag.attrib.get("type"))  # Tiled 1.9 renamed 'type' attribute to 'class'

    for tag in tile_tag:
        if tag.tag == "objectgroup":
            if not check_objectgroup_tag(tag):
                error_list.append(f"Tile {tile_id}: Tile collision MUST cover the whole tile in a single rectangle")
            tile_solid = True

        elif tag.tag == "properties":
            for ptag in tag:
                if ptag.tag == "property":
                    p_name = ptag.attrib.get("name")
                    p_value = ptag.attrib.get("value")

                    if p_name == "priority":
                        try:
                            tile_priority = read_tile_priority_value(p_value)
                        except ValueError as e:
                            error_list.append(f"Tile {tile_id}: { e }")

                    elif p_name == "not_projectile_solid":
                        # ::TODO find a better name for this property::

                        if p_value == "true":
                            projectile_solid = False
                        elif p_value == "false":
                            # If the override is false, projectile_solid will the same value as tile_solid
                            projectile_solid = None
                        else:
                            error_list.append(f"Tile {tile_id}: Invalid '{p_name}' property value: {p_value}")

    if projectile_solid is None:
        # if `projecile_solid` is not overridden; projectile uses same solidity as regular enemies.
        projectile_solid = tile_solid

    return tile_id, TileProperty(solid=tile_solid, projectile_solid=projectile_solid, type=tile_type, priority=tile_priority)


def read_tsx_file(tsx_filename: Filename) -> TsxFile:
    with open(tsx_filename, "r") as tsx_fp:
        tsx_et = xml.etree.ElementTree.parse(tsx_fp)

    error_list: list[str] = list()

    image_filename: Optional[Filename] = None
    palette: Optional[str] = None

    read_tiles: set[int] = set()
    tile_properties = [TileProperty(solid=False, projectile_solid=False, type=None, priority=DEFAULT_PRIORITY)] * N_TILES

    root_tag = tsx_et.getroot()
    if root_tag.tag != "tileset":
        raise TsxFileError(f"Error reading { tsx_filename }", ["Expected a <tileset> tag"])

    name = root_tag.get("name")
    if not name:
        error_list.append("Missing tileset name")

    if name:
        if name + ".tsx" != os.path.basename(tsx_filename):
            error_list.append("")

    for tag in root_tag:
        if tag.tag == "image":
            if image_filename:
                error_list.append("Only one <image> tag is allowed")
            image_filename = tag.attrib.get("source")
            if not image_filename:
                error_list.append("<image> tag is missing attribute: source")

        if tag.tag == "properties":
            for ptag in tag:
                if ptag.tag == "property":
                    pname = ptag.attrib.get("name")
                    if pname == "palette":
                        palette = ptag.attrib.get("value")
                        if not palette:
                            error_list.append("palette property is missing a value")
                    else:
                        error_list.append(f"Unknown property: { pname }")

        if tag.tag == "tile":
            tile_id, t = read_tile_tag(tag, error_list)

            if tile_id not in read_tiles:
                read_tiles.add(tile_id)
                tile_properties[tile_id] = t
            else:
                error_list.append(f"Duplicate <tile> id: { tile_id }")

    if not image_filename:
        error_list.append("Missing image filename")

    if not palette:
        error_list.append("Missing palette")

    if name:
        if image_filename:
            if not image_filename.startswith(name + "-"):
                error_list.append(f"Invalid image filename (expected `{ name }-*`): { image_filename }")

    if error_list:
        raise TsxFileError(f"Error reading { tsx_filename }", error_list)

    assert name and image_filename and palette

    dirname = os.path.dirname(tsx_filename)

    return TsxFile(
        name=name,
        image_filename=os.path.join(dirname, image_filename),
        palette=palette,
        tile_properties=tile_properties,
    )


def create_properties_array(
    tile_properties: list[TileProperty], interactive_tile_functions: list[str], error_list: list[str]
) -> bytes:
    data = bytearray(256)

    for i, tile in enumerate(tile_properties):
        p = 0

        if tile.type:
            try:
                tile_type_id = interactive_tile_functions.index(tile.type) + 1
                p |= tile_type_id
            except ValueError:
                error_list.append(f"Tile { i }: Invalid interactive_tile_function { tile.type }")

        if tile.solid:
            p |= TILE_PROPERTY_SOLID_MASK

        if tile.projectile_solid:
            p |= TILE_PROPERTY_PROJECTILE_SOLID_MASK

        data[i] = p

    return data


def create_tileset_data(palette: PaletteResource, tile_data: bytes, metatile_map: bytes, properties: bytes) -> EngineData:
    wram_data = bytearray()

    # 2048 bytes = metatile map
    assert len(metatile_map) == 2048
    wram_data += metatile_map

    # 256 bytes = properties map
    assert len(properties) == 256
    wram_data += properties

    # Next 1 bytes = palette id
    wram_data.append(palette.id)

    assert len(wram_data) == 2048 + 256 + 1

    return EngineData(
        ram_data=FixedSizedData(wram_data),
        ppu_data=DynamicSizedData(tile_data),
    )


def convert_mt_tileset(
    tsx_filename: Filename, mappings: Mappings, palettes: dict[Name, PaletteResource]
) -> tuple[EngineData, ConstSmallTileMap]:
    tsx_file = read_tsx_file(tsx_filename)

    image = load_image_tile_extractor(tsx_file.image_filename)

    if image.width_px != 256 or image.height_px != 256:
        raise ImageError(tsx_file.image_filename, "Tileset Image MUST BE 256x256 px in size")

    pal = palettes.get(tsx_file.palette)
    if pal is None:
        raise RuntimeError(f"Cannot load palette: {tsx_file.palette}")
    palette_map = pal.create_map(TILE_DATA_BPP)

    tileset = SmallTilesetMap()
    tilemap = extract_tiles_and_build_tilemap(image, tileset, palette_map)

    tile_data = convert_snes_tileset(tileset.tiles(), TILE_DATA_BPP)

    error_list: list[str] = list()

    metatile_map = create_metatile_map(tilemap, tsx_file.tile_properties)
    properties = create_properties_array(tsx_file.tile_properties, mappings.interactive_tile_functions, error_list)

    if error_list:
        raise TsxFileError(f"Error compiling { tsx_filename }", error_list)

    return (
        create_tileset_data(pal, tile_data, metatile_map, properties),
        tileset.const_map(),
    )
