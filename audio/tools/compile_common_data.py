# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import sys
import argparse
from typing import Final

from _json_formats import load_samples_json, SamplesJson
from _samples import build_sample_and_instrument_data
from _driver_constants import *


def build_common_data(samples_input: SamplesJson) -> bytes:
    samples_and_instruments: Final = build_sample_and_instrument_data(samples_input)

    song_start_byte: Final = len(samples_and_instruments.brr_data) + BRR_DATA_ADDRESS

    out = bytearray()
    out += samples_and_instruments.header

    assert len(out) == EXTERNAL_DATA_HEADER_SIZE

    out += samples_and_instruments.brr_data

    return out


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('-o', '--output', required=True,
                        help='binary file output')

    parser.add_argument('samples_json', action='store',
                        help='samples json file')

    args = parser.parse_args()

    return args


def main() -> None:
    try:
        args = parse_arguments()

        samples_input = load_samples_json(args.samples_json)

        data = build_common_data(samples_input)

        with open(args.output, 'wb') as fp:
            fp.write(data)

    except Exception as e:
        sys.exit(f"ERROR: { e }")


if __name__ == '__main__':
    main()
