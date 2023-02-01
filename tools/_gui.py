#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import json
from io import StringIO
from collections import OrderedDict

import tkinter as tk
import tkinter.ttk as ttk
from tkinter.scrolledtext import ScrolledText

from abc import ABC, abstractmethod
from typing import Any, Final, Generic, Literal, Optional, TypeVar, Union

from _json_formats import Name


class VerticalScrollingFrameState:
    def __init__(self, frame: tk.Frame, canvas: tk.Canvas):
        self._frame: Final = frame
        self._canvas: Final = canvas

        self._scroll_to_bottom_request: bool = False

        frame.bind("<Configure>", self._on_frame_configure)

    def _on_frame_configure(self, event: Any) -> None:
        sr = (0, 0, event.width, event.height)
        self._canvas.config(scrollregion=sr, width=event.width)

        if self._scroll_to_bottom_request:
            self._scroll_to_bottom_request = False
            self._canvas.yview_moveto(1.0)

    def scroll_to_top(self) -> None:
        self._canvas.yview_moveto(0.0)

    def scroll_to_bottom(self) -> None:
        # `tk.Canvas.yview_moveto()` may not scroll to the very bottom if Widgets were just added to the frame.
        # Delaying the `yview_moveto(1.0)` call to the next `<Configure>` event will correctly scroll the frame to the bottom.
        self._scroll_to_bottom_request = True
        self._canvas.yview_moveto(1.0)


# Cannot add a scrollbar to a tk.Frame.
# tk.Canvas can have a scrollbar and hold a tk.Frame
def create_vertical_scrolling_frame(
    parent: tk.Frame, row: int, column: int, columnspan: int
) -> tuple[tk.Frame, VerticalScrollingFrameState]:
    canvas = tk.Canvas(parent, borderwidth=0)

    frame = tk.Frame(canvas)
    v_scroll = tk.Scrollbar(parent, orient=tk.VERTICAL, command=canvas.yview)

    canvas.grid(row=0, column=0, columnspan=columnspan, sticky=tk.NSEW)
    v_scroll.grid(row=0, column=1 + columnspan, padx=2, sticky=tk.NS)

    canvas.create_window(0, 0, window=frame, anchor=tk.NW)

    canvas.config(yscrollcommand=v_scroll.set)

    state = VerticalScrollingFrameState(frame, canvas)

    return frame, state


WidgetT = TypeVar("WidgetT", bound=tk.Widget)


class AbstractInput(Generic[WidgetT], ABC):
    DATA_CHANGED_EVENT_NAME: Final = "<<__DATA_CHANGED__>>"
    VALID_FG_COLOR: Final = "#000000"
    INVALID_FG_COLOR: Final = "#cc0000"

    MULTI_LINE_INPUT: bool
    # If true, then the widget's fg color will turn `INVALID_FG_COLOR` if the value() is not valid.
    AUTO_UPDATE_WIDGET_FG: bool = True

    def __init__(self, parent: tk.Frame, field: str, widget: WidgetT, label_text: Optional[str] = None):
        # Field, within dict, to display and edito
        self.field: Final = field

        self.widget: Final = widget

        self.label: Final = tk.Label(parent, text=label_text) if label_text else None

        # The dict object that holds the data to display and edit
        self.__dict: Optional[dict[str, Any]] = None

        # Flag used to prevent the propagation of a `self.DATA_CHANGED_EVENT_NAME` event when `set_dict()` was called.
        # (Ultimately prevents a "You have unsaved changed" dialog box when the user loads a file and immediately exits the program)
        self.__skip_next_value_changed_event: bool = True

        self._is_valid: bool = False

    def destroy(self) -> None:
        self.widget.destroy()
        if self.label:
            self.label.destroy()

    def is_valid(self) -> bool:
        return self._is_valid

    def set_dict(self, d: Optional[dict[str, Any]]) -> None:
        # Do not emit a `self.DATA_CHANGED_EVENT_NAME` event when the widget is edited by `_load_value` or `clear`
        self.__skip_next_value_changed_event = True

        self.__dict = d
        if d is not None:
            self.widget.configure(state=tk.NORMAL)  # type: ignore
            v = d.get(self.field)
            if v is not None:
                self._load_value(v)
            else:
                self._clear()
            _, is_valid = self.value()
        else:
            self.widget.configure(bg=None)  # type: ignore
            self.widget.configure(state=tk.DISABLED)  # type: ignore
            self._clear()
            is_valid = True

        self._is_valid = is_valid

        fg = self.VALID_FG_COLOR if is_valid else self.INVALID_FG_COLOR
        self.widget.configure(fg=fg)  # type: ignore
        if self.label:
            self.label.configure(fg=fg)

    def on_value_changed(self) -> None:
        if self.__skip_next_value_changed_event:
            self.__skip_next_value_changed_event = False
            return

        self.update_dict()
        self.widget.event_generate(self.DATA_CHANGED_EVENT_NAME)

    # I am not confident the `self.__skip_next_value_changed_event` flag will be cleared
    # before the user starts editing the widgets.
    #
    # This method should be called before saving and/or unloading the underlying dict data.
    def update_dict(self) -> None:
        if self.__dict:
            value, is_valid = self.value()

            fg = self.VALID_FG_COLOR if is_valid else self.INVALID_FG_COLOR

            if self.label:
                self.label.configure(fg=fg)

            if self.AUTO_UPDATE_WIDGET_FG:
                self.widget.configure(fg=fg)  # type:ignore

            if is_valid:
                self.__dict[self.field] = value
            self._is_valid = is_valid

    @abstractmethod
    def _clear(self) -> None:
        ...

    @abstractmethod
    def _load_value(self, value: Any) -> None:
        ...

    # Returns (value, is_valid)
    @abstractmethod
    def value(self) -> tuple[Optional[Any], bool]:
        ...


