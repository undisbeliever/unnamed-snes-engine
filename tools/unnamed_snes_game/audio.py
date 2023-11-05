# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import os.path
import re
import subprocess
from typing import Final

from .json_formats import Filename, Name, Mappings, AudioProject

COMMON_AUDIO_DATA_RESOURCE_NAME: Final = "__null__common_data__"

TAD_COMPILER_BINARY_PATH: Final = "tad-compiler"


class TadCompilerError(Exception):
    pass


def _verify_tad_compiler_binary(tad_compiler_binary: str) -> None:
    command = (tad_compiler_binary, "--version")
    c = subprocess.run(command, capture_output=True, text=True, timeout=3)

    if c.returncode != 0:
        raise RuntimeError("tad_compiler_binary did not return EXIT_SUCCESS")
    if re.match(r"^tad-compiler \d+\.\d+\.\d+$", c.stdout.strip()) is None:
        raise RuntimeError("tad_compiler_binary is not tad-compiler")


class AudioCompiler:
    def __init__(self, mappings: Mappings, project_filename: Filename):
        tad_compiler_binary: Final = os.path.join(mappings.tad_binary_directory, TAD_COMPILER_BINARY_PATH)

        _verify_tad_compiler_binary(tad_compiler_binary)

        self._tad_compiler_binary: Final = tad_compiler_binary
        self._project_filename: Final = project_filename

    def compile_common_audio_data(self) -> bytes:
        command = (self._tad_compiler_binary, "common", "--stdout", self._project_filename)
        c = subprocess.run(command, capture_output=True, timeout=3)

        if c.returncode == 0:
            return c.stdout
        else:
            raise TadCompilerError(c.stderr.decode("UTF-8"))

    def compile_song(self, r_name: Name) -> bytes:
        command = (self._tad_compiler_binary, "song", "--stdout", self._project_filename, r_name)
        c = subprocess.run(command, capture_output=True, timeout=3)

        if c.returncode == 0:
            return c.stdout
        else:
            raise TadCompilerError(c.stderr.decode("UTF-8"))
