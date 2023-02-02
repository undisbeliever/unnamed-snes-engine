#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import json
import os.path
import argparse
import traceback
from collections import OrderedDict
from typing import Any, Final, Generic, Literal, Optional, TypeVar, Union

import tkinter as tk
import tkinter.ttk as ttk
import tkinter.messagebox

from . import gui as gui

from ..metasprite import extract_frame_locations
from ..json_formats import (
    load_ms_export_order_json,
    load_metasprite_frameset_from_dict,
    Name,
    Filename,
    MsExportOrder,
    MsSpritesheet,
    MsFrameset,
)


ZOOM = 9

FRAME_WIDTH = 3

OBJ_WIDTH = 3
OBJ_COLOR = "#22cc22"

TILE_HITBOX_WIDTH = 3
TILE_HITBOX_COLOR = "#ffaa00"

HITBOX_WIDTH = 3
HITBOX_COLOR = "#aa0000"

HURTBOX_WIDTH = 3
HURTBOX_COLOR = "#0000aa"


def load_ms_spritesheet(json_filename: str) -> Optional[OrderedDict[Name, Any]]:
    with open(json_filename, "r") as fp:
        ss = json.load(fp, object_pairs_hook=OrderedDict)
        if isinstance(ss, OrderedDict):
            return ss
        else:
            return None


def save_ms_spritesheet(json_data: OrderedDict[Name, Any], json_filename: str) -> None:
    s = json.dumps(json_data, allow_nan=False, indent=2)
    if s:
        # The following is a hack to remove the newlines and indentation
        # on frame lists and animation frame lists (ie, `list[Union[str, int]]`)

        # Single element lists
        s = re.sub(r'\[\n {4,12}("[^"]*"|[0-9.]+)\n {2,10}\](,?)\n', r"[ \1 ]\2\n", s)

        # A line that holds a single list element
        s = re.sub(r'\n {4,12}("[^"]*"|[0-9.]+),', r" \1,", s)
        # The final element in a list
        s = re.sub(r',\n {4,12}("[^"]*"|[0-9.]+)\n {2,10}\](,?)\n', r", \1 ]\2\n", s)

        with open(json_filename, "w") as fp:
            fp.write(s)


def create_blank_frameset() -> OrderedDict[Name, Any]:
    """
    Create a new Frameset with empty values.
    This will ensure the frameset JSON data is saved in a consistent order.
    """
    return OrderedDict(
        (
            ("name", ""),
            ("source", ""),
            ("frameWidth", None),
            ("frameHeight", None),
            ("frames", list()),
            ("ms-export-order", ""),
            ("order", 2),
            ("shadowSize", "MEDIUM"),
            ("tileHitbox", None),
            ("defaultLayout", None),
            ("layouts", OrderedDict()),
            ("defaultHitbox", None),
            ("hitboxes", OrderedDict()),
            ("defaultHurtbox", None),
            ("hurtboxes", OrderedDict()),
            ("clones", OrderedDict()),
            ("animations", OrderedDict()),
        )
    )


