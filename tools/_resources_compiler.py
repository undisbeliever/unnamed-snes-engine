# Unnamed SNES Game resource compiler and resource data store
#
# Copyright (c) 2023, Marcus Rowe <undisbeliever@gmail.com>.
# Distributed under The MIT License, see the LICENSE file for more details.

import re
import sys
import os.path
import argparse
import threading
import subprocess
import multiprocessing
from abc import abstractmethod, ABCMeta
from dataclasses import dataclass

from typing import cast, Any, Callable, ClassVar, Final, NamedTuple, Optional, Union

from _common import ResourceType
from _entity_data import create_entity_rom_data
from convert_mt_tileset import convert_mt_tileset
from convert_metasprite import convert_spritesheet, MsFsEntry, build_ms_fs_data
from convert_rooms import get_list_of_tmx_files, extract_room_id, compile_room
from convert_other_resources import convert_tiles, convert_bg_image

from _json_formats import load_mappings_json, load_entities_json, load_ms_export_order_json, load_other_resources_json
from _json_formats import load_metasprites_json, Name, ScopedName, Filename, JsonError, MemoryMap, Mappings, EntitiesJson
from _json_formats import MsExportOrder, OtherResources, TilesInput, BackgroundImageInput


#
# Resource Data Storage
# =====================


@dataclass(frozen=True)
class BaseResourceData:
    # If `resource_type` is `None` the resource is a room
    resource_type: Optional[ResourceType]
    resource_id: int
    resource_name: Name


@dataclass(frozen=True)
class ResourceData(BaseResourceData):
    data: bytes


@dataclass(frozen=True)
class MetaSpriteResourceData(ResourceData):
    msfs_entries: list[MsFsEntry]


@dataclass(frozen=True)
class ResourceError(BaseResourceData):
    error: Exception


class MsFsAndEntityOutput(NamedTuple):
    msfs_data: Optional[bytes] = None
    entity_rom_data: Optional[bytes] = None
    error: Optional[Exception] = None


# Thread Safety: This class MUST ONLY be accessed via method calls.
# Thread Safety: All methods in this class must acquire the `_lock` before accessing fields.
class DataStore:
    ROOMS_PER_WORLD: Final = 256

    def __init__(self, mappings: Mappings):
        self._lock: Final[threading.Lock] = threading.Lock()

        with self._lock:
            self._resources: list[list[Optional[BaseResourceData]]] = [list() for rt in ResourceType]
            self._rooms: list[Optional[BaseResourceData]] = list()

            self._msfs_lists: list[Optional[list[MsFsEntry]]] = list()

            self._msfs_and_entity_data: Optional[MsFsAndEntityOutput] = None
            self._msfs_and_entity_data_valid: bool = False

            # Incremented if the resource type is not ROOM
            self._not_room_counter: int = 0
        self.reset_data(mappings)

    def reset_data(self, mappings: Mappings) -> None:
        with self._lock:
            for rt in ResourceType:
                n_resources = len(getattr(mappings, rt.name))
                self._resources[rt] = [None] * n_resources

            self._msfs_lists = [None] * len(mappings.ms_spritesheets)
            self._rooms = [None] * self.ROOMS_PER_WORLD

    def insert_data(self, c: BaseResourceData) -> None:
        with self._lock:
            if c.resource_type is None:
                self._rooms[c.resource_id] = c
            else:
                self._resources[c.resource_type][c.resource_id] = c
                self._not_room_counter += 1

            if isinstance(c, MetaSpriteResourceData):
                assert c.resource_type == ResourceType.ms_spritesheets
                self._msfs_lists[c.resource_id] = c.msfs_entries
                self._msfa_and_entity_rom_data = None
                self._msfs_and_entity_data_valid = False

    def insert_msfs_and_entity_data(self, me: MsFsAndEntityOutput) -> None:
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

    def get_resource_data(self, r_type: ResourceType, r_id: int) -> Optional[BaseResourceData]:
        with self._lock:
            return self._resources[r_type][r_id]

    def get_room_data(self, room_id: int) -> Optional[BaseResourceData]:
        with self._lock:
            return self._rooms[room_id]

    # Assumes no errors in the DataStore
    def get_all_data_for_type(self, r_type: ResourceType) -> list[bytes]:
        with self._lock:
            return [r.data for r in self._resources[r_type]]  # type: ignore

    # Assumes no errors in the DataStore
    def get_data_for_all_rooms(self) -> list[Optional[bytes]]:
        with self._lock:
            return [r.data if isinstance(r, ResourceData) else None for r in self._rooms]


#
# Fixed Input
# ===========

