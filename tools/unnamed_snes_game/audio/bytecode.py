# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import re
import math
from dataclasses import dataclass
from collections import OrderedDict
from typing import Any, Callable, Final, Optional

from .json_formats import SamplesJson, Name, Instrument, NAME_REGEX

MAX_PAN: Final = 128

N_OCTAVES: Final = 8
N_NOTES: Final = N_OCTAVES * 12

# Opcode values MUST MATCH `src/bytecode.wiz`
DISABLE_CHANNEL: Final = 0xFE

SET_INSTRUMENT: Final = 0xC0
REST: Final = 0xC2
REST_KEYOFF: Final = 0xC4
CALL_SUBROUTINE: Final = 0xC6
END_LOOP_0: Final = 0xC8
END_LOOP_1: Final = 0xCA
SET_ADSR: Final = 0xCC
SET_GAIN: Final = 0xCE

SET_VOLUME: Final = 0xD0
SET_PAN: Final = 0xD2
SET_PAN_AND_VOLUME: Final = 0xD4

END: Final = 0xD6
RETURN_FROM_SUBROUTINE: Final = 0xD8
START_LOOP_0: Final = 0xDA
START_LOOP_1: Final = 0xDC


assert SET_INSTRUMENT == N_NOTES * 2

MAX_N_LOOPS: Final = 2

assert START_LOOP_1 == START_LOOP_0 + 2
assert END_LOOP_1 == END_LOOP_0 + 2


# Number of ticks between key-off and the next instruction
KEY_OFF_TICK_DELAY = 1


@dataclass
class BcMappings:
    instruments: dict[Name, int]
    subroutines: dict[Name, int]


def _instrument_mapping(instruments: list[Instrument]) -> OrderedDict[Name, int]:
    out = OrderedDict()
    for i, inst in enumerate(instruments):
        out[inst.name] = i
    return out


def create_bc_mappings(samples: SamplesJson) -> BcMappings:
    return BcMappings(
        instruments=_instrument_mapping(samples.instruments),
        subroutines={},
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


def two_integer_arguments(s: str) -> tuple[int, int]:
    if "," in s:
        args = s.split(",")
    else:
        args = s.split(" ")

    if len(args) != 2:
        raise ValueError(f"Instruction requires 2 arguments")

    return int(args[0], 0), int(args[1], 0)


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
SPECIFIC_NOTE_REGEX: Final = re.compile(r"([a-gA-G])([\-+]*)([0-8])$")
KEY_OFF_ARGS: Final = ("keyoff",)
NO_KEY_OFF_ARGS: Final = ("no_keyoff", "nko", "slur_next", "sn")


def play_note_argument(s: str) -> tuple[int, bool, int]:
    if "," in s:
        args = s.split(",")
    else:
        args = s.split()

    if not args:
        raise ValueError("Missing argument")
    if len(args) > 3:
        raise ValueError("Too many arguments")

    note: Final = args.pop(0).strip()

    m: Final = SPECIFIC_NOTE_REGEX.match(note)
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
            raise ValueError(
                f"Cannot parse note: Expected note (a-g, followed by + or -, then 0-{N_OCTAVES-1}) or an integer note id (0-{N_NOTES-1})"
            )

    length = None
    key_off = None

    for a in args:
        a = a.strip()
        if a:
            if a in KEY_OFF_ARGS and key_off is None:
                key_off = True
            elif a in NO_KEY_OFF_ARGS and key_off is None:
                key_off = False
            elif length is None:
                length = int(a, 0)
            else:
                raise ValueError(f"Unknown argument: {a}")

    if key_off is None:
        key_off = True

    if length is None:
        raise ValueError("Missing note length")

    return decoded_note, key_off, length


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

    @_instruction(play_note_argument)
    def play_note(self, note_id: int, key_off: bool, length: int) -> None:
        if note_id < 0 or note_id > N_NOTES:
            raise BytecodeError("note is out of range")
        length = test_length_argument(length)

        self.bytecode.append((note_id << 1) | (key_off & 1))
        self.bytecode.append(length)

    @_instruction(name_argument)
    def set_instrument(self, name: Name) -> None:
        instrument_id = self.mappings.instruments.get(name)
        if instrument_id is None:
            raise BytecodeError(f"Unknown instrument: {name}")
        self.bytecode.append(SET_INSTRUMENT)
        self.bytecode.append(instrument_id)

    @_instruction(integer_argument)
    def rest(self, length: int) -> None:
        length = test_length_argument(length)
        self.bytecode.append(REST)
        self.bytecode.append(length)

    @_instruction(integer_argument)
    def rest_keyoff(self, length: int) -> None:
        length = test_length_argument(length - KEY_OFF_TICK_DELAY)
        self.bytecode.append(REST_KEYOFF)
        self.bytecode.append(length)

    @_instruction(no_argument)
    def start_loop(self) -> None:
        if self.n_nested_loops >= MAX_N_LOOPS:
            raise BytecodeError(f"Too many loops.  The maximum number of nested loops is { MAX_N_LOOPS}.")
        opcode = START_LOOP_0 + self.n_nested_loops * 2
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

        opcode = END_LOOP_0 + self.n_nested_loops * 2

        self.bytecode.append(opcode)
        self.bytecode.append(loop_count - 2)

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

    @_instruction(integer_argument)
    def set_volume(self, v: int) -> None:
        if v < 0 or v > 255:
            raise BytecodeError(f"Volume out of range")
        self.bytecode.append(SET_VOLUME)
        self.bytecode.append(v)

    @_instruction(integer_argument)
    def set_pan(self, p: int) -> None:
        if p < 0 or p > MAX_PAN:
            raise BytecodeError(f"Pan out of range (0 - {MAX_PAN})")
        self.bytecode.append(SET_PAN)
        self.bytecode.append(p)

    @_instruction(two_integer_arguments)
    def set_pan_and_volume(self, p: int, v: int) -> None:
        if p < 0 or p > MAX_PAN:
            raise BytecodeError(f"Pan out of range (0 - {MAX_PAN})")
        if v < 0 or v > 255:
            raise BytecodeError(f"Volume out of range")
        self.bytecode.append(SET_PAN_AND_VOLUME)
        self.bytecode.append(p)
        self.bytecode.append(v)

    @_instruction(no_argument)
    def disable_channel(self) -> None:
        self.bytecode.append(DISABLE_CHANNEL)

    @_instruction(no_argument)
    def end(self) -> None:
        self.bytecode.append(END)

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


def test_length_argument(length: int) -> int:
    if length <= 0:
        raise BytecodeError("Note length is too short")
    if length > 0x100:
        raise BytecodeError("Note length is too long")
    return length & 0xFF