class Canvas:
    def __init__(self, main_window: tk.Tk, ms_export_orders: MsExportOrder, ms_dir: str):
        self.ms_dir: Final = ms_dir
        self.ms_export_orders: Final = ms_export_orders

        self.frameset: Optional[MsFrameset] = None
        self.image: Optional[tk.PhotoImage] = None
        self.image_source: Optional[Filename] = None

        self.frame: Final = tk.Frame(main_window)
        self.frame.columnconfigure(0, weight=1)
        self.frame.rowconfigure(1, weight=1)

        self.reload_image_button: Final = tk.Button(self.frame, text="Reload Image", command=self._on_reload_image_clicked)
        self.reload_image_button.grid(row=0, column=0, sticky=tk.E)

        toolbar_column = 1

        def add_show_cb(text: str) -> tk.IntVar:
            nonlocal toolbar_column

            v = tk.IntVar()
            v.set(1)
            cb = tk.Checkbutton(self.frame, text=text, variable=v, command=self._update_canvas)
            cb.grid(row=0, column=toolbar_column)

            toolbar_column += 1
            return v

        self.show_hitboxes = add_show_cb("Hitboxes")
        self.show_hurtboxes = add_show_cb("Hurtboxes")
        self.show_tilehitboxes = add_show_cb("TileHitboxes")
        self.show_objects = add_show_cb("Objects")
        self.show_labels = add_show_cb("Labels")

        self.canvas_frame = tk.Frame(self.frame)
        self.canvas_frame.grid(row=1, column=0, columnspan=toolbar_column, sticky=tk.NSEW)

        self.canvas_frame.columnconfigure(0, weight=1)
        self.canvas_frame.rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(self.canvas_frame, width=600, height=300)
        self.canvas.grid(row=0, column=0, sticky=tk.NSEW)

        h_scroll = tk.Scrollbar(self.canvas_frame, orient=tk.HORIZONTAL, command=self.canvas.xview)

        v_scroll = tk.Scrollbar(self.canvas_frame, orient=tk.VERTICAL, command=self.canvas.yview)

        self.canvas.config(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)

        v_scroll.grid(row=0, column=1, sticky=tk.NS)
        h_scroll.grid(row=1, column=0, sticky=tk.EW)

    def print_exception_traceback(self) -> None:
        self.clear()
        self.canvas.create_text(10, 10, text=traceback.format_exc(16), anchor=tk.NW)

    def set_frameset(self, fs: Optional[MsFrameset]) -> None:
        if fs:
            self.frameset = fs
            self.reload_image_button.configure(state=tk.NORMAL)
            self._update_canvas()
        else:
            self.clear()

    def clear(self) -> None:
        self.frameset = None
        self.canvas.delete("all")
        self.canvas.xview(tk.MOVETO, 0)
        self.canvas.yview(tk.MOVETO, 0)
        self.reload_image_button.configure(state=tk.DISABLED)

    def _on_reload_image_clicked(self) -> None:
        if self.frameset is None:
            return

        self.image_source = None
        self.image = None
        self._update_canvas()

    def _update_canvas(self) -> None:
        if self.frameset is None:
            return

        c = self.canvas
        fs = self.frameset

        # Load image (if image_source changed)
        if fs.source != self.image_source:
            try:
                self.image = tk.PhotoImage(file=os.path.join(self.ms_dir, fs.source)).zoom(ZOOM, ZOOM)
            except:
                self.image = None
                self.image_source = None
                self.print_exception_traceback()
                return

            self.image_source = fs.source

            self.canvas["scrollregion"] = (0, 0, self.image.width() + FRAME_WIDTH, self.image.height() + FRAME_WIDTH)

        assert self.image is not None

        image_width = self.image.width()
        image_height = self.image.height()

        try:
            frame_locations: Final = extract_frame_locations(fs, self.ms_export_orders, image_width // ZOOM, image_height // ZOOM)
        except:
            self.print_exception_traceback()
            return

        c.delete("all")
        c.create_image(0, 0, image=self.image, anchor=tk.NW)

        show_labels = self.show_labels.get()
        show_objects = self.show_objects.get()
        show_tilehitboxes = self.show_tilehitboxes.get()
        show_hurtboxes = self.show_hurtboxes.get()
        show_hitboxes = self.show_hitboxes.get()

        frame_width = ZOOM * fs.frame_width
        frame_height = ZOOM * fs.frame_height

        x_origin = fs.x_origin * ZOOM
        y_origin = fs.y_origin * ZOOM

        th_x1 = x_origin - fs.tilehitbox.half_width * ZOOM
        th_x2 = x_origin + fs.tilehitbox.half_width * ZOOM
        th_y1 = y_origin - fs.tilehitbox.half_height * ZOOM
        th_y2 = y_origin + fs.tilehitbox.half_height * ZOOM

        # Frame grid
        for x in range(0, image_width + 1, frame_width):
            c.create_line(((x, 0), (x, image_height)), width=FRAME_WIDTH)

        for y in range(0, image_height + 1, frame_height):
            c.create_line(((0, y), (image_width, y)), width=FRAME_WIDTH)

        for frame_name, fl in frame_locations.items():
            if not fl.is_clone:
                x = fl.frame_x * ZOOM
                y = fl.frame_y * ZOOM

                # Draw origin
                c.create_line(((x + x_origin, y), (x + x_origin, y + frame_height)), width=1)
                c.create_line(((x, y + y_origin), (x + frame_width, y + y_origin)), width=1)

                if show_labels:
                    c.create_text(x + 5, y + 3, anchor=tk.NW, text=frame_name)

                if show_objects and fl.pattern:
                    assert fl.x_offset is not None and fl.y_offset is not None

                    for o in fl.pattern.objects:
                        ox = x + (fl.x_offset + o.xpos) * ZOOM
                        oy = y + (fl.y_offset + o.ypos) * ZOOM
                        osize = o.size * ZOOM
                        c.create_rectangle(ox, oy, ox + osize, oy + osize, width=OBJ_WIDTH, outline=OBJ_COLOR)

                if show_tilehitboxes:
                    c.create_rectangle(x + th_x1, y + th_y1, x + th_x2, y + th_y2, width=TILE_HITBOX_WIDTH, outline=TILE_HITBOX_COLOR)

                if show_hurtboxes and fl.hurtbox:
                    box = fl.hurtbox
                    c.create_rectangle(
                        x + box.x * ZOOM,
                        y + box.y * ZOOM,
                        x + (box.x + box.width) * ZOOM,
                        y + (box.y + box.height) * ZOOM,
                        width=HURTBOX_WIDTH,
                        outline=HURTBOX_COLOR,
                    )

                if show_hitboxes and fl.hitbox:
                    box = fl.hitbox
                    if box is not None:
                        c.create_rectangle(
                            x + box.x * ZOOM,
                            y + box.y * ZOOM,
                            x + (box.x + box.width) * ZOOM,
                            y + (box.y + box.height) * ZOOM,
                            width=HITBOX_WIDTH,
                            outline=HITBOX_COLOR,
                        )


class AnimationEditor:
    VALID_FG_COLOR: Final = gui.AbstractInput.VALID_FG_COLOR
    INVALID_FG_COLOR: Final = gui.AbstractInput.INVALID_FG_COLOR

    N_ROWS: Final = 8

    def __init__(self, fs_editor: "FramesetEditor", parent: tk.Frame):
        self.fs_editor: Final = fs_editor

        self._animation: Optional[dict[str, Any]] = None

        self.use_fixed_delay_value = tk.IntVar()

        self.separator: Final = ttk.Separator(parent, orient=tk.HORIZONTAL)
        self.name_label: Final = tk.Label(parent, text="Animation", font="bold")
        self.name: Final = tk.Label(parent, font="bold")
        self.use_fixed_delay_cb: Final = tk.Checkbutton(
            parent, text="Fixed Frame Delay", variable=self.use_fixed_delay_value, command=self._on_use_fixed_delay_clicked
        )

        self.loop: Final = gui.BoolInput(parent, "loop", "Looping Animation")
        self.delay_type: Final = gui.NameInput(parent, "delay-type", "Delay Type:")
        self.fixed_delay: Final = gui.FloatInput(parent, "fixed-delay", "Fixed Delay:")
        self.frames: Final = gui.AnimationFramesInput(parent, "frames", "Animation Frames:")

        self._inputs: Final[tuple[gui.AbstractInput[Any], ...]] = (
            self.loop,
            self.delay_type,
            self.fixed_delay,
            self.frames,
        )

        for w in self._inputs:
            assert isinstance(w, gui.AbstractInput)
            w.widget.bind(gui.AbstractInput.DATA_CHANGED_EVENT_NAME, self._on_data_changed)

    def layout_grid(self, row: int) -> None:
        assert self.delay_type.label and self.fixed_delay.label and self.frames.label

        r = row

        self.separator.grid(row=r + 0, column=0, columnspan=2, pady=6, sticky=tk.EW)

        self.name_label.grid(row=r + 1, column=0, sticky=tk.W)
        self.name.grid(row=r + 1, column=1, sticky=tk.W)

        self.loop.widget.grid(row=r + 2, column=1, sticky=tk.W)

        self.delay_type.label.grid(row=r + 3, column=0, sticky=tk.W)
        self.delay_type.widget.grid(row=r + 3, column=1, sticky=tk.EW)

        self.use_fixed_delay_cb.grid(row=r + 4, column=1, sticky=tk.W)

        self.fixed_delay.label.grid(row=r + 5, column=0, sticky=tk.W)
        self.fixed_delay.widget.grid(row=r + 5, column=1, sticky=tk.EW)

        self.frames.label.grid(row=r + 6, column=0, sticky=tk.W)
        self.frames.widget.grid(row=r + 7, column=0, columnspan=2, sticky=tk.EW)

    def destroy(self) -> None:
        self.separator.destroy()
        self.name_label.destroy()
        self.name.destroy()
        self.loop.destroy()
        self.delay_type.destroy()
        self.use_fixed_delay_cb.destroy()
        self.fixed_delay.destroy()
        self.frames.destroy()

    def update_and_check_valid(self) -> bool:
        assert self._animation is not None

        for w in self._inputs:
            w.update_dict()
        return all([w.is_valid() for w in self._inputs])

    def set_selected_animation(self, name: str, a: Optional[dict[str, Any]]) -> None:
        self._animation = a
        self.name.configure(text=name)

        self.loop.set_dict(a)
        self.delay_type.set_dict(a)

        ffd = bool(a and "fixed-delay" in a)
        if ffd:
            self.fixed_delay.set_dict(a)
        else:
            self.fixed_delay.set_dict(None)

        self.use_fixed_delay_value.set(ffd)

        self.frames.set_fixed_frame_delay__no_value_changed(ffd)
        self.frames.set_dict(a)

        self.update_name_fg()

    def update_name_fg(self) -> None:
        is_valid = all([w.is_valid() for w in self._inputs])

        fg = self.VALID_FG_COLOR if is_valid else self.INVALID_FG_COLOR
        self.name.configure(fg=fg)
        self.name_label.configure(fg=fg)

    def _on_data_changed(self, event: Any) -> None:
        self.update_name_fg()
        self.fs_editor.on_animation_data_changed()

    def _on_use_fixed_delay_clicked(self) -> None:
        if self._animation is None:
            return

        ffd = bool(self.use_fixed_delay_value.get())
        self.frames.set_fixed_frame_delay(ffd)
        if ffd:
            self._animation["fixed-delay"] = ""
            self.fixed_delay.set_dict(self._animation)
        else:
            self.fixed_delay.set_dict(None)
            self._animation.pop("fixed-delay", None)

        self._on_data_changed(None)


class FramesetEditor:
    MAX_N_ANIMATIONS: Final = 255

    def __init__(self, editor_window: "EditorWindow", parent: ttk.Notebook):
        self.editor_window: Final = editor_window

        self._ms_export_orders: Optional[MsExportOrder] = None

        self._ms_frameset: Optional[dict[Name, Any]] = None
        self._ms_fs_no_animations: Final = 0

        self.frame: Final = tk.Frame(parent)
        self.frame.rowconfigure(0, weight=1)
        self.frame.columnconfigure(1, weight=1)

        sf, sf_state = gui.create_vertical_scrolling_frame(self.frame, row=0, column=0, columnspan=3)
        self._scrolling_frame: Final = sf
        self._scrolling_frame.columnconfigure(1, weight=1)

        self._scrolling_state: Final = sf_state

        self.add_missing_animations_button: Final = tk.Button(
            self.frame, text="Add Missing Animations", command=self.add_missing_animations
        )
        self.add_missing_animations_button.grid(row=1, column=0, columnspan=2)

        self._fs_inputs: Final[list[gui.AbstractInput[Any]]] = [
            gui.NameInput(sf, "name", "Name:"),
            gui.PngFilenameInput(sf, "source", "Source: "),
            gui.IntInput(sf, "frameWidth", "Frame Width:"),
            gui.IntInput(sf, "frameHeight", "Frame Height:"),
            gui.IntInput(sf, "xorigin", "X Origin:"),
            gui.IntInput(sf, "yorigin", "Y Origin:"),
            gui.FramesInput(sf, "frames", "Frames :"),
            gui.NameInput(sf, "ms-export-order", "Export Order:"),
            gui.IntInput(sf, "order", "Sprite Priority:"),
            gui.NameInput(sf, "shadowSize", "Shadow Size:"),
            gui.TileHitboxInput(sf, "tilehitbox", "Tile Hitbox:"),
            gui.DefaultLayoutInput(sf, "defaultLayout", "Default Layout:"),
            gui.LayoutOverridesInput(sf, "layouts", "Layout Overrides:"),
            gui.DefaultAabbInput(sf, "defaultHitbox", "Default Hitbox:"),
            gui.AabbOverridesInput(sf, "hitboxes", "Hitbox Overrides:"),
            gui.DefaultAabbInput(sf, "defaultHurtbox", "Default Hurtbox:"),
            gui.AabbOverridesInput(sf, "hurtboxes", "Hurtbox Overrides:"),
            gui.ClonedFramesInput(sf, "clones", "Cloned Frames:"),
        ]

        sf_row = 0
        for w in self._fs_inputs:
            if w.field == "name":
                w.widget.bind(w.DATA_CHANGED_EVENT_NAME, self._on_fs_name_changed)
            elif w.field == "ms-export-order":
                w.widget.bind(w.DATA_CHANGED_EVENT_NAME, self._on_fs_eo_changed)
            else:
                w.widget.bind(w.DATA_CHANGED_EVENT_NAME, self._on_fs_input_changed)

            if w.label:
                w.label.grid(row=sf_row, column=0, sticky=tk.W)
                if w.MULTI_LINE_INPUT:
                    sf_row += 1

            if w.MULTI_LINE_INPUT:
                w.widget.grid(row=sf_row, column=0, columnspan=2, sticky=tk.EW)
            else:
                w.widget.grid(row=sf_row, column=1, sticky=tk.EW)
            sf_row += 1

        self._animations: Final[list[AnimationEditor]] = list()
        self.first_animation_row: Final = sf_row

    def update_all_and_check_valid(self) -> bool:
        if self._ms_frameset is None:
            return True

        for w in self._fs_inputs:
            w.update_dict()

        valid = all(i.is_valid() for i in self._fs_inputs)

        if not self.update_and_check_animations():
            valid = False

        return valid

    def update_and_check_animations(self) -> bool:
        assert self._ms_frameset is not None

        valid = True

        for a in self._animations:
            if not a.update_and_check_valid():
                valid = False

        return valid

    def _resize_animations(self, size: int) -> None:
        size = max(size, 0)
        size = min(size, self.MAX_N_ANIMATIONS)

        if len(self._animations) < size:
            # Add animations
            for i in range(len(self._animations), size):
                a = AnimationEditor(self, self._scrolling_frame)
                a.layout_grid(row=self.first_animation_row + i * AnimationEditor.N_ROWS)
                self._animations.append(a)
        else:
            # Remove animations
            while len(self._animations) > size:
                a = self._animations.pop()
                a.destroy()

    def set_ms_export_orders(self, mseo: Optional[MsExportOrder]) -> None:
        self._ms_export_orders = mseo
        self._update_add_missing_animations_button()

    def set_selected_frameset(self, fs: Optional[dict[str, Any]]) -> None:
        if self._ms_frameset is fs:
            return
        self._ms_frameset = fs

        for i in self._fs_inputs:
            i.set_dict(fs)

        self._build_animations_gui()
        self._update_add_missing_animations_button()

        self._scrolling_state.scroll_to_top()

    def get_frameset_data(self) -> Optional[dict[Name, Any]]:
        return self._ms_frameset

    def _build_animations_gui(self) -> None:
        if self._ms_frameset:
            animations: Optional[dict[str, Any]] = self._ms_frameset.get("animations")
            if animations is None:
                animations = OrderedDict()
                self._ms_frameset["animations"] = animations

            self._resize_animations(len(animations))

            for a_id, a_it in enumerate(animations.items()):
                aname, ani = a_it
                if a_id >= len(self._animations):
                    break
                self._animations[a_id].set_selected_animation(aname, ani)
        else:
            self._resize_animations(0)

    def _update_add_missing_animations_button(self) -> None:
        enable_button: bool = False

        if self._ms_frameset and self._ms_export_orders:
            eo_name = self._ms_frameset.get("ms-export-order")
            if eo_name:
                eo = self._ms_export_orders.animation_lists.get(eo_name)
                if eo:
                    ms_animations = self._ms_frameset["animations"]
                    enable_button = any(a not in ms_animations for a in eo.animations)

        if enable_button:
            self.add_missing_animations_button.configure(state=tk.NORMAL)
        else:
            self.add_missing_animations_button.configure(state=tk.DISABLED)

    def add_missing_animations(self) -> None:
        if not self.update_and_check_animations():
            # This limitation exists because the entire animation GUI is rebuilt after the missing animations are created.
            tk.messagebox.showerror("ERROR", "Cannot add any animations while current animations contain errors")
            return

        self.add_missing_animations_button.configure(state=tk.DISABLED)

        if self._ms_frameset is None or self._ms_export_orders is None:
            return

        eo_name = self._ms_frameset.get("ms-export-order")
        if not eo_name:
            return
        eo = self._ms_export_orders.animation_lists.get(eo_name)
        if not eo:
            return

        ms_animations = self._ms_frameset["animations"]

        for a_name in eo.animations:
            if a_name not in ms_animations:
                ms_animations[a_name] = OrderedDict()

        self._build_animations_gui()
        self._scrolling_state.scroll_to_bottom()

    def _on_fs_name_changed(self, event: Any) -> None:
        self._on_fs_input_changed(event)
        self.editor_window.on_fs_name_changed()

    def _on_fs_eo_changed(self, event: Any) -> None:
        self._update_add_missing_animations_button()
        self._on_fs_input_changed(event)

    def _on_fs_input_changed(self, event: Any) -> None:
        self.editor_window.on_fs_inputs_changed()

    def on_animation_data_changed(self) -> None:
        self.editor_window.on_fs_animations_changed()


class SpritesheetTab:
    def __init__(self, editor_window: "EditorWindow", parent: ttk.Notebook) -> None:
        self.editor_window: Final = editor_window

        self._ms_spritesheet: Optional[dict[Name, Any]] = None

        self.frame: Final = tk.Frame(parent)
        self.frame.columnconfigure(1, weight=1)

        self._inputs: Final[list[gui.AbstractInput[Any]]] = [
            gui.NameInput(self.frame, "name", "Name:"),
            gui.PngFilenameInput(self.frame, "palette", "Palette Image: "),
            gui.IntInput(self.frame, "firstTile", "First Tile ID:"),
            gui.IntInput(self.frame, "endTile", "End Tile ID:"),
        ]

        row = 0
        for w in self._inputs:
            w.widget.bind(w.DATA_CHANGED_EVENT_NAME, self._on_input_changed)

            if w.label:
                w.label.grid(row=row, column=0, sticky=tk.W)
                if w.MULTI_LINE_INPUT:
                    row += 1

            if w.MULTI_LINE_INPUT:
                w.widget.grid(row=row, column=0, columnspan=3, sticky=tk.EW)
            else:
                w.widget.grid(row=row, column=1, columnspan=2, sticky=tk.EW)
            row += 1

        separator: Final = ttk.Separator(self.frame, orient=tk.HORIZONTAL)
        separator.grid(row=row, column=0, columnspan=3, pady=6, sticky=tk.EW)
        row += 1

        self._add_fs_button: Final = tk.Button(self.frame, text="Add Frameset", command=editor_window.add_frameset)
        self._add_fs_button.grid(row=row, column=1, sticky=tk.E)
        row += 1

        fs_listbox_sb: Final = tk.Scrollbar(self.frame, orient=tk.VERTICAL)

        self._fs_listbox_list: Final = tk.StringVar()
        self._fs_listbox: Final = tk.Listbox(self.frame, listvariable=self._fs_listbox_list, yscrollcommand=fs_listbox_sb.set)

        self._fs_listbox.grid(row=row, column=0, columnspan=2, sticky=tk.NSEW)
        fs_listbox_sb.grid(row=row, column=2, sticky=tk.NS)

        self.frame.rowconfigure(row, weight=1)
        row += 1

        self._fs_listbox.bind("<<ListboxSelect>>", self._on_fs_listbox_selected)

    def update_and_check_valid(self) -> bool:
        if self._ms_spritesheet is None:
            return True

        for w in self._inputs:
            w.update_dict()

        return all(i.is_valid() for i in self._inputs)

    def _on_input_changed(self, event: Any) -> None:
        self.editor_window.on_spritesheet_data_changed()

    def set_ms_spritesheet(self, ss: Optional[dict[Name, Any]]) -> None:
        if self._ms_spritesheet is ss:
            return
        self._ms_spritesheet = ss

        for w in self._inputs:
            w.set_dict(ss)

    def update_fs_listbox(self, frameset_names: list[str]) -> None:
        self._fs_listbox_list.set(frameset_names)  # type: ignore

    def _on_fs_listbox_selected(self, event: Any) -> None:
        sel: list[int] = self._fs_listbox.curselection()  # type: ignore

        if sel:
            self.editor_window.set_selected_frameset_index(sel[0])

    def set_selected_fs_index(self, index: int) -> None:
        self._fs_listbox.selection_set(index)
        self._fs_listbox.see(index)


class EditorWindow:
    SS_TAB: Final = 0
    FS_TAB: Final = 1

    def __init__(self, json_filename: Filename, ms_export_orders: MsExportOrder):
        self.json_filename: Filename = json_filename
        self.ms_dir: Filename = os.path.dirname(json_filename)
        self.ms_export_orders: MsExportOrder = ms_export_orders

        self.ms_spritesheet_data: Optional[OrderedDict[Name, Any]] = None

        self._ms_ss_data_unsaved: bool = False

        # Selected frameset index
        self._frameset_index: int = -1

        self.window: Final = tk.Tk()
        self.window.title("MetaSprite Editor")
        self.window.minsize(width=800, height=400)
        # Start maximized
        self.window.attributes("-zoomed", True)

        self.window.protocol("WM_DELETE_WINDOW", self._on_close_request)

        self.window.columnconfigure(1, weight=1)
        self.window.rowconfigure(0, weight=1)

        self.left_sidebar: Final = tk.Frame(self.window)
        self.left_sidebar.grid(row=0, column=0, sticky=tk.NSEW, padx=4, pady=2)
        self.left_sidebar.rowconfigure(1, weight=1)
        self.left_sidebar.columnconfigure(1, weight=1)

        self._save_button: Final = tk.Button(self.left_sidebar, text="Save", command=self.save_ms_spritesheet)
        self._save_button.grid(row=0, column=0, padx=2, pady=2, sticky=tk.EW)

        self._fs_combo: Final = ttk.Combobox(self.left_sidebar, state="readonly", exportselection=False)
        self._fs_combo.grid(row=0, column=1, padx=4, sticky=tk.EW)
        self._fs_combo.bind("<<ComboboxSelected>>", self._on_fs_combo_selected)

        self.notebook: Final = ttk.Notebook(self.left_sidebar)
        self.notebook.grid(row=1, column=0, columnspan=2, sticky=tk.NS)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_notebook_tab_changed)

        self.spritesheet_tab: Final = SpritesheetTab(self, self.notebook)
        self.notebook.add(self.spritesheet_tab.frame, text="Spritesheet")

        self.frameset_editor: Final = FramesetEditor(self, self.notebook)
        self.notebook.add(self.frameset_editor.frame, text="FrameSet")

        self.canvas: Final = Canvas(self.window, ms_export_orders, self.ms_dir)
        self.canvas.frame.grid(row=0, column=1, sticky=tk.NSEW)

        self.frameset_editor.set_ms_export_orders(self.ms_export_orders)

        self.__load_json_file()

    def print_exception_traceback(self) -> None:
        self.canvas.print_exception_traceback()

    def _redraw_canvas(self) -> None:
        fs_json = self.frameset_editor.get_frameset_data()
        if fs_json:
            try:
                fs = load_metasprite_frameset_from_dict(fs_json["name"], fs_json, skip_animations=True)
                self.canvas.set_frameset(fs)
                return
            except:
                self.print_exception_traceback()

    def _update_fs_namelists(self) -> None:
        if self.ms_spritesheet_data:
            fs_names = [fs.get("name", "") for fs in self.ms_spritesheet_data["framesets"]]
        else:
            fs_names = list()

        self._fs_combo.configure(values=fs_names)
        self.spritesheet_tab.update_fs_listbox(fs_names)

        if 0 <= self._frameset_index < len(fs_names):
            self._fs_combo.current(self._frameset_index)
            self.spritesheet_tab.set_selected_fs_index(self._frameset_index)
        else:
            self._fs_combo.set("")
            self.spritesheet_tab.set_selected_fs_index(-1)

    def _on_fs_combo_selected(self, event: Any) -> None:
        self._fs_combo.selection_clear()
        self.set_selected_frameset_index(self._fs_combo.current())
        self.notebook.select(self.FS_TAB)

    def _on_notebook_tab_changed(self, event: Any) -> None:
        # The frameset listbox selection (on the spritesheet tab) disappears when SS tab is unselected.
        if self.notebook.index("current") == self.SS_TAB:  # type: ignore
            # Restore the listbox selection when the SS tab is visible again
            self.spritesheet_tab.set_selected_fs_index(self._frameset_index)

    def _update_and_check_valid(self, event_string: str) -> bool:
        ss_valid: Final = self.spritesheet_tab.update_and_check_valid()
        fs_valid: Final = self.frameset_editor.update_all_and_check_valid()

        if not fs_valid:
            tk.messagebox.showerror("invalid Frameset", f"You must fix all errors in the frameset\nbefore { event_string }.")
            self.notebook.select(self.FS_TAB)
            return False

        elif not ss_valid:
            tk.messagebox.showerror("Invalid Spritesheet", f"You must fix all errors in the spritesheet\nbefore { event_string }.")
            self.notebook.select(self.SS_TAB)
            return False

        else:
            return True

    def set_selected_frameset_index(self, index: int) -> None:
        if self._frameset_index == index:
            return

        if self._update_and_check_valid("changing the frameset"):
            self._frameset_index = -1
            self.canvas.clear()

            fs = None

            if self.ms_spritesheet_data:
                try:
                    fs = self.ms_spritesheet_data["framesets"][index]
                    self._frameset_index = index
                except:
                    fs = None
                    self._frameset_index = -1
                    self.print_exception_traceback()

            self.frameset_editor.set_selected_frameset(fs)
            if fs:
                self._redraw_canvas()

        # Change or reset the selected frameset_index
        self.spritesheet_tab.set_selected_fs_index(self._frameset_index)
        self._fs_combo.current(self._frameset_index)

    def add_frameset(self) -> None:
        if self._update_and_check_valid("creating a new frameset"):
            if self.ms_spritesheet_data:
                fs_list = self.ms_spritesheet_data.get("framesets")
                if isinstance(fs_list, list):
                    self.notebook.select(self.FS_TAB)

                    fs_list.append(create_blank_frameset())
                    self._update_fs_namelists()
                    self.set_selected_frameset_index(len(fs_list) - 1)

    def on_spritesheet_data_changed(self) -> None:
        self._ms_ss_data_unsaved = True
        # ::TODO compile SS data in the background::

    # A non-animation input was changed.
    def on_fs_inputs_changed(self) -> None:
        self._ms_ss_data_unsaved = True
        self._redraw_canvas()
        # ::TODO compile FS data in the background::

    def on_fs_animations_changed(self) -> None:
        self._ms_ss_data_unsaved = True
        # ::TODO compile FS data in the background::

    def on_fs_name_changed(self) -> None:
        self._ms_ss_data_unsaved = True
        self._update_fs_namelists()

    def __load_json_file(self) -> None:
        self.spritesheet_tab.set_ms_spritesheet(None)
        self.frameset_editor.set_selected_frameset(None)

        try:
            self.ms_spritesheet_data = load_ms_spritesheet(self.json_filename)
            self._ms_ss_data_unsaved = False
        except:
            self.ms_spritesheet_data = None
            self.print_exception_traceback()

        self.spritesheet_tab.set_ms_spritesheet(self.ms_spritesheet_data)
        self._update_fs_namelists()
        self.set_selected_frameset_index(0)

    def save_ms_spritesheet(self) -> None:
        if self.ms_spritesheet_data is None:
            return

        if self._update_and_check_valid("saving"):
            try:
                save_ms_spritesheet(self.ms_spritesheet_data, self.json_filename)
                self._ms_ss_data_unsaved = False
            except:
                self._ms_ss_data_unsaved = True
                self.print_exception_traceback()

    def _on_close_request(self) -> None:
        self.force_quit: bool = False

        if self.ms_spritesheet_data is None:
            self.force_quit = True

        if self.ms_spritesheet_data:
            if self._ms_ss_data_unsaved:
                r = tk.messagebox.askyesnocancel("There are unsaved changes", "Save changes before closing?")
                if r is True:
                    self.save_ms_spritesheet()
                if r is False:
                    self.force_quit = True

        if self.force_quit or self._ms_ss_data_unsaved is False:
            self.window.destroy()

    def mainloop(self) -> None:
        self.window.mainloop()


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("json_filename", action="store", help="Sprite map JSON file")
    parser.add_argument("ms_export_order_json_file", action="store", help="metasprite export order map JSON file")

    args = parser.parse_args()

    return args


def main() -> None:
    args = parse_arguments()

    ms_export_orders = load_ms_export_order_json(args.ms_export_order_json_file)

    editor_window = EditorWindow(args.json_filename, ms_export_orders)
    editor_window.mainloop()


if __name__ == "__main__":
    main()
