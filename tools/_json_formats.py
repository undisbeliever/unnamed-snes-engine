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

MsExportOrder = namedtuple('MsExportOrder', ('name', 'frames'))


def load_ms_export_order_json(filename):
    with open(filename, 'r') as fp:
        mseo_input = json.load(fp)

    export_orders = dict()

    for name, m in mseo_input.items():
        eo = MsExportOrder(
                name = check_name(name),
                frames = check_name_list(m['frames']),
        )

        if eo.name in export_orders:
            raise ValueError(f"Duplicate MetaSprite Export Order Name: { eo.name }")
        export_orders[eo.name] = eo


    return export_orders



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


