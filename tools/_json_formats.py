#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import json
from collections import namedtuple, OrderedDict



def check_name(s):
    if re.match(r'[a-zA-Z0-9_]+$', s):
        return s
    else:
        raise ValueError(f"Invalid name: {s}")


def check_name_with_dot(s):
    if re.match(r'[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$', s):
        return s
    else:
        raise ValueError(f"Invalid name: {s}")


def check_optional_name(s):
    if s:
        return check_name(s)
    else:
        return None


def check_room_name(s):
    if re.match(r'[a-zA-Z0-9_-]+$', s):
        return s
    else:
        raise ValueError(f"Invalid name: {s}")


def check_name_list(l):
    if not isinstance(l, list):
        raise ValueError('Error: Not a list')

    for n in l:
        check_name(n)

    return l


def check_obj_size(v):
    i = int(v)
    if i not in (8, 16):
        raise ValueError(f"Invalid Object Size: { i }")
    return i


def optional_int(v):
    if v is not None:
        return int(v)
    else:
        return None





# entities.json
# =============

EntitiesJson = namedtuple('EntitiesJson', ('entity_functions', 'entities'))

EntityFunction = namedtuple('EntityFunction', ('name', 'id', 'ms_export_order', 'parameter'))
EfParameter = namedtuple('EfParameter', ('type', 'values'))

Entity = namedtuple('Entity', ('name', 'id', 'code', 'metasprites', 'shadow_size', 'zpos', 'half_height', 'half_width'))



def load_entities_json(filename):
    with open(filename, 'r') as fp:
        entities_json = json.load(fp)


    entity_functions = OrderedDict()

    for i, e in enumerate(entities_json['entity_functions']):
        parameter = None
        if 'parameter' in e:
            t = e['parameter']['type']
            if t == 'enum':
                parameter = EfParameter('enum', check_name_list(e['parameter']['values']))
            else:
                raise ValueError(f"Unknown parameter type: { t }")

        ef = EntityFunction(
                name = check_name(e['name']),
                id = i,
                ms_export_order = check_name(e['ms-export-order']),
                parameter = parameter,
        )

        if ef.name in entity_functions:
            raise ValueError(f"Duplicate entity function name: { ef.name }")
        entity_functions[ef.name] = ef


    entities = OrderedDict()

    for i, e in enumerate(entities_json['entities']):
        entity = Entity(
                    name = check_name(e['name']),
                    id = i,
                    code = entity_functions[e['code']],
                    metasprites = check_name_with_dot(e['metasprites']),
                    shadow_size = check_name(e['shadowSize']),
                    zpos = int(e['zpos']),
                    half_width = optional_int(e.get('halfWidth')),
                    half_height = optional_int(e.get('halfHeight')),
        )

        if entity.name in entities:
            raise ValueError(f"Duplicate entity name: { entity.name }")
        entities[entity.name] = entity


    if len(entities) > 254:
        raise ValueError("Too many entities")


    return EntitiesJson(entity_functions=entity_functions, entities=entities)




# ms-export-order.json
# ====================

MsPatternObject = namedtuple('MsPatternObject', ('xpos', 'ypos', 'size'))
MsPattern       = namedtuple('MsPattern', ('name', 'id', 'objects'))
MsFrameOrder    = namedtuple('MsFrameOrder', ('name', 'frames'))
MsExportOrder   = namedtuple('MsExportOrder', ('patterns', 'frame_lists'))


def _load_pattern_objects(json_list):
    objs = list()

    for o in json_list:
        objs.append(
            MsPatternObject(
                xpos = int(o['x']),
                ypos = int(o['y']),
                size = check_obj_size(o['size'])
            )
        )

    return objs



