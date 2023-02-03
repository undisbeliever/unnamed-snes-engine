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
import json
import os.path
import secrets
import argparse
import posixpath
import threading
import contextlib
import subprocess
import multiprocessing
from enum import IntEnum, unique

import websocket  # type: ignore[import]

import watchdog.events
import watchdog.observers

from typing import cast, Any, Callable, Final, NamedTuple, Optional, Union

from .ansi_color import AnsiColors
from .entity_data import ENTITY_ROM_DATA_LABEL, ENTITY_ROM_DATA_BYTES_PER_ENTITY
from .insert_resources import read_binary_file, validate_sfc_file, ROM_HEADER_V3_ADDR
from .resources_compiler import DataStore, Compilers, SharedInput, SharedInputType, ResourceData, ResourceError, MetaSpriteResourceData
from .resources_compiler import load_shared_inputs, check_shared_input_file_changed
from .resources_compiler import compile_all_resources, compile_resource_lists, compile_msfs_and_entity_data
from .json_formats import Name, Filename, MemoryMap

from .common import MultilineError, ResourceType, MS_FS_DATA_BANK_OFFSET, USB2SNES_DATA_BANK_OFFSET, USE_RESOURCES_OVER_USB2SNES_LABEL
from .common import print_error as __print_error


# Sleep delay when waiting for the device to run the correct ROM
INCORRECT_ROM_SLEEP_DELAY: Final[float] = 3.0

# Sleep delay when waiting on a resource
# (seconds)
WAITING_FOR_RESOURCE_DELAY: Final[float] = 0.5

# Sleep delay when there is no request to process
# (seconds)
NORMAL_REQUEST_SLEEP_DELAY: Final[float] = 1 / 10

# Sleep delay after processing a request.
# Used to prevent lag when the game sends multiple requests.
# (seconds)
BURST_SLEEP_DELAY: Final[float] = 1 / 100

# Sleep delay when waiting for `update_requested_spinloop` signal
UPDATE_ROM_SPINLOOP_SLEEP_DELAY: Final[float] = 1 / 60


# Number of times to sleep for `BURST_SLEEP_DELAY`, before returning to `NORMAL_REQUEST_SLEEP_DELAY`
BURST_COUNT: Final[int] = 5


N_RESOURCE_TYPES: Final[int] = len(ResourceType)


# MUST match `RomUpdateRequired` address in `src/resources-over-usb2snes.wiz`
# (+6 is maker code & game code)
ROM_UPDATE_REQUIRED_ADDR = ROM_HEADER_V3_ADDR + 6 + 3


# Must match `SpecialRequestType` in `src/resources-over-usb2snes.wiz`
class SpecialRequestType(IntEnum):
    rooms = 0xFF
    init = 0xAA


# =======
# Logging
# =======

# Disable the `print` function
print: Final = None

print_error: Final = None


__log_lock: Final = threading.Lock()


# Thread safe printing
def __log(s: str, c: str) -> None:
    with __log_lock:
        sys.stdout.write(c)
        sys.stdout.write(s)
        sys.stdout.write(AnsiColors.RESET + "\n")


# Thread safe printing
def log_error(s: str, e: Optional[Exception] = None) -> None:
    with __log_lock:
        __print_error(s, e, sys.stdout)


def log_resource_error(re: ResourceError) -> None:
    log_error(f"ERROR: { re.resource_type }[{ re.resource_id}] { re.resource_name }", re.error)


def log_fs_watcher(s: str) -> None:
    __log(s, AnsiColors.BRIGHT_BLUE)


def log_request(s: str) -> None:
    __log(s, AnsiColors.BRIGHT_MAGENTA)


def log_response(s: str) -> None:
    __log(s, AnsiColors.BRIGHT_YELLOW)


def log_notice(s: str) -> None:
    __log(s, AnsiColors.BRIGHT_CYAN)


