# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import re
import math
from dataclasses import dataclass
from collections import OrderedDict
from typing import Any, Callable, Final, NamedTuple, Optional, Union

from .json_formats import SamplesJson, Name, Instrument, NAME_REGEX
from .driver_constants import MAX_N_SUBROUTINES, MIN_TICK_TIMER

MAX_VOLUME: Final = 255
MAX_PAN: Final = 128

N_OCTAVES: Final = 8
N_NOTES: Final = N_OCTAVES * 12

MAX_LOOP_COUNT: Final = 256
MAX_VIBRATO_QUARTER_WAVELENGTH_TICKS: Final = 0x100 // 4


# Opcode values MUST MATCH `src/bytecode.wiz`

PORTAMENTO_DOWN: Final = 0xC0
PORTAMENTO_UP: Final = 0xC2

SET_VIBRATO: Final = 0xC4
SET_VIBRATO_DEPTH_AND_PLAY_NOTE: Final = 0xC6

REST: Final = 0xC8
REST_KEYOFF: Final = 0xCA
CALL_SUBROUTINE: Final = 0xCC

START_LOOP_0: Final = 0xCE
START_LOOP_1: Final = 0xD0
START_LOOP_2: Final = 0xD2
SKIP_LAST_LOOP_0: Final = 0xD4
SKIP_LAST_LOOP_1: Final = 0xD6
SKIP_LAST_LOOP_2: Final = 0xD8

SET_INSTRUMENT: Final = 0xDA
SET_INSTRUMENT_AND_ADSR_OR_GAIN: Final = 0xDC
SET_ADSR: Final = 0xDE
SET_GAIN: Final = 0xE0

ADJUST_PAN: Final = 0xE2
SET_PAN: Final = 0xE4
SET_PAN_AND_VOLUME: Final = 0xE6
ADJUST_VOLUME: Final = 0xE8
SET_VOLUME: Final = 0xEA

SET_SONG_TICK_CLOCK = 0xEC

END: Final = 0xEE
RETURN_FROM_SUBROUTINE: Final = 0xF0
END_LOOP_0: Final = 0xF2
END_LOOP_1: Final = 0xF4
END_LOOP_2: Final = 0xF6

ENABLE_ECHO: Final = 0xF8
DISABLE_ECHO: Final = 0xFA

DISABLE_CHANNEL: Final = 0xFE


assert PORTAMENTO_DOWN == N_NOTES * 2

MAX_NESTED_LOOPS: Final = 3

assert START_LOOP_1 == START_LOOP_0 + 2
assert SKIP_LAST_LOOP_1 == SKIP_LAST_LOOP_0 + 2
assert END_LOOP_1 == END_LOOP_0 + 2

assert START_LOOP_2 == START_LOOP_1 + 2
assert SKIP_LAST_LOOP_2 == SKIP_LAST_LOOP_1 + 2
assert END_LOOP_2 == END_LOOP_1 + 2


# Number of ticks between key-off and the next instruction
KEY_OFF_TICK_DELAY = 1


I8_MIN: Final = -128
I8_MAX: Final = 127


def cast_i8(i: int) -> int:
    "Cast an i8 to a u8 with boundary checking."
    if i < -128 or i > 127:
        raise ValueError(f"i8 integer out of bounds: {i}")
    if i < 0:
        return i + 0x100
    return i


class BcSubroutine(NamedTuple):
    name: Name
    subroutine_id: int


@dataclass
class BcMappings:
    instruments: dict[Name, int]
    subroutines: dict[Name, BcSubroutine]


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


class Adsr(NamedTuple):
    adsr1: int
    adsr2: int


def parse_adsr(args: list[str]) -> Adsr:
    if len(args) != 4:
        raise ValueError(f"ADSR requires 4 integers")

    a = int(args[0])
    d = int(args[1])
    sl = int(args[2])
    sr = int(args[3])

    errors = list()

    if a & 0b1111 != a:
        errors.append("attack")
    if d & 0b111 != d:
        errors.append("decay")
    if sl & 0b111 != sl:
        errors.append("sustain level")
    if sr & 0b11111 != sr:
        errors.append("sustain rate")

    if errors:
        if len(errors) == 1:
            raise ValueError(f"Invalid ADSR {errors[0]} value")
        else:
            raise ValueError(f"Invalid ADSR {', '.join(errors)} values")

    return Adsr(
        ((1 << 7) | (d << 4) | (a)),
        ((sl << 5) | (sr)),
    )


def split_arguments(s: str, expected_n_arguments: int) -> list[str]:
    if "," in s:
        args = [a.strip() for a in s.split(",")]
    else:
        args = s.split()

    if len(args) != expected_n_arguments:
        raise ValueError(f"Instruction requires {expected_n_arguments} arguments")

    return args


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
    args = split_arguments(s, 2)

    return int(args[0], 0), int(args[1], 0)


