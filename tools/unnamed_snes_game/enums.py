# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from enum import IntEnum, unique


# order MUST match `ResourceType` enum in `src/metasprites.wiz`
#
# enum fields MUST be plural
@unique
class ResourceType(IntEnum):
    palettes = 0
    mt_tilesets = 1
    second_layers = 2
    ms_spritesheets = 3
    tiles = 4
    bg_images = 5
    audio_data = 6
    dungeons = 7