def log_success(s: str) -> None:
    __log(s, AnsiColors.GREEN)


#
# Signals
# =======


class StopTokenEncountered(Exception):
    pass


class SfcFileChanged(Exception):
    pass


# Synchronisation signals/events for FsEventHandler and ResourcesOverUsb2Snes.
#
# No idea if this is thread safe or not
class FsWatcherSignals:
    def __init__(self) -> None:
        # MUST NOT be modified outside this class
        self._resource_changed_event: Final = threading.Event()
        self._stop_token: Final = threading.Event()
        self._continue_token: Final = threading.Event()
        self._sym_file_changed_event: Final = threading.Event()

    # FsEventHandler methods

    def resource_changed(self) -> None:
        self._resource_changed_event.set()

    def set_stop_token(self) -> None:
        self._stop_token.set()
        self._resource_changed_event.set()

    def is_stopped(self) -> bool:
        return self._stop_token.is_set()

    def set_continue_token(self) -> None:
        self._continue_token.set()
        self._stop_token.set()
        self._resource_changed_event.set()

    def sfc_file_changed(self) -> None:
        # Reset token is only checked when waiting for a continue token
        self._sym_file_changed_event.set()
        self._stop_token.set()
        self._resource_changed_event.set()

    # ResourcesOverUsb2Snes methods

    def sleep(self, delay: float) -> None:
        s = self._stop_token.wait(delay)
        if s:
            raise StopTokenEncountered()

    def wait_until_resource_changed(self) -> None:
        self._resource_changed_event.wait()
        self._resource_changed_event.clear()
        if self._stop_token.is_set():
            raise StopTokenEncountered()

    def wait_for_continue_token(self) -> None:
        self._continue_token.wait()
        self._continue_token.clear()
        self._stop_token.clear()
        if self._sym_file_changed_event.is_set():
            raise SfcFileChanged()

    def wait_until_sfc_binary_changed(self) -> None:
        self._sym_file_changed_event.wait()
        self._sym_file_changed_event.clear()
        self._continue_token.clear()
        self._stop_token.clear()


#
# Filesystem watcher
# ==================


