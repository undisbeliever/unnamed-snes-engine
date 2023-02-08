# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import os.path
from typing import Any, Final, Optional, Union

import tkinter as tk
import tkinter.ttk as ttk
import tkinter.messagebox

from ..resources_over_usb2snes import FsWatcherSignals
from ..resources_compiler import DataStore, ResourceError, NonResourceError
from ..common import MultilineError
from ..snes import InvalidTilesError
from ..json_formats import Filename
from .. import metasprite as ms


class ErrorsTab:
    ZOOM: Final = 2

    def __init__(self, data_store: DataStore, parent: ttk.Notebook) -> None:
        self.data_store: Final = data_store

        self.selected_error: Optional[Union[ResourceError, NonResourceError]] = None
        self.errors: list[Union[ResourceError, NonResourceError]] = list()

        self.cursor_ypos: int = 0

        # Must store the images used by the canvas or else they are not display on screen
        self._images: list[tk.PhotoImage] = list()
        self._image_cache: dict[Filename, tk.PhotoImage] = dict()

        self.frame: Final = tk.Frame(parent)
        self.frame.rowconfigure(0, weight=1)
        self.frame.columnconfigure(2, weight=1)

        listbox_sb: Final = tk.Scrollbar(self.frame, orient=tk.VERTICAL)

        self.listbox_list: Final = tk.StringVar()
        self.listbox: Final = tk.Listbox(self.frame, width=30, listvariable=self.listbox_list, yscrollcommand=listbox_sb.set)

        self.listbox.grid(row=0, column=0, rowspan=2, sticky=tk.NSEW)
        listbox_sb.grid(row=0, column=1, rowspan=2, sticky=tk.NS)

        self._canvas: Final = tk.Canvas(self.frame, width=400, height=400)
        self._canvas.grid(row=0, column=2, sticky=tk.NSEW)

        h_scroll = tk.Scrollbar(self.frame, orient=tk.HORIZONTAL, command=self._canvas.xview)
        v_scroll = tk.Scrollbar(self.frame, orient=tk.VERTICAL, command=self._canvas.yview)

        self._canvas.config(xscrollcommand=h_scroll.set, yscrollcommand=v_scroll.set)

        v_scroll.grid(row=0, column=3, sticky=tk.NS)
        h_scroll.grid(row=1, column=2, sticky=tk.EW)

        self.listbox.bind("<<ListboxSelect>>", self.on_listbox_selected)

    def update_list(self) -> None:
        self.errors = self.data_store.get_errors()
        str_list: list[str] = [e.res_string() for e in self.errors]

        self.listbox_list.set(str_list)  # type: ignore

        if self.errors:
            e_id = 0
            if self.selected_error:
                se_key = self.selected_error.error_key

                # Find the list item with the same error_key (if it exists)
                for i, e in enumerate(self.errors):
                    if e.error_key == se_key:
                        e_id = i
                        break

            self.listbox.select_clear(0, "end")
            self.listbox.selection_set(e_id)
            self.listbox.see(e_id)
            self.listbox.activate(e_id)
            self.listbox.selection_anchor(e_id)
            self.update_canvas(self.errors[e_id])
        else:
            self.selected_error = None
            self.clear_canvas()

    def on_listbox_selected(self, event: Any) -> None:
        sel: list[int] = self.listbox.curselection()  # type: ignore
        if sel:
            selected = sel[0]
            if 0 <= selected < len(self.errors):
                res_error = self.errors[selected]
                self.update_canvas(res_error)

    def on_resource_compiled(self, event: Any) -> None:
        self._image_cache.clear()
        self.update_list()

    def update_canvas(self, error: Union[ResourceError, NonResourceError]) -> None:
        if self.selected_error is error:
            return

        self.clear_canvas()

        self.selected_error = error

        if isinstance(error, NonResourceError):
            self.draw_large_text(f"Error loading { error.res_string() }")
        else:
            self.draw_large_text(f"Error compiling { error.res_string() }")
        self.cursor_ypos += 10

        e = error.error
        if isinstance(e, ms.SpritesheetError):
            self.draw_ms_error(e)
        elif isinstance(e, InvalidTilesError):
            self.draw_invalid_tiles_error(e)
        elif isinstance(e, MultilineError):
            self.draw_text(e.string_indented())
        else:
            self.draw_text(str(e))

        self._canvas["scrollregion"] = self._canvas.bbox("all")

    def clear_canvas(self) -> None:
        self._canvas.delete("all")
        self._canvas.xview(tk.MOVETO, 0)
        self._canvas.yview(tk.MOVETO, 0)

        self.selected_error = None

        # should be after canvas delete
        self._images.clear()

        self.cursor_ypos = 3

    def draw_text(self, s: str) -> None:
        t_id = self._canvas.create_text(10, self.cursor_ypos, text=s, font=("TkDefaultFont", 11), anchor=tk.NW)
        self.cursor_ypos = self._canvas.bbox(t_id)[3] + 3

    def draw_large_text(self, s: str) -> None:
        t_id = self._canvas.create_text(10, self.cursor_ypos, text=s, font=("TkDefaultFont", 16), anchor=tk.NW)
        self.cursor_ypos = self._canvas.bbox(t_id)[3] + 3

    # Returns image position
    def draw_image(self, image_fn: Filename) -> tuple[int, int]:
        image = self._image_cache.get(image_fn)
        if image is None:
            image = tk.PhotoImage(file=image_fn).zoom(self.ZOOM, self.ZOOM)
            self._image_cache[image_fn] = image

        # Keep a reference to the image to ensure it remains visible
        self._images.append(image)

        x = 20
        y = self.cursor_ypos

        self._canvas.create_image(x, y, image=image, anchor=tk.NW)
        self.cursor_ypos += image.height() + 3

        return x, y

    def draw_invalid_tiles_error(self, error: InvalidTilesError) -> None:
        ZOOM: Final = self.ZOOM

        self.draw_text(f"{ error.message } for { len(error.invalid_tiles) } { error.tile_size }px tiles in { error.filename }:")
        ix, iy = self.draw_image(error.filename)

        tmw: Final = error.tilemap_width
        ts: Final = error.tile_size * ZOOM

        for t in error.invalid_tiles:
            tx = ix + ((t % tmw) * ts)
            ty = iy + ((t // tmw) * ts)
            self._canvas.create_rectangle(tx, ty, tx + ts, ty + ts, width=5, outline="#ffffff")
            self._canvas.create_rectangle(tx, ty, tx + ts, ty + ts, width=3, outline="#ff0000")

    def draw_ms_tile_errors(self, image_fn: Filename, errors: set[ms.TileError]) -> None:
        ZOOM: Final = self.ZOOM

        ix, iy = self.draw_image(image_fn)

        for e in errors:
            tx = ix + e.x * ZOOM
            ty = iy + e.y * ZOOM
            ts = e.tile_size * ZOOM
            self._canvas.create_rectangle(tx, ty, tx + ts, ty + ts, width=5, outline="#ffffff")
            self._canvas.create_rectangle(tx, ty, tx + ts, ty + ts, width=3, outline="#ff0000")

    def draw_ms_error(self, error: ms.SpritesheetError) -> None:
        self.draw_large_text(f"{ len(error.errors) } invalid spritesheets")
        self.cursor_ypos += 5

        for fs_error in error.errors:
            self.cursor_ypos += 5
            self.draw_large_text(fs_error.fs_name)

            if fs_error.tiles:
                self.draw_text(f"{ len(fs_error.tiles) } tile errors in {fs_error.image_fn}")
                self.draw_ms_tile_errors(os.path.join(error.ms_dir, fs_error.image_fn), fs_error.tiles)

            self.draw_text(fs_error.string_indented())
