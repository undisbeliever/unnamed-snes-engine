# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import re
import math
from collections import OrderedDict
from dataclasses import dataclass

from .driver_constants import N_MUSIC_CHANNELS, TIMER_HZ, MIN_TICK_TIMER, MAX_TICK_TIMER
from .samples import SEMITONES_PER_OCTAVE, PitchTable, build_pitch_table
from .json_formats import SamplesJson
from .bytecode import Bytecode, BcMappings, create_bc_mappings, N_OCTAVES, MAX_NESTED_LOOPS, MAX_LOOP_COUNT, MAX_PAN, MAX_VOLUME
from .bytecode import validate_adsr

from .json_formats import Instrument as SamplesJsonInstrument

from typing import Final, NamedTuple, Optional, Union


CLOCK_CYCLES_PER_BPM: Final = 48


MIN_OCTAVE: Final = 1
MAX_OCTAVE: Final = N_OCTAVES - 1

MAX_QUANTIZATION: Final = 8

MAX_PLAY_NOTE_TICKS: Final = 0xFF
MAX_REST_TICKS: Final = 0xFF

DEFAULT_BPM: Final = 60
MIN_BPM: Final = int((TIMER_HZ * 60) / (48 * MAX_TICK_TIMER)) + 1
MAX_BPM: Final = int((TIMER_HZ * 60) / (48 * MIN_TICK_TIMER)) + 1

DEFAULT_ZENLEN: Final = 96
MIN_ZENLEN: Final = 4
MAX_ZENLEN: Final = 255

STARTING_DEFAULT_NOTE_LENGTH: Final = 4


@dataclass
class MmlError:
    message: str
    line_number: int
    char_start: Optional[int]

    def str(self) -> str:
        if self.char_start:
            return f"{self.line_number}:{self.char_start}: {self.message}"
        else:
            return f"{self.line_number}: {self.message}"


class CompileError(RuntimeError):
    def __init__(self, errors: list[MmlError]):
        self.errors = errors

    def __str__(self) -> str:
        return "Error compiling MML:\n    " + "\n    ".join(map(MmlError.str, self.errors))


class MetaData(NamedTuple):
    title: Optional[str]
    date: Optional[str]
    composer: Optional[str]
    author: Optional[str]
    copyright: Optional[str]
    license: Optional[str]

    tick_timer: int
    zenlen: int


class Instrument(NamedTuple):
    name: str
    instrument_name: str
    instrument_id: int
    first_note: int
    last_note: int

    # Override adsr or gain values
    adsr: Optional[tuple[int, int, int, int]]
    gain: Optional[int]


class ChannelData(NamedTuple):
    name: str
    bytecode: bytes
    tick_counter: int

    max_nested_loops: int
    last_instrument: Optional[Instrument]


class MmlData(NamedTuple):
    metadata: MetaData
    instruments: OrderedDict[str, Instrument]
    subroutines: list[ChannelData]
    channels: list[ChannelData]

    def tick_counts_string(self) -> str:
        return "\n".join(f"Channel {c.name}: {c.tick_counter}" for c in self.channels)


class Line(NamedTuple):
    line_number: int
    char_start: int
    text: str


class MmlLines(NamedTuple):
    headers: list[Line]
    instruments: list[Line]
    subroutines: OrderedDict[str, list[Line]]
    channels: list[list[Line]]


def is_identifier_valid(name: str) -> bool:
    if not name:
        return False

    # If the identifier starts with a digit it must be a number
    if name[0] in "1234567890":
        try:
            int(name)
        except ValueError:
            return False

    return True


CHANNEL_REGEX: Final = re.compile(r"^([A-F]+)\s+(.+)$")
SUBROUTINE_REGEX: Final = re.compile(r"^!([^\s]+)\s+(.+)$")
FIRST_CHANNEL_ORD: Final = ord("A")


def split_lines(mml_text: str) -> MmlLines:
    errors = list()

    headers = list()
    instruments = list()
    subroutines: OrderedDict[str, list[Line]] = OrderedDict()
    channels: list[list[Line]] = list()

    for i in range(N_MUSIC_CHANNELS):
        channels.append(list())

    # ::TODO add MML skip (`'` and `"`)::

    # ::TODO add segments (in comments) ::

    for line_no, line in enumerate(mml_text.splitlines(), 1):
        line, _sep, _comment = line.partition(";")
        line.strip()
        if line:
            c = line[0]

            if c == "#":
                headers.append(Line(line_no, 0, line))
            elif c == "@":
                instruments.append(Line(line_no, 0, line))
            elif c == "!":
                # Subroutine
                m = SUBROUTINE_REGEX.match(line)
                if m:
                    s_name = m.group(1)
                    if not is_identifier_valid(s_name):
                        errors.append(MmlError("Invalid subroutine name: {s_name}", line_no, None))
                    if s_name not in subroutines:
                        subroutines[s_name] = list()
                    subroutines[s_name].append(Line(line_no, m.start(2), line))
                else:
                    errors.append(MmlError("Cannot parse line: Expected `!<subroutine_name> <MML>`", line_no, None))
            else:
                # Channel
                m = CHANNEL_REGEX.match(line)
                if m:
                    c_line = Line(line_no, m.start(2), line)

                    # adding a channel bitset to ensure each line is only added to a channel once
                    channel_bitset = 0
                    for c in m.group(1):
                        channel_index = ord(c) - FIRST_CHANNEL_ORD
                        channel_mask = 1 << channel_index
                        if channel_bitset & channel_mask == 0:
                            channels[channel_index].append(c_line)
                            channel_bitset |= channel_mask
                else:
                    errors.append(MmlError("Cannot parse line", line_no, None))

    if errors:
        raise CompileError(errors)

    return MmlLines(headers=headers, instruments=instruments, subroutines=subroutines, channels=channels)


HEADER_REGEX = re.compile(r"^#([^\s]+)\s+(.+)$")
VALID_HEADERS = frozenset(("Title", "Composer", "Author", "Copyright", "Date", "License"))


