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


NAME_REGEX: Final = re.compile(r"[a-zA-Z][a-zA-Z0-9_]*$")


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


def _read_optional_int(v: Union[str, int, float, None]) -> Optional[int]:
    if v is None:
        return None
    else:
        return int(v)


#
# samples.json
# ============
#


@dataclass
class Adsr:
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

    # Duplicates `N` blocks to the end of the sample in an attempt to improve the sample quality of the first-looping BRR block.
    # Increases the sample size by `N * 9` bytes.
    # Most samples created by this hack will not loop perfectly which adds low-frequency oscillation to the sample.
    # (Hence the name `duple_block_hack`.)
    dupe_block_hack: Optional[int]

    first_octave: int
    last_octave: int

    adsr: Optional[Adsr]
    # ::TODO figure out how the gain byte works::
    gain: Optional[int]


@dataclass
class SamplesJson:
    instruments: list[Instrument]


def _read_optional_adsr(s: Optional[str]) -> Optional[Adsr]:
    if s is None:
        return None

    if type(s) != str:
        raise ValueError("Expected a string containing 4 values (and optionally prepended with D or E)")

    values = s.strip().upper().split()

    if len(values) != 4:
        raise ValueError("ADSR: Expected 4 integers")

    try:
        return Adsr(int(values[0]), int(values[1]), int(values[2]), int(values[3]))
    except ValueError as e:
        raise ValueError(f"ADSR: {e}")


def _read_instruments(json_input: dict[str, list[dict[str, Any]]], filename: Filename) -> list[Instrument]:
    JSON_KEY: Final = "instruments"

    dirname: Final = os.path.dirname(filename)

    j_instruments = json_input.get(JSON_KEY)
    if type(j_instruments) != list:
        raise JsonError(filename, JSON_KEY, "Missing instruments")

    out: list[Instrument] = list()

    for i, ji in enumerate(j_instruments):
        if type(ji) != dict:
            raise JsonError(filename, JSON_KEY, "Expected a dictionary")

        try:
            inst = Instrument(
                name=parse_name(ji["name"]),
                source=_read_filename(ji["source"], dirname),
                looping=bool(ji["looping"]),
                dupe_block_hack=_read_optional_int(ji.get("dupe_block_hack")),
                freq=float(ji["freq"]),
                loop_point=_read_optional_int(ji.get("loop_point")),
                first_octave=int(ji["first_octave"]),
                last_octave=int(ji["last_octave"]),
                adsr=_read_optional_adsr(ji.get("adsr")),
                gain=_read_optional_int(ji.get("gain")),
            )
            out.append(inst)

        except KeyError as e:
            raise JsonError(filename, f"{JSON_KEY} {i}", f"Missing key: {e}")
        except Exception as e:
            raise JsonError(filename, f"{JSON_KEY} {i}", str(e))

    return out


def load_samples_json(filename: Filename) -> SamplesJson:
    with open(filename, "r") as fp:
        json_input = json.load(fp)

    return SamplesJson(instruments=_read_instruments(json_input, filename))
