# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import re
import math
from dataclasses import dataclass
from collections import OrderedDict
from typing import Any, Callable, Final, Optional

from .driver_constants import KEY_OFF_DELAY
from .json_formats import SamplesJson, Name, Instrument, NAME_REGEX


# Opcode values MUST MATCH `src/bytecode.wiz`
SET_INSTRUMENT: Final = 0
SET_CHANNEL_VOLUME: Final = 1
REST: Final = 2
CALL_SUBROUTINE: Final = 3
END_LOOP_0: Final = 4
END_LOOP_1: Final = 5
SET_SEMITONE_OFFSET: Final = 6
RELATIVE_SEMITONE_OFFSET: Final = 7
SET_ADSR: Final = 8
SET_GAIN: Final = 9
PLAY_SPECIFIC_NOTE: Final = 10
PLAY_SPECIFIC_NOTE_SLUR_NEXT: Final = 11

DISABLE_CHANNEL: Final = 12
END: Final = 13
RETURN_FROM_SUBROUTINE: Final = 14
START_LOOP_0: Final = 15
START_LOOP_1: Final = 16

PLAY_NOTE: Final = 1 << 5
PLAY_NOTE_SLUR_NEXT: Final = 2 << 5
CHANGE_OCTAVE: Final = 3 << 5


MAX_N_LOOPS: Final = 2

assert START_LOOP_1 == START_LOOP_0 + 1
assert END_LOOP_1 == END_LOOP_0 + 1


@dataclass
class BcMappings:
    instruments: dict[Name, int]
    subroutines: dict[Name, int]
    minimum_note_length: int


def _instrument_mapping(instruments: list[Instrument]) -> OrderedDict[Name, int]:
    out = OrderedDict()
    for i, inst in enumerate(instruments):
        out[inst.name] = i
    return out


def create_bc_mappings(samples: SamplesJson, tempo: int) -> BcMappings:
    return BcMappings(
        instruments=_instrument_mapping(samples.instruments),
        subroutines={},
        minimum_note_length=math.ceil((KEY_OFF_DELAY + 1) / tempo),
    )


class BytecodeError(Exception):
    pass


def cast_i8(i: int) -> int:
    """Cast an i8 to a u8 with boundary checking."""
    if i < -128 or i > 127:
        raise BytecodeError(f"integer cannot be represented by an i8: {i}")
    return i if i >= 0 else 0x100 + i


def no_argument(s: str) -> tuple[()]:
    if s:
        raise ValueError(f"Instruction has no argument")
    return ()


def name_argument(s: str) -> tuple[Name]:
    if NAME_REGEX.match(s):
        return (s,)
    else:
        raise ValueError(f"Expected a name: {s}")


def integer_argument(s: str) -> tuple[int]:
    return (int(s, 0),)


def adsr_argument(s: str) -> tuple[int, int, int, int]:
    if "," in s:
        args = s.split(",")
    else:
        args = s.split(" ")

    if len(args) != 4:
        raise ValueError(f"ADSR instruction requires 4 arguments")

    return tuple(int(a.strip()) for a in args)  # type: ignore


def optional_integer_argument(s: str) -> tuple[Optional[int]]:
    if s:
        return (int(s, 0),)
    return (None,)


NOTE_MAP: Final = {
    "c": 0,
    "d": 2,
    "e": 4,
    "f": 5,
    "g": 7,
    "a": 9,
    "b": 11,
}


def note_argument(s: str) -> tuple[int, Optional[int]]:
    if "," in s:
        note, _sep, length = s.partition(",")
        note = note.strip()
        length = length.strip()
    elif " " in s:
        note, _sep, length = s.partition(" ")
        length = length.strip()
    else:
        note = s
        length = None

    if not note:
        raise ValueError("Cannot parse note: Missing argument")

    decoded_note = NOTE_MAP.get(note[0].lower())
    if decoded_note is not None:
        for c in note[1:]:
            if c == "-":
                decoded_note -= 1
            elif c == "+":
                decoded_note += 1
            else:
                raise ValueError("Cannot parse note: Expected sharp (+) or flat(-)")
    else:
        try:
            decoded_note = int(note, 0)
        except ValueError as e:
            raise ValueError("Cannot parse note: Expected note (a-g, followed by + or -) or an integer note id (0-15)")

    if length:
        decoded_length: Optional[int] = int(length, 0)
    if not length:
        decoded_length = None

    return decoded_note, decoded_length


SPECIFIC_NOTE_REGEX: Final = re.compile(r"([a-fA-F])([\-+]*)([0-8])$")