def parse_int_range(s: str, min_value: int, max_value: int) -> int:
    i = int(s)
    if i < min_value or i > max_value:
        raise ValueError(f"Integer out of range ({min_value}-{max_value})")
    return i


def bpm_to_tick_timer(bpm: int) -> int:
    if bpm < MIN_BPM or bpm > MAX_BPM:
        raise ValueError(f"bpm out of range ({MIN_BPM}-{MAX_BPM})")
    tick_timer = round((TIMER_HZ * 60) / (bpm * CLOCK_CYCLES_PER_BPM))
    assert tick_timer >= MIN_TICK_TIMER and tick_timer <= MAX_TICK_TIMER
    return tick_timer


def parse_headers(headers: list[Line], error_list: list[MmlError]) -> MetaData:
    header_map = dict()

    tick_timer = None
    zenlen = None

    for line in headers:
        try:
            m = HEADER_REGEX.match(line.text)
            if not m:
                raise ValueError("Invalid header format")

            name = m.group(1).title()
            value = m.group(2).strip()

            if name == "Tempo" or name == "Timer":
                if tick_timer is not None:
                    raise RuntimeError("Cannot set {name}: tick_timer already set")
                if name == "Tempo":
                    tick_timer = bpm_to_tick_timer(int(value))
                else:
                    tick_timer = parse_int_range(value, MIN_TICK_TIMER, MAX_TICK_TIMER)

            elif name == "Zenlen":
                if zenlen is not None:
                    raise RuntimeError("Cannot set zenlen: zenlen already set")
                zenlen = parse_int_range(value, MIN_ZENLEN, MAX_ZENLEN)

            elif name in VALID_HEADERS:
                if name in header_map:
                    raise RuntimeError("Duplicate header name: {name}")
                header_map[name] = value

            else:
                raise ValueError(f"Unknown header: {m.group(1)}")

        except Exception as e:
            error_list.append(MmlError(str(e), line.line_number, None))

    if tick_timer is None:
        tick_timer = bpm_to_tick_timer(60)

    if zenlen is None:
        zenlen = DEFAULT_ZENLEN

    return MetaData(
        title=header_map.get("Title"),
        date=header_map.get("Date"),
        composer=header_map.get("Composer"),
        author=header_map.get("Author"),
        copyright=header_map.get("Copyright"),
        license=header_map.get("License"),
        tick_timer=tick_timer,
        zenlen=zenlen,
    )


INSTRUNMENT_REGEX: Final = re.compile(r"^@([^\s]+)\s+(.+)$")


def _parse_instrument_line(line: str, inst_map: dict[str, tuple[int, SamplesJsonInstrument]]) -> Instrument:
    m: Final = INSTRUNMENT_REGEX.fullmatch(line)
    if not m:
        raise RuntimeError("Invalid instrument format.")

    name: Final = m.group(1)
    args: Final = m.group(2).split()

    inst_name: Final = args.pop(0)

    if not is_identifier_valid(name):
        raise RuntimeError(f"Invalid instrument name: {name}")

    map_value: Final = inst_map.get(inst_name)
    if map_value is None:
        raise RuntimeError(f"Unknown samples.json instrument: {inst_name}")

    inst_id, inst = map_value

    adsr = None
    gain = None

    if args:
        arg_name: Final = args[0]
        if arg_name == "adsr":
            if len(args) != 5:
                raise RuntimeError("adsr argument requires 4 integers")
            adsr = int(args[1]), int(args[2]), int(args[3]), int(args[4])
            validate_adsr(*adsr)

        elif arg_name == "gain":
            if len(args) != 2:
                raise RuntimeError("Invalid gain: expected 1 integer")
            # ::TODO parse gain::
            gain = int(args[1], 0)
            if gain < 0 or gain > 0xFF:
                raise RuntimeError("Invalid gain byte")
        else:
            raise RuntimeError(f"Unknown instrument argument: {arg_name}")

    return Instrument(
        name=name,
        instrument_name=inst.name,
        instrument_id=inst_id,
        first_note=inst.first_octave * SEMITONES_PER_OCTAVE,
        last_note=(inst.last_octave + 1) * SEMITONES_PER_OCTAVE - 1,
        adsr=adsr,
        gain=gain,
    )


def parse_instruments(instrument_lines: list[Line], samples: SamplesJson) -> OrderedDict[str, Instrument]:
    out = OrderedDict()
    errors = list()

    inst_map: Final = {inst.name: (i, inst) for i, inst in enumerate(samples.instruments)}

    for line in instrument_lines:
        try:
            inst = _parse_instrument_line(line.text, inst_map)
            if inst.name in out:
                raise RuntimeError(f"Duplicate instrument: {inst.name}")

            out[inst.name] = inst
        except Exception as e:
            errors.append(MmlError(str(e), line.line_number, None))

    if errors:
        raise CompileError(errors)

    return out


NOTE_MAP: Final = {
    "c": 0,
    "d": 2,
    "e": 4,
    "f": 5,
    "g": 7,
    "a": 9,
    "b": 11,
}
WHITESPACE_REGEX: Final = re.compile(r"\s+")
UINT_REGEX: Final = re.compile(r"[0-9]+")
RELATIVE_INT_REGEX: Final = re.compile(r"[+-]?[0-9]+")
IDENTIFIER_REGEX: Final = re.compile(r"[0-9]+|([^0-9][^\s]*(\s|$))")
NOTE_REGEX: Final = re.compile(r"([a-g](?:\-+|\++)?)\s*(%?)([0-9]*)(\.*)")
PITCH_REGEX: Final = re.compile(r"([a-g](?:\-+|\++)?)\s*([0-9]*)(\.*)")
NOTE_LENGTH_REGEX: Final = re.compile(r"(%?)([0-9]*)(\.*)")

FIND_LOOP_END_REGEX: Final = re.compile(r"\[|\]")
LOOP_END_COUNT_REGEX: Final = re.compile(r"\s*([0-9]+)")


