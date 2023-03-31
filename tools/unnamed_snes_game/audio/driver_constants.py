# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from typing import Final, TypeAlias


Addr: TypeAlias = int


MIN_TICK_TIMER: Final = 64

SFX_TICK_TIMER: Final = 64


BYTES_PER_SAMPLE_DIRECTORY_ITEM: Final = 4
N_FIELDS_IN_INSTRUMENTS_SOA: Final = 5


# These values MUST match `src/audio-driver.wiz`
N_BRR_SAMPLES_IN_DIRECTORY: Final = 64
N_INSTRUMENTS: Final = 64
N_SOUND_EFFECTS: Final = 128
N_PITCHES_IN_PITCH_TABLE: Final = 256

assert N_BRR_SAMPLES_IN_DIRECTORY.bit_count() == 1
assert N_INSTRUMENTS.bit_count() == 1
assert N_PITCHES_IN_PITCH_TABLE.bit_count() == 1

BRR_DIRECTORY_SIZE: Final = N_BRR_SAMPLES_IN_DIRECTORY * 4

SAMPLE_AND_INSTRUMENT_HEADER_SIZE: Final = (
    BRR_DIRECTORY_SIZE + N_PITCHES_IN_PITCH_TABLE * 2 + N_INSTRUMENTS * N_FIELDS_IN_INSTRUMENTS_SOA
)


# These values MUST match `src/audio-driver.wiz`
EXTERNAL_DATA_HEADER_ADDR: Final[Addr] = 0x700
EXTERNAL_DATA_HEADER_SIZE: Final = SAMPLE_AND_INSTRUMENT_HEADER_SIZE + 2 * N_SOUND_EFFECTS

BRR_DATA_ADDRESS: Final[Addr] = EXTERNAL_DATA_HEADER_ADDR + EXTERNAL_DATA_HEADER_SIZE
MAX_COMMON_DATA_SIZE: Final = 0xD000

assert EXTERNAL_DATA_HEADER_ADDR & 0xFF == 0

# Song constants

N_MUSIC_CHANNELS: Final = 6

# MUST match `audio/src/common_memmap.wiz`
# MUST match `SongHeader` in `audio/src/audio-driver.wiz`
SONG_HEADER_SIZE = N_MUSIC_CHANNELS * 4 + 2
