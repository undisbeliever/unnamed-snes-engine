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
from abc import abstractmethod, ABCMeta
from enum import IntEnum, Enum, auto, unique

import websocket  # type: ignore[import]

import watchdog.events
import watchdog.observers

from typing import cast, final, Any, Callable, Final, NamedTuple, Optional, Union

from .ansi_color import AnsiColors
from .entity_data import ENTITY_ROM_DATA_LABEL, ENTITY_ROM_DATA_BYTES_PER_ENTITY
from .insert_resources import read_binary_file, validate_sfc_file, ROM_HEADER_V3_ADDR
from .resources_compiler import DataStore, ProjectCompiler, SharedInputType, ResourceData, ResourceError
from .json_formats import Name, Filename, Mappings, MemoryMap

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
def log_error(s: str, e: Optional[Union[Exception, str]] = None) -> None:
    with __log_lock:
        __print_error(s, e, sys.stdout)


def log_compiler_error(e: Union[ResourceError, Exception, str]) -> None:
    if isinstance(e, ResourceError):
        log_error(f"ERROR: { e.res_string() }", e.error)
    else:
        log_error("ERROR", e)


def log_compiler_message(s: str) -> None:
    __log(s, AnsiColors.BRIGHT_CYAN)


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
# Commands
# ========

MAX_COMMAND_SIZE: Final = 8192

MAX_COMMAND_DATA_SIZE: Final = MAX_COMMAND_SIZE - 4


# Order MUST MATCH Rou2sCommands in `src/rou2s-commands.wiz`
@unique
class Rou2sCommands(Enum):
    # Skipping `null`.  `auto()` starts at 1
    COMMON_AUDIO_DATA_CHANGED = auto()
    UPLOAD_SONG = auto()


class Command(NamedTuple):
    command: Rou2sCommands
    data: bytes


COMMON_AUDIO_DATA_CHANGED_COMMAND: Final = Command(Rou2sCommands.COMMON_AUDIO_DATA_CHANGED, bytes())


#
# Signals
# =======


class RebuildRequiredException(Exception):
    pass


class DisconnectEventException(Exception):
    pass


