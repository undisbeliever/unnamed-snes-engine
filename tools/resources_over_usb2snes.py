#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:
#
# Resources over usb2snes
#
# Distributed under the MIT License (MIT)
#
# Copyright (c) 2020 - 2022, Marcus Rowe <undisbeliever@gmail.com>
#
# Permission is hereby granted, free of charge, to any person obtaining a copy
# of this software and associated documentation files (the "Software"), to deal
# in the Software without restriction, including without limitation the rights
# to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
# copies of the Software, and to permit persons to whom the Software is
# furnished to do so, subject to the following conditions:
#
# The above copyright notice and this permission notice shall be included in all
# copies or substantial portions of the Software.
#
# THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
# IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
# FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
# AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
# LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
# OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
# SOFTWARE.


import re
import sys
import os.path
import argparse
import threading
import subprocess
import multiprocessing
from enum import IntEnum, unique

import json
import asyncio
import posixpath
import websockets.client

import watchdog.events # type: ignore
import watchdog.observers # type: ignore

from typing import cast, Any, Callable, Final, NamedTuple, Optional, Union

from convert_tileset import convert_mt_tileset
from convert_metasprite import convert_spritesheet, generate_pattern_grids, PatternGrid, MsFsEntry, build_ms_fs_data
from convert_rooms import get_list_of_tmx_files, extract_room_id, compile_room
from convert_resources import convert_tiles
from insert_resources import read_binary_file, read_symbols_file, validate_sfc_file

from _json_formats import load_mappings_json, load_entities_json, load_ms_export_order_json, load_resources_json, load_metasprites_json, \
                          Name, Filename, MemoryMap, Mappings, EntitiesJson, MsExportOrder, ResourcesJson

from _common import ResourceType, MS_FS_DATA_BANK_OFFSET, USB2SNES_DATA_BANK_OFFSET, USE_RESOURCES_OVER_USB2SNES_LABEL

from _entity_data import create_entity_rom_data, ENTITY_ROM_DATA_LABEL, ENTITY_ROM_DATA_BYTES_PER_ENTITY


# Sleep delay when waiting on a resource
# (seconds)
WAITING_FOR_RESOURCE_DELAY : Final[float] = 0.5

# Sleep delay when there is no request to process
# (seconds)
NORMAL_REQUEST_SLEEP_DELAY : Final[float] = 1 / 10

# Sleep delay after processing a request.
# Used to prevent lag when the game sends multiple requests.
# (seconds)
BURST_SLEEP_DELAY : Final[float] = 1 / 100

# Number of times to sleep for `BURST_SLEEP_DELAY`, before returning to `NORMAL_REQUEST_SLEEP_DELAY`
BURST_COUNT : Final[int] = 5


N_RESOURCE_TYPES : Final[int] = len(ResourceType)


# Must match `SpecialRequestType` in `src/resources-over-usb2snes.wiz`
class SpecialRequestType(IntEnum):
    rooms           = 0xff
    init            = 0xaa



# =======
# Logging
# =======

if __name__ != '__main__':
    raise ImportError("Cannot import this file as a python module")

# Disable the `print` function
print : Final = None

# Thread safe printing
def log(s : str) -> None:
    with __log_lock:
        sys.stdout.write(s)
        sys.stdout.write('\n')

__log_lock : Final = threading.Lock()



# =====================
# Resource Data Storage
# =====================

@unique
class DataType(IntEnum):
    MT_TILESET           = ResourceType.mt_tilesets
    MS_SPRITESHEET       = ResourceType.ms_spritesheets
    TILE                 = ResourceType.tiles
    ROOM                 = SpecialRequestType.rooms



class CompilerOutput(NamedTuple):
    data_type        : DataType
    resource_id      : int

    data             : Optional[bytes]           = None
    msfs_entries     : Optional[list[MsFsEntry]] = None
    error            : Optional[str]             = None



class MsFsAndEntityOutput(NamedTuple):
    msfs_data       : Optional[bytes] = None
    entity_rom_data : Optional[bytes] = None
    error           : Optional[str]   = None



class DataStoreKey(NamedTuple):
    data_type    : DataType
    resource_id  : int



