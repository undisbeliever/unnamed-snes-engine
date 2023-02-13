# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from typing import Final, Optional

from .driver_constants import SONG_HEADER_SIZE, N_MUSIC_CHANNELS, TICKS_PER_SECOND


def calc_tempo(bpm: int) -> int:
    t = int((TICKS_PER_SECOND * 60 * 0x100) // (bpm * 48))
    if t < 2:
        raise ValueError("Tempo is too fast")
    if t > 0x6000:
        raise ValueError("Tempo is too slow")
    return t


def song_header(bpm: int, channels: list[bytes], loop_points: list[Optional[int]], subroutines: list[bytes]) -> bytes:
    if len(channels) == 0:
        raise RuntimeError("No music channels")

    if len(channels) > N_MUSIC_CHANNELS:
        raise RuntimeError("Too many music channels")

    if len(subroutines) > 128:
        raise RuntimeError("Too many subroutines")

    assert len(channels) == len(loop_points)

    out = bytearray()

    song_offset = SONG_HEADER_SIZE + len(subroutines) * 2

    for i in range(N_MUSIC_CHANNELS):
        c_size = None
        if i < len(channels):
            c_size = len(channels[i])
            if c_size <= 0:
                c_size = None

        c_loop = None
        if i < len(loop_points):
            c_loop = i
        if (c_loop is None) or (not c_size) or (c_loop >= c_size):
            c_loop = 0xFFFF

        if c_size:
            out.append(song_offset & 0xFF)
            out.append(song_offset >> 8)
            song_offset += c_size
        else:
            out.append(0xFF)
            out.append(0xFF)

        out.append(c_loop & 0xFF)
        out.append(c_loop >> 8)

    tempo: Final = calc_tempo(bpm)
    out.append(tempo & 0xFF)
    out.append(tempo >> 8)

    out.append(len(subroutines))

    assert len(out) == SONG_HEADER_SIZE

    # Subroutine table
    subroutine_offset = SONG_HEADER_SIZE
    for s in subroutines:
        assert s

        out.append(subroutine_offset & 0xFF)
        out.append(subroutine_offset >> 8)

        subroutine_offset += len(s)

    return out