class NoteLength(NamedTuple):
    is_clock_value: int
    value: Optional[int]
    dot_count: int


class Note(NamedTuple):
    note: int
    length: NoteLength


class Tokenizer:
    TWO_CHARACTER_TOKENS: Final = frozenset(("__", "{{", "}}"))

    def __init__(self, lines: list[Line]):
        self.lines: Final = lines

        self._line_str: Optional[str] = None
        self._line_pos: int = 0

        # Index into `self.lines`
        self._line_index: int = -1

        self._line_no: int = -1

        self._at_end: bool = False

        self.skip_new_line()

    def _set_pos_skip_whitespace(self, new_pos: int) -> None:
        if self._line_str is None:
            return

        if new_pos < len(self._line_str):
            m = WHITESPACE_REGEX.match(self._line_str, new_pos)
            if m:
                new_pos = m.end(0)

        if new_pos < len(self._line_str):
            self._line_pos = new_pos
        else:
            self._line_str = None

    def at_end(self) -> bool:
        return self._at_end

    def skip_new_line(self) -> None:
        """
        Advances to the next line if the current line has been completely read.
        """
        if self._line_str is None:
            # Previous line ended, start a new line
            if self._line_index + 1 < len(self.lines):
                self._line_index += 1
                l: Final = self.lines[self._line_index]
                self._line_str = l.text
                self._line_no = l.line_number
                self._line_pos = l.char_start
            else:
                self._at_end = True

    def get_pos(self) -> tuple[int, int]:
        return self._line_no, self._line_pos + 1

    def read_loop_end_count(self) -> tuple[bool, Optional[int]]:
        """
        Scan for the `]` token in the current loop and return the loop count without
        advancing the position.

        Assumes the previous token was `[`.
        """

        nested_loops = 1

        for line_index in range(self._line_index, len(self.lines)):
            line = self.lines[line_index]

            if line_index == self._line_index:
                line_pos = self._line_pos
                if self._line_str is None or line_pos > len(line.text):
                    # All characters in the line have been processed.
                    # Skip to the next line
                    continue
            else:
                line_pos = line.char_start

            for m in FIND_LOOP_END_REGEX.finditer(line.text, line_pos):
                token = m.group(0)
                if token == "[":
                    nested_loops += 1
                elif token == "]":
                    nested_loops -= 1
                    if nested_loops <= 0:
                        loop_end_pos = m.end(0)
                        m2 = LOOP_END_COUNT_REGEX.match(line.text, loop_end_pos)
                        if m2 is None:
                            return True, None
                        else:
                            return True, int(m2.group(1))

            line_index += 1

        return False, None

    def parse_regex(self, pattern: re.Pattern[str]) -> Optional[re.Match[str]]:
        if self._line_str is None:
            return None

        m = pattern.match(self._line_str, self._line_pos)
        if m:
            self._set_pos_skip_whitespace(m.end(0))
        return m

    # NOTE: Does not parse integer or note tokens
    def next_token(self) -> Optional[str]:
        if self._line_str is None:
            return None

        t = self._line_str[self._line_pos : self._line_pos + 2]
        if t in self.TWO_CHARACTER_TOKENS:
            self._set_pos_skip_whitespace(self._line_pos + 2)
            return t
        elif t:
            self._set_pos_skip_whitespace(self._line_pos + 1)
            return t[0]
        else:
            return None

    def peek_next_token(self) -> Optional[str]:
        if self._line_str is None:
            return None

        t = self._line_str[self._line_pos : self._line_pos + 2]
        if t in self.TWO_CHARACTER_TOKENS:
            return t
        else:
            return t[0]

    def parse_bool(self) -> bool:
        if self._line_str is None:
            raise RuntimeError("Cannot parse bool, expected a 0 or a 1")

        t = self._line_str[self._line_pos]
        if t == "0":
            self._set_pos_skip_whitespace(self._line_pos + 1)
            return False
        elif t == "1":
            self._set_pos_skip_whitespace(self._line_pos + 1)
            return True
        else:
            raise RuntimeError("Cannot parse bool, expected a 0 or a 1")

    def parse_uint(self) -> int:
        m = self.parse_regex(UINT_REGEX)
        if not m:
            raise RuntimeError("Cannot parse integer, expected a decimal digit")

        return int(m.group(0))

    def parse_optional_uint(self) -> Optional[int]:
        m = self.parse_regex(UINT_REGEX)
        if m:
            return int(m.group(0))
        else:
            return None

    def parse_relative_int(self) -> tuple[int, bool]:
        """Returns (value, is_relative)"""

        m = self.parse_regex(RELATIVE_INT_REGEX)
        if not m:
            raise RuntimeError("Cannot parse integer, expected a decimal digit, - or +")

        s = m.group(0)
        value = int(s)
        if s[0] in "+-":
            return value, True
        else:
            return value, False

    def parse_identifier(self) -> str:
        m = self.parse_regex(IDENTIFIER_REGEX)
        if not m:
            raise RuntimeError("Cannot parse identifier")

        return m.group(0).rstrip()

    def parse_optional_pitch(self) -> Optional[int]:
        m = self.parse_regex(PITCH_REGEX)
        if m is None:
            return None

        note_str = m.group(1)
        n = NOTE_MAP[note_str[0]]

        n_semitone_shifts = len(note_str) - 1
        if n_semitone_shifts > 0:
            if note_str[1] == "-":
                n -= n_semitone_shifts
            else:
                n += n_semitone_shifts

        return n

    def parse_optional_note(self) -> Optional[Note]:
        "Returns (note, length, number_of_dots)"

        m = self.parse_regex(NOTE_REGEX)
        if m is None:
            return None

        note_str = m.group(1)

        n = NOTE_MAP[note_str[0]]

        is_clock_value = bool(m.group(2))

        n_semitone_shifts = len(note_str) - 1
        if n_semitone_shifts > 0:
            if note_str[1] == "-":
                n -= n_semitone_shifts
            else:
                n += n_semitone_shifts

        length_str = m.group(3)
        if length_str:
            length = int(length_str)
        else:
            length = None

        dot_count = len(m.group(4))

        return Note(n, NoteLength(is_clock_value, length, dot_count))

    def parse_optional_note_length(self) -> Optional[NoteLength]:
        m = self.parse_regex(NOTE_LENGTH_REGEX)
        if m is None:
            return None

        if not m.group(0):
            return None

        is_clock_value = bool(m.group(1))

        length_str = m.group(2)
        if length_str:
            length = int(length_str)
        else:
            length = None

        dot_count = len(m.group(3))

        return NoteLength(is_clock_value, length, dot_count)


