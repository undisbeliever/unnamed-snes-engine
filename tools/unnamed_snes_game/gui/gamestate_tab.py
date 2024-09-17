# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

from . import gui
from ..data_store import DataStore
from ..gamestate import gamestate_header, read_flags, FLAG_ARRAY_SIZE, PLAYER_POSITION, PLAYER_POSITION_OFFSET, U8_VARS_OFFSET
from ..resources_over_usb2snes import FsWatcherSignals, Rou2sCommands, Command
from ..json_formats import Name, GameState, GameStateVar

import struct
import itertools
from collections import OrderedDict
from typing import Any, Final, Generator, Optional

import tkinter as tk
import tkinter.ttk as ttk
import tkinter.messagebox


def _validate_u8(new_value: str) -> bool:
    if not new_value:
        return True
    try:
        value = int(new_value)
        return 0 <= value <= 0xFF
    except ValueError:
        return False


def _validate_u16(new_value: str) -> bool:
    if not new_value:
        return True
    try:
        value = int(new_value)
        return 0 <= value <= 0xFFFF
    except ValueError:
        return False


class VarColumn:
    def __init__(self, parent: tk.Frame, column: int) -> None:
        self.__parent: Final = parent
        self.__column: Final = column

        self.validate_u8: Final = (parent.register(_validate_u8), "%P")
        self.validate_u16: Final = (parent.register(_validate_u16), "%P")

        self.__disabled = False
        self.__player_vars: Final[list[tuple[tk.Label, tk.Entry, tk.IntVar]]] = list()
        self.__u8_vars: Final[list[tuple[tk.Label, tk.Entry, tk.IntVar]]] = list()
        self.__u16_vars: Final[list[tuple[tk.Label, tk.Entry, tk.IntVar]]] = list()

        pp_label: Final = tk.Label(parent, text="playerPosition:", font="bold")
        pp_label.grid(row=0, column=self.__column, columnspan=3, sticky=tk.W)

        self.__update_var_widget_size_and_names(self.__player_vars, 1, len(PLAYER_POSITION), PLAYER_POSITION, self.validate_u8)

        u8_label: Final = tk.Label(parent, text="u8_vars:", font="bold")
        u8_label.grid(row=self.FIRST_U8_ROW - 1, column=self.__column, columnspan=3, sticky=tk.W)

        self.__u16_label: Final = tk.Label(parent, text="u16_vars:", font="bold")

    def disable_all(self) -> None:
        if not self.__disabled:
            self.__disabled = True
            for l, w, v in itertools.chain(self.__player_vars, self.__u8_vars, self.__u16_vars):
                w["state"] = "disabled"

    def enable_all(self) -> None:
        if self.__disabled:
            self.__disabled = False
            for l, w, v in itertools.chain(self.__player_vars, self.__u8_vars, self.__u16_vars):
                w["state"] = "normal"

    def __update_var_widget_size_and_names(
        self,
        widgets: list[tuple[tk.Label, tk.Entry, tk.IntVar]],
        row: int,
        array_len: int,
        gs_vars: OrderedDict[Name, GameStateVar],
        validator: Any,
    ) -> None:
        if len(widgets) != array_len:
            for i in range(len(widgets) - array_len):
                l, w, v = widgets.pop()
                l.destroy()
                w.destroy()
                del v

            for l, w, v in widgets:
                l.grid(row=row, column=self.__column + 1, sticky=tk.W)
                w.grid(row=row, column=self.__column + 2, sticky=tk.EW)
                row += 1

            for i in range(len(widgets), array_len):
                l = tk.Label(self.__parent)
                l.grid(row=row, column=self.__column + 1, sticky=tk.W)

                v = tk.IntVar()

                w = tk.Entry(self.__parent, textvariable=v, width=8, justify=tk.RIGHT, validate=tk.ALL, validatecommand=validator)
                w.grid(row=row, column=self.__column + 2, sticky=tk.EW)

                widgets.append((l, w, v))
                row += 1

        for name, flag in gs_vars.items():
            l, w, v = widgets[flag.var_index]
            l["text"] = name

    FIRST_U8_ROW: Final = 2 + len(PLAYER_POSITION)

    def rebuild_widgets(self, gs: GameState) -> None:
        self.__update_var_widget_size_and_names(self.__u8_vars, self.FIRST_U8_ROW, gs.u8_array_len, gs.u8_vars, self.validate_u8)

        row = self.FIRST_U8_ROW + gs.u8_array_len
        self.__u16_label.grid(row=row, column=self.__column, columnspan=3, sticky=tk.W)
        row += 1

        self.__update_var_widget_size_and_names(self.__u16_vars, row, gs.u16_array_len, gs.u16_vars, self.validate_u16)

    @staticmethod
    def _update_u8_values(gs_data: bytes, offset: int, widgets: list[tuple[tk.Label, tk.Entry, tk.IntVar]]) -> None:
        for i, (l, w, v) in enumerate(widgets):
            v.set(gs_data[offset + i])
            w["state"] = "normal"

    @staticmethod
    def _update_u16_values(gs_data: bytes, offset: int, widgets: list[tuple[tk.Label, tk.Entry, tk.IntVar]]) -> None:
        for l, w, v in widgets:
            v.set(gs_data[offset] | (gs_data[offset + 1] << 8))
            offset += 2
            w["state"] = "normal"

    def update_widget_values(self, gs_data: bytes) -> None:
        VarColumn._update_u8_values(gs_data, PLAYER_POSITION_OFFSET, self.__player_vars)
        VarColumn._update_u8_values(gs_data, U8_VARS_OFFSET, self.__u8_vars)
        VarColumn._update_u16_values(gs_data, U8_VARS_OFFSET + len(self.__u8_vars), self.__u16_vars)
        self.__disabled = False

    def player_pos_data(self) -> bytes:
        return bytes(v.get() for l, w, v in self.__player_vars)

    def u8_vars_data(self) -> bytes:
        return bytes(v.get() for l, w, v in self.__u8_vars)

    def u16_vars_data(self) -> bytes:
        return struct.pack(
            f"<{len(self.__u16_vars)}H",
            *(v.get() for l, w, v in self.__u16_vars),
        )