# Synchronisation signals/events for the fs_watcher, usb2snes and gui threads.
#
# No idea if this is thread safe or not
class FsWatcherSignals(metaclass=ABCMeta):
    def __init__(self) -> None:
        # All fields MUST NOT be modified outside this class
        self._resource_changed_event: Final = threading.Event()
        self._rebuild_required_event: Final = threading.Event()
        self._sym_file_changed_event: Final = threading.Event()

        self._connect_button_event: Final = threading.Event()
        self._disconnect_event: Final = threading.Event()

        self._interrupt_request_sleep_event: Final = threading.Event()

        self._quit_event: Final = threading.Event()

        # lock MUST be used on all non event or queue fields
        self._lock: Final = threading.Lock()

        # MUST access status via `_lock`
        self._fs_watcher_status: str = ""
        self._usb2snes_status: str = ""

        # MUST access _command via `_lock`
        self._command: Optional[Command] = None

    # Status methods
    def set_fs_watcher_status(self, s: str) -> None:
        with self._lock:
            if self._fs_watcher_status != s:
                self._fs_watcher_status = s
                changed = True
            else:
                changed = False

        if changed:
            self.signal_status_changed()

    def set_usb2snes_status(self, s: str) -> None:
        with self._lock:
            if self._usb2snes_status != s:
                self._usb2snes_status = s
                changed = True
            else:
                changed = False

        if changed:
            self.signal_status_changed()

    def get_status(self) -> tuple[str, str]:
        with self._lock:
            return self._fs_watcher_status, self._usb2snes_status

    # Command methods
    def send_command(self, c: Command) -> None:
        with self._lock:
            # ::TODO add a priority system. ::
            # ::: Ensure important commands (like COMMON_AUDIO_DATA_CHANGED) do not get overridden. ::

            self._command = c
            self._interrupt_request_sleep_event.set()

    def pop_command(self) -> Optional[Command]:
        with self._lock:
            c = self._command
            self._command = None
            return c

    # GUI methods
    def is_connected(self) -> bool:
        return not self._disconnect_event.is_set()

    def send_connect_event(self) -> None:
        self._connect_button_event.set()

    def send_disconnect_event(self) -> None:
        self._disconnect_event.set()
        # Interrupt `sleep()` and `wait_until_*()` events
        self._sym_file_changed_event.set()
        self._resource_changed_event.set()

    def send_quit_event(self) -> None:
        self._quit_event.set()
        # Interrupt `sleep()` and `wait_until_*()` events
        self.send_disconnect_event()
        self._connect_button_event.set()

    # FsEventHandler methods

    def resource_changed(self) -> None:
        self.signal_resource_compiled()
        self._resource_changed_event.set()

    def set_rebuild_required_flag(self) -> None:
        self._rebuild_required_event.set()
        # Interrupt `sleep()` and `wait_until_resource_changed()`
        self._resource_changed_event.set()

    def audio_samples_changed(self) -> None:
        self.signal_audio_samples_changed()

    def sfc_file_changed(self) -> None:
        self._interrupt_request_sleep_event.set()
        self._sym_file_changed_event.set()

    def wait_until_quit(self) -> None:
        self._quit_event.wait()

    # ResourcesOverUsb2Snes methods

    def is_quit_event_set(self) -> bool:
        return self._quit_event.is_set()

    def set_disconnected_flag(self) -> None:
        self._disconnect_event.set()
        self._connect_button_event.clear()
        self.signal_ws_connection_changed()

    def clear_disconnected_flag(self) -> None:
        self._disconnect_event.clear()
        self._connect_button_event.clear()
        self.signal_ws_connection_changed()

    def sleep(self, delay: float) -> None:
        s = self._disconnect_event.wait(delay)
        if s:
            raise DisconnectEventException()

    def request_sleep(self, delay: float) -> None:
        s = self._interrupt_request_sleep_event.wait(delay)
        self._interrupt_request_sleep_event.clear()

        if self._disconnect_event.is_set():
            raise DisconnectEventException()
        if self._rebuild_required_event.is_set():
            raise RebuildRequiredException()

    def wait_until_resource_changed(self) -> None:
        self._resource_changed_event.wait()
        self._resource_changed_event.clear()

        if self._disconnect_event.is_set():
            raise DisconnectEventException()
        if self._rebuild_required_event.is_set():
            raise RebuildRequiredException()

    def wait_until_sfc_binary_changed(self) -> None:
        self._sym_file_changed_event.wait()
        self._sym_file_changed_event.clear()
        self._rebuild_required_event.clear()
        if self._disconnect_event.is_set():
            raise DisconnectEventException()

    def wait_for_ws_connect_button(self) -> None:
        self._connect_button_event.wait()
        self._connect_button_event.clear()

    # GUI methods

    @abstractmethod
    def signal_status_changed(self) -> None:
        pass

    @abstractmethod
    def signal_resource_compiled(self) -> None:
        pass

    @abstractmethod
    def signal_audio_samples_changed(self) -> None:
        pass

    @abstractmethod
    def signal_ws_connection_changed(self) -> None:
        pass

    # Called when a BgThread stops
    @abstractmethod
    def signal_bg_thread_stopped(self) -> None:
        pass


#
# BG Threads
# ==========


class BgThread(threading.Thread):
    def __init__(self, signals: FsWatcherSignals, name: str) -> None:
        super().__init__(name=name)
        self.signals: Final = signals

    @final
    def run(self) -> None:
        try:
            self.run_bg_thread()
        finally:
            self.signals.signal_bg_thread_stopped()

    @abstractmethod
    def run_bg_thread(self) -> None:
        pass


#
# Filesystem watcher
# ==================


