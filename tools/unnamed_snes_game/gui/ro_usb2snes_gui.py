# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


from typing import Any, Final

import tkinter as tk
import tkinter.ttk as ttk
import tkinter.messagebox

from .errors_tab import ErrorsTab
from .gamestate_tab import GameStateTab

from ..resources_over_usb2snes import FsWatcherSignals, BgThread
from ..data_store import DataStore


# https://tkdocs.com/tutorial/eventloop.html#threads
if tkinter.Tcl().eval("set tcl_platform(threaded)") != "1":
    raise RuntimeError("Tcl/Tk is not compiled with threading")


class GuiSignals(FsWatcherSignals):
    MAPPINGS_CHANGED_EVENT_NAME: Final = "<<MappingsChanged>>"
    RES_COMPILED_EVENT_NAME: Final = "<<ResCompiled>>"
    STATUS_CHANGED_EVENT_NAME: Final = "<<StatusChanged>>"
    WS_CONNECTION_CHANGED_EVENT_NAME: Final = "<<WsConnectionChanged>>"
    BG_THREAD_STOPPED_EVENT_NAME: Final = "<<BgThreadStopped>>"
    READ_GAMESTATE_DATA_EVENT_NAME: Final = "<<ReadGamestateData>>"

    def __init__(self, root: tk.Tk):
        super().__init__()
        self.root: Final = root

    def signal_status_changed(self) -> None:
        # `event_generate` is thread safe
        # https://tkdocs.com/tutorial/eventloop.html#threads
        self.root.event_generate(self.STATUS_CHANGED_EVENT_NAME)

    def signal_mappings_changed(self) -> None:
        self.root.event_generate(self.MAPPINGS_CHANGED_EVENT_NAME)

    def signal_resource_compiled(self) -> None:
        self.root.event_generate(self.RES_COMPILED_EVENT_NAME)

    def signal_ws_connection_changed(self) -> None:
        self.root.event_generate(self.WS_CONNECTION_CHANGED_EVENT_NAME)

    def signal_bg_thread_stopped(self) -> None:
        self.root.event_generate(self.BG_THREAD_STOPPED_EVENT_NAME)

    def signal_read_gamestate_data(self) -> None:
        self.root.event_generate(self.READ_GAMESTATE_DATA_EVENT_NAME)


class StatusBar:
    def __init__(self, signals: GuiSignals, parent: tk.Tk):
        self.signals: Final = signals

        self.frame: Final = tk.Frame(parent)
        self.frame.columnconfigure(2, weight=1)
        self.frame.columnconfigure(4, weight=2)

        status1: Final = tk.Label(self.frame, text="FS Watcher: ")
        status1.grid(row=0, column=1, sticky=tk.W)

        self.fs_status: Final = tk.StringVar()
        fs_status_l: Final = tk.Label(self.frame, textvariable=self.fs_status, anchor=tk.W, borderwidth=2, relief=tk.SUNKEN, width=15)
        fs_status_l.grid(row=0, column=2, sticky=tk.EW)

        status2: Final = tk.Label(self.frame, text="  usb2snes: ")
        status2.grid(row=0, column=3, sticky=tk.W)

        self.usb2snes_status: Final = tk.StringVar()
        usb2snes_status_l: Final = tk.Label(
            self.frame, textvariable=self.usb2snes_status, anchor=tk.W, borderwidth=2, relief=tk.SUNKEN, width=25
        )
        usb2snes_status_l.grid(row=0, column=4, sticky=tk.EW)

        self.button: Final = tk.Button(self.frame, width=9, command=self.on_button_pressed)
        self.button.grid(row=0, column=5)

        self.on_status_changed(None)

    def on_status_changed(self, event: Any) -> None:
        fs_status, usb2snes_status = self.signals.get_status()
        self.fs_status.set(fs_status)
        self.usb2snes_status.set(usb2snes_status)

    def on_ws_connected_status_changed(self, event: Any) -> None:
        self.button["text"] = "Disconnect" if self.signals.is_connected() else "Connect"

    def on_button_pressed(self) -> None:
        if self.signals.is_connected():
            self.signals.send_disconnect_event()
        else:
            self.signals.send_connect_event()


class Rou2sWindow:
    def __init__(self, data_store: DataStore):
        self.data_store: Final = data_store

        self._window: Final = tk.Tk()

        self.signals: Final = GuiSignals(self._window)

        self._running: bool = False
        self._bg_threads: Final[list[BgThread]] = list()
        self._n_stopped_threads = 0

        self._window.protocol("WM_DELETE_WINDOW", self._on_close_request)

        self._window.title("Resources over usb2snes")
        self._window.minsize(width=400, height=400)

        self._window.columnconfigure(0, weight=1)
        self._window.rowconfigure(2, weight=1)

        self._statusbar: Final = StatusBar(self.signals, self._window)
        self._statusbar.frame.grid(row=0, column=0, sticky=tk.EW)

        separator: Final = ttk.Separator(self._window, orient=tk.HORIZONTAL)
        separator.grid(row=1, column=0, sticky=tk.EW)

        self._notebook: Final = ttk.Notebook(self._window)
        self._notebook.grid(row=2, column=0, sticky=tk.NSEW)

        self._errors_tab: Final = ErrorsTab(data_store, self._notebook)
        self._notebook.add(self._errors_tab.frame, text="Errors")

        self._gamestate_tab: Final = GameStateTab(data_store, self.signals, self._notebook)
        self._notebook.add(self._gamestate_tab.frame, text="GameState")

        # Signals
        self._window.bind(GuiSignals.STATUS_CHANGED_EVENT_NAME, self._statusbar.on_status_changed)
        self._window.bind(GuiSignals.WS_CONNECTION_CHANGED_EVENT_NAME, self._statusbar.on_ws_connected_status_changed)
        self._window.bind(GuiSignals.RES_COMPILED_EVENT_NAME, self._errors_tab.on_resource_compiled)
        self._window.bind(GuiSignals.MAPPINGS_CHANGED_EVENT_NAME, self._gamestate_tab.on_mappings_changed)
        self._window.bind(GuiSignals.READ_GAMESTATE_DATA_EVENT_NAME, self._gamestate_tab.on_gamestate_data_read)
        self._window.bind(GuiSignals.BG_THREAD_STOPPED_EVENT_NAME, self._on_bg_thread_stopped)

    def add_bg_thread(self, t: BgThread) -> None:
        if self._running:
            raise RuntimeError("Cannot add a BG thread after mainloop() has started")
        self._bg_threads.append(t)

    def _on_close_request(self) -> None:
        self.signals.send_quit_event()
        # Cannot destroy _window while a BG threads is active, it might generate an event (`event_generate()` call in `GuiSignals).
        # Cannot put a join here, system deadlocks in when a BG Thread generates an event.
        #
        # Instead, closed BgThreads will send a `GuiSignals.BG_THREAD_STOPPED_EVENT_NAME` event when they are finished.
        # (which is handled by `_on_bg_thread_stopped()`)

    # Close the window when all of the BG threads have ended
    def _on_bg_thread_stopped(self, event: Any) -> None:
        self._n_stopped_threads += 1
        if self._n_stopped_threads >= len(self._bg_threads):
            self._window.quit()

    def __start_bg_threads(self) -> None:
        self._running = True
        for t in self._bg_threads:
            t.start()

    def mainloop(self) -> None:
        # Start BG threads after the Tk mainloop has started
        self._window.after(10, self.__start_bg_threads)

        self._window.mainloop()
