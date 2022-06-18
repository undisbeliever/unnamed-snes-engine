#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import json
import os.path

from collections import OrderedDict

from typing import Any, NamedTuple, Optional, Union



Name       = str
ScopedName = str
RoomName   = str

Filename   = str



def check_name(s : str) -> Name:
    if re.match(r'[a-zA-Z0-9_]+$', s):
        return s
    else:
        raise ValueError(f"Invalid name: {s}")


def check_name_with_dot(s : str) -> ScopedName:
    if re.match(r'[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$', s):
        return s
    else:
        raise ValueError(f"Invalid name: {s}")


def check_optional_name(s : Optional[str]) -> Optional[Name]:
    if s:
        return check_name(s)
    else:
        return None


def check_room_name(s : str) -> RoomName:
    if re.match(r'[a-zA-Z0-9_-]+$', s):
        return s
    else:
        raise ValueError(f"Invalid name: {s}")


def check_name_list(l : list[str]) -> list[Name]:
    if not isinstance(l, list):
        raise ValueError('Error: Not a list')

    for n in l:
        check_name(n)

    return l


def check_obj_size(v : Union[int, str]) -> int:
    i = int(v)
    if i not in (8, 16):
        raise ValueError(f"Invalid Object Size: { i }")
    return i


def optional_int(v : Optional[Union[int, str]]) -> Optional[int]:
    if v is not None:
        return int(v)
    else:
        return None


def check_bool(v : Union[bool, str, None]) -> bool:
    if v is None:
        return False

    if isinstance(v,str):
        if v == '0':
            return False

        if v == '1':
            return True

        raise ValueError(f"Invalid bool value: Expected \"1\" or \"0\": { v }")

    return bool(v)


def check_float(v : Union[float, int]) -> Union[float, int]:
    if not isinstance(v, float) and not isinstance(v, int):
        raise ValueError(f"Invalid float value: { v }")
    return v


def check_hex_or_int(v : Union[int, str]) -> int:
    if isinstance(v, int):
        return v
    if isinstance(v, str):
        return int(v, 16)

    raise ValueError(f"Invalid type: expected int or hex string")



# entities.json
# =============


class EfParameter(NamedTuple):
    type    : str
    values  : list[Name]


class EntityFunction(NamedTuple):
    name                        : Name
    id                          : int
    ms_export_order             : Name
    parameter                   : Optional[EfParameter]
    uses_process_function_from  : Optional[Name]


class EntityVision(NamedTuple):
    a   : int
    b   : int


class Entity(NamedTuple):
    name        : Name
    id          : int
    code        : EntityFunction
    metasprites : ScopedName
    zpos        : int
    vision      : Optional[EntityVision]
    health      : int
    attack      : int


class EntitiesJson(NamedTuple):
    entity_functions : OrderedDict[Name, EntityFunction]
    entities         : OrderedDict[Name, Entity]



def _read_entity_vision_parameter(s : Optional[str]) -> Optional[EntityVision]:
    if s is None:
        return None
    if not isinstance(s, str):
        raise ValueError('Error: Expected a string containing two integers (vision)')
    v = s.split()
    if len(v) != 2:
        raise ValueError('Error: Expected a string containing two integers (vision)')
    return EntityVision(int(v[0]), int(v[1]))



def load_entities_json(filename : Filename) -> EntitiesJson:
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
                uses_process_function_from = check_optional_name(e.get('uses-process-function-from')),
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


class MsPatternObject(NamedTuple):
    xpos : int
    ypos : int
    size : int


class MsPattern(NamedTuple):
    name    : Name
    id      : int
    objects : list[MsPatternObject]


class MsAnimationExportOrder(NamedTuple):
    name        : Name
    animations  : list[Name]


class MsExportOrder(NamedTuple):
    patterns        : OrderedDict[Name, MsPattern]
    shadow_sizes    : OrderedDict[Name, int]
    animation_lists : OrderedDict[Name, MsAnimationExportOrder]



def _load_pattern_objects(json_list : list[dict[str, Any]]) -> list[MsPatternObject]:
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



def load_ms_export_order_json(filename : Filename) -> MsExportOrder:
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


class MemoryMap(NamedTuple):
    mode                : str
    first_resource_bank : int
    n_resource_banks    : int


class Mappings(NamedTuple):
    starting_room               : RoomName
    mt_tilesets                 : list[Name]
    ms_spritesheets             : list[Name]
    tiles                       : list[Name]
    interactive_tile_functions  : list[Name]
    memory_map                  : MemoryMap


VALID_MEMORY_MAP_MODES = ('hirom', 'lorom')



def __load_memory_map(json_map : dict[str, Any]) -> MemoryMap:
    mode = json_map['mode']
    if mode not in VALID_MEMORY_MAP_MODES:
        raise ValueError(f"Unknown memory mapping mode: { mode }")

    return MemoryMap(
            mode = mode,
            first_resource_bank = check_hex_or_int(json_map['first_resource_bank']),
            n_resource_banks = int(json_map['n_resource_banks'])
    )



def load_mappings_json(filename : Filename) -> Mappings:
    with open(filename, 'r') as fp:
        json_input = json.load(fp)

    return Mappings(
            starting_room = check_room_name(json_input['starting_room']),
            mt_tilesets = check_name_list(json_input['mt_tilesets']),
            ms_spritesheets = check_name_list(json_input['ms_spritesheets']),
            tiles = check_name_list(json_input['tiles']),
            interactive_tile_functions = check_name_list(json_input['interactive_tile_functions']),
            memory_map = __load_memory_map(json_input['memory_map'])
    )



