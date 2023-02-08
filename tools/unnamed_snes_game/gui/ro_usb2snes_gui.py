# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import os.path
from typing import Any, Final, Optional

import tkinter as tk
import tkinter.ttk as ttk
import tkinter.messagebox

from .errors_tab import ErrorsTab

from ..resources_over_usb2snes import FsWatcherSignals
from ..resources_compiler import DataStore
from .. import metasprite as ms


# https://tkdocs.com/tutorial/eventloop.html#threads
if tkinter.Tcl().eval("set tcl_platform(threaded)") != "1":
    raise RuntimeError("Tcl/Tk is not compiled with threading")


class GuiSignals(FsWatcherSignals):
    RES_COMPILED_EVENT_NAME: Final = "<<ResCompiled>>"

    def __init__(self, root: tk.Tk):
        super().__init__()
        self.root: Final = root

    def signal_resource_compiled(self) -> None:
        # `event_generate` is thread safe
        # https://tkdocs.com/tutorial/eventloop.html#threads
        self.root.event_generate(self.RES_COMPILED_EVENT_NAME)


class Rou2sWindow:
    def __init__(self, data_store: DataStore):
        self.data_store: Final = data_store

        self._window: Final = tk.Tk()

        self.signals: Final = GuiSignals(self._window)

        self._window.title("Resources over usb2snes")
        self._window.minsize(width=1000, height=700)

        self._window.columnconfigure(0, weight=1)
        self._window.rowconfigure(0, weight=1)

        self._notebook: Final = ttk.Notebook(self._window)
        self._notebook.grid(row=0, column=0, columnspan=2, sticky=tk.NSEW)

        self._errors_tab: Final = ErrorsTab(data_store, self._notebook)
        self._notebook.add(self._errors_tab.frame, text="Errors")

        # Signals
        self._window.bind(GuiSignals.RES_COMPILED_EVENT_NAME, self._errors_tab.on_resource_compiled)

    def mainloop(self) -> None:
        self._window.mainloop()