class _EntryInput(AbstractInput[tk.Entry]):
    MULTI_LINE_INPUT = False

    def __init__(self, parent: tk.Frame, field: str, label_text: Optional[str] = None):
        self.string_var = tk.StringVar()
        super().__init__(parent, field, tk.Entry(parent, textvariable=self.string_var), label_text)

        self.string_var.trace_add("write", self.on_entry_changed)

    def on_entry_changed(self, var: str, index: str, mode: str) -> None:
        self.on_value_changed()

    def _clear(self) -> None:
        self.string_var.set("")


class BoolInput(AbstractInput[tk.Checkbutton]):
    MULTI_LINE_INPUT = False

    def __init__(self, parent: tk.Frame, field: str, text: str):
        self.var = tk.IntVar()
        cb = tk.Checkbutton(parent, text=text, variable=self.var, command=self.on_value_changed)
        super().__init__(parent, field, cb, label_text=None)

    def _clear(self) -> None:
        self.var.set(False)

    def _load_value(self, v: Any) -> None:
        self.var.set(bool(v))

    # Returns (value, is_valid)
    def value(self) -> tuple[bool, bool]:
        return bool(self.var.get()), True


class AbstractTextInput(AbstractInput[tk.Text]):
    MULTI_LINE_INPUT = True

    TEXTAREA_WIDTH: Final = 50

    def __init__(self, parent: tk.Frame, field: str, widget: tk.Text, label_text: Optional[str]):
        super().__init__(parent, field, widget, label_text)

        self.widget.bind("<<Modified>>", self.__on_modified)

    def __on_modified(self, event: Any) -> None:
        if not self.widget.edit_modified():
            return
        self.on_value_changed()
        self.widget.edit_modified(False)

    def _clear(self) -> None:
        self.widget.delete("1.0", "end")

    def set_text(self, s: str) -> None:
        self.widget.delete("1.0", "end")
        self.widget.insert("1.0", s)

    def get_text(self) -> str:
        return self.widget.get(1.0, "end")


class ExpandingTextInput(AbstractTextInput):
    def __init__(self, parent: tk.Frame, field: str, label_text: Optional[str] = None):
        super().__init__(parent, field, tk.Text(parent, wrap=tk.NONE, width=self.TEXTAREA_WIDTH, height=4), label_text)
        self.widget.bind("<<WidgetViewSync>>", self._on_line_height_changed)

    def _on_line_height_changed(self, event: Any) -> None:
        try:
            # Not adding a `-1` to add an extra line to end of the Text widget.
            # This stops the text from flickering if the user adds a newline when the cursor is on the last line
            h = int(self.widget.index("end").split(".")[0])
            self.widget.configure(height=max(4, h))
        except ValueError:
            pass