# If this data changes, all resources must be recompiled
class FixedInput(NamedTuple):
    mappings: Mappings
    entities: EntitiesJson
    other_resources: OtherResources
    ms_export_order: MsExportOrder
    symbols: dict[ScopedName, int]


def read_symbols_file(symbol_filename: Filename) -> dict[str, int]:
    regex = re.compile(r"([0-9A-F]{2}):([0-9A-F]{4}) (.+)")

    out = dict()

    with open(symbol_filename, "r") as fp:
        for line in fp:
            line = line.strip()

            if line == "[labels]":
                continue

            m = regex.match(line)
            if not m:
                raise ValueError("Cannot read symbol file: invalid line")
            addr = (int(m.group(1), 16) << 16) | (int(m.group(2), 16))
            out[m.group(3)] = addr

    return out


# ASSUMES: current working directory is the resources directory
def load_fixed_inputs(sym_filename: Filename) -> FixedInput:
    return FixedInput(
        mappings=load_mappings_json("mappings.json"),
        entities=load_entities_json("entities.json"),
        other_resources=load_other_resources_json("other-resources.json"),
        ms_export_order=load_ms_export_order_json("ms-export-order.json"),
        symbols=read_symbols_file(sym_filename),
    )


#
# Resource Compilers
# ==================

MT_TILESET_FILE_REGEX: Final = re.compile(r"^metatiles/(\w+)(\.tsx|-.+)$")
MS_SPRITESHEET_FILE_REGEX: Final = re.compile(r"^metasprites/(\w+)/")


class BaseResourceCompiler(metaclass=ABCMeta):
    def __init__(self, r_type: ResourceType, name_list: list[Name]) -> None:
        # All fields in a BaseResourceCompiler MUST be final
        self.resource_type: Final = r_type
        self.name_list: Final = name_list

    # Returns resource id if the filename is used by the compiler
    # ASSUMES: current working directory is the resources directory
    @abstractmethod
    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        pass

    @abstractmethod
    def compile_resource(self, resource_id: int) -> BaseResourceData:
        pass


class MsSpritesheetCompiler(BaseResourceCompiler):
    def __init__(self, fixed_input: FixedInput) -> None:
        super().__init__(ResourceType.ms_spritesheets, fixed_input.mappings.ms_spritesheets)
        self.ms_export_orders: Final = fixed_input.ms_export_order

    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        if m := MS_SPRITESHEET_FILE_REGEX.match(filename):
            return self.name_list.index(m.group(1))
        return None

    def compile_resource(self, resource_id: int) -> BaseResourceData:
        r_name = self.name_list[resource_id]
        try:
            ms_dir = os.path.join("metasprites", r_name)
            json_filename = os.path.join(ms_dir, "_metasprites.json")

            ms_input = load_metasprites_json(json_filename)

            data, msfs_entries = convert_spritesheet(ms_input, self.ms_export_orders, ms_dir)

            return MetaSpriteResourceData(self.resource_type, resource_id, r_name, data, msfs_entries)
        except Exception as e:
            return ResourceError(self.resource_type, resource_id, r_name, e)


class SimpleResourceCompiler(BaseResourceCompiler):
    def compile_resource(self, resource_id: int) -> BaseResourceData:
        r_name = self.name_list[resource_id]
        try:
            data = self._compile(r_name)
            return ResourceData(self.resource_type, resource_id, r_name, data)
        except Exception as e:
            return ResourceError(self.resource_type, resource_id, r_name, e)

    @abstractmethod
    def _compile(self, r_name: Name) -> bytes:
        pass


class MetaTileTilesetCompiler(SimpleResourceCompiler):
    def __init__(self, fixed_input: FixedInput) -> None:
        super().__init__(ResourceType.mt_tilesets, fixed_input.mappings.mt_tilesets)
        self.mappings: Final = fixed_input.mappings

    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        if m := MT_TILESET_FILE_REGEX.match(filename):
            return self.name_list.index(m.group(1))
        return None

    def _compile(self, r_name: Name) -> bytes:
        filename = os.path.join("metatiles/", r_name + ".tsx")
        return convert_mt_tileset(filename, self.mappings)


class TileCompiler(SimpleResourceCompiler):
    def __init__(self, fixed_input: FixedInput) -> None:
        super().__init__(ResourceType.tiles, fixed_input.mappings.tiles)
        self.tiles: Final = fixed_input.other_resources.tiles
        self.filename_map: Final = TileCompiler.build_filename_map(self.tiles, self.name_list)

    @staticmethod
    def build_filename_map(tiles: dict[Name, TilesInput], name_list: list[Name]) -> dict[Filename, int]:
        d = dict()
        for i, n in enumerate(name_list):
            t = tiles.get(n)
            if t:
                d[t.source] = i
        return d

    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        return self.filename_map.get(filename)

    def _compile(self, r_name: Name) -> bytes:
        t = self.tiles[r_name]
        return convert_tiles(t)