# Thread Safety: This class MUST ONLY be accessed via method calls.
# Thread Safety: All methods in this class must acquire the `_lock` before accessing fields.
class DataStore:
    def __init__(self, mappings : Mappings):
        self._lock : Final[threading.Lock] = threading.Lock()

        with self._lock:
            self._resources             : dict[DataStoreKey, CompilerOutput] = dict()

            self._msfs_lists            : list[Optional[list[MsFsEntry]]] = [ None ] * len(mappings.ms_spritesheets)

            self._msfs_and_entity_data       : Optional[MsFsAndEntityOutput] = None
            self._msfs_and_entity_data_valid : bool = False

            # Not incremented when room data changes
            self._not_room_counter : int = 0


    def insert_data(self, c : CompilerOutput) -> None:
        with self._lock:
            key = DataStoreKey(c.data_type, c.resource_id)
            self._resources[key] = c

            if c.data_type != DataType.ROOM:
                self._not_room_counter += 1

            if c.data_type == DataType.MS_SPRITESHEET:
                self._msfs_lists[c.resource_id] = c.msfs_entries
                self._msfa_and_entity_rom_data = None
                self._msfs_and_entity_data_valid = False


    def insert_msfs_and_entity_data(self, me : MsFsAndEntityOutput) -> None:
        with self._lock:
            self._msfs_and_entity_data = me
            self._not_room_counter += 1


    def get_not_room_counter(self) -> int:
        with self._lock:
            return self._not_room_counter


    def get_msfs_lists(self) -> list[Optional[list[MsFsEntry]]]:
        with self._lock:
            return self._msfs_lists


    def is_msfs_and_entity_data_valid(self) -> bool:
        with self._lock:
            return self._msfs_and_entity_data_valid


    def mark_msfs_and_entity_data_valid(self) -> None:
        with self._lock:
            self._msfs_and_entity_data_valid = True


    def get_msfs_and_entity_data(self) -> Optional[MsFsAndEntityOutput]:
        with self._lock:
            return self._msfs_and_entity_data


    def get_resource_data(self, r_type : Union[ResourceType, SpecialRequestType], r_id : int) -> Optional[CompilerOutput]:
        with self._lock:
            key = DataStoreKey(DataType(r_type.value), r_id)
            return self._resources.get(key)



# ==================
# Resource Compilers
# ==================



# ASSUMES: current working directory is the resources directory
class Compiler:
    # ::TODO do something about these hard coded filenames::

    def __init__(self, sym_filename : Filename) -> None:
        # Fixed input
        self.mappings         : Final[Mappings]          = load_mappings_json('mappings.json')
        self.entities         : Final[EntitiesJson]      = load_entities_json('entities.json')
        self.resources        : Final[ResourcesJson]     = load_resources_json('resources.json')
        self.ms_export_orders : Final[MsExportOrder]     = load_ms_export_order_json('ms-export-order.json')
        self.ms_pattern_grids : Final[list[PatternGrid]] = generate_pattern_grids(self.ms_export_orders)
        self.symbols          : Final[dict[str, int]]    = read_symbols_file(sym_filename)


    def compile_mt_tileset(self, rid : int) -> CompilerOutput:
        name = self.mappings.mt_tilesets[rid]

        try:
            tsx_filename     = f"metatiles/{ name }.tsx"
            image_filename   = f"metatiles/{ name }-tiles.png"
            palette_filename = f"metatiles/{ name }-palette.png"

            data = convert_mt_tileset(tsx_filename, image_filename, palette_filename, self.mappings)

            return CompilerOutput(DataType.MT_TILESET, rid, data=data)

        except Exception as e:
            return CompilerOutput(DataType.MT_TILESET, rid, error=f"Cannot compile tiles `{ name }`: { e }")


    def compile_ms_spritesheet(self, rid : int) -> CompilerOutput:
        name = self.mappings.ms_spritesheets[rid]

        try:
            json_filename = f"metasprites/{ name }/_metasprites.json"

            ms_input = load_metasprites_json(json_filename)
            ms_dir = os.path.dirname(json_filename)

            data, msfs_entries = convert_spritesheet(ms_input, self.ms_export_orders, self.ms_pattern_grids, ms_dir)

            return CompilerOutput(DataType.MS_SPRITESHEET, rid, data=data, msfs_entries=msfs_entries)

        except Exception as e:
            return CompilerOutput(DataType.MS_SPRITESHEET, rid, error=f"Cannot compile tiles `{ name }`: { e }")


    def compile_tiles(self, rid : int) -> CompilerOutput:
        name = self.mappings.tiles[rid]

        try:
            t = self.resources.tiles[name]

            data = convert_tiles(t)

            return CompilerOutput(DataType.TILE, rid, data=data)

        except Exception as e:
            return CompilerOutput(DataType.TILE, rid, error=f"Cannot compile tiles `{ name }`: { e }")


    def compile_room(self, basename : Filename) -> CompilerOutput:
        room_id = -1

        try:
            room_id = extract_room_id(basename)

            filename = os.path.join('rooms', basename)

            data = compile_room(filename, self.entities, self.mappings)

            return CompilerOutput(DataType.ROOM, room_id, data=data)

        except Exception as e:
            return CompilerOutput(DataType.ROOM, room_id, error=f"Cannot compile room: { filename }: { e }")


    def compile_msfs_and_entity_data(self, optional_msfs_lists : list[Optional[list[MsFsEntry]]]) -> MsFsAndEntityOutput:

        if any(l is None for l in optional_msfs_lists):
            return MsFsAndEntityOutput(error=f"Cannot compile MsFs data.  There is an error in a MS Spritesheet.")

        msfs_lists = cast(list[list[MsFsEntry]], optional_msfs_lists)

        try:
            rom_data, ms_map = build_ms_fs_data(msfs_lists, self.symbols, self.mappings.memory_map.mode)
            ms_fs_data = bytes(rom_data.data())
        except Exception as e:
            return MsFsAndEntityOutput(error=f"Cannot compile MsFs data: { e }")

        try:
            entity_rom_data = create_entity_rom_data(self.entities.entities, self.entities.entity_functions, self.symbols, ms_map)
        except Exception as e:
            return MsFsAndEntityOutput(error=f"Cannot compile Entity ROM data: { e }")

        return MsFsAndEntityOutput(ms_fs_data, entity_rom_data)



