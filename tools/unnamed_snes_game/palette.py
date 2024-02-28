# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import PIL.Image  # type: ignore
import struct
import itertools
from typing import Final, NamedTuple

from .common import EngineData, FixedSizedData, DynamicSizedData
from .json_formats import PaletteInput, Filename, Name
from .snes import convert_rgb_color, PaletteMap, SnesColor, ImageError


IMAGE_WIDTH: Final = 16


class PaletteColors(NamedTuple):
    name: Name
    id: int
    colors: list[SnesColor]

    def create_map(self, bpp: int) -> list[PaletteMap]:
        # ::TODO should this be precalculated or cached?::
        colors: Final = self.colors
        assert len(self.colors) <= 256, "Invalid number of palette colors"

        if bpp < 1 or bpp > 8:
            raise ValueError("Invalid bpp")

        colors_per_palette: Final = 1 << bpp
        n_palettes: Final = min(len(self.colors) // colors_per_palette, 8)

        palettes_map = list()

        for p in range(n_palettes):
            pal_map = dict()
            pi = p * colors_per_palette
            for i, c in enumerate(colors[pi : pi + colors_per_palette]):
                if c not in pal_map:
                    pal_map[c] = i
            palettes_map.append(pal_map)
        return palettes_map


def load_palette_image(filename: Filename) -> list[SnesColor]:
    with PIL.Image.open(filename) as image:
        image.load()

    if image.mode != "RGB":
        image = image.convert("RGB")

    if image.width != IMAGE_WIDTH:
        raise ImageError(image.filename, f"Palette image must be {IMAGE_WIDTH} pixels in width")

    return [convert_rgb_color(c) for c in image.getdata()]


def convert_palette(pi: PaletteInput, id: int) -> tuple[EngineData, PaletteColors]:
    if pi.n_rows < 1 or pi.n_rows > 16:
        raise ValueError(f"Invalid n_rows ({pi.n_rows}, min 1, max 16)")

    colors: Final = load_palette_image(pi.source)

    rows_in_image: Final = len(colors) // IMAGE_WIDTH

    if rows_in_image != pi.n_rows:
        raise RuntimeError(f"Image height ({rows_in_image}) does not match n_rows ({pi.n_rows})")

    header: Final = bytes(
        [
            len(colors),
        ]
    )
    data = EngineData(
        FixedSizedData(header),
        DynamicSizedData(struct.pack(f"<{len(colors)}H", *colors)),
    )

    return data, PaletteColors(name=pi.name, id=id, colors=colors)
