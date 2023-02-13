# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


from typing import Any, Final, Optional, Union

import tkinter as tk
import tkinter.ttk as ttk
from tkinter import font
from tkinter.scrolledtext import ScrolledText

from ..audio.json_formats import SamplesJson
from ..audio.bytecode import Bytecode
from ..audio.driver_constants import SFX_BPM
from ..audio.sound_effects import compile_sound_effect
from ..audio.songs import song_header
from ..resources_compiler import DataStore
from ..resources_over_usb2snes import FsWatcherSignals, Rou2sCommands, Command


DUMMY_SFX_SONG_HEADER = song_header(SFX_BPM, [b"\0xff"], [None], [])


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

        # Column 1

        inst_label: Final = tk.Label(self.frame, text="Instruments:")
        inst_label.grid(column=1, row=2, columnspan=2, sticky=tk.SW)

        self._instruments: Final = ScrolledText(self.frame, state=tk.DISABLED, width=25, height=4, font=fixed_font)
        self._instruments.grid(column=1, row=4, columnspan=2, sticky=tk.NSEW)

        bc_label: Final = tk.Label(self.frame, text="Bytecodes:")
        bc_label.grid(column=1, row=5, columnspan=2, sticky=tk.SW)

        bc_instructions: Final = ScrolledText(self.frame, width=25, height=4, font=fixed_font)
        bc_instructions.insert("1.0", bytecode_instructions_string())
        bc_instructions["state"] = tk.DISABLED
        bc_instructions.grid(column=1, row=6, columnspan=2, rowspan=2, sticky=tk.NSEW)

        # Column 2

        compile_button: Final = tk.Button(self.frame, text="Compile", width=10, command=self._compile)
        compile_button.grid(column=2, row=0, sticky="new")

        send_button: Final = tk.Button(self.frame, text="Play", width=10, command=self._send_to_console)
        send_button.grid(column=2, row=1, sticky="new")

    def on_audio_samples_changed(self, event: Any) -> None:
        self._audio_samples = self.data_store.get_audio_samples()

        self._instruments["state"] = tk.NORMAL
        self._instruments.delete("1.0", "end")
        if self._audio_samples:
            if self._populate_text:
                # Only populate text if _text is empty
                if len(self._text.get("1.0", "end")) <= 1:
                    self._text.insert("1.0", example_sound_effect(self._audio_samples))
                self._populate_text = False

            s = "\n".join(i.name for i in self._audio_samples.instruments)
            self._instruments.insert("1.0", s)
        self._instruments["state"] = tk.DISABLED

        self._compile()

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
