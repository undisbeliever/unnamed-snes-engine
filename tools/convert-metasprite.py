#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import json
import os.path
import PIL.Image
import argparse
from collections import namedtuple
from io import StringIO


TILE_DATA_BPP = 4

ROM_BANK = 'rodata0'


def convert_rgb_color(c):
    r, g, b = c

    b = (b >> 3) & 31;
    g = (g >> 3) & 31;
    r = (r >> 3) & 31;

    return (b << 10) | (g << 5) | r;



def convert_snes_tileset_4bpp(tiles):
    out = bytearray()

    for tile in tiles:
        for b in range(0, TILE_DATA_BPP, 2):
            for y in range(0, 8):
                for bi in range(b, min(b+2, TILE_DATA_BPP)):
                    byte = 0
                    mask = 1 << bi
                    for x in range(0, 8):
                        byte <<= 1
                        if tile[x + y * 8] & mask:
                            byte |= 1
                    out.append(byte)
    return out



def extract_tiles(image):
    """ Extracts 8x8px tiles from the image. """

    if image.mode != 'RGB':
        image = image.convert('RGB')


    if image.width != 128:
        raise ValueError('MetaSprite Tileset Image MUST BE 128 px in width')

    if image.height % 8 != 0:
        raise ValueError('MetaSprite Tileset Image height MUST BE multiple of 8')


    for ty in range(0, image.height, 8):
        for tx in range(0, image.width, 8):

            tile_data = list()

            for y in range(ty, ty + 8):
                for x in range(tx, tx + 8):
                    tile_data.append(convert_rgb_color(image.getpixel((x, y))))

            yield tile_data



def convert_palette(image):
    if image.mode != 'RGB':
        image = image.convert('RGB')

    if image.width != 16 or image.height != 8:
        raise Value('Palette Image MUST BE 16x8 px in size')

    palette = list()

    for y in range(0, image.height):

        pal_line = list()
        pal_map = dict()

        for x in range(16):
            c = convert_rgb_color(image.getpixel((x, y)))

            pal_line.append(c)
            if c not in pal_map:
                pal_map[c] = x

        palette.append((pal_line, pal_map))

    return palette



def get_palette_id(tile, palettes):
    # Returns a tuple of (palette_id, palette_map)
    for palette_id, _pal in enumerate(palettes):
        pal_map = _pal[1]

        if all([c in pal_map for c in tile]):
            return palette_id, pal_map

    return None, None



def convert_tileset(tiles, palettes):
    # Returns a tuple(tileset, tile_palette_map)

    invalid_tiles = list()

    tileset = list()
    tile_palette_map = list()

    for tile_index, tile in enumerate(tiles):
        palette_id, palette_map = get_palette_id(tile, palettes)

        if palette_map:
            tile_data = bytes([palette_map[c] for c in tile])

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

SpriteSheet = namedtuple('SpriteSheet', ('name', 'pattern', 'frames'))

SpriteFrame = namedtuple('SpriteFrame', ('name', 'xoffset', 'yoffset', 'objects'))
ObjectEntry = namedtuple('ObjectEntry', ('tile_id', 'palette_id', 'order', 'hflip', 'vflip'))

SpriteFrameData = namedtuple('SpriteFrameData', ('name', 'data'))


def hflip_object_entry(o):
    return ObjectEntry(o.tile_id, o.palette_id, o.order, not o.hflip, o.vflip)

def vflip_object_entry(o):
    return ObjectEntry(o.tile_id, o.palette_id, o.order, o.hflip, not o.vflip)



