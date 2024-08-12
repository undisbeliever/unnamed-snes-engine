# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import sys
from io import StringIO

from typing import Final, Optional, TextIO, Union
from abc import abstractmethod

from .ansi_color import NoAnsiColors, AnsiColors


class MultilineError(Exception):
    @abstractmethod
    def print_indented(self, fp: TextIO) -> None:
        pass

    def string_indented(self) -> str:
        with StringIO() as f:
            self.print_indented(f)
            return f.getvalue()


class SimpleMultilineError(MultilineError):
    def __init__(self, short_message: str, errors: list[str]):
        self.short_message: Final = short_message
        self.errors: Final = errors

    def print_indented(self, fp: TextIO) -> None:
        if len(self.errors) == 1:
            fp.write(f"{ self.short_message }: { self.errors[0] }")
        else:
            fp.write(f"{ self.short_message }:")
            for e in self.errors:
                fp.write(f"\n    { e }")


class FileError(Exception):
    def __init__(self, message: str, path: tuple[str, ...]):
        self.message: Final = message
        self.path: Final = path

    def __str__(self) -> str:
        return f"{ ' '.join(self.path) }: { self.message }"


def print_error(msg: str, e: Optional[Union[str, Exception]] = None, fp: Optional[TextIO] = None) -> None:
    if fp is None:
        fp = sys.stderr

    ac = AnsiColors if fp.isatty() else NoAnsiColors

    fp.write(ac.BOLD + ac.BRIGHT_RED)
    fp.write(msg)
    if e:
        fp.write(": ")
        fp.write(ac.NORMAL)
        if isinstance(e, str):
            fp.write(e)
        elif isinstance(e, ValueError) or isinstance(e, RuntimeError):
            fp.write(str(e))
        elif isinstance(e, FileError):
            if e.path:
                fp.write(ac.BOLD + ac.BRIGHT_WHITE)
                fp.write(e.path[0])
                fp.write(ac.NORMAL)
                if len(e.path) > 1:
                    fp.write(f" { ': '.join(e.path[1:]) }: ")
                else:
                    fp.write(": ")
            fp.write(ac.BRIGHT_RED)
            fp.write(e.message)
        elif isinstance(e, MultilineError):
            e.print_indented(fp)
        else:
            fp.write(f"{ type(e).__name__ }({ e })")
    fp.write(ac.RESET + "\n")
