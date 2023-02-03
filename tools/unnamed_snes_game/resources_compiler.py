# Unnamed SNES Game resource compiler and resource data store
#
# Copyright (c) 2023, Marcus Rowe <undisbeliever@gmail.com>.
# Distributed under The MIT License, see the LICENSE file for more details.

import re
import sys
import os.path
import threading
import subprocess
import multiprocessing
from abc import abstractmethod, ABCMeta
from enum import unique, auto, Enum
from dataclasses import dataclass
from typing import cast, final, Any, Callable, ClassVar, Final, Iterable, NamedTuple, Optional, Union

from .common import ResourceType
from .entity_data import create_entity_rom_data
from .mt_tileset import convert_mt_tileset
from .metasprite import convert_spritesheet, MsFsEntry, build_ms_fs_data
from .rooms import get_list_of_tmx_files, extract_room_id, compile_room
from .other_resources import convert_tiles, convert_bg_image
from .audio.common_audio_data import build_common_data as build_common_audio_data
from .audio.common_audio_data import load_sfx_file

from .json_formats import load_mappings_json, load_entities_json, load_ms_export_order_json, load_other_resources_json
from .json_formats import load_metasprites_json, Name, ScopedName, Filename, JsonError, MemoryMap, Mappings, EntitiesJson
from .json_formats import MsExportOrder, OtherResources, TilesInput, BackgroundImageInput
from .audio.json_formats import load_samples_json, SamplesJson


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

            self._msfs_and_entity_data = None
            self._msfs_and_entity_data_valid = False

    def reset_resources(self, rt_to_reset: Iterable[Optional[ResourceType]], mappings: Mappings) -> None:
        with self._lock:
            for rt in rt_to_reset:
                if rt is not None:
                    n_resources = len(getattr(mappings, rt.name))
                    self._resources[rt] = [None] * n_resources
                else:
                    self._rooms = [None] * self.ROOMS_PER_WORLD

            self._msfs_and_entity_data = None
            self._msfs_and_entity_data_valid = False

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
# Shared Input
# ============

# If this data changes, multiple resources must be recompiled
@dataclass
class SharedInput:
    mappings: Mappings
    entities: EntitiesJson
    other_resources: OtherResources
    ms_export_order: MsExportOrder
    audio_samples: SamplesJson
    symbols: dict[ScopedName, int]
    symbols_filename: Filename


MAPPINGS_FILENAME: Final = "mappings.json"
ENTITIES_FILENAME: Final = "entities.json"
OTHER_RESOURCES_FILENAME: Final = "other-resources.json"
MS_EXPORT_ORDER_FILENAME: Final = "ms-export-order.json"
AUDIO_SAMPLES_FILENAME: Final = "audio/samples.json"


@unique
class SharedInputType(Enum):
    MAPPINGS = auto()
    ENTITIES = auto()
    OTHER_RESOURCES = auto()
    MS_EXPORT_ORDER = auto()
    AUDIO_SAMPLES = auto()
    SYMBOLS = auto()

    def rebuild_required(self) -> bool:
        return self in REBUILD_REQUIRED_MAPPINS


REBUILD_REQUIRED_MAPPINS: Final = (SharedInputType.MAPPINGS, SharedInputType.ENTITIES, SharedInputType.MS_EXPORT_ORDER)


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
def load_shared_inputs(sym_filename: Filename) -> SharedInput:
    return SharedInput(
        mappings=load_mappings_json(MAPPINGS_FILENAME),
        entities=load_entities_json(ENTITIES_FILENAME),
        other_resources=load_other_resources_json(OTHER_RESOURCES_FILENAME),
        ms_export_order=load_ms_export_order_json(MS_EXPORT_ORDER_FILENAME),
        audio_samples=load_samples_json(AUDIO_SAMPLES_FILENAME),
        symbols=read_symbols_file(sym_filename),
        symbols_filename=sym_filename,
    )


