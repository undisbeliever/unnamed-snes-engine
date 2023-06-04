# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from collections import OrderedDict

from typing import Final, Optional

from .driver_constants import (
    SONG_HEADER_SIZE,
    N_MUSIC_CHANNELS,
    MAX_N_SUBROUTINES,
    MIN_TICK_TIMER,
    MAX_TICK_TIMER,
    SFX_TICK_TIMER,
    IDENITIY_FIR_FILTER,
    FIR_FILTER_SIZE,
    ECHO_BUFFER_MAX_EDL,
)
from .mml_compiler import MmlData, MetaData, ChannelData
from .bytecode import cast_i8


def song_header(mml_data: MmlData) -> bytes:
    md: Final = mml_data.metadata

    if len(mml_data.channels) == 0:
        raise RuntimeError("No music channels")

    if len(mml_data.channels) > N_MUSIC_CHANNELS:
        raise RuntimeError("Too many music channels")

    if len(mml_data.subroutines) > MAX_N_SUBROUTINES:
        raise RuntimeError("Too many subroutines")

    tick_clock: Final = mml_data.metadata.tick_timer
    if tick_clock < MIN_TICK_TIMER or tick_clock > MAX_TICK_TIMER:
        raise RuntimeError(f"Tick clock out of bounds (got {tick_clock}, min: {MIN_TICK_TIMER}, max: {MAX_TICK_TIMER})")

    out = bytearray()

    data_offset = SONG_HEADER_SIZE + len(mml_data.subroutines) * 2

    for i in range(N_MUSIC_CHANNELS):
        if i < len(mml_data.channels):
            channel = mml_data.channels[i]

            c_size = len(channel.bytecode)

            if channel.loop_point is not None:
                if channel.loop_point < 0 or channel.loop_point >= c_size:
                    raise ValueError("Invalid channel loop point")
                c_loop = data_offset + channel.loop_point
            else:
                c_loop = 0xFFFF

            # starting offset
            out.append(data_offset & 0xFF)
            out.append(data_offset >> 8)

            # loop offset
            out.append(c_loop & 0xFF)
            out.append(c_loop >> 8)

            data_offset += c_size
        else:
            # starting offset
            out.append(0xFF)
            out.append(0xFF)
            # loop offset
            out.append(0xFF)
            out.append(0xFF)

    #
    # EchoBufferSettings

    if md.echo_edl < 0 or md.echo_edl > ECHO_BUFFER_MAX_EDL:
        raise RuntimeError("Echo buffer EDL is out of bounds")

    if len(md.echo_fir) != FIR_FILTER_SIZE:
        raise RuntimeError("Invalid FIR filter")

    out.append(md.echo_edl)
    out += md.echo_fir
    out.append(cast_i8(md.echo_feedback))
    out.append(cast_i8(md.echo_volume))

    out.append(tick_clock)

    out.append(len(mml_data.subroutines))

    assert len(out) == SONG_HEADER_SIZE

    # Subroutine table
    for s in mml_data.subroutines:
        out.append(data_offset & 0xFF)
        out.append(data_offset >> 8)

        data_offset += len(s.bytecode)

    return out


def mml_data_to_song_data(mml_data: MmlData) -> bytes:
    out = song_header(mml_data)

    for s in mml_data.channels:
        out += s.bytecode

    for s in mml_data.subroutines:
        out += s.bytecode

    return out


def dummy_sfx_song_header() -> bytes:
    "A dummy song header that has a single non-looping music channel to test sound effects with"

    return song_header(
        MmlData(
            metadata=MetaData(
                title=None,
                date=None,
                composer=None,
                author=None,
                copyright=None,
                license=None,
                echo_edl=0,
                echo_fir=IDENITIY_FIR_FILTER,
                echo_feedback=0,
                echo_volume=0,
                tick_timer=SFX_TICK_TIMER,
                zenlen=96,
            ),
            instruments=OrderedDict(),
            subroutines=[],
            channels=[
                ChannelData(
                    name="A",
                    bytecode=b"\0xff",
                    loop_point=None,
                    tick_counter=0,
                    max_nested_loops=0,
                    last_instrument=None,
                )
            ],
        )
    )
