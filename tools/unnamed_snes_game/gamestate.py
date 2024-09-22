# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from .json_formats import Name, GameState, GameStateVar

from typing import Final, Generator
from collections import OrderedDict


SIGNATURE_VERSION_SIZE: Final = 10
CHECKSUM_SIZE: Final = 2
GAMESTATE_DATA_VERSION = 1

# Order MUST match RoomToLoad in `engine/game/gamestate.wiz`
PLAYER_POSITION: Final[OrderedDict[Name, GameStateVar]] = OrderedDict(
    (
        ("dungeon", GameStateVar(0, None)),
        ("room_x", GameStateVar(1, None)),
        ("room_y", GameStateVar(2, None)),
        ("player_x", GameStateVar(3, None)),
        ("player_y", GameStateVar(4, None)),
        ("player_z", GameStateVar(5, None)),
        ("player_state", GameStateVar(6, None)),
        ("player_direction", GameStateVar(7, None)),
    )
)
PLAYER_POSITION_OFFSET: Final = SIGNATURE_VERSION_SIZE + CHECKSUM_SIZE + 1
PLAYER_POSITION_SIZE: Final = 8

N_FLAG_ARRAYS: Final = 2
FLAG_ARRAY_SIZE: Final = 256 // 8
FLAG_ARRAY_OFFSET: Final = PLAYER_POSITION_OFFSET + PLAYER_POSITION_SIZE

U8_VARS_OFFSET: Final = FLAG_ARRAY_OFFSET + N_FLAG_ARRAYS * FLAG_ARRAY_SIZE


def gamestate_data_size(gs: GameState) -> int:
    return PLAYER_POSITION_OFFSET + PLAYER_POSITION_SIZE + N_FLAG_ARRAYS * FLAG_ARRAY_SIZE + gs.u8_array_len + 2 * gs.u16_array_len


def validate_gamestate_cart_size(gs: GameState) -> None:
    assert 1024 <= gs.cart_ram_size <= 8192
    assert 1 <= gs.n_save_slots <= 8
    assert 2 <= gs.n_save_copies <= 8

    if gs.cart_ram_size.bit_count() != 1:
        raise ValueError("GameState cart_ram_size is not a power of 2")

    cart_save_size = gs.cart_ram_size // (gs.n_save_slots * gs.n_save_copies)
    gs_data_size = gamestate_data_size(gs)

    if cart_save_size < gs_data_size:
        raise ValueError(
            f"gamestate cannot fit in a save slot ({cart_save_size} bytes/save on cart, gamestate is {gs_data_size} bytes)"
        )


def gamestate_header(gs: GameState) -> bytes:
    return gs.identifier.encode("ASCII") + bytes(
        [
            GAMESTATE_DATA_VERSION ^ 0xFF,
            gs.version,
            N_FLAG_ARRAYS,
            gs.u8_array_len,
            gs.u16_array_len,
            0,
        ]
    )


def read_flags(gs_data: bytes, flag_array: int) -> Generator[bool, None, None]:
    if flag_array < 0 or flag_array > N_FLAG_ARRAYS:
        raise ValueError("Invalid flag_array")

    o = FLAG_ARRAY_OFFSET + flag_array * FLAG_ARRAY_SIZE

    for b in gs_data[o : o + FLAG_ARRAY_SIZE]:
        for i in range(8):
            yield b & 1 == 1
            b >>= 1