# Returns True if the binary needs to be recompiled
def check_shared_input_file_changed(filename: Filename, si: SharedInput) -> Optional[SharedInputType]:
    if filename == MAPPINGS_FILENAME:
        si.mappings = load_mappings_json(filename)
        return SharedInputType.MAPPINGS

    elif filename == ENTITIES_FILENAME:
        si.entities = load_entities_json(filename)
        return SharedInputType.ENTITIES

    elif filename == OTHER_RESOURCES_FILENAME:
        si.other_resources = load_other_resources_json(filename)
        return SharedInputType.OTHER_RESOURCES

    elif filename == MS_EXPORT_ORDER_FILENAME:
        si.ms_export_order = load_ms_export_order_json(filename)
        return SharedInputType.MS_EXPORT_ORDER

    elif filename == AUDIO_SAMPLES_FILENAME:
        si.audio_samples = load_samples_json(filename)
        return SharedInputType.AUDIO_SAMPLES

    elif filename == si.symbols_filename:
        si.symbols = read_symbols_file(filename)
        return SharedInputType.SYMBOLS

    else:
        return None


#
# Resource Compilers
# ==================

MT_TILESET_FILE_REGEX: Final = re.compile(r"^metatiles/(\w+)(\.tsx|-.+)$")
MS_SPRITESHEET_FILE_REGEX: Final = re.compile(r"^metasprites/(\w+)/")


class BaseResourceCompiler(metaclass=ABCMeta):
    def __init__(self, r_type: ResourceType, shared_input: SharedInput) -> None:
        # All fields in a BaseResourceCompiler MUST be final
        self.resource_type: Final = r_type
        self.rt_name: Final = r_type.name
        self.shared_input: Final = shared_input

    def name_list(self) -> list[Name]:
        return getattr(self.shared_input.mappings, self.rt_name)  # type: ignore[no-any-return]

    # Returns resource id if the filename is used by the compiler
    # ASSUMES: current working directory is the resources directory
    @abstractmethod
    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        pass

    @abstractmethod
    def compile_resource(self, resource_id: int) -> BaseResourceData:
        pass

    # Returns true if all resources need recompiling
    @abstractmethod
    def shared_input_changed(self, s_type: SharedInputType) -> bool:
        pass


class MsSpritesheetCompiler(BaseResourceCompiler):
    def __init__(self, shared_input: SharedInput) -> None:
        super().__init__(ResourceType.ms_spritesheets, shared_input)

    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        if m := MS_SPRITESHEET_FILE_REGEX.match(filename):
            return self.name_list().index(m.group(1))
        return None

    def compile_resource(self, resource_id: int) -> BaseResourceData:
        r_name = self.name_list()[resource_id]
        try:
            ms_dir = os.path.join("metasprites", r_name)
            json_filename = os.path.join(ms_dir, "_metasprites.json")

            ms_input = load_metasprites_json(json_filename)

            data, msfs_entries = convert_spritesheet(ms_input, self.shared_input.ms_export_order, ms_dir)

            return MetaSpriteResourceData(self.resource_type, resource_id, r_name, data, msfs_entries)
        except Exception as e:
            return ResourceError(self.resource_type, resource_id, r_name, e)

    def shared_input_changed(self, s_type: SharedInputType) -> bool:
        return s_type == SharedInputType.MS_EXPORT_ORDER


class SimpleResourceCompiler(BaseResourceCompiler):
    def compile_resource(self, resource_id: int) -> BaseResourceData:
        r_name = self.name_list()[resource_id]
        try:
            data = self._compile(r_name)
            return ResourceData(self.resource_type, resource_id, r_name, data)
        except Exception as e:
            return ResourceError(self.resource_type, resource_id, r_name, e)

    @abstractmethod
    def _compile(self, r_name: Name) -> bytes:
        pass


class MetaTileTilesetCompiler(SimpleResourceCompiler):
    def __init__(self, shared_input: SharedInput) -> None:
        super().__init__(ResourceType.mt_tilesets, shared_input)

    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        if m := MT_TILESET_FILE_REGEX.match(filename):
            return self.name_list().index(m.group(1))
        return None

    def _compile(self, r_name: Name) -> bytes:
        filename = os.path.join("metatiles/", r_name + ".tsx")
        return convert_mt_tileset(filename, self.shared_input.mappings)

    def shared_input_changed(self, s_type: SharedInputType) -> bool:
        return False


