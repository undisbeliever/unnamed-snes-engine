# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from enum import IntEnum, unique


# order MUST match `ResourceType` enum in `src/metasprites.wiz`
#
# enum fields MUST be plural
@unique
class ResourceType(IntEnum):
    palettes = 0
    ms_palettes = 1
    mt_tilesets = 2
    second_layers = 3
    ms_spritesheets = 4
    tiles = 5
    bg_images = 6
    audio_data = 7
    dungeons = 8