# ASSUMES: current working directory is the resources directory
class FsEventHandler(watchdog.events.FileSystemEventHandler):
    FILES_THAT_CANNOT_CHANGE : Final[tuple[str, ...]] = (
        'mappings.json',
        'entities.json',
        'resources.json',
        'ms-export-order.json',
    )

    MT_TILESET_REGEX     : Final = re.compile(r'^metatiles/(\w+)(\.tsx|-tiles\.png|palette\.png)$')
    MS_SPRITESHEET_REGEX : Final = re.compile(r'^metasprites/(\w+)/')


    def __init__(self, data_store : DataStore, compiler : Compiler):
        super().__init__()

        self.data_store : Final = data_store
        self.compiler   : Final = compiler

        # Set by `FsEventHandler` if a FILES_THAT_CANNOT_CHANGE is modified
        self.stop_token : Final = threading.Event()


    def on_closed(self, event : watchdog.events.FileSystemEvent) -> None:
        if event.is_directory is False:
            self.process_file(event.src_path)


    def on_deleted(self, event : watchdog.events.FileSystemEvent) -> None:
        if event.is_directory is False:
            self.process_file(event.src_path)


    def on_moved(self, event : watchdog.events.FileSystemMovedEvent) -> None:
        if event.is_directory is False:
            self.process_file(event.dest_path)


    def process_file(self, src_path : str) -> None:
        # ::TODO do something about these hard coded filenames::

        filename : Final = src_path.removeprefix('./')
        ext = os.path.splitext(filename)[1]

        log(f"File Changed: { filename }")

        if filename in self.FILES_THAT_CANNOT_CHANGE:
            log(f"STOP: { filename } changed, restart required")
            self.stop_token.set()
            return


        compiler : Final = self.compiler
        mappings : Final = compiler.mappings


        if ext == '.aseprite':
            self.process_aseprite_file_changed(filename)
            return

        elif ext == '.tmx':
            self.process_room(filename)
            return

        elif m := self.MT_TILESET_REGEX.match(filename):
            self.process_resource('mt_tileset', compiler.compile_mt_tileset, mappings.mt_tilesets, m.group(1))
            return

        elif m := self.MS_SPRITESHEET_REGEX.match(filename):
            self.process_resource('ms_spritesheet', compiler.compile_ms_spritesheet, mappings.ms_spritesheets, m.group(1))
            self.process_msfs_and_entity_data()
            return

        elif (tile_name := self._search_through_tiles(filename)) is not None:
            self.process_resource('tile', compiler.compile_tiles, mappings.tiles, tile_name)
            return


    def _search_through_tiles(self, filename : Filename) -> Optional[str]:
        for name, t in self.compiler.resources.tiles.items():
            if filename == t.source:
                return name
        return None


    def process_aseprite_file_changed(self, filename : str) -> None:
        # ASSUMES: current directory is the resources directory
        png_filename = os.path.splitext(filename)[0] + '.png'
        log(f"    make { png_filename }")
        subprocess.call(('make', png_filename ))


    def process_resource(self, rtype : str, func : Callable[[int], CompilerOutput], name_list : list[Name], name : str) -> None:
        try:
            i = name_list.index(name)
        except ValueError:
            log("    Cannot find resource id for {rtype}: { name }")
            return

        log(f"    Compiling { rtype }: { name }")
        co = func(i)
        if co.error:
            log(f"    ERROR: { rtype } { name }: { co.error }")
        self.data_store.insert_data(co)


    def process_room(self, filename : str) -> None:
        basename = os.path.basename(filename)

        log(f"    Compiling room: { basename }")
        co = self.compiler.compile_room(basename)
        if co.error:
            log(f"    ERROR: room { basename }: { co.error }")
        self.data_store.insert_data(co)


    def process_msfs_and_entity_data(self) -> None:
        log(f"    Compiling MsFs and Entity Data")
        me_data = self.compiler.compile_msfs_and_entity_data(self.data_store.get_msfs_lists())
        if me_data.error:
            log(f"    ERROR: MsFs and entity_rom_data: { me_data.error }")
        self.data_store.insert_msfs_and_entity_data(me_data)