def specific_note_argument(s: str) -> tuple[int, Optional[int]]:
    if "," in s:
        note, _sep, length = s.partition(",")
        note = note.strip()
        length = length.strip()
    elif " " in s:
        note, _sep, length = s.partition(" ")
        length = length.strip()
    else:
        note = s
        length = None

    if not note:
        raise ValueError("Cannot parse note: Missing argument")

    m = SPECIFIC_NOTE_REGEX.match(note)
    if m:
        decoded_note = NOTE_MAP.get(m.group(1).lower())
        if decoded_note is None:
            raise ValueError(f"Cannot parse note: Unknown note {m.group(1)}")

        for c in m.group(2):
            if c == "-":
                decoded_note -= 1
            elif c == "+":
                decoded_note += 1
            else:
                raise ValueError("Cannot parse note: Expected sharp (+) or flat(-)")

        octave = int(m.group(3))
        decoded_note += octave * 12
    else:
        try:
            decoded_note = int(note, 0)
        except ValueError as e:
            raise ValueError("Cannot parse note: Expected note (a-g, followed by + or -, then 0-8) or an integer note id (0-15)")

    if length:
        decoded_length: Optional[int] = int(length, 0)
    if not length:
        decoded_length = None

    return decoded_note, decoded_length


def change_octave_argument(s: str) -> tuple[bool, int]:
    if s[0] == "-":
        return True, -int(s[1:], 0)
    elif s[0] == "+":
        return True, +int(s[1:], 0)
    else:
        return False, int(s, 0)


def _instruction(argument_parser: Callable[[str], Any]) -> Callable[..., Callable[..., None]]:
    def decorator(f: Callable[..., None]) -> Callable[..., None]:
        f.__instruction_argument_parser = argument_parser  # type: ignore
        return f

    return decorator


def __bytecode_class(cls: type["Bytecode"]) -> type:
    instructions = dict()

    for field_name, field in cls.__dict__.items():
        if hasattr(field, "__instruction_argument_parser"):
            name_argument(field_name)
            instructions[field_name] = field.__instruction_argument_parser, field

    cls.instructions = instructions

    return cls