# ASSUMES: current working directory is the resources directory
class FsEventHandler(watchdog.events.FileSystemEventHandler):
    def __init__(self, signals: FsWatcherSignals, data_store: DataStore, sym_filename: Filename, n_processes: Optional[int]):
        super().__init__()

        self.signals: Final = signals
        self.data_store: Final = data_store
        self._project_compiler: Final = ProjectCompiler(
            data_store, sym_filename, n_processes, log_compiler_error, log_compiler_message
        )

        signals.set_fs_watcher_status("Compiling")

        self._project_compiler.compile_everything()

        signals.set_fs_watcher_status("Running")

        signals.resource_changed()
        self.signals.audio_samples_changed()

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
        filename: Final = src_path.removeprefix("./")
        ext = os.path.splitext(filename)[1]

        log_fs_watcher(f"File Changed: { filename }")

        if ext == ".aseprite":
            self.process_aseprite_file_changed(filename)
            return

        r = self._project_compiler.file_changed(filename)
        if r is not None:
            if isinstance(r, ResourceData):
                # Test if common audio data changed.
                if r.resource_type == ResourceType.songs and r.resource_id == 0:
                    self.signals.send_command(COMMON_AUDIO_DATA_CHANGED_COMMAND)
            elif isinstance(r, SharedInputType):
                if r.rebuild_required():
                    self.rebuild_required = True
                    # Stop ResourcesOverUsb2Snes until the `.sfc` file has been rebuilt
                    self.signals.set_rebuild_required_flag()

            if r == SharedInputType.SYMBOLS:
                # Assumes the `.sfc` file changes when the symbol file changes
                self.rebuild_required = False
                self.signals.set_fs_watcher_status("Running")
                self.signals.sfc_file_changed()

            if r == SharedInputType.AUDIO_SAMPLES:
                self.signals.audio_samples_changed()
                self.signals.send_command(COMMON_AUDIO_DATA_CHANGED_COMMAND)

            if self.rebuild_required:
                self.signals.set_fs_watcher_status("REBUILD REQUIRED")
                log_fs_watcher(f"REBUILD REQUIRED")

            # Signal to ResourcesOverUsb2Snes that a resource has changed
            self.signals.resource_changed()

    def process_aseprite_file_changed(self, filename: str) -> None:
        # ASSUMES: current directory is the resources directory
        png_filename = os.path.splitext(filename)[0] + ".png"
        log_fs_watcher(f"    make { png_filename }")
        subprocess.call(("make", png_filename))


class FsWatcherThread(BgThread):
    def __init__(
        self, data_store: DataStore, signals: FsWatcherSignals, sfc_file_relpath: Filename, n_processes: Optional[int]
    ) -> None:
        super().__init__(signals, name="FS Watcher")

        self.data_store: Final = data_store
        self.sym_file_relpath: Final = os.path.splitext(sfc_file_relpath)[0] + ".sym"
        self.n_processes: Final = n_processes

    @final
    def run_bg_thread(self) -> None:
        try:
            log_fs_watcher("Starting filesystem watcher")
            fs_handler = FsEventHandler(self.signals, self.data_store, self.sym_file_relpath, self.n_processes)

            fs_observer = watchdog.observers.Observer()  # type: ignore[no-untyped-call]
            fs_observer.schedule(fs_handler, path=".", recursive=True)  # type: ignore[no-untyped-call]
            fs_observer.schedule(fs_handler, path=self.sym_file_relpath)  # type: ignore[no-untyped-call]
            fs_observer.start()  # type: ignore[no-untyped-call]

            self.signals.wait_until_quit()

        finally:
            log_fs_watcher("Stopping filesystem watcher")
            self.signals.set_fs_watcher_status("Stopped")

            fs_observer.stop()  # type: ignore[no-untyped-call]
            fs_observer.join()


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
    last_command_id: int


