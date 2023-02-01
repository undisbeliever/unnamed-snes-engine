# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import sys
import argparse
from typing import Final, Sequence

from _bytecode import create_bc_mappings
from _json_formats import load_samples_json, load_mapping_json, SamplesJson, Mappings
from _samples import build_sample_and_instrument_data
from _sound_effects import compile_sound_effects_file, build_sfx_header_and_data
from _driver_constants import *


def build_common_data(samples_input: SamplesJson, mappings: Mappings, sfx_file: Sequence[str], sfx_filename: str) -> bytes:
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


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", required=True, help="binary file output")

    parser.add_argument("samples_json", action="store", help="samples json file")

    parser.add_argument("mapping_json", action="store", help="mapping json file")

    parser.add_argument("sound_effects", action="store", help="sound effects source file")

    args = parser.parse_args()

    return args


def main() -> None:
    try:
        args = parse_arguments()

        samples_input = load_samples_json(args.samples_json)
        mapping_json = load_mapping_json(args.mapping_json)

        with open(args.sound_effects, "r") as fp:
            sfx_file = list(fp)

        data = build_common_data(samples_input, mapping_json, sfx_file, args.sound_effects)

        with open(args.output, "wb") as fp:
            fp.write(data)

    except Exception as e:
        sys.exit(f"ERROR: { e }")


if __name__ == "__main__":
    main()