def adsr_argument(s: str) -> tuple[Adsr]:
    args = split_arguments(s, 4)

    return (parse_adsr(args),)


def name_and_adsr_arguments(s: str) -> tuple[Name, Adsr]:
    args = split_arguments(s, 5)

    name = args.pop(0)
    if not NAME_REGEX.match(name):
        raise ValueError(f"Expected a name: {name}")

    return name, parse_adsr(args)


def name_and_integer_arguments(s: str) -> tuple[Name, int]:
    args = split_arguments(s, 2)

    name = args[0]
    if not NAME_REGEX.match(name):
        raise ValueError(f"Expected a name: {name}")

    return name, int(args[1])


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


def parse_note(note: str) -> int:
    m: Final = SPECIFIC_NOTE_REGEX.match(note)
    if m:
        n = NOTE_MAP.get(m.group(1).lower())
        if n is None:
            raise ValueError(f"Cannot parse note: Unknown note {m.group(1)}")

        for c in m.group(2):
            if c == "-":
                n -= 1
            elif c == "+":
                n += 1
            else:
                raise ValueError("Cannot parse note: Expected sharp (+) or flat(-)")

        octave = int(m.group(3))

        return n + octave * 12
    else:
        try:
            return int(note, 0)
        except ValueError as e:
            raise ValueError(
                f"Cannot parse note: Expected note (a-g, followed by + or -, then 0-{N_OCTAVES-1}) or an integer note id (0-{N_NOTES-1})"
            )


def _parse_play_note_arguments(args: list[str]) -> tuple[int, bool, int]:
    if not args:
        raise ValueError("Missing play note argument")
    if len(args) > 3:
        raise ValueError("Too many arguments")

    note: Final = args.pop(0).strip()
    decoded_note: Final = parse_note(note)

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


def play_note_argument(s: str) -> tuple[int, bool, int]:
    if "," in s:
        args = s.split(",")
    else:
        args = s.split()
    return _parse_play_note_arguments(args)


def integer_and_play_note_argument(s: str) -> tuple[int, int, bool, int]:
    if "," in s:
        args = s.split(",")
    else:
        args = s.split()

    if not args:
        raise ValueError("Missing argument")

    i = int(args.pop(0))

    return i, *_parse_play_note_arguments(args)


