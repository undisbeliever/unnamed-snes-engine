# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import sys
import argparse
from typing import Final, Sequence

from .bytecode import create_bc_mappings
from .samples import build_sample_and_instrument_data
from .sound_effects import compile_sound_effects_file, build_sfx_header_and_data
from .driver_constants import *
from .json_formats import SamplesJson
from ..json_formats import Filename, Mappings


def build_common_data(samples_input: SamplesJson, mappings: Mappings, sfx_file: Sequence[str], sfx_filename: Filename) -> bytes:
    samples_and_instruments: Final = build_sample_and_instrument_data(samples_input)

    sfx_addr: Final = BRR_DATA_ADDRESS + len(samples_and_instruments.brr_data)

    bc_mappings = create_bc_mappings(samples_input, SFX_TEMPO)
    sfx = compile_sound_effects_file(sfx_file, sfx_filename, bc_mappings)
    sfx_header, sfx_data = build_sfx_header_and_data(sfx, mappings, sfx_addr)

    out = bytearray()
    out += samples_and_instruments.header
    out += sfx_header

    assert len(out) == EXTERNAL_DATA_HEADER_SIZE

    out += samples_and_instruments.brr_data
    out += sfx_data

    if len(out) > MAX_COMMON_DATA_SIZE:
        raise RuntimeError(f"Common data is too large: {len(out)} bytes, max {MAX_COMMON_DATA_SIZE} bytes")

    return out


def load_sfx_file(sfx_filename: Filename) -> list[str]:
    with open(sfx_filename, "r") as fp:
        return list(fp)
