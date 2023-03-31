# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


from typing import Any, Final, Optional, Union

import re
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import font
from tkinter.scrolledtext import ScrolledText

from ..audio.json_formats import SamplesJson
from ..audio.bytecode import Bytecode
from ..audio.bytecode import no_argument as bc_parser__no_argument
from ..audio.driver_constants import SFX_TICK_TIMER
from ..audio.sound_effects import compile_sound_effect
from ..audio.songs import song_header
from ..resources_compiler import DataStore
from ..resources_over_usb2snes import FsWatcherSignals, Rou2sCommands, Command


DUMMY_SFX_SONG_HEADER = song_header(SFX_TICK_TIMER, [b"\0xff"], [None], [])


def bytecode_instructions_string() -> str:
    bci = list(Bytecode.instructions.keys())
    bci.sort()
    return "\n".join(bci)


def example_sound_effect(s: SamplesJson) -> str:
    if s.instruments:
        first_instrument = s.instruments[0].name
    else:
        first_instrument = "null"

    return f"""
    set_instrument {first_instrument}
    set_channel_volume 96

    play_note c

    disable_channel
"""


class TestSoundEffectTab:
    def __init__(self, data_store: DataStore, signals: FsWatcherSignals, parent: ttk.Notebook) -> None:
        self.data_store: Final = data_store
        self.signals: Final = signals

        self._audio_samples: Optional[SamplesJson] = None

        # Sound effect compiled as a song
        self._song_data: Optional[bytes] = None
        self._has_errors: bool = False

        self._populate_text: bool = True

        self.frame: Final = tk.Frame(parent)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(4, weight=3)
        self.frame.rowconfigure(6, weight=2)

        # Column 0

        fixed_font = tk.font.nametofont("TkFixedFont")

        text_font = fixed_font.copy()
        text_font.config(size=12)

        self._text: Final = ScrolledText(
            self.frame, wrap=tk.WORD, undo=True, width=50, height=5, background="#ffffff", foreground="#000000", font=text_font
        )
        self._text.tag_configure("invalid", background="#ffcccc", foreground="#220000")
        self._text.bind("<<Modified>>", self._on_text_modified)
        self._text.grid(column=0, row=0, rowspan=7, sticky=tk.NSEW)

        self._errors: Final = ScrolledText(
            self.frame, wrap=tk.WORD, state=tk.DISABLED, width=0, height=6, background=self.frame["background"], font=fixed_font
        )
        self._errors.grid(column=0, row=7, sticky=tk.NSEW)

        # Columns 1 & 4

        inst_label: Final = tk.Label(self.frame, text="Instruments:")
        inst_label.grid(column=1, row=2, columnspan=2, sticky=tk.SW)

        instrument_sb: Final = tk.Scrollbar(self.frame, orient=tk.VERTICAL)

        self.instrument_list: Final = tk.StringVar()
        self._instruments: Final = tk.Listbox(
            self.frame, width=25, height=4, font=fixed_font, listvariable=self.instrument_list, yscrollcommand=instrument_sb.set
        )
        self._instruments.grid(column=1, row=4, columnspan=2, sticky=tk.NSEW)
        instrument_sb.grid(column=3, row=4, sticky=tk.NS)

        self._instruments.bind("<Double-1>", self._on_instrument_dclicked)

        bc_label: Final = tk.Label(self.frame, text="Bytecodes:")
        bc_label.grid(column=1, row=5, columnspan=2, sticky=tk.SW)

        bc_instruction_sb: Final = tk.Scrollbar(self.frame, orient=tk.VERTICAL)

        bc_instruction_list: Final = tk.StringVar(value=sorted(list(Bytecode.instructions.keys())))  # type: ignore
        self._bc_instructions: Final = tk.Listbox(
            self.frame, width=25, height=4, font=fixed_font, listvariable=bc_instruction_list, yscrollcommand=bc_instruction_sb.set
        )
        self._bc_instructions.grid(column=1, row=6, columnspan=2, rowspan=2, sticky=tk.NSEW)
        bc_instruction_sb.grid(column=3, row=6, rowspan=2, sticky=tk.NS)

        self._bc_instructions.bind("<Double-1>", self._on_bc_instruction_dclicked)

        # Column 2

        compile_button: Final = tk.Button(self.frame, text="Compile", width=10, command=self._compile)
        compile_button.grid(column=2, row=0, columnspan=2, sticky="new")

        send_button: Final = tk.Button(self.frame, text="Play", width=10, command=self._send_to_console)
        send_button.grid(column=2, row=1, columnspan=2, sticky="new")

    def on_audio_samples_changed(self, event: Any) -> None:
        self._audio_samples = self.data_store.get_audio_samples()

        if self._populate_text:
            # Only populate text if _text is empty
            if len(self._text.get("1.0", "end")) <= 1:
                if self._audio_samples:
                    self._text.insert("1.0", example_sound_effect(self._audio_samples))
                self._populate_text = False

        if self._audio_samples:
            instruments = [i.name for i in self._audio_samples.instruments]
        else:
            instruments = list()

        self.instrument_list.set(instruments)  # type: ignore

        self._compile()

    def _on_instrument_dclicked(self, event: Any) -> None:
        sel: list[int] = self._instruments.curselection()  # type: ignore

        if sel:
            selected = self._instruments.get(sel[0])
            self.insert_instruction(f"set_instrument {selected}", False)

    def _on_bc_instruction_dclicked(self, event: Any) -> None:
        sel: list[int] = self._bc_instructions.curselection()  # type: ignore

        if sel:
            selected = self._bc_instructions.get(sel[0])

            bc = Bytecode.instructions.get(selected)
            if bc:
                if bc[0] == bc_parser__no_argument:
                    self.insert_instruction(selected, False)
                else:
                    self.insert_instruction(selected, True)

    PADDING_REGEX: Final = re.compile(r"[ \t]*")

    def insert_instruction(self, text: str, has_argument: bool) -> None:
        current_line = self._text.get("insert linestart", "insert lineend")
        m = self.PADDING_REGEX.match(current_line)
        if m:
            padding = m.group()
            if len(padding) != len(current_line):
                text = f"\n{padding}{text}"
        else:
            padding = ""

        if has_argument:
            text += " "
        else:
            text += "\n" + padding

        self._text.focus_force()
        self._text.insert("insert lineend", text)

    def _on_text_modified(self, event: Any) -> None:
        if self._text.edit_modified():
            self._song_data = None
            if self._has_errors:
                self._text.tag_remove("invalid", "0.0", "end")
                self._has_errors = False

    def _set_error_text(self, s: str) -> None:
        self._errors["state"] = tk.NORMAL
        self._errors.delete("1.0", "end")
        self._errors.insert("1.0", s)
        self._errors["state"] = tk.DISABLED

        if s:
            self._has_errors = True
            self._text.tag_add("invalid", "0.0", "end")
        else:
            self._has_errors = False
            self._text.tag_remove("invalid", "0.0", "end")

    def _compile(self) -> None:
        # Mark text as unmodified, `_on_text_modified()` will be called the next time the text changes
        self._text.edit_modified(False)

        self._song_data = None

        if self._audio_samples is None:
            self._set_error_text("Cannot read audio sample JSON file")
            return

        text = self._text.get(1.0, "end")
        error_text = ""

        try:
            self._song_data = DUMMY_SFX_SONG_HEADER + compile_sound_effect(text, self._audio_samples)

        except Exception as e:
            self._text.tag_add("invalid", "0.0", "end")
            error_text = str(e)

        finally:
            self._set_error_text(error_text)

    def _send_to_console(self) -> None:
        if not self._song_data:
            self._compile()

        if self._song_data:
            c = Command(Rou2sCommands.UPLOAD_SONG, self._song_data)
            self.signals.send_command(c)