# ASSUMES: current working directory is the resources directory
class FsEventHandler(watchdog.events.FileSystemEventHandler):
    def __init__(self, signals: FsWatcherSignals, data_store: DataStore, compilers: Compilers, n_processes: Optional[int]):
        super().__init__()

        self.signals: Final = signals
        self.data_store: Final = data_store
        self.compilers: Final = compilers
        self.n_processes: Final = n_processes

        self.shared_files_with_errors: Final[set[Filename]] = set()
        self.lists_to_recompile: Final[set[Optional[ResourceType]]] = set()

        self.rebuild_required: bool = False

    def on_closed(self, event: watchdog.events.FileSystemEvent) -> None:
        if event.is_directory is False:
            self.process_file(event.src_path)

    def on_deleted(self, event: watchdog.events.FileSystemEvent) -> None:
        if event.is_directory is False:
            self.process_file(event.src_path)

    def on_moved(self, event: watchdog.events.FileSystemMovedEvent) -> None:
        if event.is_directory is False:
            self.process_file(event.dest_path)

    def process_file(self, src_path: str) -> None:
        # ::TODO do something about these hard coded filenames::

        filename: Final = src_path.removeprefix("./")
        ext = os.path.splitext(filename)[1]

        log_fs_watcher(f"File Changed: { filename }")

        if ext == ".aseprite":
            self.process_aseprite_file_changed(filename)
            return

        shared_file_changed = self.process_shared_input_file_changed(filename)

        file_is_resource = shared_file_changed

        if not shared_file_changed:
            if not self.shared_files_with_errors:
                rdata = self.compilers.file_changed(filename)
                if rdata:
                    file_is_resource = True
                    self.data_store.insert_data(rdata)
                    if isinstance(rdata, MetaSpriteResourceData):
                        self.process_msfs_and_entity_data()
                    if isinstance(rdata, ResourceError):
                        log_resource_error(rdata)
                    else:
                        log_fs_watcher(f"    Compiled { rdata.resource_type }[{ rdata.resource_id }]: { rdata.resource_name }")
            else:
                log_fs_watcher(f"COMPILER PAUSED: Waiting for {self.shared_files_with_errors}")

        if self.rebuild_required:
            log_fs_watcher(f"REBUILD REQUIRED")

        if file_is_resource:
            self.signals.resource_changed()

    def process_aseprite_file_changed(self, filename: str) -> None:
        # ASSUMES: current directory is the resources directory
        png_filename = os.path.splitext(filename)[0] + ".png"
        log_fs_watcher(f"    make { png_filename }")
        subprocess.call(("make", png_filename))

    def process_shared_input_file_changed(self, filename: str) -> bool:
        had_shared_error: Final = bool(self.shared_files_with_errors)

        try:
            s_type: Final = check_shared_input_file_changed(filename, self.compilers.shared_input)
        except Exception as e:
            log_error(f"ERROR loading {filename}", e)
            self.shared_files_with_errors.add(filename)
            self.signals.set_stop_token()
            return True

        if s_type is None:
            return False

        self.lists_to_recompile.update(self.compilers.shared_input_changed(s_type))

        self.shared_files_with_errors.discard(filename)

        if not self.shared_files_with_errors:
            # No errors in shared files, we can now recompile data
            for rt in self.lists_to_recompile:
                if rt:
                    log_fs_watcher(f"    Compiling all {rt.name}")
                else:
                    log_fs_watcher(f"    Compiling all rooms")

            compile_resource_lists(self.lists_to_recompile, self.data_store, self.compilers, self.n_processes, log_resource_error)
            self.lists_to_recompile.clear()

        if s_type == SharedInputType.SYMBOLS:
            # Assumes the `.sfc` file changes when the symbol file changes
            self.rebuild_required = False
            self.signals.sfc_file_changed()

        elif s_type.rebuild_required():
            self.rebuild_required = True
            self.signals.set_stop_token()

        if self.signals.is_stopped():
            # Set continue token if there are no shared files with errors
            if not self.rebuild_required and not self.shared_files_with_errors:
                self.signals.set_continue_token()

        return True

    def process_msfs_and_entity_data(self) -> None:
        log_fs_watcher(f"    Compiling MsFs and Entity Data")
        me_data = compile_msfs_and_entity_data(self.compilers.shared_input, self.data_store.get_msfs_lists())
        if me_data.error:
            log_error(f"    ERROR: MsFs and entity_rom_data", me_data.error)
        self.data_store.insert_msfs_and_entity_data(me_data)


# ==================
# Usb2Snes Data Link
# ==================


