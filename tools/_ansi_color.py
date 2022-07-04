# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import os
import sys
from typing import final, Final, Type, Union


@final
class NoAnsiColors:
    RESET           : Final = ''

    BOLD            : Final = ''
    NORMAL          : Final = ''

    BLACK           : Final = ''
    RED             : Final = ''
    GREEN           : Final = ''
    YELLOW          : Final = ''
    BLUE            : Final = ''
    MAGENTA         : Final = ''
    CYAN            : Final = ''
    WHITE           : Final = ''
    BRIGHT_BLACK    : Final = ''
    BRIGHT_RED      : Final = ''
    BRIGHT_GREEN    : Final = ''
    BRIGHT_YELLOW   : Final = ''
    BRIGHT_BLUE     : Final = ''
    BRIGHT_MAGENTA  : Final = ''
    BRIGHT_CYAN     : Final = ''
    BRIGHT_WHITE    : Final = ''



@final
class ForceAnsiColors:
    RESET           : Final = '\033[0m'

    BOLD            : Final = '\033[1m'
    NORMAL          : Final = '\033[22m'

    BLACK           : Final = '\033[30m'
    RED             : Final = '\033[31m'
    GREEN           : Final = '\033[32m'
    YELLOW          : Final = '\033[33m'
    BLUE            : Final = '\033[34m'
    MAGENTA         : Final = '\033[35m'
    CYAN            : Final = '\033[36m'
    WHITE           : Final = '\033[37m'
    BRIGHT_BLACK    : Final = '\033[90m'
    BRIGHT_RED      : Final = '\033[91m'
    BRIGHT_GREEN    : Final = '\033[92m'
    BRIGHT_YELLOW   : Final = '\033[93m'
    BRIGHT_BLUE     : Final = '\033[94m'
    BRIGHT_MAGENTA  : Final = '\033[95m'
    BRIGHT_CYAN     : Final = '\033[96m'
    BRIGHT_WHITE    : Final = '\033[97m'



def should_use_ansi_colors() -> bool:
    return (
        os.getenv('NO_COLOR', '') == ''
        and sys.stdout.isatty()
        and sys.stderr.isatty()
    )


# Assumes `sys.stdout` and `sys.stderr` is never changed.
AnsiColors : Union[Type[NoAnsiColors], Type[ForceAnsiColors]] = ForceAnsiColors if should_use_ansi_colors() else NoAnsiColors;


