# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from .json_formats import GameState

from typing import Final


SIGNATURE_VERSION_SIZE: Final = 10
CHECKSUM_SIZE: Final = 2
PLAYER_POSITION_SIZE: Final = 8

HEADER_SIZE: Final = SIGNATURE_VERSION_SIZE + CHECKSUM_SIZE + PLAYER_POSITION_SIZE + 1

FLAGS_ARRAY_SIZE: Final = 256 // 8


def gamestate_data_size(gs: GameState) -> int:
    return HEADER_SIZE + 2 * FLAGS_ARRAY_SIZE + gs.u8_array_len + 2 * gs.u16_array_len


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