# `Usb2Snes` class is based on `usb2snes-uploader` by undisbeliever
# https://github.com/undisbeliever/usb2snes-uploader
#
# usb2snes-uploader: Copyright (c) 2020, Marcus Rowe <undisbeliever@gmail.com>
#                    Distributed under the MIT License (MIT)
class Usb2Snes:
    BLOCK_SIZE: Final[int] = 1024

    USB2SNES_WRAM_OFFSET: Final[int] = 0xF50000
    USB2SNES_SRAM_OFFSET: Final[int] = 0xE00000

    def __init__(self, socket: websocket.WebSocket) -> None:
        self._socket: Final = socket
        self._device: Optional[str] = None

    def device_name(self) -> Optional[str]:
        return self._device

    def _assert_attached(self) -> None:
        if self._socket is None or self._socket.status is None:
            raise RuntimeError("Socket is closed")

        if self._device is None:
            raise RuntimeError("Not attached to device")

    def _request(self, opcode: str, *operands: str) -> None:
        self._assert_attached()
        self._socket.send(
            json.dumps(
                {
                    "Opcode": opcode,
                    "Space": "SNES",
                    "Flags": None,
                    "Operands": operands,
                }
            )
        )

    def _request_not_attached(self, opcode: str, *operands: str) -> None:
        if self._socket is None or self._socket.status is None:
            raise RuntimeError("Socket is closed")

        self._socket.send(
            json.dumps(
                {
                    "Opcode": opcode,
                    "Space": "SNES",
                    "Flags": None,
                    "Operands": operands,
                }
            )
        )

    def _response(self) -> list[str]:
        r = json.loads(self._socket.recv())
        r = r["Results"]

        if not isinstance(r, list):
            raise TypeError("Invalid response type, expected a list of strings.")

        if not all(isinstance(i, str) for i in r):
            raise TypeError("Invalid response type, expected a list of strings.")

        return r

    def _request_response(self, opcode: str, *operands: str) -> list[str]:
        self._request(opcode, *operands)
        return self._response()

    def find_and_attach_device(self) -> bool:
        """
        Look through the DeviceList and connect to the first SD2SNES reported.
        """

        self._request_not_attached("DeviceList")
        device_list = self._response()

        device = None
        for d in device_list:
            if "SD2SNES" in d.upper():
                device = d
                break

        if device is None:
            return False

        self._request_not_attached("Attach", device)

        self._device = device

        return True

    def get_playing_filename(self) -> str:
        r = self._request_response("Info")
        return r[2]

    def get_playing_basename(self) -> str:
        return posixpath.basename(self.get_playing_filename())

    def send_reset_command(self) -> None:
        # Reset command does not return a response
        self._request("Reset")

    def read_offset(self, offset: int, size: int) -> bytes:
        if size < 0:
            raise ValueError("Invalid size")

        self._request("GetAddress", hex(offset), hex(size))

        out = bytes()

        # This loop is required.
        # On my system, Work-RAM addresses are sent in 128 byte blocks.
        while len(out) < size:
            o = self._socket.recv()
            if not isinstance(o, bytes):
                raise RuntimeError(f"Unknown response from QUsb2Snes, expected bytes got { type(out) }")
            out += o

        if len(out) != size:
            raise RuntimeError(f"Size mismatch: got { len(out) } bytes, expected { size }")

        return out

    def write_to_offset(self, offset: int, data: bytes) -> None:
        if not isinstance(data, bytes) and not isinstance(data, bytearray):
            raise ValueError(f"Expected bytes data, got { type(data) }")

        if offset >= self.USB2SNES_WRAM_OFFSET and offset < self.USB2SNES_SRAM_OFFSET:
            raise ValueError(f"Cannot write to Work-RAM")

        size: Final[int] = len(data)

        if size == 0:
            return

        self._request("PutAddress", hex(offset), hex(size))

        for chunk_start in range(0, size, self.BLOCK_SIZE):
            chunk_end = min(chunk_start + self.BLOCK_SIZE, size)

            self._socket.send_binary(data[chunk_start:chunk_end])

    def read_wram_addr(self, addr: int, size: int) -> bytes:
        wram_bank = addr >> 16

        if wram_bank == 0x7E or wram_bank == 0x7F:
            return self.read_offset((addr & 0x01FFFF) | self.USB2SNES_WRAM_OFFSET, size)
        elif wram_bank & 0x7F < 0x40:
            if addr & 0xFFFF >= 0x2000:
                return self.read_offset((addr & 0x1FFF) | self.USB2SNES_WRAM_OFFSET, size)

        raise ValueError(f"addr is not a Work-RAM address")


# =======================
# Resources Over Usb2Snes
# =======================


class Request(NamedTuple):
    request_id: int
    request_type: Union[ResourceType, SpecialRequestType]
    resource_id: int


# Must match `INIT_REQUEST` in `src/resources-over-usb2snes.wiz`
INIT_REQUEST: Final = Request(0, SpecialRequestType.init, SpecialRequestType.init ^ 0xFF)


