# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import re
from typing import Final, Optional, Sequence

from .driver_constants import N_SOUND_EFFECTS, TICKS_PER_SECOND, SFX_BPM, KEY_OFF_DELAY, Addr
from .bytecode import BcMappings, Bytecode, create_bc_mappings
from .json_formats import parse_name, SamplesJson
from ..json_formats import Filename, Name, Mappings


END_INSTRUCTION: Final = "disable_channel"

SOUND_EFFECT_SEPARATOR_REGEX = re.compile(r"^==+ *([a-zA-Z][a-zA-Z0-9_]*) *==+")


def compile_sound_effect(sfx: str, samples_input: SamplesJson) -> bytes:
    errors = list()
    previous_line = ""

    bc_mappings = create_bc_mappings(samples_input, SFX_BPM)

    bc = Bytecode(bc_mappings, False)

    for line_no, line in enumerate(sfx.split("\n")):
        try:
            line, _sep, _comment = line.partition(";")
            line = line.strip()

            if line:
                bc.parse_line(line)
                previous_line = line
        except Exception as e:
            errors.append(f"Line {line_no+1}: {e}")

    if previous_line != END_INSTRUCTION:
        errors.append(f"The sound effect must end with a `{END_INSTRUCTION}` instruction")

    if errors:
        raise RuntimeError(f"{len(errors)} errors compiling sound effects\n    " + "\n    ".join(errors))

    return bc.bytecode


def compile_sound_effects_file(lines: Sequence[str], filename: str, bcMappings: BcMappings) -> dict[Name, bytes]:
    sound_effects: dict[Name, bytes] = dict()

    current_sfx: Optional[Name] = None
    current_bc: Optional[Bytecode] = None

    prev_line = ""

    errors = list()

    def add_error(message: str) -> None:
        if current_sfx:
            errors.append(f"{filename}:{line_no+1} ({current_sfx}): {message}")
        else:
            errors.append(f"{filename}:{line_no+1}: {message}")

    for line_no, line in enumerate(lines):
        line, _sep, _comment = line.partition(";")
        line = line.strip()
        if line:
            try:
                m = SOUND_EFFECT_SEPARATOR_REGEX.match(line)
                if m is not None:
                    if current_bc and prev_line != END_INSTRUCTION:
                        add_error(f"The sound effect must end with a `(END_INSTRUCTION)` instruction")

                    current_sfx = m.group(1)
                    current_bc = Bytecode(bcMappings, False)
                    sound_effects[current_sfx] = current_bc.bytecode
                else:
                    if current_bc:
                        current_bc.parse_line(line)
                    else:
                        add_error("Cannot assign bytecode instruction to sound effect")
            except Exception as e:
                add_error(str(e))

            prev_line = line

    if current_bc and prev_line != END_INSTRUCTION:
        add_error(f"The sound effect must end with a `{END_INSTRUCTION}` instruction")

    if errors:
        raise RuntimeError(f"{len(errors)} errors compiling sound effects\n    " + "\n    ".join(errors))

    return sound_effects


def build_sfx_header_and_data(sfx: dict[Name, bytes], mappings: Mappings, starting_addr: Addr) -> tuple[bytes, bytes]:
    missing_sfx: list[str] = list()

    current_addr = starting_addr
    data = bytearray()
    addresses: list[int] = list()

    if len(mappings.sound_effects) > N_SOUND_EFFECTS:
        raise RuntimeError(f"Too many sound effects: {len(mappings.sound_effects)}, max is {N_SOUND_EFFECTS}")

    for sfx_name in mappings.sound_effects:
        sfx_data = sfx.get(sfx_name)
        if sfx_data:
            addresses.append(current_addr)
            data += sfx_data
            current_addr += len(sfx_data)
        else:
            missing_sfx.append(sfx_name)

    if missing_sfx:
        raise RuntimeError(f"Missing {len(missing_sfx)} sound effects: {', '.join(missing_sfx)}")

    if current_addr >= 0xFFFF:
        raise RuntimeError(f"Cannot fit sound effect data in Audio-RAM")

    padding = bytes(N_SOUND_EFFECTS - len(addresses))

    header = bytes(a & 0xFF for a in addresses)
    header += padding
    header += bytes(a >> 8 for a in addresses)
    header += padding

    return header, data