class ScrolledTextInput(AbstractTextInput):
    MULTI_LINE_INPUT = True

    TEXTAREA_HEIGHT: int

    def __init__(self, parent: tk.Frame, field: str, label_text: Optional[str] = None):
        super().__init__(
            parent, field, ScrolledText(parent, wrap=tk.WORD, width=self.TEXTAREA_WIDTH, height=self.TEXTAREA_HEIGHT), label_text
        )


class IntInput(_EntryInput):
    def _load_value(self, v: Any) -> None:
        self.string_var.set(str(v))

    def value(self) -> tuple[Optional[int], bool]:
        try:
            return int(self.string_var.get()), True
        except ValueError:
            return None, False


class FloatInput(_EntryInput):
    def _load_value(self, v: Optional[Any]) -> None:
        self.string_var.set(str(v))

    def value(self) -> tuple[Optional[Union[float, int]], bool]:
        s = self.string_var.get()
        try:
            return int(s), True
        except ValueError:
            try:
                return float(self.string_var.get()), True
            except ValueError:
                return None, False


class PngFilenameInput(_EntryInput):
    def _load_value(self, v: Any) -> None:
        self.string_var.set(str(v))

    def value(self) -> tuple[str, bool]:
        s = self.string_var.get()
        valid = s.endswith(".png") and len(s) > 4
        return s, valid


class _RegexInput(_EntryInput):
    REGEX: re.Pattern[str]
    REQUIRED: bool

    def __init__(self, parent: tk.Frame, field: str, label_text: Optional[str] = None):
        super().__init__(parent, field, label_text)
        assert isinstance(self.REGEX, re.Pattern)
        assert isinstance(self.REQUIRED, bool)

    def _load_value(self, v: Any) -> None:
        if v is not None:
            self.string_var.set(str(v))
        else:
            self.string_var.set("")

    def value(self) -> tuple[Optional[str], bool]:
        s = self.string_var.get()

        if s:
            m = self.REGEX.match(s)
            if m:
                return s, True
        else:
            # s is empty
            if not self.REQUIRED:
                return None, True

        return None, False


class NameInput(_RegexInput):
    REGEX = re.compile(r"[a-zA-Z0-9_]+$")
    REQUIRED = True


class TileHitboxInput(_RegexInput):
    REGEX = re.compile(r"[0-9]+ +[0-9]+$")
    REQUIRED = True


class FramesInput(ScrolledTextInput):
    TEXTAREA_HEIGHT = 10

    def _load_value(self, value: Any) -> None:
        if isinstance(value, list):
            s = ", ".join(str(i) for i in value)
        else:
            s = str(value)
        self.set_text(s)

    def value(self) -> tuple[list[Name], bool]:
        regex = NameInput.REGEX

        l = list(filter(None, [s.strip() for s in self.get_text().split(",")]))
        valid = bool(l and all(regex.match(i) for i in l))

        return l, valid