# ASSUMES: current working directory is the resources directory
def start_filesystem_watcher(data_store : DataStore, compiler : Compiler) -> tuple[watchdog.observers.Observer, threading.Event]:

    handler = FsEventHandler(data_store, compiler)

    observer = watchdog.observers.Observer()
    observer.schedule(handler, path='.', recursive=True)
    observer.start()

    return observer, handler.stop_token



# ASSUMES: current working directory is the resources directory
def compile_all_resources(data_store : DataStore, compiler : Compiler, n_processes : Optional[int]) -> None:
    # Uses multiprocessing to speed up the compiling

    mappings = compiler.mappings

    room_filenames = get_list_of_tmx_files('rooms')

    with multiprocessing.Pool(processes=n_processes) as mp:
        co_lists = (
            mp.imap_unordered(compiler.compile_mt_tileset,     range(len(mappings.mt_tilesets))),
            mp.imap_unordered(compiler.compile_ms_spritesheet, range(len(mappings.ms_spritesheets))),
            mp.imap_unordered(compiler.compile_tiles,          range(len(mappings.tiles))),
            mp.imap_unordered(compiler.compile_room,           room_filenames),
        )

        for co_l in co_lists:
            for co in co_l:
                data_store.insert_data(co)

                if co.error:
                    # ::TODO find a better way to handle the errors::
                    log(f"ERROR: { co.error }")


    msfs_and_entity_data = compiler.compile_msfs_and_entity_data(data_store.get_msfs_lists())
    data_store.insert_msfs_and_entity_data(msfs_and_entity_data)
    if msfs_and_entity_data.error:
        # ::TODO find a better way to handle the errors::
        log(f"ERROR: { msfs_and_entity_data.error }")



# ==================
# Usb2Snes Data Link
# ==================