@__bytecode_class
class Bytecode:
    # Populated by the __bytecode_class decorator
    instructions: dict[str, tuple[Callable[[str], Any], Callable[..., None]]]

    def __init__(self, mappings: BcMappings, is_subroutine: bool) -> None:
        self.mappings: Final = mappings
        self.is_subroutine: Final = is_subroutine
        self.bytecode = bytearray()
        self.n_nested_loops = 0

    # NOTE: line must not contain any comments
    def parse_line(self, line: str) -> None:
        instruction, _sep, argument = line.partition(" ")
        argument = argument.strip()

        arg_parser_and_inst = Bytecode.instructions.get(instruction)
        if arg_parser_and_inst is None:
            raise BytecodeError(f"Unknown instruction: { instruction }")
        arg_parser, inst = arg_parser_and_inst
        inst(self, *arg_parser(argument))

    @_instruction(name_argument)
    def set_instrument(self, name: Name) -> None:
        instrument_id = self.mappings.instruments.get(name)
        if instrument_id is None:
            raise BytecodeError(f"Unknown instrument: {name}")
        self.bytecode.append(SET_INSTRUMENT)
        self.bytecode.append(instrument_id)

    @_instruction(integer_argument)
    def set_channel_volume(self, v: int) -> None:
        if v < 0 or v > 127:
            raise BytecodeError(f"Volume out of range")
        self.bytecode.append(SET_CHANNEL_VOLUME)
        self.bytecode.append(v)

    @_instruction(integer_argument)
    def rest(self, beats: int) -> None:
        self.bytecode.append(REST)
        self.bytecode.append(beats)

    @_instruction(no_argument)
    def start_loop(self) -> None:
        if self.n_nested_loops >= MAX_N_LOOPS:
            raise BytecodeError(f"Too many loops.  The maximum number of nested loops is { MAX_N_LOOPS}.")
        opcode = START_LOOP_0 + self.n_nested_loops
        self.n_nested_loops += 1
        self.bytecode.append(opcode)

    @_instruction(integer_argument)
    def end_loop(self, loop_count: int) -> None:
        if loop_count < 2:
            raise BytecodeError("Loop count is too low (minimum is 2)")
        if loop_count > 257:
            raise BytecodeError("Loop count is too high (maximum is 257)")
        if self.n_nested_loops == 0:
            raise BytecodeError("There is no loop to end")
        self.n_nested_loops -= 1
        assert self.n_nested_loops >= 0
        self.bytecode.append(END_LOOP_0 + self.n_nested_loops)
        self.bytecode.append(loop_count - 2)

    @_instruction(integer_argument)
    def set_semitone_offset(self, offset: int) -> None:
        self.bytecode.append(SET_SEMITONE_OFFSET)
        self.bytecode.append(cast_i8(offset))

    @_instruction(integer_argument)
    def relative_semitone_offset(self, offset: int) -> None:
        self.bytecode.append(RELATIVE_SEMITONE_OFFSET)
        self.bytecode.append(cast_i8(offset))

    @_instruction(adsr_argument)
    def set_adsr(self, a: int, d: int, sl: int, sr: int) -> None:
        if a & 0b1111 != a:
            raise BytecodeError("Invalid ADSR attack value")
        if d & 0b111 != d:
            raise BytecodeError("Invalid ADSR decay value")
        if sl & 0b111 != sl:
            raise BytecodeError("Invalid ADSR sustain level value")
        if sr & 0b11111 != sr:
            raise BytecodeError("Invalid ADSR sustain rate value")

        self.bytecode.append(SET_ADSR)
        self.bytecode.append((1 << 7) | (d << 4) | (a))
        self.bytecode.append((sl << 5) | (sr))

    # ::TODO parse gain (after I figure out what it does)::
    @_instruction(integer_argument)
    def set_gain(self, gain: int) -> None:
        if gain < 0 or gain > 0xFF:
            raise BytecodeError("Invalid GAIN value")
        self.bytecode.append(SET_GAIN)
        self.bytecode.append(gain)

    @_instruction(no_argument)
    def disable_channel(self) -> None:
        self.bytecode.append(DISABLE_CHANNEL)

    @_instruction(no_argument)
    def end(self) -> None:
        self.bytecode.append(END)

    @_instruction(note_argument)
    def play_note(self, note_id: int, length: Optional[int] = None) -> None:
        self._play_note(note_id, length, PLAY_NOTE)

    @_instruction(note_argument)
    def play_note_slur_next(self, note_id: int, length: Optional[int] = None) -> None:
        self._play_note(note_id, length, PLAY_NOTE_SLUR_NEXT)

    def _play_note(self, note_id: int, length: Optional[int], opcode: int) -> None:
        if note_id < 0 or note_id > 15:
            raise BytecodeError("note is out of range")
        if length is not None:
            if length < self.mappings.minimum_note_length:
                raise BytecodeError("Note length is too short")
            if length > 255:
                raise BytecodeError("Note length is too long")
            self.bytecode.append(opcode | (note_id << 1) | 1)
            self.bytecode.append(length)
        else:
            self.bytecode.append(opcode | (note_id << 1))

    @_instruction(specific_note_argument)
    def play_specific_note(self, note_id: int, length: Optional[int] = None) -> None:
        self._specific_note(note_id, length, PLAY_SPECIFIC_NOTE)

    @_instruction(specific_note_argument)
    def play_specific_note_slur_next(self, note_id: int, length: Optional[int] = None) -> None:
        self._specific_note(note_id, length, PLAY_SPECIFIC_NOTE_SLUR_NEXT)

    def _specific_note(self, note_id: int, length: Optional[int], opcode: int) -> None:
        if note_id < 0 or note_id > 127:
            raise BytecodeError("note is out of range")
        if length is not None:
            if length < self.mappings.minimum_note_length:
                raise BytecodeError("Note length is too short")
            if length > 255:
                raise BytecodeError("Note length is too long")
            self.bytecode.append(opcode)
            self.bytecode.append((note_id << 1) | 1)
            self.bytecode.append(length)
        else:
            self.bytecode.append(opcode)
            self.bytecode.append(note_id << 1)

    @_instruction(change_octave_argument)
    def change_octave(self, relative_change: bool, octave: int) -> None:
        if octave < -6 or octave > 9:
            raise BytecodeError("Octave is out of range (range is -6 .. 9 inclusive)")
        opcode = CHANGE_OCTAVE | ((octave + 6) << 1) | bool(relative_change)
        self.bytecode.append(opcode)

    @_instruction(optional_integer_argument)
    def increment_octave(self, n_steps: Optional[int] = None) -> None:
        if n_steps is not None:
            self.change_octave(True, n_steps)
        else:
            self.change_octave(True, 1)

    @_instruction(optional_integer_argument)
    def decrement_octave(self, n_steps: Optional[int] = None) -> None:
        if n_steps is not None:
            self.change_octave(True, -n_steps)
        else:
            self.change_octave(True, -1)

    @_instruction(name_argument)
    def call_subroutine(self, name: Name) -> None:
        if self.is_subroutine:
            raise BytecodeError("Cannot call a subroutine in a subroutine")
        subroutine_id = self.mappings.subroutines.get(name)
        if subroutine_id is None:
            raise BytecodeError(f"Unknown subroutine: {name}")
        assert subroutine_id < 128
        self.bytecode.append(CALL_SUBROUTINE)
        self.bytecode.append(subroutine_id)

    @_instruction(no_argument)
    def return_from_subroutine(self) -> None:
        if not self.is_subroutine:
            raise BytecodeError("Not a subroutine")
        self.bytecode.append(RETURN_FROM_SUBROUTINE)
