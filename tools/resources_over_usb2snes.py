#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:

import os
import argparse
from unnamed_snes_game.resources_compiler import DataStore
from unnamed_snes_game.resources_over_usb2snes import create_fs_watcher_thread, create_websocket_thread
from unnamed_snes_game.gui.ro_usb2snes_gui import Rou2sWindow, GuiSignals


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("-a", "--address", required=False, default="ws://localhost:8080", help="Websocket address")
    parser.add_argument("-j", "--processes", required=False, type=int, default=None, help="Number of processors to use (default=all)")
    parser.add_argument("resources_directory", action="store", help="resources directory")
    parser.add_argument("sfc_file", action="store", help="sfc file (without resources)")

    args = parser.parse_args()

    sfc_file_relpath = os.path.relpath(args.sfc_file, args.resources_directory)
    os.chdir(args.resources_directory)

    data_store = DataStore()
    gui = Rou2sWindow(data_store)
    signals = gui.signals

    fs_watcher_thread = create_fs_watcher_thread(data_store, signals, sfc_file_relpath, args.processes)
    websocket_thread = create_websocket_thread(data_store, signals, sfc_file_relpath, args.address)

    fs_watcher_thread.start()
    websocket_thread.start()

    gui.mainloop()


if __name__ == "__main__":
    main()