# `Usb2Snes` class is based on `usb2snes-uploader` by undisbeliever
# https://github.com/undisbeliever/usb2snes-uploader
#
# usb2snes-uploader: Copyright (c) 2020, Marcus Rowe <undisbeliever@gmail.com>
#                    Distributed under the MIT License (MIT)
class Usb2Snes:
    BLOCK_SIZE : Final[int] = 1024

    USB2SNES_WRAM_OFFSET : Final[int] = 0xF50000
    USB2SNES_SRAM_OFFSET : Final[int] = 0xE00000


    def __init__(self, socket : websockets.client.WebSocketClientProtocol) -> None:
        self._socket : Final[websockets.client.WebSocketClientProtocol] = socket
        self._device : Optional[str] = None


    def device_name(self) -> Optional[str]:
        return self._device


    def _assert_attached(self) -> None:
        if self._socket is None or not self._socket.open or self._socket.closed:
            raise RuntimeError("Socket is closed")

        if self._device is None:
            raise RuntimeError("Not attached to device")


    async def _request(self, opcode : str, *operands : str) -> None:
        self._assert_attached()

        await self._socket.send(json.dumps({
                'Opcode': opcode,
                'Space': "SNES",
                'Flags': None,
                "Operands": operands,
        }))


    async def _request_not_attached(self, opcode : str, *operands : str) -> None:
        if self._socket is None or not self._socket.open or self._socket.closed:
            raise RuntimeError("Socket is closed")

        await self._socket.send(json.dumps({
                'Opcode': opcode,
                'Space': "SNES",
                'Flags': None,
                "Operands": operands,
        }))


    async def _response(self) -> list[str]:
        r = json.loads(await self._socket.recv())
        r = r['Results']

        if not isinstance(r, list):
            raise TypeError("Invalid response type, expected a list of strings.")

        if not all(isinstance(i, str) for i in r):
            raise TypeError("Invalid response type, expected a list of strings.")

        return r


    async def _request_response(self, opcode : str, *operands : str) -> list[str]:
        await self._request(opcode, *operands)
        return await self._response()


    async def find_and_attach_device(self) -> bool:
        """
        Look through the DeviceList and connect to the first SD2SNES reported.
        """

        await self._request_not_attached('DeviceList')
        device_list = await self._response()

        device = None
        for d in device_list:
            if 'SD2SNES' in d.upper():
                device = d
                break

        if device is None:
            return False

        await self._request_not_attached("Attach", device)

        self._device = device

        return True


    async def get_playing_filename(self) -> str:
        r = await self._request_response('Info')
        return r[2]


    async def get_playing_basename(self) -> str:
        return posixpath.basename(await self.get_playing_filename())


    async def read_offset(self, offset : int, size : int) -> bytes:
        if size < 0:
            raise ValueError('Invalid size')

        await self._request('GetAddress', hex(offset), hex(size))

        if size <= self.BLOCK_SIZE:
            out = await self._socket.recv()

            if not isinstance(out, bytes):
                raise RuntimeError(f"Unknown response from QUsb2Snes, expected bytes got { type(out) }")
        else:
            out = bytes()

            while len(out) < size:
                o = await self._socket.recv()
                if not isinstance(o, bytes):
                    raise RuntimeError(f"Unknown response from QUsb2Snes, expected bytes got { type(out) }")
                out += o

        if len(out) != size:
            raise RuntimeError(f"Size mismatch: got { len(out) } bytes, expected { size }")

        return out


    async def write_to_offset(self, offset : int, data : bytes) -> None:
        if not isinstance(data, bytes) and not isinstance(data, bytearray):
            raise ValueError(f"Expected bytes data, got { type(data) }")

        if offset >= self.USB2SNES_WRAM_OFFSET and offset < self.USB2SNES_SRAM_OFFSET:
            raise ValueError(f"Cannot write to Work-RAM")

        size : Final[int] = len(data)

        if size == 0:
            return

        await self._request('PutAddress', hex(offset), hex(size))

        for chunk_start in range(0, size, self.BLOCK_SIZE):
            chunk_end = min(chunk_start + self.BLOCK_SIZE, size)

            await self._socket.send(data[chunk_start : chunk_end])


    async def read_wram_addr(self, addr : int, size : int) -> bytes:
        wram_bank = addr >> 16

        if wram_bank == 0x7e or wram_bank == 0x7f:
            return await self.read_offset((addr & 0x01ffff) | self.USB2SNES_WRAM_OFFSET, size)
        elif wram_bank & 0x7f < 0x40:
            if addr & 0xffff >= 0x2000:
                return await self.read_offset((addr & 0x1fff) | self.USB2SNES_WRAM_OFFSET, size)

        raise ValueError(f"addr is not a Work-RAM address")



# =======================
# Resources Over Usb2Snes
# =======================


class Request(NamedTuple):
    request_id   : int
    request_type : Union[ResourceType, SpecialRequestType]
    resource_id  : int


# Must match `INIT_REQUEST` in `src/resources-over-usb2snes.wiz`
INIT_REQUEST : Final = Request(0, SpecialRequestType.init, SpecialRequestType.init ^ 0xff)