# Must match `ResponseStatus` enum in `src/resources-over-usb2snes.wiz`
@unique
class ResponseStatus(IntEnum):
    NOT_CONNECTED = 0
    OK = 0x20
    OK_RESOURCES_CHANGED = 0x21  # Only `room` requests can return this response.
    INIT_OK = 0xBB  # Only `init` requests can return this response.
    NOT_FOUND = 0x40
    ERROR = 0xFF


RESPONSE_SIZE: Final = 4


def address_at_bank_offset(memory_map: MemoryMap, bank_offset: int) -> int:
    return ((memory_map.first_resource_bank + bank_offset) << 16) | memory_map.mode.bank_start


class ResourcesOverUsb2Snes:
    # If a request is one of these types, then update msfs_and_entity_data before transferring request data.
    REQUEST_TYPE_USES_MSFS_OR_ENTITY_DATA: Final = (SpecialRequestType.rooms.value, ResourceType.ms_spritesheets.value)

    def __init__(self, usb2snes: Usb2Snes, signals: FsWatcherSignals, data_store: DataStore, shared_input: SharedInput) -> None:
        self.usb2snes: Final = usb2snes
        self.data_store: Final = data_store
        self.signals: Final = signals

        symbols = shared_input.symbols
        memory_map = shared_input.mappings.memory_map
        n_entities = len(shared_input.entities.entities)

        address_to_rom_offset: Final = memory_map.mode.address_to_rom_offset

        self.address_to_rom_offset: Final[Callable[[int], int]] = address_to_rom_offset

        self.request_addr: Final[int] = symbols["resources_over_usb2snes.request"]
        self.response_offset: Final[int] = address_to_rom_offset(symbols["resources_over_usb2snes.response"])
        self.urou2s_offset: Final[int] = address_to_rom_offset(symbols[USE_RESOURCES_OVER_USB2SNES_LABEL])
        self.entity_rom_data_offset: Final[int] = address_to_rom_offset(symbols[ENTITY_ROM_DATA_LABEL])
        self.response_data_offset: Final[int] = address_to_rom_offset(address_at_bank_offset(memory_map, USB2SNES_DATA_BANK_OFFSET))
        self.msfs_data_offset: Final[int] = address_to_rom_offset(address_at_bank_offset(memory_map, MS_FS_DATA_BANK_OFFSET))

        self.rom_update_required_offset: Final[int] = address_to_rom_offset(ROM_UPDATE_REQUIRED_ADDR)

        self.expected_entity_rom_data_size: Final[int] = n_entities * ENTITY_ROM_DATA_BYTES_PER_ENTITY

        self.max_data_size: Final[int] = min(memory_map.mode.bank_size, 0xFFFF)

        self.not_room_counter: int = data_store.get_not_room_counter()

    def is_correct_rom_running(self, sfc_file_basename: Filename) -> bool:
        playing_basename: Final[str] = self.usb2snes.get_playing_basename()
        if playing_basename != sfc_file_basename:
            log_error(f"{ self.usb2snes.device_name() } is not running { sfc_file_basename } (currently playing { playing_basename })")
            return False

        urou2s_data = self.usb2snes.read_offset(self.urou2s_offset, 1)
        if urou2s_data != b"\xff":
            log_error(f"{ self.usb2snes.device_name() } is not running the build without resources")
            return False

        return True

    def test_game_matches_sfc_file(self, sfc_file_data: bytes, memory_map: MemoryMap) -> bool:
        # Assumes `sfc_file_data` passes `validate_sfc_file()` tests

        n_bytes_to_test: Final = (memory_map.first_resource_bank & 0x3F) * memory_map.mode.bank_size
        assert len(sfc_file_data) >= n_bytes_to_test

        usb2snes_data = bytearray(self.usb2snes.read_offset(0, n_bytes_to_test))

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

        return usb2snes_data == sfc_file_data[:n_bytes_to_test]

    def reset_and_update_rom(self, rom_data: bytes) -> None:
        # NOTE: This does not modify the file on the SD-card.

        # ASSUMES: rom_data passes `validate_sfc_file()` tests

        if len(rom_data) > 512 * 1024:
            raise ValueError("rom_data is too large")

        # Set rom update requested byte
        self.usb2snes.write_to_offset(self.rom_update_required_offset, bytes([0xFF]))

        log_notice("Reset")
        self.usb2snes.send_reset_command()

        # Wait until the game is executing the rom-update-required spinloop (which resides in Work-RAM)
        log_notice("Waiting for RomUpdateRequiredSpinloop...")
        self.__update_rom_spinloop_test_and_wait(0x42)
        self.__update_rom_spinloop_test_and_wait(0x42 ^ 0xFF)
        self.__update_rom_spinloop_test_and_wait(secrets.randbelow(256))
        self.__update_rom_spinloop_test_and_wait(secrets.randbelow(256))
        self.__update_rom_spinloop_test_and_wait(secrets.randbelow(256))
        self.__update_rom_spinloop_test_and_wait(secrets.randbelow(256))

        # Confirmed game is executing Work-RAM code and the ROM can be safely modified.

        log_notice(f"Updating game...")
        self.usb2snes.write_to_offset(0, rom_data)

        log_notice("Reset")
        self.usb2snes.send_reset_command()

    def __update_rom_spinloop_test_and_wait(self, b: int) -> None:
        """
        Set ROM_UPDATE_REQUIRED_ADDR to `b` and wait until the all bytes in zeropage is `b`.

        Used to confirm the SNES is executing the `RomUpdateRequiredSpinloop`.
        """

        self.usb2snes.write_to_offset(self.rom_update_required_offset, bytes([b]))

        expected_zeropage: Final = bytes([b]) * 256

        zeropage: Optional[bytes] = None

        while zeropage != expected_zeropage:
            self.signals.sleep(UPDATE_ROM_SPINLOOP_SLEEP_DELAY)
            zeropage = self.usb2snes.read_wram_addr(0x7E0000, 256)

    def read_request(self) -> Request:
        rb = self.usb2snes.read_wram_addr(self.request_addr, 3)

        r_type_id = rb[1]

        rt: Union[ResourceType, SpecialRequestType]
        if r_type_id < N_RESOURCE_TYPES:
            rt = ResourceType(r_type_id)
        else:
            rt = SpecialRequestType(r_type_id)

        return Request(rb[0], rt, rb[2])

    # NOTE: This method will sleep until the resource data is valid
    def process_request(self, request: Request) -> None:
        assert request.request_type != SpecialRequestType.init

        if request.request_id == 0:
            return

        log_request(f"Request 0x{request.request_id:02x}: { request.request_type.name }[{ request.resource_id }]")

        try:
            if request.request_type == SpecialRequestType.rooms:
                status, data = self.get_room(request.resource_id)
            else:
                status, data = self.get_resource(ResourceType(request.request_type), request.resource_id)

            if request.request_type in self.REQUEST_TYPE_USES_MSFS_OR_ENTITY_DATA:
                if not self.data_store.is_msfs_and_entity_data_valid():
                    self.transmit_msfs_and_entity_data()

            self.write_response(request.request_id, status, data)

        except Exception as e:
            self.write_response(request.request_id, ResponseStatus.ERROR, None)
            raise

    def get_room(self, room_id: int) -> tuple[ResponseStatus, Optional[bytes]]:
        co = self.data_store.get_room_data(room_id)

        if isinstance(co, ResourceError):
            log_resource_error(co)
            log_notice(f"    Waiting until resource data is ready...")
            while isinstance(co, ResourceError):
                self.signals.wait_until_resource_changed()
                co = self.data_store.get_room_data(room_id)

        if isinstance(co, ResourceData):
            status = ResponseStatus.OK

            nrc = self.data_store.get_not_room_counter()
            if nrc != self.not_room_counter:
                self.not_room_counter = nrc
                status = ResponseStatus.OK_RESOURCES_CHANGED

            return status, co.data
        else:
            # Room does not exist
            return ResponseStatus.NOT_FOUND, None

    def get_resource(self, resource_type: ResourceType, resource_id: int) -> tuple[ResponseStatus, Optional[bytes]]:
        co = self.data_store.get_resource_data(resource_type, resource_id)

        if not isinstance(co, ResourceData):
            if isinstance(co, ResourceError):
                log_resource_error(co)

            log_notice(f"    Waiting until resource data is ready...")

            while not isinstance(co, ResourceData):
                self.signals.wait_until_resource_changed()
                co = self.data_store.get_resource_data(resource_type, resource_id)

        return ResponseStatus.OK, co.data

    # NOTE: This method will sleep until the resource data is valid
    def transmit_msfs_and_entity_data(self) -> None:
        me = self.data_store.get_msfs_and_entity_data()

        if me is None or (not me.msfs_data) or (not me.entity_rom_data):
            if me is None:
                log_error(f"    Cannot access MsFsData or Entity ROM Data")
            elif not me.msfs_data:
                log_error(f"    Cannot access MsFsData", me.error)
            elif not me.entity_rom_data:
                log_error(f"    Cannot access entity_rom_data", me.error)

            log_notice(f"    Waiting until data is ready...")

            while me is None or (not me.msfs_data) or (not me.entity_rom_data):
                self.signals.wait_until_resource_changed()
                me = self.data_store.get_msfs_and_entity_data()

        assert len(me.entity_rom_data) == self.expected_entity_rom_data_size

        log_response(f"    MsFsData { len(me.msfs_data) } bytes")
        self.usb2snes.write_to_offset(self.msfs_data_offset, me.msfs_data)

        log_response(f"    entity_rom_data { len(me.entity_rom_data) } bytes")
        self.usb2snes.write_to_offset(self.entity_rom_data_offset, me.entity_rom_data)

        self.data_store.mark_msfs_and_entity_data_valid()

    def write_response(self, response_id: int, status: ResponseStatus, data: Optional[bytes]) -> None:
        data_size = len(data) if data else 0
        if data_size > self.max_data_size:
            raise ValueError(f"data is too large: { data_size }")

        log_response(f"    { status.name } { data_size } bytes")

        if data is not None:
            self.usb2snes.write_to_offset(self.response_data_offset, data)

        r = bytearray(RESPONSE_SIZE)
        r[0] = data_size & 0xFF
        r[1] = data_size >> 8
        r[2] = status.value
        r[3] = response_id

        self.usb2snes.write_to_offset(self.response_offset, r)

    def process_init_request(self) -> None:
        try:
            while self.read_request() != INIT_REQUEST:
                log_notice("Waiting for correctly formatted init request")
                self.signals.sleep(NORMAL_REQUEST_SLEEP_DELAY)

            log_request("Init")

            self.transmit_msfs_and_entity_data()

            self.write_response(INIT_REQUEST.request_id, ResponseStatus.INIT_OK, None)

        except Exception as e:
            self.write_response(INIT_REQUEST.request_id, ResponseStatus.ERROR, None)
            raise

    def run(self) -> None:
        burst_read_counter: int = 0
        current_request_id: int = 0

        while True:
            request = self.read_request()

            if request.request_type == SpecialRequestType.init:
                # `SpecialRequestType.init` is a special case.
                # The game has been reset, current_request_id is invalid
                self.process_init_request()
                current_request_id = INIT_REQUEST.request_id

            elif request.request_id != current_request_id:
                self.process_request(request)
                current_request_id = request.request_id
                burst_read_counter = BURST_COUNT

            if burst_read_counter > 0:
                burst_read_counter -= 1
                self.signals.sleep(BURST_SLEEP_DELAY)
            else:
                self.signals.sleep(NORMAL_REQUEST_SLEEP_DELAY)


