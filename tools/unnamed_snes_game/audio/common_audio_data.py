# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import sys
import struct
import argparse
from typing import Final, Sequence

from .bytecode import create_bc_mappings
from .samples import build_sample_and_instrument_data
from .sound_effects import build_sound_effects
from .driver_constants import *
from .json_formats import SamplesJson
from ..json_formats import Filename, Mappings


def build_common_data(samples_input: SamplesJson, mappings: Mappings, sfx_file: Sequence[str], sfx_filename: Filename) -> bytes:
    samples_and_instruments: Final = build_sample_and_instrument_data(samples_input)
    sfx_data, sfx_offsets = build_sound_effects(sfx_file, sfx_filename, samples_input, mappings)

    n_dir_items: Final = len(samples_and_instruments.dir_offsets)
    n_instruments: Final = samples_and_instruments.n_instruments
    n_sound_effects: Final = len(sfx_offsets)

    assert n_instruments <= MAX_INSTRUMENTS
    assert n_dir_items <= MAX_DIR_ITEMS
    assert n_sound_effects <= MAX_SOUND_EFFECTS
    assert len(samples_and_instruments.instruments_soa) == n_instruments * COMMON_DATA_BYTES_PER_INSTRUMENT

    header_size: Final = (
        COMMON_DATA_HEADER_SIZE
        + n_dir_items * COMMON_DATA_BYTES_PER_DIR
        + n_instruments * COMMON_DATA_BYTES_PER_INSTRUMENT
        + n_sound_effects * COMMON_DATA_BYTES_PER_SOUND_EFFECT
    )
    out_size: Final = header_size + len(samples_and_instruments.brr_data) + len(sfx_data)

    if out_size > MAX_COMMON_DATA_SIZE:
        raise RuntimeError(f"Common data is too large: {out_size} bytes, max {MAX_COMMON_DATA_SIZE} bytes")

    brr_data_addr: Final = COMMON_DATA_HEADER_ADDR + header_size
    sfx_data_addr: Final = brr_data_addr + len(samples_and_instruments.brr_data)

    out = bytearray()
    out += samples_and_instruments.pitch_table

    out.append(n_dir_items)
    out.append(n_instruments)
    out.append(n_sound_effects)
    out.append(0)  # padding

    assert len(out) == COMMON_DATA_HEADER_SIZE

    for d in samples_and_instruments.dir_offsets:
        out += struct.pack("<2H", d.start + brr_data_addr, d.loop_point + brr_data_addr)

    out += samples_and_instruments.instruments_soa

    # soundEffects_l
    out += bytes((s + sfx_data_addr) & 0xFF for s in sfx_offsets)

    # soundEffects_h
    out += bytes((s + sfx_data_addr) >> 8 for s in sfx_offsets)

    assert len(out) == header_size

    out += samples_and_instruments.brr_data
    out += sfx_data

    assert len(out) == out_size

    return out


def load_sfx_file(sfx_filename: Filename) -> list[str]:
    with open(sfx_filename, "r") as fp:
        return list(fp)