class CheckboxColumn:
    def __init__(self, parent: tk.Frame, label: str, column: int) -> None:
        self.__parent: Final = parent
        self.__column: Final = column

        self.__label: Final = tk.Label(parent, text=label, font="bold")
        self.__label.grid(row=0, column=self.__column, sticky=tk.EW)

        self.__disabled = False
        self.__check_buttons: Final[list[tuple[tk.Checkbutton, tk.IntVar]]] = list()

    def disable_all(self) -> None:
        if not self.__disabled:
            self.__disabled = True
            for c, v in self.__check_buttons:
                c["state"] = "disabled"

    def enable_all(self) -> None:
        if self.__disabled:
            self.__disabled = False
            for c, v in self.__check_buttons:
                c["state"] = "normal"

    def update_checkbox_names(self, flags: OrderedDict[Name, GameStateVar]) -> None:
        cbuttons = self.__check_buttons
        n_buttons: Final = next(reversed(flags.values())).var_index + 1 if flags else 0

        for i in range(len(cbuttons), n_buttons):
            v = tk.IntVar()
            c = tk.Checkbutton(self.__parent, variable=v)
            c.grid(row=i + 1, column=self.__column, sticky=tk.W)
            cbuttons.append((c, v))

        prev_index = 0

        for name, flag in flags.items():
            for i in range(prev_index, flag.var_index):
                cbuttons[i][0]["text"] = ""
            prev_index = flag.var_index + 1

            cbuttons[flag.var_index][0]["text"] = name

        for i in range(prev_index, len(cbuttons)):
            cbuttons[i][0]["text"] = ""

    def update_checkbox_values(self, gs: bytes, flag_array: int) -> None:
        for f, (c, v) in zip(read_flags(gs, flag_array), self.__check_buttons):
            v.set(f)
            c["state"] = "normal"
        self.__disabled = False

    def array_iter(self) -> Generator[bool, None, None]:
        for c, v in self.__check_buttons:
            yield bool(v.get())

    def array_data(self) -> bytes:
        out = bytearray(FLAG_ARRAY_SIZE)

        for i, (c, v) in enumerate(self.__check_buttons):
            if v.get():
                out[i // 8] |= 1 << (i & 7)

        return out


class GameStateTab:
    def __init__(self, data_store: DataStore, signals: FsWatcherSignals, parent: ttk.Notebook) -> None:
        self.data_store: Final = data_store
        self._signals: Final = signals

        N_TOOLBAR_COLUMNS: Final = 4
        TOOLBAR_EXPANDING_COLUMN: Final = 2

        self.frame: Final = tk.Frame(parent)
        self.frame.rowconfigure(0, weight=0)
        self.frame.rowconfigure(1, weight=1)
        self.frame.columnconfigure(TOOLBAR_EXPANDING_COLUMN, weight=1)

        self._toolbar: Final = tk.Frame(self.frame, padx=3, pady=3)
        self._toolbar.grid(row=0, column=0, columnspan=2, sticky=tk.NSEW)
        self._toolbar.columnconfigure(0, weight=10)

        self._read_button: Final = tk.Button(self._toolbar, text="Read", command=self.on_read_pressed)
        self._read_button.grid(row=0, column=0, sticky=tk.NSEW)
        self._write_button: Final = tk.Button(self._toolbar, text="Write", command=self.on_write_pressed)
        self._write_button.grid(row=0, column=1, sticky=tk.NSEW)

        sf, sf_state = gui.create_vertical_scrolling_frame(self.frame, row=1, column=0, columnspan=N_TOOLBAR_COLUMNS)
        sf.columnconfigure(0, weight=0, minsize=16)
        sf.columnconfigure(1, weight=2)
        sf.columnconfigure(2, weight=0, minsize=64)
        sf.columnconfigure(3, weight=2)
        sf.columnconfigure(4, weight=2)

        self._vars: Final = VarColumn(sf, 0)
        self._global_flags: Final = CheckboxColumn(sf, "globalFlags", 3)
        self._dungeon_flags: Final = CheckboxColumn(sf, "dungeonFlags", 4)

        self._main_frame: Final = sf
        self._main_frame_scroll: Final = sf_state

        self.gamestate: Optional[bytes] = None
        self.gamestate_header: Optional[bytes] = None

    def disable_all(self) -> None:
        self._vars.disable_all()
        self._global_flags.disable_all()
        self._dungeon_flags.disable_all()

    def enable_all(self) -> None:
        self._vars.enable_all()
        self._global_flags.enable_all()
        self._dungeon_flags.enable_all()

    def on_mappings_changed(self, event: Any) -> None:
        self.gamestate_header = None
        self.gamestate = None

        mappings = self.data_store.try_get_mappings()
        if mappings is not None:
            gs = mappings.gamestate

            self._vars.rebuild_widgets(gs)
            self._global_flags.update_checkbox_names(gs.global_flags)
            self._dungeon_flags.update_checkbox_names(gs.dungeon_flags)

            self.gamestate_header = gamestate_header(gs)
        self.disable_all()

    def on_read_pressed(self) -> None:
        self.disable_all()
        self._signals.request_gamestate_data()

    def on_gamestate_data_read(self, event: Any) -> None:
        gs = self._signals.pop_gamestate_data()
        if gs and self.gamestate_header and gs.startswith(self.gamestate_header):
            self.gamestate = gs

            self._vars.update_widget_values(gs)
            self._global_flags.update_checkbox_values(gs, 0)
            self._dungeon_flags.update_checkbox_values(gs, 1)
        else:
            self.gamestate = None
            self.disable_all()

    def on_write_pressed(self) -> None:
        if self.gamestate:
            gs = (
                self.gamestate[:PLAYER_POSITION_OFFSET]
                + self._vars.player_pos_data()
                + self._global_flags.array_data()
                + self._dungeon_flags.array_data()
                + self._vars.u8_vars_data()
                + self._vars.u16_vars_data()
            )

            self._signals.send_command(Command(Rou2sCommands.SET_GAMESTATE_AND_RESTART, gs))