# Must match `INIT_REQUEST` in `src/resources-over-usb2snes.wiz`
INIT_REQUEST: Final = Request(0, SpecialRequestType.init, SpecialRequestType.init ^ 0xFF, 0xFF)


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

    def __init__(self, usb2snes: Usb2Snes, data_store: DataStore, signals: FsWatcherSignals) -> None:
        self.usb2snes: Final = usb2snes
        self.data_store: Final = data_store
        self.signals: Final = signals

        # Previous command_id sent by `ResourcesOverUsb2Snes`.
        # The next command will only be sent if `Request.last_command_id` == previous_command_id
        # (setting it to a value > 0x100 to ensure no commands are sent until the next `sync_command_id()`)
        self.previous_command_id: int = 0x1000

    def update_mappings(self, mappings: Mappings, symbols: dict[Name, int], n_entities: int) -> None:
        memory_map = mappings.memory_map

        self.mappings = mappings

        address_to_rom_offset: Final = memory_map.mode.address_to_rom_offset

        self.address_to_rom_offset: Callable[[int], int] = address_to_rom_offset

        self.request_addr: int = symbols["resources_over_usb2snes.request"]
        self.response_offset: int = address_to_rom_offset(symbols["resources_over_usb2snes.response"])
        self.urou2s_offset: int = address_to_rom_offset(symbols[USE_RESOURCES_OVER_USB2SNES_LABEL])
        self.entity_rom_data_offset: int = address_to_rom_offset(symbols[ENTITY_ROM_DATA_LABEL])
        self.response_data_offset: int = address_to_rom_offset(address_at_bank_offset(memory_map, USB2SNES_DATA_BANK_OFFSET))
        self.msfs_data_offset: int = address_to_rom_offset(address_at_bank_offset(memory_map, MS_FS_DATA_BANK_OFFSET))

        self.rom_update_required_offset: int = address_to_rom_offset(ROM_UPDATE_REQUIRED_ADDR)

        self.expected_entity_rom_data_size: int = n_entities * ENTITY_ROM_DATA_BYTES_PER_ENTITY

        self.max_data_size: int = min(memory_map.mode.bank_size, 0xFFFF)

        assert self.max_data_size > MAX_COMMAND_SIZE * 2

        # Steal `MAX_COMMAND_SIZE` bytes from the end of the self.response_data_offset and use it for commands.
        # ::TODO find a proper place to put the command block::
        self.max_data_size -= MAX_COMMAND_SIZE
        self.command_offset: int = self.response_data_offset + self.max_data_size

        self.not_room_counter: int = self.data_store.get_not_room_counter()

    def is_correct_rom_running(self, sfc_file_basename: Filename) -> bool:
        playing_basename: Final[str] = self.usb2snes.get_playing_basename()
        if playing_basename != sfc_file_basename:
            log_error(f"{ self.usb2snes.device_name() } is not running { sfc_file_basename } (currently playing { playing_basename })")
            return False

        return True

    def test_game_matches_sfc_file(self, sfc_file_data: bytes) -> bool:
        # Assumes `sfc_file_data` passes `validate_sfc_file()` tests

        memory_map: Final = self.mappings.memory_map

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

        urou2s_data = self.usb2snes.read_offset(self.urou2s_offset, 1)
        if urou2s_data != b"\xff":
            log_error(f"{ self.usb2snes.device_name() } is not running the build without resources")
            return False

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

    def read_request(self) -> Optional[Request]:
        rb = self.usb2snes.read_wram_addr(self.request_addr, 4)

        r_type_id = rb[1]

        rt: Union[ResourceType, SpecialRequestType]
        if r_type_id < N_RESOURCE_TYPES:
            rt = ResourceType(r_type_id)
        else:
            try:
                rt = SpecialRequestType(r_type_id)
            except ValueError:
                # r_type_id is invalid
                return None

        return Request(rb[0], rt, rb[2], rb[3])

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

            self.signals.set_usb2snes_status("Running")

        except Exception as e:
            self.signals.set_usb2snes_status(f"ERROR: {type(e).__name__}")
            self.write_response(request.request_id, ResponseStatus.ERROR, None)
            raise

    def get_room(self, room_id: int) -> tuple[ResponseStatus, Optional[bytes]]:
        co = self.data_store.get_room_data(room_id)

        if isinstance(co, ResourceError):
            log_compiler_error(co)
            log_notice(f"    Waiting until resource data is ready...")
            self.signals.set_usb2snes_status(f"WAITING for room {room_id}")

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
                log_compiler_error(co)

            log_notice(f"    Waiting until resource data is ready...")
            self.signals.set_usb2snes_status(f"WAITING for {resource_type.name}[{resource_id}]")

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
            self.signals.set_usb2snes_status("WAITING for MsFs and entity data")

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

            # Wait until request_type is 0
            r = self.read_request()
            while r is None or r.request_type != 0:
                self.signals.sleep(BURST_SLEEP_DELAY)
                r = self.read_request()

            self.sync_command_id()

        except Exception as e:
            self.signals.set_usb2snes_status(f"Init ERROR: {type(e).__name__}")
            self.write_response(INIT_REQUEST.request_id, ResponseStatus.ERROR, None)
            raise

    def sync_command_id(self) -> None:
        # When ResourcesOverUsb2Snes starts `command_id` is unknown.
        #
        # We cannot just grab the command_id from `Request` as we have no idea if the console
        # is executing the previous command or not.
        #
        # Instead we send a `null` command (which does nothing) with a new `command_id`, without
        # changing the command data.  As we did not change the command data, the previous command
        # continues without issues.
        #
        # When the console is ready to process new commands it will process the `null` command
        # and update `Request.last_command_id`, signaling the console is ready to receive new
        # commands.

        request = self.read_request()
        if request is None:
            raise ValueError("Invalid request")

        command_id = ((request.last_command_id + 2) & 0x7F) | 0x80
        # Command id cannot be 0xff (console sets last_command_id to 0xff on reset)
        if command_id >= 0xFF:
            command_id = 0x80

        # MUST only change `comamnd_id` and `command`.
        # `data_size` and `data` might still be used by the console.
        b = bytes([command_id, 0])
        self.usb2snes.write_to_offset(self.command_offset, b)
        log_response(f"Reset command_id")

        self.previous_command_id = command_id

    # Assumes previous command has been processed and the console is ready for new commands
    def send_command(self, c: Command) -> None:
        # command_id's >= 0x80 are reserved for `send_null_command()`
        command_id = (self.previous_command_id + 1) & 0x7F
        if command_id == 0:
            command_id = 1

        d_size = len(c.data)
        if d_size < MAX_COMMAND_DATA_SIZE:
            b = bytes([command_id, c.command.value, d_size & 0xFF, d_size >> 8]) + c.data
            assert len(b) < MAX_COMMAND_SIZE

            self.usb2snes.write_to_offset(self.command_offset, b)

            self.previous_command_id = command_id

            m = f"Sent {c.command.name.lower()} command"
            log_response(m)
            self.signals.set_usb2snes_status(m)
        else:
            self.signals.set_usb2snes_status("Cannot send command: command too large")
            log_error("Cannot send command", "command too large")

    def run(self) -> None:
        burst_read_counter: int = 0
        current_request_id: int = 0

        self.sync_command_id()

        while True:
            request = self.read_request()
            if request:
                if request.request_type == SpecialRequestType.init:
                    # `SpecialRequestType.init` is a special case.
                    # The game has been reset, current_request_id is invalid
                    self.process_init_request()
                    current_request_id = INIT_REQUEST.request_id

                else:
                    if request.last_command_id == self.previous_command_id:
                        # Previous command has been processed by the console, a new command can now be sent
                        command = self.signals.pop_command()
                        if command:
                            self.send_command(command)
                            burst_read_counter = BURST_COUNT

                    if request.request_id != current_request_id:
                        self.process_request(request)
                        current_request_id = request.request_id
                        burst_read_counter = BURST_COUNT

            if burst_read_counter > 0:
                burst_read_counter -= 1
                self.signals.request_sleep(BURST_SLEEP_DELAY)
            else:
                self.signals.request_sleep(NORMAL_REQUEST_SLEEP_DELAY)