class OtherResourcesCompiler(SimpleResourceCompiler):
    def __init__(self, r_type: ResourceType, shared_input: SharedInput) -> None:
        super().__init__(r_type, shared_input)
        self.filename_map = self.build_filename_map()

    @final
    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        return self.filename_map.get(filename)

    @final
    def shared_input_changed(self, s_type: SharedInputType) -> bool:
        if s_type == SharedInputType.MAPPINGS or s_type == SharedInputType.OTHER_RESOURCES:
            self.filename_map = self.build_filename_map()
            return True
        return False

    @final
    def compile_resource(self, resource_id: int) -> BaseResourceData:
        r_name = self.name_list()[resource_id]
        try:
            data = self._compile(r_name)
            return ResourceData(self.resource_type, resource_id, r_name, data)
        except Exception as e:
            return ResourceError(self.resource_type, resource_id, r_name, e)

    @abstractmethod
    def _compile(self, r_name: Name) -> bytes:
        pass

    @abstractmethod
    def build_filename_map(self) -> dict[Filename, int]:
        pass


class TileCompiler(OtherResourcesCompiler):
    def __init__(self, shared_input: SharedInput) -> None:
        super().__init__(ResourceType.tiles, shared_input)

    def build_filename_map(self) -> dict[Filename, int]:
        tiles = self.shared_input.other_resources.tiles

        d = dict()
        for i, n in enumerate(self.name_list()):
            t = tiles.get(n)
            if t:
                d[t.source] = i
        return d

    def _compile(self, r_name: Name) -> bytes:
        t = self.shared_input.other_resources.tiles[r_name]
        return convert_tiles(t)


class BgImageCompiler(OtherResourcesCompiler):
    def __init__(self, shared_input: SharedInput) -> None:
        super().__init__(ResourceType.bg_images, shared_input)

    def build_filename_map(self) -> dict[Filename, int]:
        tiles = self.shared_input.other_resources.bg_images

        d = dict()
        for i, n in enumerate(self.name_list()):
            t = tiles.get(n)
            if t:
                d[t.source] = i
                d[t.palette] = i
        return d

    def _compile(self, r_name: Name) -> bytes:
        bi = self.shared_input.other_resources.bg_images[r_name]
        return convert_bg_image(bi)


class SongCompiler(SimpleResourceCompiler):
    COMMON_DATA_NAME: Final = "__null__common_data__"
    SFX_FILE: Final = "audio/sound_effects.txt"

    def __init__(self, shared_input: SharedInput) -> None:
        super().__init__(ResourceType.songs, shared_input)
        if self.name_list()[0] != self.COMMON_DATA_NAME:
            raise RuntimeError(f"The first entry in the song resource list MUST BE `{self.COMMON_DATA_NAME}`")

    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        if filename == self.SFX_FILE:
            return 0
        return None

    def _compile(self, r_name: Name) -> bytes:
        if r_name == self.COMMON_DATA_NAME:
            sfx_file = load_sfx_file(self.SFX_FILE)
            return build_common_audio_data(self.shared_input.audio_samples, self.shared_input.mappings, sfx_file, self.SFX_FILE)
        else:
            raise NotImplementedError("Songs is not yet implemented")

    def shared_input_changed(self, s_type: SharedInputType) -> bool:
        return s_type == SharedInputType.AUDIO_SAMPLES


class RoomCompiler:
    def __init__(self, shared_input: SharedInput) -> None:
        self.shared_input: Final = shared_input

    def compile_room(self, filename: Filename) -> BaseResourceData:
        room_id = -1
        basename = os.path.basename(filename)
        name = os.path.splitext(basename)[0]

        try:
            room_id = extract_room_id(basename)
            filename = os.path.join("rooms", basename)
            data = compile_room(filename, self.shared_input.entities, self.shared_input.mappings)

            return ResourceData(None, room_id, name, data)
        except Exception as e:
            return ResourceError(None, room_id, name, e)

    def shared_input_changed(self, s_type: SharedInputType) -> bool:
        return s_type == SharedInputType.ENTITIES


