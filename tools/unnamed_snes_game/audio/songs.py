# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from typing import Final, Optional

from .driver_constants import SONG_HEADER_SIZE, N_MUSIC_CHANNELS, MAX_N_SUBROUTINES, MIN_TICK_TIMER
from .mml_compiler import MmlData


def song_header(tick_clock: int, channels: list[bytes], loop_points: list[Optional[int]], subroutines: list[bytes]) -> bytes:
    if len(channels) == 0:
        raise RuntimeError("No music channels")

    if len(channels) > N_MUSIC_CHANNELS:
        raise RuntimeError("Too many music channels")

    if len(subroutines) > MAX_N_SUBROUTINES:
        raise RuntimeError("Too many subroutines")

    if tick_clock < MIN_TICK_TIMER or tick_clock > 0xFF:
        raise RuntimeError(f"Tick clock out of bounds (got {tick_clock}, min: {MIN_TICK_TIMER}, max: {0xff})")

    assert len(channels) == len(loop_points)

    out = bytearray()

    data_offset = SONG_HEADER_SIZE + len(subroutines) * 2

    for i in range(N_MUSIC_CHANNELS):
        c_size = None
        if i < len(channels):
            c_size = len(channels[i])
            if c_size <= 0:
                c_size = None

        c_loop = None
        if i < len(loop_points):
            c_loop = loop_points[i]
        if (c_loop is None) or (not c_size) or (c_loop >= c_size):
            c_loop = 0xFFFF

        if c_size:
            out.append(data_offset & 0xFF)
            out.append(data_offset >> 8)
            data_offset += c_size
        else:
            out.append(0xFF)
            out.append(0xFF)

        out.append(c_loop & 0xFF)
        out.append(c_loop >> 8)

    out.append(tick_clock)

    out.append(len(subroutines))

    assert len(out) == SONG_HEADER_SIZE

    # Subroutine table
    for s in subroutines:
        assert s

        out.append(data_offset & 0xFF)
        out.append(data_offset >> 8)

        data_offset += len(s)

    return out


def mml_data_to_song_data(mml_data: MmlData) -> bytes:
    channels = [c.bytecode for c in mml_data.channels]
    loop_points: list[Optional[int]] = [None for c in mml_data.channels]
    subroutines = [c.bytecode for c in mml_data.channels]

    # ::TODO set tick timer::
    out = song_header(MIN_TICK_TIMER, channels, loop_points, subroutines)

    for s in mml_data.channels:
        out += s.bytecode

    for s in mml_data.subroutines:
        out += s.bytecode

    return out
