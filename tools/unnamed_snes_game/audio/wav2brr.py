#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import wave
import struct

import sys
import argparse

from typing import Callable, Final, Generator, NamedTuple, Optional, Sequence, TypeAlias


SAMPLES_PER_BLOCK: Final = 16
BYTES_PER_BRR_BLOCK: Final = 9

MAX_SHIFT: Final = 12


def bound(i: int, minimum: int, maximum: int) -> int:
    if i < minimum:
        return minimum
    if i > maximum:
        return maximum
    return i


def clamp_s4(i: int) -> int:
    """Clamp i to a 4 bit signed integer."""
    return bound(i, -8, 7)


# Contains 16 int16 sample
SampleBlock: TypeAlias = Sequence[int]


class WaveFile(NamedTuple):
    samplerate: int
    blocks: list[SampleBlock]


def load_wav_file(filename: str) -> WaveFile:
    with wave.open(filename, "rb") as wf:
        if wf.getnchannels() != 1 or wf.getsampwidth() != 2:
            raise ValueError("Expected a 16bit mono wave file")

        samplerate = wf.getframerate()

        n_frames = wf.getnframes()
        if n_frames > 16 * 1024:
            raise ValueError("wav file is too large")

        data = wf.readframes(n_frames)

        if len(data) == 0:
            raise ValueError(f"wav file has no samples")

        if len(data) % (SAMPLES_PER_BLOCK * 2) != 0:
            raise ValueError(f"wav file must be a multiple of {SAMPLES_PER_BLOCK} in size")

        samples: list[SampleBlock] = list(struct.iter_unpack("<16h", data))

        return WaveFile(samplerate, samples)


class BrrBlock(NamedTuple):
    shift: int
    filter_id: int
    nibbles: list[int]
    decoded_samples: SampleBlock


def __encode(
    samples: SampleBlock, shift: int, prev1: int, prev2: int, filter_id: int, offset_calc: Callable[[int, int], int]
) -> BrrBlock:
    assert len(samples) == SAMPLES_PER_BLOCK
    assert 0 <= shift <= MAX_SHIFT

    nibbles = list[int]()
    decoded = list[int]()

    div = 1 << shift

    for s in samples:
        offset = offset_calc(prev1, prev2)

        n = clamp_s4((s - offset) // div)

        prev2 = prev1
        prev1 = (n * div) + offset

        nibbles.append(n)
        decoded.append(prev1)

    return BrrBlock(shift, filter_id, nibbles, decoded)


def encode__filter0(samples: SampleBlock, shift: int) -> BrrBlock:
    return __encode(samples, shift, 0, 0, 0, lambda p1, p2: 0)


def encode__filter1(samples: SampleBlock, shift: int, prev_sample: int) -> BrrBlock:
    return __encode(samples, shift, prev_sample, 0, 1, lambda p1, p2: p1 * 15 // 16)


def encode__filter2(samples: SampleBlock, shift: int, prev1: int, prev2: int) -> BrrBlock:
    return __encode(samples, shift, prev1, prev2, 2, lambda p1, p2: (p1 * 61 // 32) - (p2 * 15 // 16))


def encode__filter3(samples: SampleBlock, shift: int, prev1: int, prev2: int) -> BrrBlock:
    return __encode(samples, shift, prev1, prev2, 3, lambda p1, p2: (p1 * 115 // 64) - (p2 * 13 // 16))


def calc_error(to_test: BrrBlock, target_samples: SampleBlock) -> int:
    return sum(abs(t - s) ** 2 for t, s in zip(to_test.decoded_samples, target_samples))


def encode_brr(wave_file: WaveFile, loop_flag: bool, dupe_block_hack: Optional[int]) -> bytes:
    out = bytearray()

    if dupe_block_hack is None:
        dupe_block_hack = 0
    else:
        if dupe_block_hack < 0:
            raise ValueError("dupe_block_hack MUST be >= 0")
        if dupe_block_hack > 0 and not loop_flag:
            raise ValueError("dupe_block_hack can only be applied to looping samples")

    len_blocks: Final = len(wave_file.blocks)
    last_block: Final = len_blocks + dupe_block_hack - 1

    for block_number in range(last_block + 1):
        samples = wave_file.blocks[block_number % len_blocks]

        tests = list[BrrBlock]()

        for s in range(MAX_SHIFT + 1):
            tests.append(encode__filter0(samples, s))

        if block_number != 0:
            for s in range(MAX_SHIFT + 1):
                tests.append(encode__filter1(samples, s, prev_sample_1))
                tests.append(encode__filter2(samples, s, prev_sample_1, prev_sample_2))
                tests.append(encode__filter3(samples, s, prev_sample_1, prev_sample_2))

        # Score each encoding and find the test one
        score, best = min((calc_error(t, samples), t) for t in tests)

        # Write BRR data
        header = (best.shift << 4) | (best.filter_id << 2)
        if block_number == last_block:
            header |= 0x1
            if loop_flag:
                header |= 0x2

        out.append(header)
        for i in range(0, SAMPLES_PER_BLOCK, 2):
            out.append(((best.nibbles[i] & 0xF) << 4) | (best.nibbles[i + 1] & 0xF))

        prev_sample_1: int = best.decoded_samples[-1]
        prev_sample_2: int = best.decoded_samples[-2]

    return out


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("-o", "--output", required=True, help="brr file output")

    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("-l", "--loop", action="store_true", help="Set the loop flag")
    g.add_argument("-n", "--no-loop", action="store_true", help="Do not set the loop flag")

    g.add_argument("--dupe-block-hack", action="store_int")

    parser.add_argument("wav_file", action="store", help="wav file input")

    args = parser.parse_args()

    return args


def main() -> None:
    try:
        args = parse_arguments()

        wave_file = load_wav_file(args.wav_file)
        brr_data = encode_brr(wave_file, args.loop, args.dupe_block_hack)

        with open(args.output, "wb") as fp:
            fp.write(brr_data)

    except Exception as e:
        sys.exit(f"ERROR: { e }")


if __name__ == "__main__":
    main()