class AnimationFramesInput(ExpandingTextInput):
    AUTO_UPDATE_WIDGET_FG = False

    NO_DELAY_REGEX: Final = re.compile(r"[a-zA-Z0-9_]+$")
    WITH_DELAY_REGEX: Final = re.compile(r"([a-zA-Z0-9_]+) +([0-9\.]+)$")

    def __init__(self, parent: tk.Frame, field: str, label_text: Optional[str] = None):
        super().__init__(parent, field, label_text)
        self.widget.tag_configure("invalid", foreground=self.INVALID_FG_COLOR)
        self._fixed_frame_delay: bool = False

    def set_fixed_frame_delay(self, b: bool) -> None:
        if b != self._fixed_frame_delay:
            self._fixed_frame_delay = b
            self.on_value_changed()

    def set_fixed_frame_delay__no_value_changed(self, b: bool) -> None:
        self._fixed_frame_delay = b

    def _load_value(self, value: Any) -> None:
        if not value:
            s = ""
        elif isinstance(value, list):
            io = StringIO()

            io.write(str(value[0]))
            for v in value[1:]:
                if isinstance(v, str):
                    io.write("\n")
                    io.write(v)
                else:
                    io.write(f" {v}")
            s = io.getvalue()
        else:
            s = str(value)
        self.set_text(s)

    def value(self) -> tuple[Optional[list[Union[Name, float]]], bool]:
        out: list[Union[Name, float]] = list()
        valid: bool = True

        self.widget.tag_remove("invalid", "0.0", "end")

        if self._fixed_frame_delay:
            # No frame delay
            regex = self.NO_DELAY_REGEX
            for i, line in enumerate(self.get_text().splitlines()):
                line = line.strip()
                if line:
                    if regex.match(line):
                        out.append(line)
                    else:
                        valid = False
                        self.widget.tag_add("invalid", f"{i+1}.0", f"{i+2}.0")
        else:
            # Includes frame delay
            regex = self.WITH_DELAY_REGEX
            for i, line in enumerate(self.get_text().splitlines()):
                line = line.strip()
                if line:
                    m = regex.match(line)
                    if m:
                        delay_str = m.group(2)
                        try:
                            delay: Union[int, float] = int(delay_str)
                        except ValueError:
                            try:
                                delay = float(delay_str)
                            except ValueError:
                                valid = False
                                self.widget.tag_add("invalid", f"{i+1}.0", f"{i+2}.0")

                        out.append(m.group(1))
                        out.append(delay)
                    else:
                        valid = False
                        self.widget.tag_add("invalid", f"{i+1}.0", f"{i+2}.0")

        if valid:
            return out, True
        else:
            return None, False


class _OverrideInput(ExpandingTextInput):
    AUTO_UPDATE_WIDGET_FG = False

    LINE_REGEX: re.Pattern[str]

    def __init__(self, parent: tk.Frame, field: str, label_text: Optional[str] = None):
        super().__init__(parent, field, label_text)
        self.widget.tag_configure("invalid", foreground=self.INVALID_FG_COLOR)

    def _load_value(self, value: Any) -> None:
        if isinstance(value, dict):
            s = "\n".join(f"{k}: {v}" for k, v in value.items())
        else:
            s = str(value)
        self.set_text(s)

    def value(self) -> tuple[Optional[OrderedDict[str, str]], bool]:
        out: OrderedDict[str, str] = OrderedDict()
        valid = True

        regex: Final = self.LINE_REGEX

        self.widget.tag_remove("invalid", "0.0", "end")

        for i, line in enumerate(self.get_text().splitlines()):
            line = line.strip()
            if line:
                m = regex.match(line)
                if m and m.group(1) not in out:
                    out[m.group(1)] = m.group(2)
                else:
                    valid = False
                    self.widget.tag_add("invalid", f"{i+1}.0", f"{i+2}.0")

        if valid:
            return out, True
        else:
            return None, False


class DefaultLayoutInput(_RegexInput):
    REGEX = re.compile(r"[a-zA-Z0-9_]+ +[0-9]+ +[0-9]+$")
    REQUIRED = False


class LayoutOverridesInput(_OverrideInput):
    LINE_REGEX = re.compile(r"^([a-zA-Z0-9_]+ *(?:-? *[a-zA-Z0-9_]+)? *): *([a-zA-Z0-9_]+ +[0-9]+ +[0-9]+)$")


class DefaultAabbInput(_RegexInput):
    REGEX = re.compile(r"[0-9]+ +[0-9]+ +[0-9]+ +[0-9]+$")
    REQUIRED = False


class AabbOverridesInput(_OverrideInput):
    LINE_REGEX = re.compile(r"^([a-zA-Z0-9_]+ *(?:-? *[a-zA-Z0-9_]+)? *): *([0-9]+ +[0-9]+ +[0-9]+ +[0-9]+)$")


class ClonedFramesInput(_OverrideInput):
    LINE_REGEX = re.compile(r"^([a-zA-Z0-9_]+) *: *([a-zA-Z0-9_]+ *(hflip|vflip|hvflip)?)$")
