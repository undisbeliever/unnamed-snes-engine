#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import json
import os.path
import PIL.Image
import argparse
from collections import namedtuple
from io import StringIO


from _snes import convert_rgb_color, extract_tileset_tiles, convert_snes_tileset, create_palettes_map, get_palette_id, convert_palette_image

from _json_formats import load_ms_export_order_json


TILE_DATA_BPP = 4

ROM_BANK = 'rodata0'



def convert_tileset(tiles, palettes_map):
    # Returns a tuple(tileset, tile_palette_map)

    invalid_tiles = list()

    tileset = list()
    tile_palette_map = list()

    for tile_index, tile in enumerate(tiles):
        palette_id, pal_map = get_palette_id(tile, palettes_map)

        if pal_map:
            tile_data = bytes([pal_map[c] for c in tile])

            tileset.append(tile_data)
            tile_palette_map.append(palette_id)
        else:
            invalid_tiles.append(tile_index)

    if invalid_tiles:
        raise ValueError(f"Cannot find palette for tiles {invalid_tiles}")

    return tileset, tile_palette_map



#
# =========================
#

SpriteSheet = namedtuple('SpriteSheet', ('name', 'pattern', 'ms_export_order', 'frames'))

SpriteFrame = namedtuple('SpriteFrame', ('xoffset', 'yoffset', 'objects'))
ObjectEntry = namedtuple('ObjectEntry', ('tile_id', 'palette_id', 'order', 'hflip', 'vflip'))


def hflip_object_entry(o):
    return ObjectEntry(o.tile_id, o.palette_id, o.order, not o.hflip, o.vflip)

def vflip_object_entry(o):
    return ObjectEntry(o.tile_id, o.palette_id, o.order, o.hflip, not o.vflip)



def to_int(value):
    try:
        return int(value)
    except ValueError:
        return int(value, 0)



PATTERN_CLASSES = dict()

def register_pattern_class(cls):
    PATTERN_CLASSES[cls.name] = cls
    return cls



class BasePattern:
    @classmethod
    def hflip(cls, objects):
        raise ValueError(f"Cannot hflip { cls.name } pattern")


    @classmethod
    def vflip(cls, objects):
        raise ValueError(f"Cannot vflip { cls.name } pattern")


    @classmethod
    def flip(cls, objects, flip):
        if flip == 'hflip':
            return cls.hflip(objects)
        elif flip == 'vflip':
            return cls.vflip(objects)
        elif flip == 'hvflip':
            return cls.vflip(cls.hflip(objects))
        else:
            raise ValueError(f"Unknown flip type { flip }")



class Base_1x2_Pattern(BasePattern):
    n_objects = 2


    @classmethod
    def hflip(cls, objs):
        return [
            hflip_object_entry(objs[0]),
            hflip_object_entry(objs[1]),
        ]


    @classmethod
    def vflip(cls, objs):
        return [
            vflip_object_entry(objs[1]),
            vflip_object_entry(objs[0]),
        ]



class Base_2x1_Pattern(BasePattern):
    n_objects = 2


    @classmethod
    def hflip(cls, objs):
        return [
            hflip_object_entry(objs[1]),
            hflip_object_entry(objs[0]),
        ]


    @classmethod
    def vflip(cls, objs):
        return [
            vflip_object_entry(objs[0]),
            vflip_object_entry(objs[1]),
        ]



@register_pattern_class
class Square_single16(BasePattern):
    name = 'square_single16'
    n_objects = 1


    @classmethod
    def starting_tile(cls, frame, tileId, default_order):
        return [
            ObjectEntry(tileId, None, default_order, False, False),
        ]


    @classmethod
    def hflip(cls, objs):
        return [
            hflip_object_entry(objs[0]),
        ]


    @classmethod
    def vflip(cls, objs):
        return [
            vflip_object_entry(objs[0]),
        ]



@register_pattern_class
class Rect_16x8_two(Base_2x1_Pattern):
    name = 'rect_16x8_two'

    @classmethod
    def starting_tile(cls, frame, tileId, default_order):
        return [
            ObjectEntry(tileId,        None, default_order, False, False),
            ObjectEntry(tileId + 0x01, None, default_order, False, False),
        ]