def find_named_item(items, name_to_find):
    for i in items:
        if i and i.name == name_to_find:
            return i;

    raise KeyError(f"Cannot find element with name '{ name_to_find }'.")



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

    def expand_sprite_frame(f, objects):
        return SpriteFrame(
            f['name'],
            to_int(f.get('xoffset', default_xoffset)),
            to_int(f.get('yoffset', default_yoffset)),
            objects
        )


    frames = [None] * len(spritesheet['frames'])


    for i, f in enumerate(spritesheet['frames']):
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

            frames[i] = expand_sprite_frame(f, objects)

        elif 'starting_tile' in f:
            frames[i] = expand_sprite_frame(f, pattern.starting_tile(f, to_int(f['starting_tile']), default_order))


    for i, f in enumerate(spritesheet['frames']):
        if 'objects' not in f:
            if 'clone' in f:
                source = find_named_item(frames, f['clone']['source']);

                flip = f['clone'].get('flip')
                if flip:
                    frames[i] = expand_sprite_frame(f, pattern.flip(source.objects, flip))
                else:
                    frames[i] = expand_sprite_frame(f, source.objects)


    if None in frames:
        missing = list()
        for i, f in enumerate(frames):
            if f is None:
                missing.append(spritesheet['frames'][i]['name'])

        raise ValueError(f"Unable to expand '{ spritesheet['name'] }', missing frames: { ', '.join(missing) }");


    return SpriteSheet(spritesheet['name'],
                       spritesheet['pattern'],
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

    return SpriteFrameData(frame.name, data)



def build_metasprites(input_data, tile_offset, tile_palette_map):
    out = list()

    for ss_input in input_data:
        ss = expand_spritesheet(ss_input)

        frames = [ build_frame(f, tile_offset, tile_palette_map) for f in ss.frames ]

        out.append(SpriteSheet(ss.name, ss.pattern, frames))

    return out



def generate_wiz_data(metasprites, spritesheet_name, binary_data_path):
    with StringIO() as out:
        out.write("""
import "../../src/memmap";
import "../../src/metasprites";
""")
        out.write(f"in { ROM_BANK } " + '{')
        out.write("""

namespace ms {
""")
        out.write(f"namespace { spritesheet_name } " + '{\n')

        out.write(f"\n  const ppu_data = embed \"{ binary_data_path }\";\n")

        for ss in metasprites:
            frame_size = len(ss.frames[0].data)
            n_frames = len(ss.frames)

            out.write('\n')
            out.write(f"  namespace { ss.name } " + '{\n')

            out.write(f"    let pattern = metasprites.drawing_functions.{ ss.pattern };\n\n")

            out.write(f"    let n_frames = { n_frames };\n\n")

            out.write(f"    const frames : [[u8 ; { frame_size }] ; { n_frames }] = [\n")

            for f in ss.frames:
                out.write(f"      [ { ', '.join(map(str, f.data)) } ],\n")

            out.write( '    ];\n\n')

            out.write(f"    const frames_table : [*const [u8 ; { frame_size }] ; { n_frames }] = [ &frames[i] for let i in 0..{ n_frames - 1 } ];\n\n")

            for i, f in enumerate(ss.frames):
                out.write(f"    let { f.name } = { i };\n");

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



def generate_ppu_data(palette, tileset):
    tile_data = convert_snes_tileset_4bpp(tileset)


    data = bytearray()

    # First two bytes = tileset size
    data.append(len(tile_data) & 0xff)
    data.append(len(tile_data) >> 8)

    # Next 256 bytes = palette data
    for pal, p_map in palette:
        for c in pal:
            data.append(c & 0xff)
            data.append(c >> 8)
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

    args = parser.parse_args()

    return args;



def main():
    args = parse_arguments()

    with PIL.Image.open(args.palette_filename) as palImage:
        palette = convert_palette(palImage)

        with PIL.Image.open(args.image_filename) as image:
            tileset, tile_palette_map = convert_tileset(extract_tiles(image), palette)

    with open(args.json_filename, 'r') as json_fp:
        input_data = json.load(json_fp)


    if os.path.basename(args.json_filename) == 'common.json':
        tile_offset = 0
    else:
        tile_offset = 512 - len(tileset)


    metasprites = build_metasprites(input_data, tile_offset, tile_palette_map)

    ppu_data = generate_ppu_data(palette, tileset)
    wiz_data = generate_wiz_data(metasprites,
                                 os.path.splitext(os.path.basename(args.wiz_output))[0],
                                 os.path.relpath(args.ppu_output, os.path.dirname(args.wiz_output)))

    with open(args.ppu_output, 'wb') as fp:
        fp.write(ppu_data)

    with open(args.wiz_output, 'w') as fp:
        fp.write(wiz_data)


if __name__ == '__main__':
    main()


