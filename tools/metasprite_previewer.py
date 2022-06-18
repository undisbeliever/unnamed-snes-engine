#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import os.path
import argparse
import traceback

import tkinter as tk
import tkinter.ttk as ttk
from tkinter.scrolledtext import ScrolledText

from typing import Any, Optional

from _json_formats import load_ms_export_order_json, load_metasprites_string, \
                          Name, Filename, MsExportOrder, MsSpritesheet



ZOOM = 9

FRAME_WIDTH = 3

OBJ_WIDTH = 3
OBJ_COLOR = '#22cc22'

TILE_HITBOX_WIDTH = 3
TILE_HITBOX_COLOR = '#ffaa00'

HITBOX_WIDTH = 3
HITBOX_COLOR = '#aa0000'

HURTBOX_WIDTH = 3
HURTBOX_COLOR = '#0000aa'


class Editor:
    def __init__(self, json_filename : Filename, ms_export_orders : MsExportOrder):
        self.json_filename    : Filename = json_filename
        self.ms_dir           : Filename = os.path.dirname(json_filename)
        self.ms_export_orders : MsExportOrder = ms_export_orders

        self.ms_data          : Optional[MsSpritesheet] = None
        self.image            : Optional[tk.PhotoImage] = None
        self.image_source     : Optional[Filename] = None


        self.window = tk.Tk()
        self.window.title('MetaSprite Previewer')


        self.textarea = ScrolledText(self.window,
                                     wrap=tk.WORD, width=70, height=25)
        self.textarea.pack(fill=tk.BOTH, expand=False, side=tk.LEFT)


        right_frame = tk.Frame(self.window, relief=tk.RAISED, borderwidth=1)
        right_frame.pack(fill=tk.BOTH, expand=True, side=tk.RIGHT)

        top_row = tk.Frame(right_frame)
        top_row.pack(fill=tk.X, expand=False, side=tk.TOP)

        refresh_button = tk.Button(top_row,
                            text='Refresh',
                            command=self.on_refresh_clicked)
        refresh_button.pack(side=tk.LEFT)


        self.fs_combo = ttk.Combobox(top_row, state='readonly')
        self.fs_combo.pack(side=tk.LEFT)

        self.fs_combo.bind('<<ComboboxSelected>>', self.on_fs_combo_selected)


        def add_show_cb(text : str) -> tk.IntVar:
            v = tk.IntVar()
            v.set(1)
            cb = tk.Checkbutton(top_row, text=text, variable=v, command=self._update_canvas)
            cb.pack(side=tk.RIGHT)
            return v
        self.show_hitboxes = add_show_cb('Hitboxes')
        self.show_hurtboxes = add_show_cb('Hurtboxes')
        self.show_tilehitboxes = add_show_cb('TileHitboxes')
        self.show_objects = add_show_cb('Objects')
        self.show_labels = add_show_cb('Labels')


        self.canvas_frame = tk.Frame(right_frame)
        self.canvas_frame.pack(fill=tk.BOTH, expand=True, side=tk.BOTTOM)

        self.canvas = tk.Canvas(self.canvas_frame, width=600, height=300)

        h_scroll = tk.Scrollbar(self.canvas_frame, orient=tk.HORIZONTAL,
                                command=self.canvas.xview)

        v_scroll = tk.Scrollbar(self.canvas_frame, orient=tk.VERTICAL,
                                command=self.canvas.yview)

        self.canvas.config(xscrollcommand=h_scroll.set,
                           yscrollcommand=v_scroll.set)

        v_scroll.pack(fill=tk.Y, expand=False, side=tk.RIGHT)
        h_scroll.pack(fill=tk.X, expand=False, side=tk.BOTTOM)
        self.canvas.pack(fill=tk.BOTH, expand=True, side=tk.LEFT)

        self.on_refresh_clicked()



    def mainloop(self) -> None:
        self.window.mainloop()



    def print_exception_traceback(self) -> None:
        self.canvas.delete('all')
        self.canvas.create_text(10, 10, text=traceback.format_exc(16), anchor=tk.NW)
        self.canvas.xview(tk.MOVETO, 0)
        self.canvas.yview(tk.MOVETO, 0)



    def on_fs_combo_selected(self, event : Any) -> None:
        self._update_canvas()



    def on_refresh_clicked(self) -> None:
        self._load_and_parse_json_file()
        self._update_combobox()
        self._update_canvas()



    def _load_and_parse_json_file(self) -> None:
        try:
            with open(self.json_filename, 'r') as fp:
                json_text = fp.read()

            # Store json in textarea
            self.textarea['state'] = tk.NORMAL
            self.textarea.delete('1.0', 'end')
            self.textarea.insert('1.0', json_text)
            self.textarea['state'] = tk.DISABLED

            # Parse json file
            self.ms_data = load_metasprites_string(json_text)
        except:
            self.ms_data = None
            self.print_exception_traceback()



    def _update_combobox(self) -> None:
        if self.ms_data is None:
            self.fs_combo['values'] = []
            return

        values = list(self.ms_data.framesets.keys())
        self.fs_combo['values'] = values

        if not self.fs_combo.get():
            if values:
                self.fs_combo.set(values[0])



    def _update_canvas(self) -> None:
        if self.ms_data is None:
            return

        try:
            fs = self.ms_data.framesets[self.fs_combo.get()]
        except:
            self.print_exception_traceback()
            return


        c = self.canvas


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

            self.canvas['scrollregion'] = (0, 0, self.image.width() + FRAME_WIDTH, self.image.height() + FRAME_WIDTH)


        c.delete('all')
        c.create_image(0, 0, image=self.image, anchor=tk.NW)


        show_labels = self.show_labels.get()
        show_objects = self.show_objects.get()
        show_tilehitboxes = self.show_tilehitboxes.get()
        show_hurtboxes = self.show_hurtboxes.get()
        show_hitboxes = self.show_hitboxes.get()


        if fs.pattern:
            base_pattern = self.ms_export_orders.patterns.get(fs.pattern, None)
        else:
            base_pattern = None


        # Frame grid
        image_width = self.image.width()
        image_height = self.image.height()
        frames_per_row = image_width / (fs.frame_width * ZOOM)

        frame_width = ZOOM * fs.frame_width
        frame_height = ZOOM * fs.frame_height

        x_origin = fs.x_origin * ZOOM
        y_origin = fs.y_origin * ZOOM

        th_x1 = x_origin - fs.tilehitbox.half_width * ZOOM
        th_x2 = x_origin + fs.tilehitbox.half_width * ZOOM
        th_y1 = y_origin - fs.tilehitbox.half_height * ZOOM
        th_y2 = y_origin + fs.tilehitbox.half_height * ZOOM


        for x in range(0, image_width + 1, frame_width):
            c.create_line(((x, 0), (x, image_height)), width=FRAME_WIDTH)

        for y in range(0, image_height + 1, frame_height):
            c.create_line(((0, y), (image_width, y)), width=FRAME_WIDTH)

        for block in fs.blocks:
            if block.pattern:
                block_pattern = self.ms_export_orders.patterns.get(block.pattern, base_pattern)
            else:
                block_pattern = base_pattern

            for i, frame_name in enumerate(block.frames):
                frame_number = block.start + i
                x = (frame_number % frames_per_row) * frame_width
                y = (frame_number // frames_per_row) * frame_height

                # Draw origin
                c.create_line(((x + x_origin, y), (x + x_origin, y + frame_height)), width=1)
                c.create_line(((x, y + y_origin), (x + frame_width, y + y_origin)), width=1)

                if show_labels:
                    c.create_text(x + 5, y + 3, anchor=tk.NW, text=frame_name)

                if block_pattern and show_objects:
                    for o in block_pattern.objects:
                        ox = x + (block.x + o.xpos) * ZOOM
                        oy = y + (block.y + o.ypos) * ZOOM
                        osize = o.size * ZOOM
                        c.create_rectangle(ox, oy, ox + osize, oy + osize,
                                           width=OBJ_WIDTH, outline=OBJ_COLOR)

                if show_tilehitboxes:
                    c.create_rectangle(x + th_x1, y + th_y1, x + th_x2, y + th_y2,
                                       width=TILE_HITBOX_WIDTH, outline=TILE_HITBOX_COLOR)

                if show_hurtboxes:
                    box = fs.hurtbox_overrides.get(frame_name, block.default_hurtbox)
                    if box is not None:
                        c.create_rectangle(x + box.x * ZOOM, y + box.y * ZOOM, x + (box.x + box.width) * ZOOM, y + (box.y + box.height) * ZOOM,
                                           width=HURTBOX_WIDTH, outline=HURTBOX_COLOR)

                if show_hitboxes:
                    box = fs.hitbox_overrides.get(frame_name, block.default_hitbox)
                    if box is not None:
                        c.create_rectangle(x + box.x * ZOOM, y + box.y * ZOOM, x + (box.x + box.width) * ZOOM, y + (box.y + box.height) * ZOOM,
                                           width=HITBOX_WIDTH, outline=HITBOX_COLOR)



def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('json_filename', action='store',
                        help='Sprite map JSON file')
    parser.add_argument('ms_export_order_json_file', action='store',
                        help='metasprite export order map JSON file')

    args = parser.parse_args()

    return args;



def main() -> None:
    args = parse_arguments()

    ms_export_orders = load_ms_export_order_json(args.ms_export_order_json_file)

    editor = Editor(args.json_filename, ms_export_orders)
    editor.mainloop()



if __name__ == '__main__':
    main()