class Compilers:
    def __init__(self, shared_input: SharedInput) -> None:
        self.shared_input: Final = shared_input
        self.resource_compilers = [
            MetaTileTilesetCompiler(shared_input),
            MsSpritesheetCompiler(shared_input),
            TileCompiler(shared_input),
            BgImageCompiler(shared_input),
            SongCompiler(shared_input),
        ]
        self.room_compiler = RoomCompiler(shared_input)

        assert len(self.resource_compilers) == len(ResourceType)

    def file_changed(self, filename: Filename) -> Optional[BaseResourceData]:
        if filename.endswith(".tmx"):
            return self.room_compiler.compile_room(filename)
        else:
            for c in self.resource_compilers:
                r_id = c.test_filename_is_resource(filename)
                if r_id is not None:
                    return c.compile_resource(r_id)
        return None

    def shared_input_changed(self, s_type: SharedInputType) -> list[Optional[ResourceType]]:
        to_recompile: list[Optional[ResourceType]] = list()

        for rc in self.resource_compilers:
            if rc.shared_input_changed(s_type):
                to_recompile.append(rc.resource_type)

        if self.room_compiler.shared_input_changed(s_type):
            to_recompile.append(None)

        if s_type == SharedInputType.MAPPINGS:
            to_recompile = list(ResourceType)
            to_recompile.append(None)

        return to_recompile


def compile_msfs_and_entity_data(
    shared_input: SharedInput, optional_msfs_lists: list[Optional[list[MsFsEntry]]]
) -> MsFsAndEntityOutput:
    if any(entry_list is None for entry_list in optional_msfs_lists):
        return MsFsAndEntityOutput(error=ValueError("Cannot compile MsFs data.  There is an error in a MS Spritesheet."))

    msfs_lists = cast(list[list[MsFsEntry]], optional_msfs_lists)

    try:
        rom_data, ms_map = build_ms_fs_data(msfs_lists, shared_input.symbols, shared_input.mappings.memory_map.mode)
        ms_fs_data = bytes(rom_data.data())
    except Exception as e:
        return MsFsAndEntityOutput(error=e)

    try:
        entity_rom_data = create_entity_rom_data(shared_input.entities, shared_input.symbols, ms_map)
    except Exception as e:
        return MsFsAndEntityOutput(error=e)

    return MsFsAndEntityOutput(ms_fs_data, entity_rom_data)


# ASSUMES: current working directory is the resources directory
def compile_resource_lists(
    to_recompile: Iterable[Optional[ResourceType]],
    data_store: DataStore,
    compilers: Compilers,
    n_processes: Optional[int],
    err_handler: Callable[[ResourceError], None],
) -> None:

    data_store.reset_resources(to_recompile, compilers.shared_input.mappings)

    with multiprocessing.Pool(processes=n_processes) as mp:
        co_lists = list()
        for rt in to_recompile:
            if rt is not None:
                c = compilers.resource_compilers[rt]
                co_lists.append(mp.imap_unordered(c.compile_resource, range(len(c.name_list()))))
            else:
                room_filenames = get_list_of_tmx_files("rooms")
                co_lists.append(mp.imap_unordered(compilers.room_compiler.compile_room, room_filenames))

        for co_l in co_lists:
            for co in co_l:
                data_store.insert_data(co)
                if isinstance(co, ResourceError):
                    err_handler(co)

    msfs_and_entity_data = compile_msfs_and_entity_data(compilers.shared_input, data_store.get_msfs_lists())
    data_store.insert_msfs_and_entity_data(msfs_and_entity_data)


# ASSUMES: current working directory is the resources directory
def compile_all_resources(
    data_store: DataStore, compilers: Compilers, n_processes: Optional[int], err_handler: Callable[[ResourceError], None]
) -> None:

    to_recompile: list[Optional[ResourceType]] = list(ResourceType)
    to_recompile.append(None)

    compile_resource_lists(to_recompile, data_store, compilers, n_processes, err_handler)