def create_and_process_websocket(
    address: str, sfc_file_relpath: Filename, fs_handler: FsEventHandler, signals: FsWatcherSignals
) -> None:
    sfc_file_basename = os.path.basename(sfc_file_relpath)

    with contextlib.closing(websocket.WebSocket()) as ws:
        ws.connect(address, origin="http://localhost")

        usb2snes = Usb2Snes(ws)

        connected: bool = usb2snes.find_and_attach_device()
        if not connected:
            raise RuntimeError("Could not connect to usb2snes device.")

        log_success(f"Connected to { usb2snes.device_name() }")

        # This sleep statement is required.
        # On my system there needs to be a 1/2 second delay between a usb2snes "Boot" command and a "GetAddress" command.
        #
        # ::TODO find a way to eliminate this::
        signals.sleep(0.5)

        while True:
            shared_input = fs_handler.compilers.shared_input

            try:
                sfc_file_data = read_binary_file(sfc_file_relpath, 512 * 1024)
                validate_sfc_file(sfc_file_data, shared_input.symbols, shared_input.mappings)
                sfc_file_valid = True
            except Exception as e:
                log_error(f"Error validating {sfc_file_basename}", e)
                sfc_file_valid = False

            if sfc_file_valid:
                try:
                    rou2s = ResourcesOverUsb2Snes(usb2snes, signals, fs_handler.data_store, shared_input)

                    while not rou2s.is_correct_rom_running(sfc_file_basename):
                        try:
                            signals.sleep(INCORRECT_ROM_SLEEP_DELAY)
                        except StopTokenEncountered:
                            pass

                    if rou2s.test_game_matches_sfc_file(sfc_file_data, shared_input.mappings.memory_map) is False:
                        log_notice(f"Running game does not match { sfc_file_basename }")
                        rou2s.reset_and_update_rom(sfc_file_data)
                    else:
                        log_success(f"Running game matches { sfc_file_basename }")

                    while True:
                        try:
                            rou2s.run()
                        except StopTokenEncountered:
                            pass
                        signals.wait_for_continue_token()
                except SfcFileChanged:
                    pass

                # Symbols file has changed.
                # It is simpler to recreate `rou2s` then update all of the addresses
                del rou2s
            else:
                log_notice(f"Waiting until {sfc_file_basename} is rebuilt")

                # Wait until the binary has been rebuilt
                signals.wait_until_sfc_binary_changed()


# ASSUMES: current working directory is the resources directory
def resources_over_usb2snes(sfc_file_relpath: Filename, websocket_address: str, n_processes: Optional[int]) -> None:

    sym_file_relpath = os.path.splitext(sfc_file_relpath)[0] + ".sym"

    shared_inputs = load_shared_inputs(sym_file_relpath)

    signals = FsWatcherSignals()

    # ::TODO compile resources while creating usb2snes connection::
    compilers = Compilers(shared_inputs)
    data_store = DataStore(shared_inputs.mappings)
    compile_all_resources(data_store, compilers, n_processes, log_resource_error)

    fs_handler = FsEventHandler(signals, data_store, compilers, n_processes)

    fs_observer = watchdog.observers.Observer()  # type: ignore[no-untyped-call]
    fs_observer.schedule(fs_handler, path=".", recursive=True)  # type: ignore[no-untyped-call]
    fs_observer.schedule(fs_handler, path=sym_file_relpath)  # type: ignore[no-untyped-call]
    fs_observer.start()  # type: ignore[no-untyped-call]

    create_and_process_websocket(websocket_address, sfc_file_relpath, fs_handler, signals)

    fs_observer.stop()  # type: ignore[no-untyped-call]
    fs_observer.join()
