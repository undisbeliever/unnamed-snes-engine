# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import itertools
from collections import OrderedDict
from typing import Final

from _json_formats import Name, ScopedName, EntitiesJson


ENTITY_ROM_DATA_SOA_LABELS = (
    "entity_rom_data.__init_funtions",
    "entity_rom_data.__process_funtions",
    "entity_rom_data.__metasprite_framesets",
    "entity_rom_data.__initial_zpos_and_death_function_and_is_enemy",
    "entity_rom_data.__vision_ab",
    "entity_rom_data.__health_and_attack_power",
)

ENTITY_ROM_DATA_LABEL = ENTITY_ROM_DATA_SOA_LABELS[0]

ENTITY_ROM_DATA_BYTES_PER_ENTITY = len(ENTITY_ROM_DATA_SOA_LABELS) * 2


def validate_entity_rom_data_symbols(symbols: dict[str, int], n_entities: int) -> None:
    array_size = n_entities * 2

    addr = symbols[ENTITY_ROM_DATA_LABEL]

    for label in ENTITY_ROM_DATA_SOA_LABELS:
        if symbols[label] != addr:
            raise RuntimeError(f"Incorrect address for `{ label }`.  Maybe N_ENTITY_TYPES has changed?")
        addr += array_size


def expected_blank_entity_rom_data(symbols: dict[str, int], n_entities: int) -> bytes:
    blank_init_function_addr = symbols["entities._blank_init_function"] & 0xFFFF
    blank_entity_function_addr = symbols["entities._blank_entity_function"] & 0xFFFF

    return (
        blank_init_function_addr.to_bytes(2, byteorder="little") * n_entities
        + blank_entity_function_addr.to_bytes(2, byteorder="little") * n_entities
        + b"\xaa" * (2 * (len(ENTITY_ROM_DATA_SOA_LABELS) - 2) * n_entities)
    )


def create_entity_rom_data(
    entities_input: EntitiesJson, symbols: dict[str, int], metasprite_map: dict[ScopedName, tuple[int, Name]]
) -> bytes:

    out = bytearray(2 * len(entities_input.entities) * len(ENTITY_ROM_DATA_SOA_LABELS))
    i = 0

    def write_function_addr(fname: str) -> None:
        nonlocal out, i

        addr = symbols[fname]
        if addr & 0x3F8000 != 0x008000:
            raise RuntimeError(f"Function `{ fname }` is not in the code bank")

        out[i] = addr & 0xFF
        out[i + 1] = (addr >> 8) & 0xFF
        i += 2

    entities: Final = entities_input.entities.values()
    death_functions: Final = entities_input.death_functions

    # init_functions
    for e in entities:
        write_function_addr(f"entities.{ e.code.name }.init")

    # process_functions
    for e in entities:
        # Some entities will reuse the process() function from a different entity
        ef = e.code
        if ef.uses_process_function_from:
            ns = ef.uses_process_function_from
        else:
            ns = ef.name
        write_function_addr(f"entities.{ ns }.process")

    # metasprite_framesets
    for e in entities:
        addr, ms_eo = metasprite_map[e.metasprites]

        expected_eo = e.code.ms_export_order
        if ms_eo != expected_eo:
            raise RuntimeError(f"Entity `{ e.name }` has the wrong ms_export_order: expected { expected_eo }")

        out[i] = addr & 0xFF
        out[i + 1] = (addr >> 8) & 0xFF
        i += 2

    # initial_zpos_and_death function and is enemy
    if len(death_functions) >= 128:
        raise RuntimeError("Too many death functions")
    for e in entities:
        try:
            death_function_id = death_functions.index(e.death_function)
        except ValueError:
            raise ValueError(f"Unknown death function for {e.name}: {e.death_function}")

        if e.code.is_enemy:
            death_function_id |= 0x80

        out[i] = e.zpos
        out[i + 1] = death_function_id
        i += 2

    # vision_ab
    for e in entities:
        if e.vision:
            out[i] = e.vision.a
            out[i + 1] = e.vision.b
        else:
            out[i] = 0xFF
            out[i + 1] = 0xFF
        i += 2

    # health_and_attack_power
    for e in entities:
        out[i] = e.health
        out[i + 1] = e.attack
        i += 2

    assert i == len(out)

    return out
