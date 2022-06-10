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


def check_bool(v):
    if v is None:
        return False

    if isinstance(v,str):
        if v == '0':
            return False

        if v == '1':
            return True

        raise ValueError(f"Invalid bool value: Expected \"1\" or \"0\": { v }")

    return bool(v)


def check_float(v):
    if not isinstance(v, float) and not isinstance(v, int):
        raise ValueError(f"Invalid float value: { v }")
    return v


def check_hex_or_int(v):
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        return int(v, 16)

    raise ValueError(f"Invalid type: expected int or hex string")



# entities.json
# =============

EntitiesJson = namedtuple('EntitiesJson', ('entity_functions', 'entities'))

EntityFunction = namedtuple('EntityFunction', ('name', 'id', 'ms_export_order', 'parameter'))
EfParameter = namedtuple('EfParameter', ('type', 'values'))
EntityVision = namedtuple('EntityVision', ('a', 'b'))

Entity = namedtuple('Entity', ('name', 'id', 'code', 'metasprites', 'zpos', 'vision', 'health', 'attack'))



def _read_entity_vision_parameter(s):
    if s is None:
        return None
    if not isinstance(s, str):
        raise ValueError('Error: Expected a string containing two integers (vision)')
    v = s.split()
    if len(v) != 2:
        raise ValueError('Error: Expected a string containing two integers (vision)')
    return EntityVision(int(v[0]), int(v[1]))



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
                    zpos = int(e['zpos']),
                    vision = _read_entity_vision_parameter(e.get('vision')),
                    health = int(e['health']),
                    attack = int(e['attack']),
        )

        if entity.name in entities:
            raise ValueError(f"Duplicate entity name: { entity.name }")
        entities[entity.name] = entity


    if len(entities) > 254:
        raise ValueError("Too many entities")


    return EntitiesJson(entity_functions=entity_functions, entities=entities)




# ms-export-order.json
# ====================

MsPatternObject         = namedtuple('MsPatternObject', ('xpos', 'ypos', 'size'))
MsPattern               = namedtuple('MsPattern', ('name', 'id', 'objects'))
MsAnimationExportOrder  = namedtuple('MsAnimationExportOrder', ('name', 'animations'))
MsExportOrder           = namedtuple('MsExportOrder', ('patterns', 'shadow_sizes', 'animation_lists'))


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


    shadow_sizes = OrderedDict()
    for i, s in enumerate(mseo_input['shadow_sizes']):
        if s in shadow_sizes:
            raise ValueError(f"Duplicate shadow size: { s }")
        shadow_sizes[check_name(s)] = i


    if len(patterns) > 256:
        raise ValueError('Too many MetaSprite patterns')


    animation_lists = OrderedDict()
    for name, al in mseo_input['animation_lists'].items():
        eo = MsAnimationExportOrder(
                name = check_name(name),
                animations = check_name_list(al),
        )

        if eo.name in animation_lists:
            raise ValueError(f"Duplicate MetaSprite Export Order Name: { eo.name }")
        animation_lists[eo.name] = eo


    return MsExportOrder(patterns=patterns, shadow_sizes=shadow_sizes, animation_lists=animation_lists)



# mappings.json
# =============


Mappings = namedtuple('Mappings', ('starting_room', 'tilesets', 'metasprite_spritesheets', 'interactive_tile_functions', 'dungeons', 'memory_map'))
MemoryMap = namedtuple('MemoryMap', ('mode', 'first_resource_bank', 'n_resource_banks'))
DungeonMapping = namedtuple('DungeonMapping', ('name', 'x_offset', 'y_offset'))


VALID_MEMORY_MAP_MODES = ('hirom', 'lorom')



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



def __load_memory_map(json_map):
    mode = json_map['mode']
    if mode not in VALID_MEMORY_MAP_MODES:
        raise ValueError(f"Unknown memory mapping mode: { mode }")

    return MemoryMap(
            mode = mode,
            first_resource_bank = check_hex_or_int(json_map['first_resource_bank']),
            n_resource_banks = int(json_map['n_resource_banks'])
    )



def load_mappings_json(filename):
    with open(filename, 'r') as fp:
        json_input = json.load(fp)

    return Mappings(
            starting_room = check_room_name(json_input['starting_room']),
            tilesets = check_name_list(json_input['tilesets']),
            metasprite_spritesheets = check_name_list(json_input['metasprite_spritesheets']),
            interactive_tile_functions = check_name_list(json_input['interactive_tile_functions']),
            dungeons = __load_dungeons_array(json_input['dungeons']),
            memory_map = __load_memory_map(json_input['memory_map'])
    )



# metasprites.json
# ================


MsSpritesheet = namedtuple('MsSpritesheet', ('name', 'palette', 'first_tile', 'end_tile', 'framesets'))
MsFrameset = namedtuple('MsFrameset', ('name', 'source', 'frame_width', 'frame_height', 'x_origin', 'y_origin',
                                       'shadow_size', 'tilehitbox', 'default_hitbox', 'default_hurtbox',
                                       'pattern', 'ms_export_order', 'order', 'blocks',
                                       'hitbox_overrides', 'hurtbox_overrides', 'animations'))
