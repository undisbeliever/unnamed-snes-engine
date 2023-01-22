# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import json
import os
import re

from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Final, Optional, TypeAlias, Union


Name: Final[TypeAlias] = str
Filename: Final[TypeAlias] = str


class JsonError(RuntimeError):
    def __init__(self, filename: Filename, pos: Optional[str], message: str):
        if pos:
            super().__init__(f"JSON ERROR {filename}: {pos}: {message}")
        else:
            super().__init__(f"JSON ERROR {filename}: {pos}: {message}")


NAME_REGEX: Final = re.compile(r'[a-zA-Z][a-zA-Z0-9_]*$')


def parse_name(s: Any) -> Name:
    name = str(s)
    if NAME_REGEX.match(name):
        return name
    else:
        raise ValueError(f"Not a name: {name[40]}")


def _read_filename(s: Any, dirname: str) -> Name:
    if type(s) != str:
        raise TypeError(f"Expected a filename string: got {type(s)}")
    return os.path.join(dirname, s)


def _read_int(v: Union[str, int, float]) -> int:
    if type(v) == int:
        return v
    elif type(v) == str:
        return int(v, 0)
    else:
        raise ValueError(f"Expectd an integer got {v}")


def _read_optional_int(v: Union[str, int, float, None]) -> Optional[int]:
    if v is None:
        return None
    else:
        return _read_int(v)


#
# mappings.json
# =============
#

@dataclass
class Mappings:
    sound_effects: list[Name]


def _read_mapping_list(json_input: dict[str, Any], key: str) -> list[Name]:
    json_list = json_input.get(key)

    if type(json_list) != list:
        raise ValueError(f"JSON Error: {key}: Expected a list of names")

    out = list()
    for n in json_list:
        out.append(parse_name(n))

    return out


def load_mapping_json(filename: Filename) -> Mappings:
    with open(filename, 'r') as fp:
        json_input = json.load(fp)

    return Mappings(
        _read_mapping_list(json_input, 'sound-effects')
    )


#
# samples.json
# ============
#


@dataclass
class Adsr:
    enabled: bool
    attack: int
    decay: int
    # Names taken from Anonie's S-DSP Doc
    sustain_level: int
    sustain_rate: int


@dataclass
class Instrument:
    name: Name

    source: Filename
    freq: float | int
    looping: bool
    loop_point: Optional[int]

    first_octave: int
    last_octave: int

    adsr: Adsr
    # ::TODO figure out how the gain byte works::
    gain: int


@dataclass
class SamplesJson:
    instruments: list[Instrument]


def _read_adsr(s: str) -> Adsr:
    if type(s) != str:
        raise ValueError('Expected a string containing 4 values (and optionally prepended with D or E)')

    values = s.strip().upper().split()

    enabled = True

    if len(values) == 5:
        e = values.pop(0)
        if e == 'D':
            enabled = True
        elif e == 'E':
            enabled = True
        else:
            raise ValueError(f"ADSR: Unknown value: {e}")

    try:
        return Adsr(
                enabled,
                int(values[0], 0),
                int(values[1], 0),
                int(values[2], 0),
                int(values[3], 0)
        )
    except ValueError as e:
        raise ValueError(f"ADSR: {e}")


def _read_instruments(json_input: dict[str, list[dict[str, Any]]], filename: Filename) -> list[Instrument]:
    JSON_KEY: Final = 'instruments'

    dirname: Final = os.path.dirname(filename)

    j_instruments = json_input.get(JSON_KEY)
    if type(j_instruments) != list:
        raise JsonError(filename, JSON_KEY, 'Missing instruments')

    out: list[Instrument] = list()

    for i, ji in enumerate(j_instruments):
        if type(ji) != dict:
            raise JsonError(filename, JSON_KEY, 'Expected a dictionary')

        try:
            inst = Instrument(
                name=parse_name(ji['name']),
                source=_read_filename(ji['source'], dirname),
                looping=bool(ji['looping']),
                freq=float(ji['freq']),
                loop_point=_read_optional_int(ji.get('loop_point')),
                first_octave=_read_int(ji['first_octave']),
                last_octave=_read_int(ji['last_octave']),
                adsr=_read_adsr(ji['adsr']),
                gain=_read_int(ji['gain']),
            )
            out.append(inst)

        except KeyError as e:
            raise JsonError(filename, f"{JSON_KEY} {i}", f"Missing key: {e}")
        except Exception as e:
            raise JsonError(filename, f"{JSON_KEY} {i}", str(e))

    return out


def load_samples_json(filename: Filename) -> SamplesJson:
    with open(filename, 'r') as fp:
        json_input = json.load(fp)

    return SamplesJson(
            instruments=_read_instruments(json_input, filename)
    )