@dataclass
class LoopState:
    loop_count: int
    # Tick counter at the start of the loop
    tc_start_of_loop: int
    # Tick counter at the optional skip_last_loop token (`:`)
    tc_skip_last_loop: Optional[int]


class MmlChannelParser:
    def __init__(
        self,
        input_lines: list[Line],
        channel_name: str,
        metadata: MetaData,
        instruments: OrderedDict[str, Instrument],
        subroutines: Optional[list[ChannelData]],
        pitch_table: PitchTable,
        bc_mappings: BcMappings,
        error_list: list[MmlError],
    ):
        self.tokenizer: Final = Tokenizer(input_lines)
        self.channel_name: Final = channel_name
        self.is_subroutine: Final = subroutines is None
        self.subroutines: Final = subroutines
        self.instruments: Final = instruments
        self.pitch_table: Final = pitch_table
        self.bc: Final = Bytecode(bc_mappings, is_subroutine=self.is_subroutine)

        self.error_list: Final = error_list

        self.zenlen: int = metadata.zenlen

        self.octave: int = 4
        self.semitone_offset: int = 0  # transpose commands (_ and __)
        self.default_length_ticks: int = self.zenlen // STARTING_DEFAULT_NOTE_LENGTH
        self.quantization: Optional[int] = None

        # Note id of the previously slurred note (if any)
        self.prev_slured_note_id: Optional[int] = None

        self.tick_counter: int = 0

        self.loop_stack: list[LoopState] = list()
        self.max_nested_loops: int = 0

        # The currently playing instrument
        self.instrument: Optional[Instrument] = None
        # Do not show a "Cannot play a note before setting an instrument" error in subroutine
        self.show_missing_set_instrument_error = not self.is_subroutine

        self.set_error_pos()

    def set_error_pos(self) -> None:
        # Set the error location for any errors created with `add_error()`
        self._pos = self.tokenizer.get_pos()

    def add_error(self, message: str) -> None:
        self.error_list.append(MmlError(message, *self._pos))

    def _parse_list_of_pitches(self, end_token: str) -> list[int]:
        note_ids = list()

        while True:
            self.set_error_pos()

            if (p := self.tokenizer.parse_optional_pitch()) is not None:
                try:
                    note_ids.append(self.calculate_note_id(p))
                except Exception as e:
                    self.add_error(str(e))

            elif t := self.tokenizer.next_token():
                if t == end_token:
                    break
                elif t == "o":
                    self.parse_o()
                elif t == ">":
                    self.parse_increase_octave()
                elif t == "<":
                    self.parse_decrease_octave()
                else:
                    self.add_error(f"Unknown token: {t}")

            else:
                # Line ended early
                raise RuntimeError("Missing `{end_token}`")

        return note_ids

    def _parse_tie_and_slur(self) -> tuple[int, bool]:
        """Returns: tie_length (in ticks), slur_note"""
        tie_length = 0
        slur_note = False

        while t := self._test_next_token_matches_multiple(r"lo><^&"):
            # Allow note length and octave change before tick/slur
            if t == "l":
                self.parse_l()
            elif t == "o":
                self.parse_o()
            elif t == ">":
                self.parse_increase_octave()
            elif t == "<":
                self.parse_decrease_octave()

            elif t == "^":
                # Extend tick_length on ties ('^')
                try:
                    tie_length += self.parse_note_length()
                except Exception as e:
                    self.add_error(str(e))

            elif t == "&":
                # Slur or tie

                nl = self.parse_optional_note_length()
                if nl is not None:
                    # This is a tie, extend the tick length
                    tie_length += nl
                else:
                    # This is a slur
                    slur_note = True

        return tie_length, slur_note

    def calculate_note_id(self, note: int) -> int:
        note_id: Final = note + self.octave * SEMITONES_PER_OCTAVE + self.semitone_offset
        self.test_note_id(note_id)
        return note_id

    def test_note_id(self, note_id: int) -> None:
        if self.instrument:
            if note_id < self.instrument.first_note or note_id > self.instrument.last_note:
                self.add_error(
                    f"Cannot play {self.instrument.instrument_name} note: note out of range ({note_id}, min: {self.instrument.first_note}, max: {self.instrument.last_note})"
                )
        else:
            # instrument is None
            if self.show_missing_set_instrument_error:
                self.add_error("Cannot play a note before setting an instrument")
                self.show_missing_set_instrument_error = False

    def calculate_note_length(self, nl: NoteLength) -> int:
        "Returns note length in ticks"
        if nl.is_clock_value:
            if nl.value is None:
                raise ValueError("Missing clock tick count")
            if nl.value < 1:
                raise ValueError("Invalid clock tick count")
            if nl.dot_count:
                raise ValueError("Dots not allowed after a % clock value")
            return nl.value

        else:
            # Whole note length divisor

            if nl.value is not None:
                if nl.value < 1 or nl.value > self.zenlen:
                    raise ValueError("Invalid note length")
                ticks = self.zenlen // nl.value
            else:
                ticks = self.default_length_ticks

            if nl.dot_count:
                assert nl.dot_count > 0

                half_t = ticks // 2
                for i in range(nl.dot_count):
                    ticks += half_t
                    half_t //= 2

            return ticks

    def parse_note_length(self) -> int:
        nl = self.tokenizer.parse_optional_note_length()
        if nl is None:
            return self.default_length_ticks
        return self.calculate_note_length(nl)

    def parse_optional_note_length(self) -> Optional[int]:
        nl = self.tokenizer.parse_optional_note_length()
        if nl is None:
            return None
        return self.calculate_note_length(nl)

    def parse_change_whole_note_length(self) -> None:
        """
        Change zenlen (AKA: whole note length) value.

        NOTE: This command resets the default note length.
        NOTE: This command changes the zenlen for the current channel/macro only.
        """
        z = self.tokenizer.parse_uint()
        if z < MIN_ZENLEN or z > MAX_ZENLEN:
            raise ValueError(f"zenlen out of bounds ({MIN_ZENLEN}-{MAX_ZENLEN})")
        self.zenlen = z
        self.default_length_ticks = z // STARTING_DEFAULT_NOTE_LENGTH

    def _play_portamento(
        self,
        note1_id: int,
        note2_id: int,
        is_slur: bool,
        speed_override: Optional[int],
        delay_ticks: int,
        portamento_ticks: int,
        after_ticks: int,
    ) -> None:
        if self.instrument is None:
            raise RuntimeError("Instrument must be set before a portamento (even in subroutines)")

        # Play note1 (if required)
        if self.prev_slured_note_id != note1_id or delay_ticks > 0:
            if delay_ticks == 0:
                delay_ticks = 1
                portamento_ticks -= 1
            self._play_note(note1_id, False, delay_ticks)

        note2_ticks = portamento_ticks
        if after_ticks:
            assert after_ticks > 0
            note2_ticks += after_ticks

        key_off: Final[bool] = not is_slur

        if speed_override:
            assert speed_override > 0

            if note2_id > note1_id:
                pitch_velocity = +speed_override
            else:
                pitch_velocity = -speed_override
        elif self.instrument:
            pitch1: Final = self.pitch_table.pitch_for_note(self.instrument.instrument_id, note1_id)
            pitch2: Final = self.pitch_table.pitch_for_note(self.instrument.instrument_id, note2_id)

            # Round towards 0
            pitch_velocity = math.trunc((pitch2 - pitch1) / portamento_ticks)
        else:
            raise RuntimeError(
                "Cannot calculate portamento velocity.  Either set an instrument or manually override speed (third parameter of `{{ }}`)."
            )

        if note2_ticks < MAX_PLAY_NOTE_TICKS:
            self.tick_counter += note2_ticks
            self.bc.portamento(note2_id, key_off, pitch_velocity, note2_ticks)
        else:
            self.tick_counter += MAX_PLAY_NOTE_TICKS
            self.bc.portamento(note2_id, False, pitch_velocity, MAX_PLAY_NOTE_TICKS)
            self._rest_after_play_note(note2_ticks - MAX_PLAY_NOTE_TICKS, key_off)

        if is_slur:
            self.prev_slured_note_id = note2_id
        else:
            self.prev_slured_note_id = None

    def _play_note(self, note_id: int, key_off: bool, tick_length: int) -> None:
        assert tick_length > 0

        if not key_off:
            self.prev_slured_note_id = note_id
        else:
            self.prev_slured_note_id = None

        if tick_length <= MAX_PLAY_NOTE_TICKS:
            self.tick_counter += tick_length
            self.bc.play_note(note_id, key_off, tick_length)
        else:
            # Cannot play note in a single instruction
            self.tick_counter += MAX_PLAY_NOTE_TICKS
            self.bc.play_note(note_id, False, MAX_PLAY_NOTE_TICKS)
            self._rest_after_play_note(tick_length - MAX_PLAY_NOTE_TICKS, key_off)

    def _rest_after_play_note(self, ticks: int, key_off: bool) -> None:
        "IMPORTANT NOTE: Does not modify self.prev_slurred_note_id"
        assert ticks > 0

        if key_off:
            self.prev_slured_note_id = None

        self.tick_counter += ticks

        while ticks > MAX_REST_TICKS:
            self.bc.rest(MAX_REST_TICKS)
            ticks -= MAX_REST_TICKS

        if key_off:
            self.bc.rest_keyoff(ticks)
        else:
            self.bc.rest(ticks)

    def _rest(self, ticks: int) -> None:
        assert ticks > 0

        self.prev_slured_note_id = None

        self.tick_counter += ticks

        while ticks > 0:
            t = min(MAX_REST_TICKS, ticks)
            ticks -= t
            self.bc.rest(t)

    def _test_next_token_matches(self, token: str) -> bool:
        """
        Tests if the next token is `token`.

        Also advances the tokenizer to a new line and sets the error_pos (if token matches).
        """
        self.tokenizer.skip_new_line()
        if self.tokenizer.peek_next_token() == token:
            self.set_error_pos()
            t = self.tokenizer.next_token()
            assert t == token
            return True
        return False

    def _test_next_token_matches_multiple(self, tokens: Union[str | list[str]]) -> Optional[str]:
        """
        Tests if the next token is a character in `tokens`.  Returns the token

        Also advances the tokenizer to a new line and sets the error_pos (if token matches).
        """
        self.tokenizer.skip_new_line()

        t = self.tokenizer.peek_next_token()
        if t and t in tokens:
            self.set_error_pos()
            nt = self.tokenizer.next_token()
            assert nt == t
            return t
        return None

    def _test_next_token_matches_no_newline(self, token: str) -> bool:
        """
        Tests if the next token is `token` without advancing to the next line.
        Also sets the error_pos (if the token matches).
        """
        if self.tokenizer.peek_next_token() == token:
            self.set_error_pos()
            t = self.tokenizer.next_token()
            assert t == token
            return True
        return False

    def _play_note_with_quantization(self, note_id: int, tick_length: int, slur_note: bool) -> None:
        key_off = not slur_note

        if self.quantization and not slur_note:
            assert 0 < self.quantization < MAX_QUANTIZATION, "Invalid quantization value"

            # -1 for the key-off tick in the `play_note` bytecode instruction
            key_on_length = tick_length * self.quantization // MAX_QUANTIZATION + 1
            key_off_length = tick_length - key_on_length

            if key_on_length > 1 and key_off_length > 1:
                self._play_note(note_id, True, key_on_length)
                self._rest(key_off_length)
            else:
                self._play_note(note_id, key_off, tick_length)
        else:
            self._play_note(note_id, key_off, tick_length)

    def parse_note(self, note: Note) -> None:
        # Must calculate note_id here to ensure error message location is correct
        note_id = self.calculate_note_id(note.note)

        note_length = self.calculate_note_length(note.length)
        tie_length, slur_note = self._parse_tie_and_slur()

        tick_length: Final = note_length + tie_length

        self._play_note_with_quantization(note_id, tick_length, slur_note)

    def parse_n(self) -> None:
        "Play midi note integer ID at default length"

        note_id: Final = self.tokenizer.parse_uint() - SEMITONES_PER_OCTAVE
        # Must test note_id here to ensure error message location is correct
        self.test_note_id(note_id)

        tie_length, slur_note = self._parse_tie_and_slur()

        tick_length: Final = self.default_length_ticks + tie_length

        self._play_note_with_quantization(note_id, tick_length, slur_note)

    def parse_r(self) -> None:
        ticks = self.parse_note_length()

        # Combine multiple "r" tokens
        while self._test_next_token_matches("r"):
            ticks += self.parse_note_length()

        self._rest(ticks)

    def parse_exclamation_mark(self) -> None:
        "Call subroutine"

        i = self.tokenizer.parse_identifier()

        if not self.is_subroutine:
            assert self.subroutines is not None

            s_index = self.bc.mappings.subroutines.get(i)

            if s_index is not None:
                s: Final = self.subroutines[s_index]

                n_nested_loops: Final = len(self.loop_stack) + s.max_nested_loops

                if n_nested_loops > self.max_nested_loops:
                    self.max_nested_loops = n_nested_loops

                if n_nested_loops > MAX_NESTED_LOOPS:
                    self.add_error(
                        f"Too many nested loops when calling subroutine {i} (requires: {n_nested_loops}, max: {MAX_NESTED_LOOPS})"
                    )

                self.bc.call_subroutine_int(s_index)

                self.tick_counter += s.tick_counter

                # Subroutine changed the current instrument
                if s.last_instrument is not None:
                    self.instrument = s.last_instrument

            else:
                self.add_error(f"Unknown subroutine {i}")
        else:
            self.add_error("Cannot call a subroutine inside a subroutine")

    def parse_start_loop(self) -> None:
        found_end, loop_count = self.tokenizer.read_loop_end_count()
        if not found_end:
            raise RuntimeError("Cannot find end of loop")
        self._start_loop(loop_count)

    def _start_loop(self, loop_count: Optional[int]) -> None:
        if loop_count is None:
            # ::TODO add infinite loops::
            loop_count = 2

        if loop_count < 2 or loop_count > MAX_LOOP_COUNT:
            self.add_error(f"Loop count is out of range (2 - {MAX_LOOP_COUNT})")

        self.loop_stack.append(LoopState(loop_count, self.tick_counter, None))
        n_nested_loops = len(self.loop_stack)

        if n_nested_loops > self.max_nested_loops:
            self.max_nested_loops = n_nested_loops

        if loop_count < 2 or loop_count > MAX_LOOP_COUNT:
            # Ignore loop count errors here so error message location is at the end of the loop
            loop_count = 2
        self.bc.start_loop(loop_count)

    def parse_skip_last_loop(self) -> None:
        if not self.loop_stack:
            raise RuntimeError("Not in a loop")

        if self.loop_stack[-1].tc_skip_last_loop is not None:
            raise RuntimeError("Only one skip last loop `:` token is allowed per loop")

        self.loop_stack[-1].tc_skip_last_loop = self.tick_counter

        self.bc.skip_last_loop()

    def parse_end_loop(self) -> None:
        loop_count: Final = self.tokenizer.parse_optional_uint()

        if not self.loop_stack:
            raise RuntimeError("Loop stack empty (missing start of loop)")

        ls: Final = self.loop_stack[-1]

        if loop_count is None:
            # ::TODO add infinite loops::
            raise RuntimeError("Missing loop count")

        if loop_count < 2 or loop_count > MAX_LOOP_COUNT:
            # Adding error message here ensures error location is correct
            self.add_error(f"Loop count is out of range (2 - {MAX_LOOP_COUNT})")

        if loop_count != ls.loop_count:
            # This should not happen.  Check `Tokenizer.read_loop_end_count()`
            self.add_error(f"SOMETHING WENT WRONG: loop_count != expected_loop_count")

        self._end_loop()

    def _end_loop(self) -> None:
        n_nested_loops: Final = len(self.loop_stack)

        ls: Final = self.loop_stack.pop()

        ticks_in_loop: Final = self.tick_counter - ls.tc_start_of_loop
        if ticks_in_loop <= 0:
            self.add_error("Loop does not play a note or rest")

        if ls.tc_skip_last_loop is not None:
            ticks_in_last_loop = ls.tc_skip_last_loop - ls.tc_start_of_loop
        else:
            ticks_in_last_loop = ticks_in_loop

        if ticks_in_loop > 0 and ls.loop_count > 2:
            self.tick_counter += ticks_in_loop * (ls.loop_count - 2)
        if ticks_in_last_loop > 0:
            self.tick_counter += ticks_in_last_loop

        # If statement ensures the "too many nested loops" error is only outputted once
        if n_nested_loops < MAX_NESTED_LOOPS:
            self.bc.end_loop()

    def parse_at(self) -> None:
        "Set Instrument"

        i: Final = self.tokenizer.parse_identifier()

        inst: Final = self.instruments.get(i)
        if inst is None:
            raise RuntimeError(f"Unknown instrument: {i}")

        old_adsr: Final = self.instrument.adsr if self.instrument else None
        old_gain: Final = self.instrument.gain if self.instrument else None

        adsr_or_gain_changed: Final[bool] = inst.adsr != old_adsr or inst.gain != old_gain

        emit_si_bc: Final[bool] = (
            # First instrument command
            self.instrument is None
            # Instrument id changed
            or inst.instrument_id != self.instrument.instrument_id
            # ADSR/GAIN needs to be restored
            or (inst.adsr is None and inst.gain is None and adsr_or_gain_changed)
        )

        if emit_si_bc:
            self.bc.set_instrument_int(inst.instrument_id)

        if inst.adsr is not None and inst.adsr != old_adsr:
            self.bc.set_adsr(*inst.adsr)

        if inst.gain is not None and inst.gain != old_gain:
            self.bc.set_gain(inst.gain)

        self.instrument = inst

    def parse_l(self) -> None:
        "Change default note length"
        self.default_length_ticks = self.parse_note_length()

    def parse_quantize(self) -> None:
        q = self.tokenizer.parse_uint()
        if q < 1 or q > MAX_QUANTIZATION:
            raise ValueError(f"Quantization out of bounds (1-{MAX_QUANTIZATION})")
        if q == MAX_QUANTIZATION:
            # Disable quantization
            self.quantization = None
        else:
            self.quantization = q

    def parse_o(self) -> None:
        "Set octave"
        o = self.tokenizer.parse_uint()
        self.octave = min(MAX_OCTAVE, max(MIN_OCTAVE, o))

        if o < MIN_OCTAVE or o > MAX_OCTAVE:
            raise RuntimeError(f"Octave out of range (min: {MIN_OCTAVE}, max: {MAX_OCTAVE})")

    def parse_underscore(self) -> None:
        "Transpose"
        value, is_relative = self.tokenizer.parse_relative_int()
        if value < -128 or value > 128:
            raise RuntimeError("Transpose out of range (-128 - +128)")
        self.semitone_offset = value

    def parse_double_underscore(self) -> None:
        "Relative transpose"
        # Relative setting
        value, is_relative = self.tokenizer.parse_relative_int()
        if value < -128 or value > 128:
            raise RuntimeError("Transpose out of range (-128 - +128)")
        self.semitone_offset += value

    def parse_v(self) -> None:
        "Volume"
        v, is_v_relative = self._parse_volume_value()
        if not is_v_relative:
            if self._test_next_token_matches("p"):
                p, is_p_relative = self._parse_pan_value()
                self._set_pan_and_volume(p, is_p_relative, v, is_v_relative)
            else:
                self.bc.set_volume(v)
        else:
            # v is relative
            if v > 0:
                self.bc.inc_volume(v)
            else:
                self.bc.dec_volume(-v)

    def parse_p(self) -> None:
        "Pan"
        p, is_p_relative = self._parse_pan_value()
        if not is_p_relative:
            if self._test_next_token_matches("v"):
                v, is_v_relative = self._parse_volume_value()
                self._set_pan_and_volume(p, is_p_relative, v, is_v_relative)
            else:
                self.bc.set_pan(p)
        else:
            # p is relative
            if p > 0:
                self.bc.inc_pan(p)
            else:
                self.bc.dec_pan(-p)

    def _parse_volume_value(self) -> tuple[int, bool]:
        v, is_v_relative = self.tokenizer.parse_relative_int()

        # Validating volume value here to ensure error message location is correct
        abs_v = abs(v)
        if abs_v < 0 or abs_v > MAX_VOLUME:
            raise RuntimeError(f"Volume out of range (1-{MAX_VOLUME})")
        return v, is_v_relative

    def _parse_pan_value(self) -> tuple[int, bool]:
        p, is_p_relative = self.tokenizer.parse_relative_int()

        # Validating pan value here to ensure error message location is correct
        abs_p = abs(p)
        if abs_p < 0 or abs_p > MAX_PAN:
            raise RuntimeError(f"Pan out of range (1-{MAX_PAN})")
        return p, is_p_relative

    def _set_pan_and_volume(self, p: int, is_p_relative: bool, v: int, is_v_relative: bool) -> None:
        if not is_p_relative and not is_v_relative:
            self.bc.set_pan_and_volume(p, v)
        else:
            if not is_v_relative:
                self.bc.set_volume(v)
            elif v > 0:
                self.bc.inc_volume(v)
            else:
                self.bc.dec_volume(-v)

            if not is_p_relative:
                self.bc.set_pan(p)
            elif p > 0:
                self.bc.inc_pan(p)
            else:
                self.bc.dec_pan(-p)

    def parse_increase_octave(self) -> None:
        self.octave = min(MAX_OCTAVE, self.octave + 1)

    def parse_decrease_octave(self) -> None:
        self.octave = max(MIN_OCTAVE, self.octave - 1)

    def parse_broken_chord(self) -> None:
        chord_notes: Final = self._parse_list_of_pitches("}}")

        if len(chord_notes) < 2:
            self.add_error("Expected 2 or more pitches in a broken chord")

        chord_end_pos: Final = self._pos

        total_length: Final = self.parse_note_length()

        note_length = 1
        tie = True
        if self._test_next_token_matches_no_newline(","):
            self.set_error_pos()
            nl = self.tokenizer.parse_optional_note_length()
            if nl:
                note_length = self.calculate_note_length(nl)

            if self._test_next_token_matches_no_newline(","):
                self.set_error_pos()
                tie = self.tokenizer.parse_bool()

        # Ensure error message for loop errors are at the correct location
        self._pos = chord_end_pos

        expected_tick_counter: Final = self.tick_counter + total_length

        # If tie is true, a keyoff note is added to the end of the loop
        notes_in_loop = total_length // note_length - int(tie)

        # `ticks_remaining` cannot be 1, remove a note to from the loop to prevent this from happening
        if note_length == 1:
            notes_in_loop -= 1

        n_loops = notes_in_loop // len(chord_notes)
        break_point: Final = notes_in_loop % len(chord_notes)
        keyoff: Final = not tie

        if break_point != 0:
            n_loops += 1

        if n_loops < 2:
            raise RuntimeError("Broken chord total length too short (a minimum of 2 loops are required)")

        self._start_loop(n_loops)

        for i, n in enumerate(chord_notes):
            if i == break_point and break_point > 0:
                self.parse_skip_last_loop()
            self._play_note(n, keyoff, note_length)

        self._end_loop()

        ticks_remaining: Final = expected_tick_counter - self.tick_counter
        if ticks_remaining > 0:
            # The last note to play is always a keyoff note
            next_note = chord_notes[(break_point + 1) % len(chord_notes)]
            self._play_note(next_note, True, ticks_remaining)

        if self.tick_counter != expected_tick_counter:
            raise RuntimeError("Broken chord tick_count mismatch")

    def parse_portamento(self) -> None:
        portamento_start_pos: Final = self._pos

        notes: Final = self._parse_list_of_pitches("}")

        total_ticks: Final = self.parse_note_length()
        delay_ticks = 0
        speed = None

        if self._test_next_token_matches_no_newline(","):
            self.set_error_pos()
            nl = self.tokenizer.parse_optional_note_length()

            if nl:
                delay_ticks = self.calculate_note_length(nl)
                if delay_ticks >= total_ticks:
                    self.add_error("Portamento delay must be < portamento length")
                    delay_ticks = 0

            if self._test_next_token_matches_no_newline(","):
                self.set_error_pos()
                speed = self.tokenizer.parse_uint()
            else:
                # Only show "Missing delay length" error if there is no third parameter
                if nl is None:
                    self.add_error("Missing delay length")

        after_ticks, slur_note = self._parse_tie_and_slur()

        if len(notes) != 2:
            raise RuntimeError("Only two notes are allowed in a portamento")

        # Ensure error message at the correct location
        self._pos = portamento_start_pos

        p_ticks: Final = total_ticks - delay_ticks
        self._play_portamento(notes[0], notes[1], slur_note, speed, delay_ticks, p_ticks, after_ticks)

    def parse_divider(self) -> None:
        """
        Skip divider (pipe `|`) tokens.
        They do not do anything and exist for aesthetic reasons (ie, splitting a line into bars).
        """
        pass

    PARSERS: Final = {
        "!": parse_exclamation_mark,
        "[": parse_start_loop,
        ":": parse_skip_last_loop,
        "]": parse_end_loop,
        "@": parse_at,
        "C": parse_change_whole_note_length,
        "n": parse_n,
        "l": parse_l,
        "o": parse_o,
        "r": parse_r,
        ">": parse_increase_octave,
        "<": parse_decrease_octave,
        "v": parse_v,
        "p": parse_p,
        "Q": parse_quantize,
        "_": parse_underscore,
        "__": parse_double_underscore,
        "{{": parse_broken_chord,
        "{": parse_portamento,
        "|": parse_divider,
    }

    def parse_mml(self) -> None:
        while not self.tokenizer.at_end():
            self.tokenizer.skip_new_line()
            self.set_error_pos()

            try:
                if note := self.tokenizer.parse_optional_note():
                    self.parse_note(note)
                else:
                    t = self.tokenizer.next_token()
                    if t:
                        p = self.PARSERS.get(t)
                        if p:
                            p(self)
                        else:
                            self.add_error(f"Unknown token: {t}")
            except Exception as e:
                self.add_error(str(e))

        if self.loop_stack:
            self.add_error("Missing loop end ]")

        if self.is_subroutine:
            self.bc.return_from_subroutine()
        else:
            self.bc.end()

    def channel_data(self) -> ChannelData:
        return ChannelData(
            name=self.channel_name,
            bytecode=self.bc.bytecode,
            tick_counter=self.tick_counter,
            max_nested_loops=self.max_nested_loops,
            last_instrument=self.instrument,
        )


