# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from typing import Final, TypeAlias


Addr: TypeAlias = int


TIMER_HZ: Final = 8000

MIN_TICK_TIMER: Final = 64
MAX_TICK_TIMER: Final = 255

SFX_TICK_TIMER: Final = 64


BYTES_PER_SAMPLE_DIRECTORY_ITEM: Final = 4
N_FIELDS_IN_INSTRUMENTS_SOA: Final = 4


# These values MUST match `src/audio-driver.wiz`
MAX_DIR_ITEMS: Final = 256
MAX_INSTRUMENTS: Final = 256
MAX_SOUND_EFFECTS: Final = 192

N_PITCHES_IN_PITCH_TABLE: Final = 256


# These values MUST match `src/audio-driver.wiz`
COMMON_DATA_HEADER_ADDR: Final[Addr] = 0x800 - 4
COMMON_DATA_HEADER_SIZE: Final = 4 + (2 * N_PITCHES_IN_PITCH_TABLE)

COMMON_DATA_BYTES_PER_DIR: Final = 4
COMMON_DATA_BYTES_PER_INSTRUMENT: Final = 4
COMMON_DATA_BYTES_PER_SOUND_EFFECT: Final = 2

MAX_COMMON_DATA_SIZE: Final = 0xD000

assert COMMON_DATA_HEADER_ADDR % 2 == 0, "Loader requires an even common data address"
assert (COMMON_DATA_HEADER_ADDR + COMMON_DATA_HEADER_SIZE) & 0xFF == 0

# Song constants

N_MUSIC_CHANNELS: Final = 6

MAX_N_SUBROUTINES: Final = 128

# MUST match `audio/src/common_memmap.wiz`
# MUST match `SongHeader` in `audio/src/audio-driver.wiz`
SONG_HEADER_SIZE = N_MUSIC_CHANNELS * 4 + 13


# S-DSP constants

FIR_FILTER_SIZE: Final = 8
IDENITIY_FIR_FILTER: Final = b"\x7f\x00\x00\x00\x00\x00\x00\x00"


# milliseconds of echo buffer per EDL value
ECHO_BUFFER_EDL_MS: Final = 16

# Bytes per EDL value
ECHO_BUFFER_EDL_SIZE: Final = 2048

ECHO_BUFFER_MAX_EDL: Final = 15