MsBlock = namedtuple('MsBlock', ('pattern', 'start', 'x', 'y', 'flip', 'frames', 'default_hitbox', 'default_hurtbox'))

# fixed_delay is optional
# if fixed_delay is None, frames contains an interleaved list of `frame_name` and `frame_delay`
MsAnimation = namedtuple('MsAnimation', ('name', 'loop', 'delay_type', 'fixed_delay', 'frames', 'frame_delays'))


TileHitbox = namedtuple('TileHitbox', ('half_width', 'half_height'))
Aabb = namedtuple('Aabb', ('x', 'y', 'width', 'height'))


def __read_tilehitbox(s):
    if not isinstance(s, str):
        raise ValueError('Error: Expected a string containing two integers (tilehitbox)')
    v = s.split()
    if len(v) != 2:
        raise ValueError('Error: Expected a string containing two integers (tilehitbox)')
    return TileHitbox(int(v[0]), int(v[1]))



def __read_animation_frames__no_fixed_delay(l):
    if not isinstance(l, list):
        raise ValueError('ERROR: Expected a list (animation frame list)')
    if len(l) % 2 != 0:
        raise ValueError('ERROR: Expected a list of `frame, delay` (animation frame list)')

    # off indexes
    frames = check_name_list(l[0::2])
    frame_delays = l[1::2]

    for i in frame_delays:
        if not isinstance(i, float) and not isinstance(i, int):
            raise ValueError('Error: Expected a float containing the delay time (animation frames list, even indexes)')

    return frames, frame_delays



def __read_aabb(s):
    # Allow blank aabb in json source
    if not s:
        return None
    if not isinstance(s, str):
        raise ValueError('Error: Expected a string containing four integers (aabb)')
    v = s.split()
    if len(v) != 4:
        raise ValueError('Error: Expected a string containing four integers (aabb)')
    return Aabb(int(v[0]), int(v[1]), int(v[2]), int(v[3]))



_VALID_FLIPS = frozenset(('hflip', 'vflip', 'hvflip'))

def __read_flip(s):
    if not s:
        return None

    if s in _VALID_FLIPS:
        return s

    raise ValueError(f"Error: Unknown flip value: {flip}")



def __load_aabb_overrides(json_map):
    out = dict()

    if json_map is None:
        return out

    if not isinstance(json_map, dict):
        raise ValueError('Error: Expected a map for AABB overrides')

    for k, v in json_map.items():
        out[k] = __read_aabb(v)

    return out



def __load_ms_blocks(json_input, fs_pattern, fs_default_hitbox, fs_default_hurtbox):
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
                flip = __read_flip(j.get('flip', None)),
                frames = check_name_list(j['frames']),
                default_hitbox = __read_aabb(j['defaultHitbox']) if 'defaultHitbox' in j else fs_default_hitbox,
                default_hurtbox = __read_aabb(j['defaultHurtbox']) if 'defaultHurtbox' in j else fs_default_hurtbox,
            )
        )

    return blocks



def __load_ms_animations(json_input):
    animations = OrderedDict()

    for name, a in json_input.items():
        name = check_name(name)

        if name in animations:
            raise ValueError(f"Duplicate MS animation name: { name }")

        if 'fixed-delay' in a:
            fixed_delay = check_float(a['fixed-delay'])
            frames = check_name_list(a['frames'])
            frame_delays = None
        else:
            fixed_delay = None
            frames, frame_delays = __read_animation_frames__no_fixed_delay(a['frames'])


        animations[name] = MsAnimation(
                name = name,
                loop = check_bool(a.get('loop', True)),
                delay_type = check_name(a['delay-type']),
                fixed_delay = fixed_delay,
                frames = frames,
                frame_delays = frame_delays,
        )

    return animations



def __load_ms_framesets(json_input):
    framesets = OrderedDict()

    for f in json_input:
        fs_pattern = check_optional_name(f['pattern'])
        fs_default_hitbox = __read_aabb(f['defaultHitbox']) if 'defaultHitbox' in f else None
        fs_default_hurtbox = __read_aabb(f['defaultHurtbox']) if 'defaultHurtbox' in f else None

        fs = MsFrameset(
                name = check_name(f['name']),
                source = str(f['source']),
                frame_width = int(f['frameWidth']),
                frame_height = int(f['frameHeight']),
                x_origin = int(f['xorigin']),
                y_origin = int(f['yorigin']),
                shadow_size = check_name(f['shadowSize']),
                tilehitbox = __read_tilehitbox(f['tilehitbox']),
                default_hitbox = fs_default_hitbox,
                default_hurtbox = fs_default_hurtbox,
                pattern = fs_pattern,
                ms_export_order = check_name(f['ms-export-order']),
                order = int(f['order']),
                blocks = __load_ms_blocks(f['blocks'], fs_pattern, fs_default_hitbox, fs_default_hurtbox),
                hitbox_overrides = __load_aabb_overrides(f.get('hitboxes')),
                hurtbox_overrides = __load_aabb_overrides(f.get('hurtboxes')),
                animations = __load_ms_animations(f['animations']),
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


