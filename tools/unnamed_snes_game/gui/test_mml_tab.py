# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


from typing import Any, Final, Optional, Union

import re
import tkinter as tk
import tkinter.ttk as ttk
from tkinter import font
from tkinter.scrolledtext import ScrolledText

from ..audio.json_formats import SamplesJson
from ..audio.mml_compiler import MmlData, CompileError, compile_mml
from ..audio.songs import mml_data_to_song_data
from ..resources_compiler import DataStore
from ..resources_over_usb2snes import FsWatcherSignals, Rou2sCommands, Command


class TestMmlTab:
    def __init__(self, data_store: DataStore, signals: FsWatcherSignals, parent: ttk.Notebook) -> None:
        self.data_store: Final = data_store
        self.signals: Final = signals

        self._audio_samples: Optional[SamplesJson] = None

        self._mml_data: Optional[MmlData] = None
        self._song_data: Optional[bytes] = None
        self._has_errors: bool = False

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

        # ::TODO add lists of instruments and macros and sections::

        # Column 2

        compile_button: Final = tk.Button(self.frame, text="Compile", width=10, command=self._compile)
        compile_button.grid(column=2, row=0, columnspan=2, sticky="new")

        send_button: Final = tk.Button(self.frame, text="Play", width=10, command=self._send_to_console)
        send_button.grid(column=2, row=1, columnspan=2, sticky="new")

    def on_audio_samples_changed(self, event: Any) -> None:
        self._audio_samples = self.data_store.get_audio_samples()
        self._compile()

    def _on_text_modified(self, event: Any) -> None:
        if self._text.edit_modified():
            self._song_data = None
            if self._has_errors:
                self._text.tag_remove("invalid", "0.0", "end")
                self._has_errors = False

    def _set_error_text(self, s: str) -> None:
        assert s

        self._errors["state"] = tk.NORMAL
        self._errors.delete("1.0", "end")
        self._errors.insert("1.0", s)
        self._errors["state"] = tk.DISABLED

        self._has_errors = True
        self._text.tag_add("invalid", "0.0", "end")

    def _show_mml_errors(self, compile_error: CompileError) -> None:
        self._errors["state"] = tk.NORMAL
        self._errors.delete("1.0", "end")
        self._errors.insert("1.0", str(compile_error))
        self._errors["state"] = tk.DISABLED

        self._has_errors = True

        self._text.tag_remove("invalid", "0.0", "end")

        for e in compile_error.errors:
            if e.char_start is not None and e.char_start > 0:
                self._text.tag_add("invalid", f"{e.line_number}.{e.char_start-1}", f"{e.line_number}.{e.char_start}")
            else:
                self._text.tag_add("invalid", f"{e.line_number}.0", f"{e.line_number}.end")

    def _set_success_text(self, s: str) -> None:
        self._errors["state"] = tk.NORMAL
        self._errors.delete("1.0", "end")
        self._errors.insert("1.0", s)
        self._errors["state"] = tk.DISABLED

        self._has_errors = False
        self._text.tag_remove("invalid", "0.0", "end")

    def _compile(self) -> None:
        # Mark text as unmodified, `_on_text_modified()` will be called the next time the text changes
        self._text.edit_modified(False)

        self._mml_data = None
        self._song_data = None

        if self._audio_samples is None:
            self._set_error_text("Cannot read audio sample JSON file")
            return

        text = self._text.get(1.0, "end")
        error_text = ""

        try:
            if text.strip():
                self._mml_data = compile_mml(text, self._audio_samples)
                self._song_data = mml_data_to_song_data(self._mml_data)

                self._set_success_text(f"MML compiled successfully.\n\nTick counts:\n{self._mml_data.tick_counts_string()}")
        except CompileError as e:
            self._show_mml_errors(e)
        except Exception as e:
            self._set_error_text(str(e))

    def _send_to_console(self) -> None:
        if not self._song_data:
            self._compile()

        if self._song_data:
            c = Command(Rou2sCommands.UPLOAD_SONG, self._song_data)
            self.signals.send_command(c)