@register_pattern_class
class Rect_8x16_two(Base_1x2_Pattern):
    name = 'rect_8x16_two'

    @classmethod
    def starting_tile(cls, frame, tileId, default_order):
        return [
            ObjectEntry(tileId,        None, default_order, False, False),
            ObjectEntry(tileId + 0x10, None, default_order, False, False),
        ]



@register_pattern_class
class Rect_16x32_two(Base_1x2_Pattern):
    name = 'rect_16x32_two'

    @classmethod
    def starting_tile(cls, frame, tileId, default_order):
        return [
            ObjectEntry(tileId,        None, default_order, False, False),
            ObjectEntry(tileId + 0x20, None, default_order, False, False),
        ]



@register_pattern_class
class Rect_16x24_three(BasePattern):
    name = 'rect_16x24_three'
    n_objects = 3


    @classmethod
    def starting_tile(cls, frame, tileId, default_order):
        return [
            ObjectEntry(tileId,        None, default_order, False, False),
            ObjectEntry(tileId + 0x20, None, default_order, False, False),
            ObjectEntry(tileId + 0x21, None, default_order, False, False),
        ]


    @classmethod
    def hflip(cls, objs):
        return [
            hflip_object_entry(objs[0]),
            hflip_object_entry(objs[2]),
            hflip_object_entry(objs[1]),
        ]



@register_pattern_class
class Rect_16x24_and_extra(BasePattern):
    name = 'rect_16x24_and_extra'
    n_objects = 4


    @classmethod
    def starting_tile(cls, frame, tileId, default_order):
        extra_tile = to_int(frame['extra_tile'])

        return [
            ObjectEntry(tileId + 0x00, None, default_order, False, False),
            ObjectEntry(tileId + 0x20, None, default_order, False, False),
            ObjectEntry(tileId + 0x21, None, default_order, False, False),
            ObjectEntry(extra_tile,    None, default_order, False, False),
        ]



def expand_spritesheet(spritesheet):
    default_order = spritesheet['order']
    default_xoffset = spritesheet['xoffset']
    default_yoffset = spritesheet['yoffset']

    pattern = PATTERN_CLASSES[spritesheet['pattern']]

    def expand_sprite_frame(name, f, objects):
        return SpriteFrame(
            to_int(f.get('xoffset', default_xoffset)),
            to_int(f.get('yoffset', default_yoffset)),
            objects
        )


    frames = dict()


    for name, f in spritesheet['frames'].items():
        if 'objects' in f:
            objects = list()

            for o in f['objects']:
                flip = o.get('flip')
                objects.append(ObjectEntry(
                            to_int(o['tile']),
                            o.get('palette', None),
                            o.get('order', default_order),
                            flip == 'hflip' or flip == 'hvflip',
                            flip == 'vflip' or flip == 'hvflip'))

            if len(objects) != pattern.n_objects:
                raise ValueError(f"Expected exactly { pattern.n_objects } objects in frame '{ spritesheet['name'] }.{ f['name'] }'")

            frames[name] = expand_sprite_frame(name, f, objects)

        elif 'starting_tile' in f:
            frames[name] = expand_sprite_frame(name, f, pattern.starting_tile(f, to_int(f['starting_tile']), default_order))


    for name, f in spritesheet['frames'].items():
        if 'objects' not in f:
            if 'clone' in f:
                source = frames[f['clone']['source']];

                flip = f['clone'].get('flip')
                if flip:
                    frames[name] = expand_sprite_frame(name, f, pattern.flip(source.objects, flip))
                else:
                    frames[name] = expand_sprite_frame(name, f, source.objects)


    if None in frames:
        missing = list()
        for i, f in enumerate(frames):
            if f is None:
                missing.append(spritesheet['frames'][i]['name'])

        raise ValueError(f"Unable to expand '{ spritesheet['name'] }', missing frames: { ', '.join(missing) }");


    return SpriteSheet(spritesheet['name'],
                       spritesheet['pattern'],
                       spritesheet['ms-export-order'],
                       frames)



def build_frame(frame, tile_offset, tile_palette_map):
    data = bytearray()

    data.append(frame.xoffset)
    data.append(frame.yoffset)

    for o in frame.objects:
        tile = o.tile_id + tile_offset
        pal = tile_palette_map[o.tile_id] if o.palette_id is None else o.palette_id

        data.append(tile & 0xff)
        data.append((tile >> 8)
                    | ((pal & 7) << 1)
                    | ((o.order & 3) << 4)
                    | (bool(o.hflip) << 6)
                    | (bool(o.vflip) << 7)
        )

    return data