# Must match `ResponseStatus` enum in `src/resources-over-usb2snes.wiz`
@unique
class ResponseStatus(IntEnum):
    NOT_CONNECTED        = 0
    OK                   = 0x20
    OK_RESOURCES_CHANGED = 0x21 # Only `room` requests can return this response.
    INIT_OK              = 0xbb # Only `init` requests can return this response.
    NOT_FOUND            = 0x40
    ERROR                = 0xff

RESPONSE_SIZE : Final = 4


def address_at_bank_offset(memory_map : MemoryMap, bank_offset : int) -> int:
    return ((memory_map.first_resource_bank + bank_offset) << 16) | memory_map.mode.bank_start



class ResourcesOverUsb2Snes:
    # If a request is one of these types, then update msfs_and_entity_data before transferring request data.
    REQUEST_TYPE_USES_MSFS_OR_ENTITY_DATA : Final = (
        SpecialRequestType.rooms.value,
        ResourceType.ms_spritesheets.value
    )


    def __init__(self, usb2snes : Usb2Snes, stop_token : threading.Event, data_store : DataStore, memory_map : MemoryMap, n_entities : int, symbols : dict[str, int]) -> None:
        address_to_rom_offset  : Final = memory_map.mode.address_to_rom_offset

        self.usb2snes   : Final[Usb2Snes] = usb2snes
        self.stop_token : Final[threading.Event] = stop_token
        self.data_store : Final[DataStore] = data_store

        self.address_to_rom_offset  : Final[Callable[[int], int]] = address_to_rom_offset

        self.request_addr           : Final[int] = symbols['resources_over_usb2snes.request']
        self.response_offset        : Final[int] = address_to_rom_offset(symbols['resources_over_usb2snes.response'])
        self.urou2s_offset          : Final[int] = address_to_rom_offset(symbols[USE_RESOURCES_OVER_USB2SNES_LABEL])
        self.entity_rom_data_offset : Final[int] = address_to_rom_offset(symbols[ENTITY_ROM_DATA_LABEL])
        self.response_data_offset   : Final[int] = address_to_rom_offset(address_at_bank_offset(memory_map, USB2SNES_DATA_BANK_OFFSET))
        self.msfs_data_offset       : Final[int] = address_to_rom_offset(address_at_bank_offset(memory_map, MS_FS_DATA_BANK_OFFSET))

        self.expected_entity_rom_data_size : Final[int] = n_entities * ENTITY_ROM_DATA_BYTES_PER_ENTITY

        self.max_data_size          : Final[int] = min(memory_map.mode.bank_size, 0xffff)

        self.not_room_counter   : int = data_store.get_not_room_counter()


    async def validate_correct_rom(self, sfc_file_basename : Filename) -> None:
        playing_basename : Final[str] = await self.usb2snes.get_playing_basename()
        if playing_basename != sfc_file_basename:
            raise RuntimeError(f"{ self.usb2snes.device_name() } is not running { sfc_file_basename } (currently playing { playing_basename })")

        urou2s_data = await self.usb2snes.read_offset(self.urou2s_offset, 1)
        if urou2s_data != b'\xff':
            raise ValueError(f"{ self.usb2snes.device_name() } is not running the build without resources")

        # ::TODO validate game title::


    async def validate_game_matches_sfc_file(self, sfc_file_data : bytes, memory_map : MemoryMap) -> None:
        # Assumes `sfc_file_data` passes `validate_sfc_file()` tests

        n_bytes_to_test : Final = (memory_map.first_resource_bank & 0x3f) * memory_map.mode.bank_size
        assert len(sfc_file_data) >= n_bytes_to_test

        usb2snes_data = bytearray(await self.usb2snes.read_offset(0, n_bytes_to_test))

        # Ignore any changes to the `Response` byte
        p1 = self.response_offset
        p2 = self.response_offset + RESPONSE_SIZE
        assert p2 < n_bytes_to_test
        usb2snes_data[p1:p2] = sfc_file_data[p1:p2]

        # Ignore any changes to `entity_rom_data`
        p1 = self.entity_rom_data_offset
        p2 = self.entity_rom_data_offset + self.expected_entity_rom_data_size
        assert p2 < n_bytes_to_test
        usb2snes_data[p1:p2] = sfc_file_data[p1:p2]

        if usb2snes_data != sfc_file_data[:n_bytes_to_test]:
            raise RuntimeError(f"{ self.usb2snes.device_name() } does not match sfc file")


    async def read_request(self) -> Request:
        rb = await self.usb2snes.read_wram_addr(self.request_addr, 3)

        r_type_id = rb[1]

        rt : Union[ResourceType, SpecialRequestType]
        if r_type_id < N_RESOURCE_TYPES:
            rt = ResourceType(r_type_id)
        else:
            rt = SpecialRequestType(r_type_id)

        return Request(rb[0], rt, rb[2])


    # NOTE: This method will sleep until the resource data is valid
    async def process_request(self, request : Request) -> None:
        assert request.request_type != SpecialRequestType.init

        if request.request_id == 0:
            return

        log(f"Request 0x{request.request_id:02x}: { request.request_type.name }[{ request.resource_id }]")

        try:
            if request.request_type in self.REQUEST_TYPE_USES_MSFS_OR_ENTITY_DATA:
                if not self.data_store.is_msfs_and_entity_data_valid():
                    await self.transmit_msfs_and_entity_data()

            co = self.data_store.get_resource_data(request.request_type, request.resource_id)

            if co is None or (not co.data):
                if co:
                    log(f"    ERROR: { request.request_type.name }[{ request.resource_id }]: { co.error }")

                # Do not wait if request_type is room and resource does not exist.
                if co is not None or request.request_type != SpecialRequestType.rooms:
                    log(f"    Waiting until resource data is ready...")
                    while co is None or (not co.data):
                        # Exit early if stop_token set
                        if self.stop_token.is_set():
                            log("    Stop waiting")
                            break
                        co = self.data_store.get_resource_data(request.request_type, request.resource_id)
                        await asyncio.sleep(WAITING_FOR_RESOURCE_DELAY)

            status = ResponseStatus.ERROR

            if co is None:
                status = ResponseStatus.NOT_FOUND
            else:
                status = ResponseStatus.OK

                if co.data_type == DataType.ROOM:
                    nrc = self.data_store.get_not_room_counter()
                    if nrc != self.not_room_counter:
                        self.not_room_counter = nrc
                        status = ResponseStatus.OK_RESOURCES_CHANGED

            await self.write_response(request.request_id, status,
                                      co.data if co else None)

        except Exception as e:
            await self.write_response(request.request_id, ResponseStatus.ERROR, None)
            raise


    # NOTE: This method will sleep until the resource data is valid
    async def transmit_msfs_and_entity_data(self) -> None:
        me = self.data_store.get_msfs_and_entity_data()

        if me is None or (not me.msfs_data) or (not me.entity_rom_data):
            if me is None:
                log(f"    Cannot access MsFsData or Entity ROM Data")
            elif not me.msfs_data:
                log(f"    Cannot access MsFsData: { me.error }")
            elif not me.entity_rom_data:
                log(f"    Cannot access entity_rom_data: { me.error }")

            log(f"    Waiting until data is ready...")

            while me is None or (not me.msfs_data) or (not me.entity_rom_data):
                # Exit early if stop_token set
                if self.stop_token.is_set():
                    log("    Stop waiting")
                    return
                me = self.data_store.get_msfs_and_entity_data()
                await asyncio.sleep(WAITING_FOR_RESOURCE_DELAY)

        assert len(me.entity_rom_data) == self.expected_entity_rom_data_size


        log(f"    MsFsData { len(me.msfs_data) } bytes")
        await self.usb2snes.write_to_offset(self.msfs_data_offset,       me.msfs_data)

        log(f"    entity_rom_data { len(me.entity_rom_data) } bytes")
        await self.usb2snes.write_to_offset(self.entity_rom_data_offset, me.entity_rom_data)

        self.data_store.mark_msfs_and_entity_data_valid()


    async def write_response(self, response_id : int, status : ResponseStatus, data : Optional[bytes]) -> None:
        data_size = len(data) if data else 0
        if data_size > self.max_data_size:
            raise ValueError(f"data is too large: { data_size }")

        log(f"    { status.name } { data_size } bytes")

        if data is not None:
            await self.usb2snes.write_to_offset(self.response_data_offset, data)

        r = bytearray(RESPONSE_SIZE)
        r[0] = response_id
        r[1] = status.value
        r[2] = data_size & 0xff
        r[3] = data_size >> 8

        await self.usb2snes.write_to_offset(self.response_offset, r)


    async def process_init_request(self) -> None:
        try:
            while await self.read_request() != INIT_REQUEST:
                log("Waiting for correctly formatted init request")
                await asyncio.sleep(NORMAL_REQUEST_SLEEP_DELAY)

            log("Init")

            await self.transmit_msfs_and_entity_data()

            await self.write_response(INIT_REQUEST.request_id, ResponseStatus.INIT_OK, None)

        except Exception as e:
            await self.write_response(INIT_REQUEST.request_id, ResponseStatus.ERROR, None)
            raise


    async def run_until_stop_token_set(self) -> None:
        burst_read_counter : int = 0
        current_request_id : int = 0

        while not self.stop_token.is_set():
            request = await self.read_request()

            if request.request_type == SpecialRequestType.init:
                # `SpecialRequestType.init` is a special case.
                # The game has been reset, current_request_id is invalid
                await self.process_init_request()
                current_request_id = INIT_REQUEST.request_id

            elif request.request_id != current_request_id:
                await self.process_request(request)
                current_request_id = request.request_id
                burst_read_counter = BURST_COUNT

            if burst_read_counter > 0:
                burst_read_counter -= 1
                await asyncio.sleep(BURST_SLEEP_DELAY)
            else:
                await asyncio.sleep(NORMAL_REQUEST_SLEEP_DELAY)



