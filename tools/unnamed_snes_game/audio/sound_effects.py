# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import re
from typing import Final, Optional, Sequence

from .driver_constants import MAX_SOUND_EFFECTS, Addr
from .bytecode import BcMappings, Bytecode, create_bc_mappings
from .json_formats import parse_name, SamplesJson
from ..json_formats import Filename, Name, Mappings


END_INSTRUCTION: Final = "disable_channel"

SOUND_EFFECT_SEPARATOR_REGEX = re.compile(r"^==+ *([a-zA-Z][a-zA-Z0-9_]*) *==+")


def compile_sound_effect(sfx: str, samples_input: SamplesJson) -> bytes:
    errors = list()
    previous_line = ""

    bc_mappings = create_bc_mappings(samples_input)

    bc = Bytecode(bc_mappings, is_subroutine=False, is_sound_effect=True)

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

    return bc.get_bytecode()


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

                    if current_bc:
                        assert current_sfx
                        sound_effects[current_sfx] = current_bc.get_bytecode()

                    current_sfx = m.group(1)
                    current_bc = Bytecode(bcMappings, is_subroutine=False, is_sound_effect=True)
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

    if current_bc:
        assert current_sfx
        sound_effects[current_sfx] = current_bc.get_bytecode()

    if errors:
        raise RuntimeError(f"{len(errors)} errors compiling sound effects\n    " + "\n    ".join(errors))

    return sound_effects


def build_sound_effects(
    lines: Sequence[str], filename: str, samples_input: SamplesJson, mappings: Mappings
) -> tuple[bytes, list[int]]:
    """Returns (data, offsets)"""

    bc_mappings: Final = create_bc_mappings(samples_input)
    sfx: Final = compile_sound_effects_file(lines, filename, bc_mappings)

    missing_sfx: list[str] = list()

    data = bytearray()
    offsets = list()

    if len(mappings.sound_effects) > MAX_SOUND_EFFECTS:
        raise RuntimeError(f"Too many sound effects: {len(mappings.sound_effects)}, max is {MAX_SOUND_EFFECTS}")

    for sfx_name in mappings.sound_effects:
        sfx_data = sfx.get(sfx_name)
        if sfx_data:
            offsets.append(len(data))
            data += sfx_data
        else:
            missing_sfx.append(sfx_name)

    if missing_sfx:
        raise RuntimeError(f"Missing {len(missing_sfx)} sound effects: {', '.join(missing_sfx)}")

    return data, offsets