def portamento_argument(s: str) -> tuple[int, bool, int, int]:
    if "," in s:
        args = s.split(",")
    else:
        args = s.split()

    if len(args) != 4:
        raise ValueError("Expected 4 arguments")

    decoded_note: Final = parse_note(args[0])

    if args[1] in KEY_OFF_ARGS:
        key_off = True
    elif args[1] in NO_KEY_OFF_ARGS:
        key_off = False
    else:
        raise ValueError(f"Unknown argument: {args[1]}")

    v_direction = args[2][0]
    if v_direction != "+" and v_direction != "-":
        raise ValueError(f"portamento velocity must start with a + or -")

    velocity: Final = int(args[2], 0)
    length: Final = int(args[3], 0)

    return decoded_note, key_off, velocity, length


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

    def __init__(self, mappings: BcMappings, is_subroutine: bool, is_sound_effect: bool) -> None:
        self.mappings: Final = mappings
        self.is_subroutine: Final = is_subroutine
        self.is_sound_effect: Final = is_sound_effect
        self.bytecode = bytearray()

        # Location of the parameter of the `skip_last_loop` instruction (if any) for each loop.
        # Also used to determine the number of nested loops
        self.skip_last_loop_pos: list[Optional[int]] = list()

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

    @_instruction(portamento_argument)
    def portamento(self, note_id: int, key_off: bool, velocity: int, length: int) -> None:
        if note_id < 0 or note_id > N_NOTES:
            raise BytecodeError("note is out of range")

        speed: Final = abs(velocity)
        if speed == 0:
            raise BytecodeError("portamento velocity cannot be 0")
        if speed > 0xFF:
            raise BytecodeError(f"portamento velocity is out of range ({speed}, max: 255 per tick)")
        length = test_length_argument(length)

        if velocity < 0:
            opcode = PORTAMENTO_DOWN
        else:
            opcode = PORTAMENTO_UP

        self.bytecode.append(opcode)
        self.bytecode.append(speed)
        self.bytecode.append(length)
        self.bytecode.append((note_id << 1) | (key_off & 1))

    @_instruction(two_integer_arguments)
    def set_vibrato(self, pitch_offset_per_tick: int, quarter_wavelength_ticks: int) -> None:
        if pitch_offset_per_tick < 1 or pitch_offset_per_tick > 0xFF:
            raise BytecodeError(f"Vibrato pitch_offset_per_tick out of range ({pitch_offset_per_tick}, min: 1, max 255)")

        if quarter_wavelength_ticks < 1 or quarter_wavelength_ticks > MAX_VIBRATO_QUARTER_WAVELENGTH_TICKS:
            raise BytecodeError(
                f"Vibrato quarter_wavelength_ticks out of range ({quarter_wavelength_ticks}, min: 1, max: {MAX_VIBRATO_QUARTER_WAVELENGTH_TICKS}"
            )

        self.bytecode.append(SET_VIBRATO)
        self.bytecode.append(pitch_offset_per_tick)
        self.bytecode.append(quarter_wavelength_ticks)

    @_instruction(integer_and_play_note_argument)
    def set_vibrato_depth_and_play_note(self, pitch_offset_per_tick: int, note_id: int, key_off: bool, length: int) -> None:
        if pitch_offset_per_tick < 0 or pitch_offset_per_tick > 0xFF:
            raise BytecodeError(f"Vibrato pitch_offset_per_tick out of range ({pitch_offset_per_tick}, min: 0, max 255)")

        self.bytecode.append(SET_VIBRATO_DEPTH_AND_PLAY_NOTE)
        self.bytecode.append(pitch_offset_per_tick)
        self.play_note(note_id, key_off, length)

    @_instruction(no_argument)
    def disable_vibrato(self) -> None:
        # ::MAYDO add a disable_vibrato bytecode instruction::
        self.bytecode.append(SET_VIBRATO)
        self.bytecode.append(0)
        self.bytecode.append(0)

    def _get_instrument_id(self, instrument: Union[Name, int]) -> int:
        if isinstance(instrument, int):
            return instrument
        else:
            instrument_id = self.mappings.instruments.get(instrument)
            if instrument_id is None:
                raise BytecodeError(f"Unknown instrument: {instrument}")
            return instrument_id

    @_instruction(name_argument)
    def set_instrument(self, instrument: Union[Name, int]) -> None:
        instrument_id = self._get_instrument_id(instrument)

        self.bytecode.append(SET_INSTRUMENT)
        self.bytecode.append(instrument_id)

    @_instruction(name_and_adsr_arguments)
    def set_instrument_and_adsr(self, instrument: Union[Name, int], adsr: Adsr) -> None:
        instrument_id = self._get_instrument_id(instrument)

        self.bytecode.append(SET_INSTRUMENT_AND_ADSR_OR_GAIN)
        self.bytecode.append(instrument_id)
        self.bytecode.append(adsr.adsr1)
        self.bytecode.append(adsr.adsr2)

    # ::TODO parse gain (after I figure out what it does)::
    @_instruction(name_and_integer_arguments)
    def set_instrument_and_gain(self, instrument: Union[Name, int], gain: int) -> None:
        instrument_id = self._get_instrument_id(instrument)

        self.bytecode.append(SET_INSTRUMENT_AND_ADSR_OR_GAIN)
        self.bytecode.append(instrument_id)
        self.bytecode.append(0)
        self.bytecode.append(gain)

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

    def _loop_id(self) -> int:
        """
        Returns the current loop id.
         * For non-subroutines: loop_id starts at 0 and increases to `MAX_NESTED_LOOPS-1`
         * For subroutines:     loop_id starts at `MAX_NESTED_LOOPS-1` and decreases to 0

        When starting a new loop, this method MUST be called after `skip_last_loop_pos` is appended.
        """

        n_loops: Final = len(self.skip_last_loop_pos)

        if n_loops <= 0:
            raise BytecodeError("Not in a loop")

        if n_loops > MAX_NESTED_LOOPS:
            raise BytecodeError(f"Too many loops.  The maximum number of nested loops is { MAX_NESTED_LOOPS}.")

        if self.is_subroutine:
            return MAX_NESTED_LOOPS - n_loops
        else:
            return n_loops - 1

    @_instruction(integer_argument)
    def start_loop(self, loop_count: int) -> None:
        self.skip_last_loop_pos.append(None)
        loop_id: Final = self._loop_id()

        if loop_count < 1 or loop_count > 256:
            raise BytecodeError("Loop count out of range (1-256)")

        if loop_count == 256:
            loop_count = 0

        opcode: Final = START_LOOP_0 + loop_id * 2
        self.bytecode.append(opcode)
        self.bytecode.append(loop_count)

    @_instruction(no_argument)
    def skip_last_loop(self) -> None:
        loop_id: Final = self._loop_id()

        if self.skip_last_loop_pos[-1] is not None:
            raise BytecodeError("Only one `skip_last_loop` instruction is allowed per loop")

        # Save location of instruction argument for the `end_loop` instruction
        self.skip_last_loop_pos[-1] = len(self.bytecode) + 1

        opcode: Final = SKIP_LAST_LOOP_0 + loop_id * 2
        self.bytecode.append(opcode)
        self.bytecode.append(0)  # Will be added later in the `end_loop` instruction

    @_instruction(no_argument)
    def end_loop(self) -> None:
        try:
            loop_id: Final = self._loop_id()
        finally:
            # Ensure loop stack is popped if _loop_id() raises an exception
            skip_last_loop_pos: Final = self.skip_last_loop_pos.pop()

        # Write the parameter of the `skip_last_loop` instruction (if required)
        if skip_last_loop_pos is not None:
            assert self.bytecode[skip_last_loop_pos - 1] == SKIP_LAST_LOOP_0 + loop_id * 2
            to_skip = len(self.bytecode) - skip_last_loop_pos
            if to_skip < 1 or to_skip > 256:
                raise BytecodeError(f"skip_last_loop parameter out of bounds: {to_skip}")
            self.bytecode[skip_last_loop_pos] = to_skip

        assert loop_id >= 0
        opcode: Final = END_LOOP_0 + loop_id * 2
        self.bytecode.append(opcode)

    @_instruction(adsr_argument)
    def set_adsr(self, adsr: Adsr) -> None:
        self.bytecode.append(SET_ADSR)
        self.bytecode.append(adsr.adsr1)
        self.bytecode.append(adsr.adsr2)

    # ::TODO parse gain (after I figure out what it does)::
    @_instruction(integer_argument)
    def set_gain(self, gain: int) -> None:
        if gain < 0 or gain > 0xFF:
            raise BytecodeError("Invalid GAIN value")
        self.bytecode.append(SET_GAIN)
        self.bytecode.append(gain)

    @_instruction(integer_argument)
    def adjust_volume(self, v: int) -> None:
        if v < I8_MIN or v > I8_MAX:
            raise BytecodeError(f"Volume adjust out of range ({I8_MIN} - {I8_MAX})")
        self.bytecode.append(ADJUST_VOLUME)
        self.bytecode.append(cast_i8(v))

    @_instruction(integer_argument)
    def set_volume(self, v: int) -> None:
        if v < 0 or v > MAX_VOLUME:
            raise BytecodeError(f"Volume out of range (1-{MAX_VOLUME})")
        self.bytecode.append(SET_VOLUME)
        self.bytecode.append(v)

    @_instruction(integer_argument)
    def adjust_pan(self, p: int) -> None:
        if p < I8_MIN or p > I8_MAX:
            raise BytecodeError(f"Pan adjust out of range ({I8_MIN} - {I8_MAX})")
        self.bytecode.append(ADJUST_PAN)
        self.bytecode.append(cast_i8(p))

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
    def call_subroutine(self, s: Union[Name, BcSubroutine]) -> None:
        if self.is_subroutine:
            raise BytecodeError("Cannot call a subroutine in a subroutine")

        if isinstance(s, BcSubroutine):
            subroutine = s
        else:
            sr = self.mappings.subroutines.get(s)
            if sr is None:
                raise BytecodeError(f"Unknown subroutine: {s}")
            subroutine = sr

        if subroutine.subroutine_id < 0 or subroutine.subroutine_id > MAX_N_SUBROUTINES:
            raise BytecodeError("Invalid subroutine id")

        self.bytecode.append(CALL_SUBROUTINE)
        self.bytecode.append(subroutine.subroutine_id)

    @_instruction(no_argument)
    def return_from_subroutine(self) -> None:
        if not self.is_subroutine:
            raise BytecodeError("Not a subroutine")
        self.bytecode.append(RETURN_FROM_SUBROUTINE)

    @_instruction(integer_argument)
    def set_song_tick_clock(self, timer: int) -> None:
        if self.is_sound_effect:
            raise BytecodeError("Cannot change song tick clock in a sound effect")

        if timer < MIN_TICK_TIMER or timer > 0xFF:
            raise BytecodeError(f"timer0 value out of range: {timer} (min: {MIN_TICK_TIMER}, max: {0xff})")
        self.bytecode.append(SET_SONG_TICK_CLOCK)
        self.bytecode.append(timer)

    @_instruction(no_argument)
    def enable_echo(self) -> None:
        self.bytecode.append(ENABLE_ECHO)

    @_instruction(no_argument)
    def disable_echo(self) -> None:
        self.bytecode.append(DISABLE_ECHO)


def test_length_argument(length: int) -> int:
    if length <= 0:
        raise BytecodeError("Note length is too short")
    if length > 0x100:
        raise BytecodeError("Note length is too long")
    return length & 0xFF