class BgImageCompiler(SimpleResourceCompiler):
    def __init__(self, fixed_input: FixedInput) -> None:
        super().__init__(ResourceType.bg_images, fixed_input.mappings.bg_images)
        self.bg_images: Final = fixed_input.other_resources.bg_images
        self.filename_map: Final = BgImageCompiler.build_filename_map(self.bg_images, self.name_list)

    @staticmethod
    def build_filename_map(tiles: dict[Name, BackgroundImageInput], name_list: list[Name]) -> dict[Filename, int]:
        d = dict()
        for i, n in enumerate(name_list):
            t = tiles.get(n)
            if t:
                d[t.source] = i
                d[t.palette] = i
        return d

    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        return self.filename_map.get(filename)

    def _compile(self, r_name: Name) -> bytes:
        bi = self.bg_images[r_name]
        return convert_bg_image(bi)


class RoomCompiler:
    def __init__(self, fixed_input: FixedInput) -> None:
        self.mappings: Final = fixed_input.mappings
        self.entities: Final = fixed_input.entities

    def compile_room(self, basename: Filename) -> BaseResourceData:
        room_id = -1
        name = os.path.splitext(basename)[0]

        try:
            room_id = extract_room_id(basename)
            filename = os.path.join("rooms", basename)
            data = compile_room(filename, self.entities, self.mappings)

            return ResourceData(None, room_id, name, data)
        except Exception as e:
            return ResourceError(None, room_id, name, e)


class Compilers:
    def __init__(self, fixed_input: FixedInput) -> None:
        self.fixed_input: Final = fixed_input
        self.resource_compilers = [
            MetaTileTilesetCompiler(fixed_input),
            MsSpritesheetCompiler(fixed_input),
            TileCompiler(fixed_input),
            BgImageCompiler(fixed_input),
        ]
        self.room_compiler = RoomCompiler(fixed_input)

    def file_changed(self, filename: Filename) -> Optional[BaseResourceData]:
        if filename.endswith(".tmx"):
            return self.room_compiler.compile_room(filename)
        else:
            for c in self.resource_compilers:
                r_id = c.test_filename_is_resource(filename)
                if r_id is not None:
                    return c.compile_resource(r_id)
        return None


def compile_msfs_and_entity_data(fixed_input: FixedInput, optional_msfs_lists: list[Optional[list[MsFsEntry]]]) -> MsFsAndEntityOutput:
    if any(entry_list is None for entry_list in optional_msfs_lists):
        return MsFsAndEntityOutput(error=ValueError("Cannot compile MsFs data.  There is an error in a MS Spritesheet."))

    msfs_lists = cast(list[list[MsFsEntry]], optional_msfs_lists)

    try:
        rom_data, ms_map = build_ms_fs_data(msfs_lists, fixed_input.symbols, fixed_input.mappings.memory_map.mode)
        ms_fs_data = bytes(rom_data.data())
    except Exception as e:
        return MsFsAndEntityOutput(error=e)

    try:
        entity_rom_data = create_entity_rom_data(fixed_input.entities, fixed_input.symbols, ms_map)
    except Exception as e:
        return MsFsAndEntityOutput(error=e)

    return MsFsAndEntityOutput(ms_fs_data, entity_rom_data)


# ASSUMES: current working directory is the resources directory
def compile_all_resources(compilers: Compilers, n_processes: Optional[int], err_handler: Callable[[ResourceError], None]) -> DataStore:
    # Uses multiprocessing to speed up the compiling

    room_filenames = get_list_of_tmx_files("rooms")

    with multiprocessing.Pool(processes=n_processes) as mp:
        co_lists = [mp.imap_unordered(c.compile_resource, range(len(c.name_list))) for c in compilers.resource_compilers]
        rooms = mp.imap_unordered(compilers.room_compiler.compile_room, room_filenames)

        data_store = DataStore(compilers.fixed_input.mappings)

        for co_l in co_lists:
            for co in co_l:
                data_store.insert_data(co)
                if isinstance(co, ResourceError):
                    err_handler(co)

        for r in rooms:
            data_store.insert_data(r)
            if isinstance(r, ResourceError):
                err_handler(r)

    msfs_and_entity_data = compile_msfs_and_entity_data(compilers.fixed_input, data_store.get_msfs_lists())
    data_store.insert_msfs_and_entity_data(msfs_and_entity_data)

    return data_store
