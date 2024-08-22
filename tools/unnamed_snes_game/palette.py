# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from typing import Final, NamedTuple

from .data_store import EngineData, FixedSizedData, DynamicSizedData
from .json_formats import PaletteInput, Name
from .snes import load_palette_image, PALETTE_IMAGE_WIDTH, Palette, PaletteMap


# The palette used by the resources subsystem
# (Named PaletteResource to prevent confusion with snes.Palette)
class PaletteResource(NamedTuple):
    name: Name
    id: int
    palette: Palette

    def create_map(self, bpp: int) -> PaletteMap:
        # ::TODO should this be precalculated or cached?::
        return self.palette.create_map(bpp)


def convert_palette(pi: PaletteInput, id: int) -> tuple[EngineData, PaletteResource]:
    if pi.n_rows < 1 or pi.n_rows > 16:
        raise ValueError(f"Invalid n_rows ({pi.n_rows}, min 1, max 16)")

    palette: Final = load_palette_image(pi.source, 256)

    rows_in_image: Final = len(palette.colors) // PALETTE_IMAGE_WIDTH

    if rows_in_image != pi.n_rows:
        raise RuntimeError(f"Image height ({rows_in_image}) does not match n_rows ({pi.n_rows})")

    header: Final = bytes(
        [
            len(palette.colors),
        ]
    )
    data = EngineData(FixedSizedData(header), DynamicSizedData(palette.snes_data()))

    return data, PaletteResource(name=pi.name, id=id, palette=palette)