async def create_and_process_websocket(address : str, stop_token : threading.Event, sfc_file_basename : Filename, sfc_file_data : bytes,
                                       data_store : DataStore, memory_map : MemoryMap, n_entities : int, symbols : dict[str, int]) -> None:

    async with websockets.client.connect(address) as socket:
        usb2snes = Usb2Snes(socket)

        connected : bool = await usb2snes.find_and_attach_device()
        if not connected:
            log(f"Could not connect to usb2snes device.")
            return

        log(f"Connected to { usb2snes.device_name() }")

        rou2s = ResourcesOverUsb2Snes(usb2snes, stop_token, data_store, memory_map, n_entities, symbols)

        # This sleep statement is required.
        # On my system there needs to be a 1/2 second delay between a usb2snes "Boot" command and a "GetAddress" command.
        #
        # ::TODO find a way to eliminate this::
        await asyncio.sleep(0.5)

        await rou2s.validate_correct_rom(sfc_file_basename)
        await rou2s.validate_game_matches_sfc_file(sfc_file_data, memory_map)
        log(f"Confirmed device is running { sfc_file_basename }")

        await rou2s.run_until_stop_token_set()



def resources_over_usb2snes(sfc_file_relpath : Filename, websocket_address : str, n_processes : Optional[int]) -> None:

    sym_file_relpath = os.path.splitext(sfc_file_relpath)[0] + '.sym'
    sfc_file_basename = os.path.basename(sfc_file_relpath)

    compiler = Compiler(sym_file_relpath)

    sfc_file_data = read_binary_file(sfc_file_relpath, 512 * 1024)
    validate_sfc_file(sfc_file_data, compiler.symbols, compiler.mappings)


    data_store = DataStore(compiler.mappings)

    # ::TODO compile resources while creating usb2snes connection::
    compile_all_resources(data_store, compiler, n_processes)

    fs_watcher, stop_token = start_filesystem_watcher(data_store, compiler)

    asyncio.run(create_and_process_websocket(websocket_address, stop_token, sfc_file_basename, sfc_file_data, data_store, compiler.mappings.memory_map, len(compiler.entities.entities), compiler.symbols))

    fs_watcher.stop()
    fs_watcher.join()




# =============================
# main
# =============================


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument('-a', '--address', required=False,
                        default='ws://localhost:8080',
                        help='Websocket address')
    parser.add_argument('-j', '--processes', required=False,
                        type=int, default=None,
                        help='Number of processors to use (default = all)')
    parser.add_argument('resources_directory', action='store',
                        help='resources directory')
    parser.add_argument('sfc_file', action='store',
                        help='sfc file (without resources)')

    args = parser.parse_args()

    return args



def main() -> None:
    args = parse_arguments()

    sfc_file_relpath = os.path.relpath(args.sfc_file, args.resources_directory)

    os.chdir(args.resources_directory)

    resources_over_usb2snes(sfc_file_relpath, args.address, args.processes)


if __name__ == '__main__':
    main()