# metasprites.json
# ================


class Aabb(NamedTuple):
    x       : int
    y       : int
    width   : int
    height  : int


class MsBlock(NamedTuple):
    pattern         : Optional[Name]
    start           : int
    x               : Optional[int]
    y               : Optional[int]
    flip            : Optional[str]
    frames          : list[Name]
    default_hitbox  : Optional[Aabb]
    default_hurtbox : Optional[Aabb]


class TileHitbox(NamedTuple):
    half_width  : int
    half_height : int


class MsAnimation(NamedTuple):
    name            : Name
    loop            : bool
    delay_type      : str
    fixed_delay     : Optional[Union[float, int]]
    frames          : list[Name]
    frame_delays    : Optional[list[Union[float, int]]]


class MsFrameset(NamedTuple):
    name                : Name
    source              : Filename
    frame_width         : int
    frame_height        : int
    x_origin            : int
    y_origin            : int
    shadow_size         : str
    tilehitbox          : TileHitbox
    default_hitbox      : Optional[Aabb]
    default_hurtbox     : Optional[Aabb]
    pattern             : Optional[Name]
    ms_export_order     : Name
    order               : int
    blocks              : list[MsBlock]
    hitbox_overrides    : dict[Name, Aabb]
    hurtbox_overrides   : dict[Name, Aabb]
    animations          : OrderedDict[Name, MsAnimation]


class MsSpritesheet(NamedTuple):
    name        : Name
    palette     : Filename
    first_tile  : int
    end_tile    : int
    framesets   : OrderedDict[Name, MsFrameset]



def __read_tilehitbox(s : str) -> TileHitbox:
    if not isinstance(s, str):
        raise ValueError('Error: Expected a string containing two integers (tilehitbox)')
    v = s.split()
    if len(v) != 2:
        raise ValueError('Error: Expected a string containing two integers (tilehitbox)')
    return TileHitbox(int(v[0]), int(v[1]))



def __read_animation_frames__no_fixed_delay(l : list[Any]) -> tuple[list[Name], list[Union[int, float]]]:
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



def __read_aabb(s : str) -> Aabb:
    if not isinstance(s, str):
        raise ValueError('Error: Expected a string containing four integers (aabb)')
    v = s.split()
    if len(v) != 4:
        raise ValueError('Error: Expected a string containing four integers (aabb)')
    return Aabb(int(v[0]), int(v[1]), int(v[2]), int(v[3]))



def __read_optional_aabb(s : Optional[str]) -> Optional[Aabb]:
    if not s:
        return None
    return __read_aabb(s)



_VALID_FLIPS = frozenset(('hflip', 'vflip', 'hvflip'))

def __read_flip(s : Optional[str]) -> Optional[str]:
    if not s:
        return None

    if s in _VALID_FLIPS:
        return s

    raise ValueError(f"Error: Unknown flip value: { s }")



def __load_aabb_overrides(json_map : Optional[dict[str, Any]]) -> dict[str, Aabb]:
    out : dict[str, Aabb] = dict()

    if json_map is None:
        return out

    if not isinstance(json_map, dict):
        raise ValueError('Error: Expected a map for AABB overrides')

    for k, v in json_map.items():
        out[k] = __read_aabb(v)

    return out



def __load_ms_blocks(json_input : list[dict[str, Any]], fs_pattern : Optional[Name], fs_default_hitbox : Optional[Aabb], fs_default_hurtbox : Optional[Aabb]) -> list[MsBlock]:
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



def __load_ms_animations(json_input : dict[str, Any]) -> OrderedDict[Name, MsAnimation]:
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



def __load_ms_framesets(json_input : list[dict[str, Any]]) -> OrderedDict[Name, MsFrameset]:
    framesets = OrderedDict()

    for f in json_input:
        fs_pattern = check_optional_name(f['pattern'])
        fs_default_hitbox = __read_optional_aabb(f.get('defaultHitbox'))
        fs_default_hurtbox = __read_optional_aabb(f.get('defaultHurtbox'))

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


def _load_metasprites(json_input : dict[str, Any]) -> MsSpritesheet:
    return MsSpritesheet(
            name = check_name(json_input['name']),
            palette = str(json_input['palette']),
            first_tile = int(json_input['firstTile']),
            end_tile = int(json_input['endTile']),
            framesets = __load_ms_framesets(json_input['framesets'])
    )



def load_metasprites_json(filename : Filename) -> MsSpritesheet:
    with open(filename, 'r') as fp:
        json_input = json.load(fp)
    return _load_metasprites(json_input)



def load_metasprites_string(text : str) -> MsSpritesheet:
    json_input = json.loads(text)
    return _load_metasprites(json_input)



#
# resources.json
#

class TilesInput(NamedTuple):
    name    : Name
    format  : str
    source  : Filename


class ResourcesJson(NamedTuple):
    tiles   : dict[Name, TilesInput]



def __load__resource_tiles(json_input : dict[str, Any], dirname : Filename) -> dict[Name, TilesInput]:
    out = dict()

    for name, v in json_input.items():
        if name in out:
            raise ValueError(f"Duplicate tiles name: { name }")

        out[name] = TilesInput(
                name = name,
                format = str(v['format']),
                source = os.path.join(dirname, v['source']),
        )

    return out



def load_resources_json(filename : Filename) -> ResourcesJson:

    dirname = os.path.dirname(filename)

    with open(filename, 'r') as fp:
        json_input = json.load(fp)

    return ResourcesJson(
            tiles = __load__resource_tiles(json_input['tiles'], dirname),
    )