def load_ms_export_order_json(filename):
    with open(filename, 'r') as fp:
        mseo_input = json.load(fp)


    patterns = OrderedDict()
    for i, p in enumerate(list(mseo_input['patterns'])):
        pat = MsPattern(
                name = check_name(p['name']),
                id = i * 2,
                objects = _load_pattern_objects(p['objects'])
        )

        if pat.name in patterns:
            raise ValueError(f"Duplicate Pattern name: { pat.name }")
        patterns[pat.name] = pat


    if len(patterns) > 256:
        raise ValueError('Too many MetaSprite patterns')


    frame_lists = dict()
    for name, m in mseo_input['frame_lists'].items():
        eo = MsFrameOrder(
                name = check_name(name),
                frames = check_name_list(m['frames']),
        )

        if eo.name in frame_lists:
            raise ValueError(f"Duplicate MetaSprite Export Order Name: { eo.name }")
        frame_lists[eo.name] = eo


    return MsExportOrder(patterns=patterns, frame_lists=frame_lists)



# mappings.json
# =============


Mappings = namedtuple('Mappings', ('starting_room', 'tilesets', 'metasprite_spritesheets', 'dungeons'))
DungeonMapping = namedtuple('DungeonMapping', ('name', 'x_offset', 'y_offset'))


def __load_dungeons_array(json_map):
    dungeons = dict()

    for name, v in json_map.items():
        d = DungeonMapping(
                name = check_room_name(name),
                x_offset = int(v['x_offset']),
                y_offset = int(v['y_offset'])
            )

        if d.name in dungeons:
            raise ValueError(f"Duplicate dungeon mapping: { d.name }")
        dungeons[d.name] = d


    return dungeons



def load_mappings_json(filename):
    with open(filename, 'r') as fp:
        json_input = json.load(fp)

    return Mappings(
            starting_room = check_room_name(json_input['starting_room']),
            tilesets = check_name_list(json_input['tilesets']),
            metasprite_spritesheets = check_name_list(json_input['metasprite_spritesheets']),
            dungeons = __load_dungeons_array(json_input['dungeons']),
    )



# metasprites.json
# ================


MsSpritesheet = namedtuple('MsSpritesheet', ('name', 'palette', 'first_tile', 'end_tile', 'framesets'))
MsFrameset = namedtuple('MsFrameset', ('name', 'source', 'frame_width', 'frame_height', 'x_origin', 'y_origin', 'pattern', 'ms_export_order', 'order', 'blocks'))
MsBlock = namedtuple('MsBlock', ('pattern', 'start', 'x', 'y', 'frames'))


def __load_ms_blocks(json_input, fs_pattern):
    blocks = list()

    for j in json_input:
        pattern = check_optional_name(j.get('pattern'))
        if pattern or fs_pattern:
            x = int(j['x'])
            y = int(j['y'])
        else:
            if 'x' in j or 'y' in j:
                raise ValueError("MS Blocks with no pattern must not have a 'x' or 'y' field")
            x = None
            y = None

        blocks.append(
            MsBlock(
                pattern = pattern,
                start = int(j['start']),
                x = x,
                y = y,
                frames = check_name_list(j['frames'])
            )
        )

    return blocks



def __load_ms_framesets(json_input):
    framesets = OrderedDict()

    for f in json_input:
        fs_pattern = check_optional_name(f['pattern'])

        fs = MsFrameset(
                name = check_name(f['name']),
                source = str(f['source']),
                frame_width = int(f['frameWidth']),
                frame_height = int(f['frameHeight']),
                x_origin = int(f['xorigin']),
                y_origin = int(f['yorigin']),
                pattern = fs_pattern,
                ms_export_order = check_name(f['ms-export-order']),
                order = int(f['order']),
                blocks = __load_ms_blocks(f['blocks'], fs_pattern)
        )

        if fs.name in framesets:
            raise ValueError(f"Duplicate MetaSprite Frameset: { fs.name }")
        framesets[fs.name] = fs

    return framesets


def _load_metasprites(json_input):
    return MsSpritesheet(
            name = check_name(json_input['name']),
            palette = str(json_input['palette']),
            first_tile = int(json_input['firstTile']),
            end_tile = int(json_input['endTile']),
            framesets = __load_ms_framesets(json_input['framesets'])
    )



def load_metasprites_json(filename):
    with open(filename, 'r') as fp:
        json_input = json.load(fp)
    return _load_metasprites(json_input)



def load_metasprites_string(text):
    json_input = json.loads(text)
    return _load_metasprites(json_input)