def build_metasprites(input_data, ms_export_orders, tile_offset, tile_palette_map):
    out = list()

    for ss_input in input_data:
        ss = expand_spritesheet(ss_input)

        ss_export_order = ms_export_orders[ss.ms_export_order]

        frames = [ build_frame(ss.frames[f], tile_offset, tile_palette_map) for f in ss_export_order.frames ]

        out.append(SpriteSheet(ss.name, ss.pattern, ss.ms_export_order, frames))

    return out



def generate_wiz_data(metasprites, spritesheet_name, binary_data_path):
    with StringIO() as out:
        out.write("""
import "../../src/memmap";
import "../../src/metasprites";
""")
        out.write(f"in { ROM_BANK } {{")
        out.write("""

namespace ms {
""")
        out.write(f"namespace { spritesheet_name } {{\n")

        for ss in metasprites:
            frame_size = len(ss.frames[0])
            n_frames = len(ss.frames)

            frame_type = f"[u8 ; { frame_size }]"

            out.write('\n')
            out.write(f"  namespace { ss.name } {{\n")

            out.write(f"    // ms_export_order = { ss.ms_export_order }\n")
            out.write(f"    let draw_function = metasprites.drawing_functions.{ ss.pattern };\n\n")

            out.write(f"    const frames : [[u8 ; { frame_size }] ; { n_frames }] = [\n")

            for frame_data in ss.frames:
                assert len(frame_data) == frame_size
                out.write(f"      [ { ', '.join(map(str, frame_data)) } ],\n")

            out.write( '    ];\n\n')

            out.write(f"    const frame_table : [*const [u8 ; { frame_size }] ; { n_frames }] = [ &frames[i] for let i in 0..{ n_frames - 1 } ];\n\n")

            out.write( '  }\n')

        out.write("""
}
}

}

""")

        return out.getvalue()



#
# =========================
#



def generate_ppu_data(palette_data, tileset):
    tile_data = convert_snes_tileset(tileset, TILE_DATA_BPP)


    data = bytearray()

    # First two bytes = tileset size
    data.append(len(tile_data) & 0xff)
    data.append(len(tile_data) >> 8)

    # Next 256 bytes = palette data
    data += palette_data
    assert(len(data) == 128 * 2 + 2)

    data += tile_data

    return data



def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('--ppu-output', required=True,
                        help='ppu data output file')
    parser.add_argument('--wiz-output', required=True,
                        help='sprite output file')
    parser.add_argument('image_filename', action='store',
                        help='Indexed png image')
    parser.add_argument('palette_filename', action='store',
                        help='palette PNG image')
    parser.add_argument('json_filename', action='store',
                        help='Sprite map JSON file')
    parser.add_argument('ms_export_order_json_file', action='store',
                        help='metasprite export order map JSON file')

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    with PIL.Image.open(args.palette_filename) as palette_image:
        if palette_image.width != 16 or palette_image.height != 8:
            raise ValueError('Palette Image MUST BE 16x8 px in size')

        palettes_map = create_palettes_map(palette_image, TILE_DATA_BPP)
        palette_data = convert_palette_image(palette_image)

        with PIL.Image.open(args.image_filename) as image:
            if image.width != 128:
                raise ValueError('MetaSprite Tileset Image MUST BE 128 px in width')

            tileset, tile_palette_map = convert_tileset(extract_tileset_tiles(image), palettes_map)

    with open(args.json_filename, 'r') as json_fp:
        input_data = json.load(json_fp)

    ms_export_orders = load_ms_export_order_json(args.ms_export_order_json_file)


    if os.path.basename(args.json_filename) == 'common.json':
        tile_offset = 0
    else:
        tile_offset = 512 - len(tileset)


    metasprites = build_metasprites(input_data, ms_export_orders, tile_offset, tile_palette_map)

    ppu_data = generate_ppu_data(palette_data, tileset)
    wiz_data = generate_wiz_data(metasprites,
                                 os.path.splitext(os.path.basename(args.wiz_output))[0],
                                 os.path.relpath(args.ppu_output, os.path.dirname(args.wiz_output)))

    with open(args.ppu_output, 'wb') as fp:
        fp.write(ppu_data)

    with open(args.wiz_output, 'w') as fp:
        fp.write(wiz_data)


if __name__ == '__main__':
    main()