def parse_mml_subroutine(
    channel_lines: list[Line],
    channel_name: str,
    metadata: MetaData,
    instruments: OrderedDict[str, Instrument],
    pitch_table: PitchTable,
    bc_mappings: BcMappings,
    error_list: list[MmlError],
) -> ChannelData:
    parser = MmlChannelParser(channel_lines, channel_name, metadata, instruments, None, pitch_table, bc_mappings, error_list)
    parser.parse_mml()
    return parser.channel_data()


def parse_mml_channel(
    channel_lines: list[Line],
    channel_name: str,
    metadata: MetaData,
    instruments: OrderedDict[str, Instrument],
    subroutines: list[ChannelData],
    pitch_table: PitchTable,
    bc_mappings: BcMappings,
    error_list: list[MmlError],
) -> ChannelData:
    parser = MmlChannelParser(channel_lines, channel_name, metadata, instruments, subroutines, pitch_table, bc_mappings, error_list)
    parser.parse_mml()
    return parser.channel_data()


def compile_mml(mml_text: str, samples: SamplesJson) -> MmlData:
    pitch_table: Final = build_pitch_table(samples)

    mml_lines: Final = split_lines(mml_text)

    error_list: Final[list[MmlError]] = list()

    metadata: Final = parse_headers(mml_lines.headers, error_list)

    instruments: Final = parse_instruments(mml_lines.instruments, samples)

    bc_mappings: Final = create_bc_mappings(samples)

    subroutines: Final = list()
    for s_name, c_lines in mml_lines.subroutines.items():
        subroutines.append(parse_mml_subroutine(c_lines, s_name, metadata, instruments, pitch_table, bc_mappings, error_list))

    # add subroutines to bc_mappings
    for i, s_name in enumerate(mml_lines.subroutines.keys()):
        bc_mappings.subroutines[s_name] = i

    channels: Final = list()
    for i, c_lines in enumerate(mml_lines.channels):
        if c_lines:
            channel_name = chr(FIRST_CHANNEL_ORD + i)
            channels.append(
                parse_mml_channel(c_lines, channel_name, metadata, instruments, subroutines, pitch_table, bc_mappings, error_list)
            )

    if error_list:
        raise CompileError(error_list)

    return MmlData(metadata, instruments, subroutines, channels)