# ASSUMES: current working directory is the resources directory
class WebsocketThread(BgThread):
    def __init__(self, data_store: DataStore, signals: FsWatcherSignals, sfc_file_relpath: Filename, ws_address: str) -> None:
        super().__init__(signals, name="Websocket")

        self.data_store: Final = data_store
        self.sfc_file_relpath: Final = sfc_file_relpath
        self.sfc_file_basename: Final = os.path.basename(sfc_file_relpath)
        self.ws_address: Final = ws_address

    def wait_until_sfc_file_valid_and_update_binary(self, rou2s: ResourcesOverUsb2Snes) -> None:
        # Wait until device is running the correct ROM
        while not rou2s.is_correct_rom_running(self.sfc_file_basename):
            self.signals.set_usb2snes_status(f"Device not running {self.sfc_file_basename}")
            self.signals.sleep(INCORRECT_ROM_SLEEP_DELAY)

        # Wait until the `sfc_file` is valid
        sfc_file_valid = False
        while not sfc_file_valid:
            try:
                mappings, symbols, n_entities = self.data_store.get_mappings_symbols_and_n_entities()

                sfc_file_data = read_binary_file(self.sfc_file_relpath, 512 * 1024 + 1)
                validate_sfc_file(sfc_file_data, symbols, mappings)

                sfc_file_valid = True

            except Exception as e:
                pass
                log_error(f"Error validating { self.sfc_file_basename }", e)
                self.signals.set_usb2snes_status(f"Invalid { self.sfc_file_basename }: {e}")

            if not sfc_file_valid:
                self.signals.wait_until_sfc_binary_changed()

        rou2s.update_mappings(mappings, symbols, n_entities)

        # Double check device is running the correct ROM
        while not rou2s.is_correct_rom_running(self.sfc_file_basename):
            self.signals.set_usb2snes_status(f"Device not running {self.sfc_file_basename}")
            self.signals.sleep(INCORRECT_ROM_SLEEP_DELAY)

        # Update the running binary (if necessary)
        if rou2s.test_game_matches_sfc_file(sfc_file_data) is False:
            log_notice(f"Running game does not match { self.sfc_file_basename }")
            self.signals.set_usb2snes_status("Updating binary...")
            rou2s.reset_and_update_rom(sfc_file_data)
        else:
            log_success(f"Running game matches { self.sfc_file_basename }")

    def process_rou2s(self, rou2s: ResourcesOverUsb2Snes) -> None:
        while True:
            try:
                self.wait_until_sfc_file_valid_and_update_binary(rou2s)

                self.signals.set_usb2snes_status("Running")

                rou2s.run()

            except RebuildRequiredException:
                pass

            self.signals.set_usb2snes_status("REBUILD REQUIRED")
            self.signals.wait_until_sfc_binary_changed()

    def process_websocket(self, ws: websocket.WebSocket) -> None:
        # This sleep statement is required.
        # On my system there needs to be a 1/2 second delay between a usb2snes "Boot" command and a "GetAddress" command.
        #
        # ::TODO find a way to eliminate this::
        self.signals.sleep(0.5)

        while True:
            usb2snes = Usb2Snes(ws)

            connected = usb2snes.find_and_attach_device()
            if connected:
                log_success(f"Connected to { usb2snes.device_name() }")

                rou2s = ResourcesOverUsb2Snes(usb2snes, self.data_store, self.signals)
                self.process_rou2s(rou2s)
            else:
                log_error("Cannot connect to usb2snes")
                self.signals.set_usb2snes_status("Cannot connect to usb2snes")
                self.signals.sleep(INCORRECT_ROM_SLEEP_DELAY)

    def start_websocket(self) -> None:
        try:
            with contextlib.closing(websocket.WebSocket()) as ws:
                ws.connect(self.ws_address, origin="http://localhost")

                self.signals.clear_disconnected_flag()

                self.process_websocket(ws)

        except DisconnectEventException:
            log_notice("Closed websocket")
            self.signals.set_usb2snes_status("Closed")

        except ConnectionRefusedError as e:
            ws_connected = False
            log_error(f"Cannot connect to {self.ws_address}", e)
            self.signals.set_usb2snes_status(f"Cannot connect to {self.ws_address}: {e}")

        except Exception as e:
            log_error("Websocket ERROR", e)
            self.signals.set_usb2snes_status(type(e).__name__)

    @final
    def run_bg_thread(self) -> None:
        while not self.signals.is_quit_event_set():
            self.start_websocket()

            if not self.signals.is_quit_event_set():
                self.signals.set_disconnected_flag()
                self.signals.wait_for_ws_connect_button()

        self.signals.set_usb2snes_status("Stopped")
