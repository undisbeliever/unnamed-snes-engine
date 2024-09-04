# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from .data_store import EngineData, DynamicSizedData
from .errors import SimpleMultilineError
from .json_formats import MsPaletteInput, MsPalettesJson
from .snes import load_palette_image, SnesColor, Palette, PaletteMap

from typing import Final, NamedTuple, Optional


N_METASPRITE_ROWS: Final = 8
COLORS_PER_ROW: Final = 16

MAX_SOURCE_PNG_COLORS: Final = N_METASPRITE_ROWS * COLORS_PER_ROW

MAX_PALETTES_LOADED_AT_ONCE: Final = 3

MAX_RAM_DATA_PALETTE_SIZE: Final = 256


class MsPaletteError(SimpleMultilineError):
    pass


class MsPalette(NamedTuple):
    palette_map: PaletteMap
    transparent_color: SnesColor


def insert_into_to_mapping_pal(mapping_pal: list[Optional[list[SnesColor]]], pi: MsPaletteInput, p: Palette) -> None:
    colors: Final = p.colors

    if len(colors) < pi.n_rows * COLORS_PER_ROW:
        raise ValueError(f"Not enough rows in ms_palette image: {pi.name}")

    for r in range(pi.n_rows):
        i = pi.starting_row + r
        if i < 0 or i > N_METASPRITE_ROWS:
            raise ValueError(f"Cannot insert ms_palette into CGRAM (out of bounds): {pi.name}")
        if mapping_pal[i] is not None:
            raise ValueError(f"Cannot insert ms_palette into CGRAM: {pi.name} overrides child palette")
        mapping_pal[i] = colors[r * COLORS_PER_ROW : (r + 1) * COLORS_PER_ROW]


def build_ms_palette_map(
    palette_input: MsPaletteInput, base_pal: Palette, ms_palettes_json: MsPalettesJson, error_list: list[str]
) -> PaletteMap:

    mapping_pal: list[Optional[list[SnesColor]]] = [None] * N_METASPRITE_ROWS

    try:
        insert_into_to_mapping_pal(mapping_pal, palette_input, base_pal)
    except Exception as e:
        error_list.append(str(e))

    count = 1
    pi: Optional[MsPaletteInput] = palette_input
    while pi and pi.spritesheet_uses_parent and pi.parent and count <= MAX_PALETTES_LOADED_AT_ONCE:
        pc = pi
        count += 1

        pi = ms_palettes_json.ms_palettes.get(pi.parent)
        if pi is not None:
            try:
                # ::TODO load from cache::
                pal = load_palette_image(pi.source, MAX_SOURCE_PNG_COLORS)
                insert_into_to_mapping_pal(mapping_pal, pi, pal)
            except Exception as e:
                error_list.append(str(e))
        else:
            error_list.append(f"Invalid parent palette: {pc.parent}")

    if count > MAX_PALETTES_LOADED_AT_ONCE:
        error_list.append("Too many nested MetaSprite Palettes")

    palette_maps = list()
    for row in mapping_pal:
        color_map = dict()
        if row:
            for i, c in enumerate(row):
                if c not in color_map:
                    color_map[c] = i
        palette_maps.append(color_map)

    return PaletteMap(palette_maps)


def compile_ms_palette(palette: MsPaletteInput, ms_palettes: MsPalettesJson) -> tuple[EngineData, MsPalette]:
    error_list = list()

    if palette.starting_row < 0 or palette.starting_row > N_METASPRITE_ROWS:
        error_list.append(f"Invalid starting_row: {palette.starting_row}")

    if palette.n_rows <= 0:
        error_list.append(f"Invalid n_rows: {palette.n_rows}")

    if palette.starting_row + palette.n_rows > N_METASPRITE_ROWS:
        error_list.append("Invalid end row (starting_row + n_row)")

    parent_id = 0xFF
    if palette.parent:
        if parent := ms_palettes.ms_palettes.get(palette.parent):
            parent_id = parent.id
        else:
            error_list.append(f"Cannot find parent ms_palette: {palette.parent}")

    try:
        pal = load_palette_image(palette.source, MAX_SOURCE_PNG_COLORS)
    except Exception as e:
        error_list.append(str(e))
        pal = None

    if pal:
        n_rows: Final = len(pal.colors) // COLORS_PER_ROW
        if n_rows != palette.n_rows:
            error_list.append(
                f"Invalid number of rows in palette image: {palette.source} has {n_rows} rows, expected {palette.n_rows}"
            )

        palette_map = build_ms_palette_map(palette, pal, ms_palettes, error_list)

    if error_list:
        raise MsPaletteError("Error compiling MetaSprite Palette", error_list)

    assert pal and palette_map

    transparent_color = pal.colors[0]

    ram_data = (
        bytes(
            [
                palette.n_rows * COLORS_PER_ROW,
                palette.starting_row * COLORS_PER_ROW,
                parent_id,
            ]
        )
        + pal.snes_data()
    )

    assert len(ram_data) < 3 + MAX_RAM_DATA_PALETTE_SIZE, "ram_data is too large"

    return (
        EngineData(
            ram_data=DynamicSizedData(ram_data),
            ppu_data=None,
        ),
        MsPalette(palette_map, transparent_color),
    )
