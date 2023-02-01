# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import math
import struct
from collections import OrderedDict

from wav2brr import load_wav_file, encode_brr, SAMPLES_PER_BLOCK, BYTES_PER_BRR_BLOCK

from _driver_constants import *

from _json_formats import SamplesJson, Instrument, Filename, Name

from dataclasses import dataclass
from typing import Final, NamedTuple, Optional, TypeAlias, Union


#
# BRR Samples
# ===========


class BrrData(NamedTuple):
    instrument_scrn_table: bytes
    brr_directory: bytes
    brr_data: bytes


def _compile_brr_samples(instruments: list[Instrument]) -> BrrData:
    instrument_scrn_table = bytearray()
    brr_directory = bytearray()
    brr_data = bytearray()

    # Mapping of wave files to the address of BRR data
    # filename, loop flag => brr address, brr size
    brr_data_map: dict[tuple[Filename, bool], tuple[Addr, int]] = dict()

    # Mapping of wave files and loop points to sample directory ids
    # filename, loop flag, loop_point => sample directory id
    scrn_map: dict[tuple[Filename, bool, int], int] = dict()

    def encode_wave_file(filename: Filename, loop_flag: bool) -> tuple[Addr, int]:
        nonlocal brr_data, brr_data_map

        key: Final = filename, loop_flag

        out = brr_data_map.get(key)
        if out is None:
            w = load_wav_file(filename)
            b = encode_brr(w, loop_flag)

            addr = len(brr_data) + BRR_DATA_ADDRESS
            size = len(b)

            brr_data += b

            brr_data_map[key] = out = addr, size
        return out

    def add_sample(filename: Filename, loop_flag: bool, loop_point: Optional[int]) -> int:
        nonlocal scrn_map

        if loop_point is None:
            loop_point = 0

        key: Final = filename, loop_flag, loop_point
        sample_id = scrn_map.get(key)
        if sample_id is None:
            brr_addr, brr_size = encode_wave_file(filename, loop_flag)

            if loop_point < 0 or loop_point % SAMPLES_PER_BLOCK != 0:
                raise ValueError(f"Loop point must be a multiple of {SAMPLES_PER_BLOCK}")

            loop_offset = (loop_point // 16) * BYTES_PER_BRR_BLOCK

            if loop_offset >= brr_size:
                raise ValueError("Loop point must be < number of samples in the wave file")

            dir_item = struct.pack("<2H", brr_addr, brr_addr + loop_offset)

            assert len(dir_item) == BYTES_PER_SAMPLE_DIRECTORY_ITEM
            brr_directory.extend(dir_item)

            sample_id = len(brr_directory) // BYTES_PER_SAMPLE_DIRECTORY_ITEM - 1

            scrn_map[key] = sample_id
        return sample_id

    errors: list[str] = list()

    for i, inst in enumerate(instruments):
        try:
            instrument_scrn_table.append(add_sample(inst.source, inst.looping, inst.loop_point))
        except Exception as e:
            errors.append(f"ERROR in Instrument {i} {inst.name}: {e}")

    if len(brr_data) >= MAX_COMMON_DATA_SIZE:
        errors.append(f"Too many BRR samples.  ({len(brr_data)} bytes, max is {MAX_COMMON_DATA_SIZE}")

    # Pad `brr_directory`
    directory_padding = BRR_DIRECTORY_SIZE - len(brr_directory)
    if directory_padding < 0:
        errors.append(
            f"Too many items in the sample directory. (The directory is {len(brr_directory)} bytes, max is {N_BRR_SAMPLES_IN_DIRECTORY})"
        )
    elif directory_padding > 0:
        brr_directory += bytes(directory_padding)

    if errors:
        error_string = "\n    ".join(errors)
        raise ValueError(f"{len(errors)} errors compiling brr samples:\n    {error_string}")

    assert len(brr_directory) == BRR_DIRECTORY_SIZE

    return BrrData(instrument_scrn_table=instrument_scrn_table, brr_directory=brr_directory, brr_data=brr_data)


# Pitch Table
# ===========

# The pitch table is generated using integer micro-semitones (instead of float semitones)
# to allow for semitone equality comparisons.

SPC_SAMPLE_RATE: Final = 32000

SEMITONE_SCALE: Final = 1000000

SEMITONES_PER_OCTAVE: Final = 12
MICROSEMITONES_PER_OCTAVE: Final = SEMITONES_PER_OCTAVE * SEMITONE_SCALE

A4_C0_MICROSEMITONE_OFFSET: Final = 57000000

A4_FREQ: Final = 440


MIN_OCTAVE: Final = 0
MAX_OCTAVE: Final = 8


MIN_PITCH: Final = 0x0001
MAX_PITCH: Final = 0x4000


class InstrumentPitch(NamedTuple):
    instrument_id: int
    octaves_above_c0: int
    min_octave_offset: int
    max_octave_offset: int


# Mapping of `microsemitones above C` => SamplePitch
def _build_microsemitone_map(instruments: list[Instrument]) -> OrderedDict[int, list[InstrumentPitch]]:
    mst_map: OrderedDict[int, list[InstrumentPitch]] = OrderedDict()

    errors: list[str] = list()

    for i, inst in enumerate(instruments):
        # Calculacte microsemitones above c0
        mst_above_c0 = round(MICROSEMITONES_PER_OCTAVE * math.log2(inst.freq / A4_FREQ)) + A4_C0_MICROSEMITONE_OFFSET

        octave = mst_above_c0 // MICROSEMITONES_PER_OCTAVE
        mst_pitch = mst_above_c0 % MICROSEMITONES_PER_OCTAVE

        sp = mst_map.get(mst_pitch)
        if sp is None:
            sp = mst_map[mst_pitch] = list()

        min_octave_offset = inst.first_octave - octave
        max_octave_offset = inst.last_octave - octave

        if min_octave_offset < -6:
            errors.append("Instrument {i} {inst.name}: first_octave is too low")
        if max_octave_offset > 2:
            errors.append("Instrument {i} {inst.name}: last_octave is too high")

        sp.append(
            InstrumentPitch(
                instrument_id=i, octaves_above_c0=octave, min_octave_offset=min_octave_offset, max_octave_offset=max_octave_offset
            )
        )

    if errors:
        error_string = "\n    ".join(errors)
        raise ValueError(f"{len(errors)} errors building the pitch table:\n    {error_string}")

    return mst_map


class PitchTable(NamedTuple):
    table_data: list[int]
    # pitch table offset for each of the instruments
    instrument_offsets: list[int]


def _build_instrument_pitch_table(mst_map: OrderedDict[int, list[InstrumentPitch]], n_instruments: int) -> PitchTable:
    # Pitch table (uint16 array)
    pitch_table: list[int] = list()

    # pitch table offset for the instruments
    instrument_pt_offsets: list[Optional[int]] = [None] * n_instruments

    for mst, inst_list in mst_map.items():
        # mst = microsemitones above C

        min_octave_offset = min(i.min_octave_offset for i in inst_list)
        max_octave_offset = max(i.max_octave_offset for i in inst_list)

        pt_offset = len(pitch_table)

        for octave_shift in range(min_octave_offset, max_octave_offset + 1):
            for note in range(SEMITONES_PER_OCTAVE):
                # Calculate milli-semitones to pitch shift for this note
                mst_to_shift = octave_shift * MICROSEMITONES_PER_OCTAVE + note * SEMITONE_SCALE - mst

                # ::TODO confirm working with milli-semitones and double-floats is accurate enough::
                pitch = 2 ** (mst_to_shift / MICROSEMITONES_PER_OCTAVE)
                pitch_fp = round(pitch * 0x1000)

                pitch_table.append(pitch_fp)

        for inst in inst_list:
            o = pt_offset - (inst.octaves_above_c0 + inst.min_octave_offset) * SEMITONES_PER_OCTAVE
            instrument_pt_offsets[inst.instrument_id] = o

    assert all(o is not None for o in instrument_pt_offsets)

    return PitchTable(
        table_data=pitch_table,
        instrument_offsets=instrument_pt_offsets,  # type: ignore
    )


def _pitch_table_data(pt: PitchTable) -> bytes:
    # ::TODO somehow add raw sound samples to the pitch table data::

    if len(pt.table_data) > N_PITCHES_IN_PITCH_TABLE:
        raise RuntimeError(f"Too many pitches in the pitch table.  (got: {len(pt)}, max: {N_PITCHES_IN_PITCH_TABLE}")

    # Ensure all pitches are valid
    assert all(MIN_PITCH <= p <= MAX_PITCH for p in pt.table_data)

    padding: Final = bytes(N_PITCHES_IN_PITCH_TABLE - len(pt.table_data))

    out = bytearray()

    # lo byte
    out += bytes(i & 0xFF for i in pt.table_data)
    out += padding

    # hi byte
    out += bytes(i >> 8 for i in pt.table_data)
    out += padding

    assert len(out) == N_PITCHES_IN_PITCH_TABLE * 2
    return out


#
# Instruments
# ===========


def validate_instrument_input(instruments: list[Instrument]) -> None:
    errors: list[str] = list()

    if len(instruments) <= 0:
        raise ValueError("Expected at least one instrument")

    if len(instruments) > N_INSTRUMENTS:
        raise ValueError(f"Too many instruments (max: {N_INSTRUMENTS})")

    for i, inst in enumerate(instruments):

        def test_value(var_name: str, v: Union[int, float], min_value: int, max_value: int) -> None:
            if v < min_value or v > max_value:
                errors.append(f"Instrument {i} {inst.name}: {var_name} out of bounds (got: {v}, min: {min_value}, max: {max_value}")

        test_value("freq", inst.freq, 100, 32000)
        # loop-point tested when building sample directory
        test_value("first_octave", inst.first_octave, MIN_OCTAVE, MAX_OCTAVE)
        test_value("last_octave", inst.last_octave, inst.first_octave, MAX_OCTAVE)
        test_value("ADSR attack", inst.adsr.attack, 0, 0b01111)
        test_value("ADSR decay", inst.adsr.decay, 0, 0b00111)
        test_value("ADSR sustain", inst.adsr.sustain_level, 0, 0b00111)
        test_value("ADSR release", inst.adsr.sustain_rate, 0, 0b11111)
        test_value("gain", inst.gain, 0, 0xFF)

    if errors:
        error_string = "\n    ".join(errors)
        raise ValueError(f"{len(errors)} errors in instruments:\n    {error_string}")


def _mask_pitch_offset(i: int) -> int:
    if i >= 0:
        return i & 0xFF
    else:
        o = (-i) & 0xFF
        if o == 0:
            return 0
        return 0x100 - o


def _instruments_soa_data(instruments: list[Instrument], instrument_scrn: bytes, pitch_table: PitchTable) -> bytes:
    padding_count: Final = N_INSTRUMENTS - len(instruments)
    assert padding_count >= 0

    padding: Final = bytes(padding_count)

    out = bytearray()

    # order MUST match `InstrumentsSoA` in `src/audio-driver.wiz`

    # scrn (Sample source)
    assert len(instrument_scrn) == len(instruments)
    out += instrument_scrn
    out += padding

    # adsr1
    out += bytes((bool(i.adsr.enabled) << 7) | (i.adsr.decay << 4) | (i.adsr.attack) for i in instruments)
    out += padding

    # adsr2
    out += bytes((i.adsr.sustain_level << 5) | (i.adsr.sustain_rate) for i in instruments)
    out += padding

    # gain
    out += bytes(i.gain for i in instruments)
    out += padding

    # pitch_offset
    out += bytes(_mask_pitch_offset(i) for i in pitch_table.instrument_offsets)
    out += padding

    assert len(out) == N_INSTRUMENTS * N_FIELDS_IN_INSTRUMENTS_SOA

    return out


class SampleAndInstrumentData(NamedTuple):
    header: bytes
    brr_data: bytes


def build_sample_and_instrument_data(samplesInput: SamplesJson) -> SampleAndInstrumentData:
    instruments: Final = samplesInput.instruments

    validate_instrument_input(instruments)

    samples = _compile_brr_samples(instruments)

    smt_map: Final = _build_microsemitone_map(instruments)
    pitch_table: Final = _build_instrument_pitch_table(smt_map, len(instruments))

    header = bytearray()
    header += samples.brr_directory
    header += _pitch_table_data(pitch_table)
    header += _instruments_soa_data(instruments, samples.instrument_scrn_table, pitch_table)

    assert len(header) == SAMPLE_AND_INSTRUMENT_HEADER_SIZE

    return SampleAndInstrumentData(header, samples.brr_data)
