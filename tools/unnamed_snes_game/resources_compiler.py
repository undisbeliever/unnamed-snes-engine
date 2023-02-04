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

    def res_string(self) -> str:
        if self.resource_type:
            return f"{ self.resource_type.name }[{ self.resource_id }] { self.resource_name }"
        else:
            return f"room[{ self.resource_id }] { self.resource_name }"


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

    def __init__(self) -> None:
        self._lock: Final[threading.Lock] = threading.Lock()

        with self._lock:
            self._mappings: Optional[Mappings] = None
            self._symbols: Optional[dict[ScopedName, int]] = None
            self._n_entities: int = 0

            self._resources: list[list[Optional[BaseResourceData]]] = [list() for rt in ResourceType]
            self._rooms: list[Optional[BaseResourceData]] = list()

            self._msfs_lists: list[Optional[list[MsFsEntry]]] = list()

            self._msfs_and_entity_data: Optional[MsFsAndEntityOutput] = None
            self._msfs_and_entity_data_valid: bool = False

            # Incremented if the resource type is not ROOM
            self._not_room_counter: int = 0

    # Must be called before data is inserted
    def reset_data(self, mappings: Mappings) -> None:
        with self._lock:
            # Confirm Mappings is immutable
            assert isinstance(mappings, tuple)
            self._mappings = mappings

            for rt in ResourceType:
                n_resources = len(getattr(mappings, rt.name))
                self._resources[rt] = [None] * n_resources

            self._msfs_lists = [None] * len(mappings.ms_spritesheets)
            self._rooms = [None] * self.ROOMS_PER_WORLD

            self._msfs_and_entity_data = None
            self._msfs_and_entity_data_valid = False

    def reset_resources(self, rt_to_reset: Iterable[Optional[ResourceType]]) -> None:
        with self._lock:
            # Confirm mappings exists and is immutable
            assert isinstance(self._mappings, tuple)

            for rt in rt_to_reset:
                if rt is not None:
                    n_resources = len(getattr(self._mappings, rt.name))
                    self._resources[rt] = [None] * n_resources
                else:
                    self._rooms = [None] * self.ROOMS_PER_WORLD

            self._msfs_and_entity_data = None
            self._msfs_and_entity_data_valid = False

    def set_symbols(self, symbols: Optional[dict[ScopedName, int]]) -> None:
        with self._lock:
            self._symbols = symbols

    def set_n_entities(self, n_entities: int) -> None:
        with self._lock:
            self._n_entities = n_entities

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

    def get_mappings(self) -> Mappings:
        with self._lock:
            if not self._mappings:
                raise RuntimeError("No mappings")
            return self._mappings

    def get_mappings_symbols_and_n_entities(self) -> tuple[Mappings, dict[ScopedName, int], int]:
        with self._lock:
            if not self._mappings:
                raise RuntimeError("No mappings")
            if not self._symbols:
                raise RuntimeError("No symbols")

            # dict (symbols) is not immutable, return a copy instead
            return self._mappings, self._symbols.copy(), self._n_entities

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


# If this data changes, multiple resources must be recompiled
# THREAD SAFETY: Must only exist in the ProjectCompiler classes
class SharedInput:
    def __init__(self, sym_filename: Filename) -> None:
        self.symbols_filename: Final[Filename] = sym_filename

        self.mappings: Optional[Mappings] = None
        self.entities: Optional[EntitiesJson] = None
        self.other_resources: Optional[OtherResources] = None
        self.ms_export_order: Optional[MsExportOrder] = None
        self.audio_samples: Optional[SamplesJson] = None
        self.symbols: Optional[dict[ScopedName, int]] = None

    # May throw an exception
    def load(self, s_type: SharedInputType) -> None:
        def _load(field: str, filename: Filename, loader: Callable[[Filename], Any]) -> None:
            try:
                setattr(self, field, loader(filename))
            except Exception:
                setattr(self, field, None)
                raise

        match s_type:
            case SharedInputType.MAPPINGS:
                _load("mappings", MAPPINGS_FILENAME, load_mappings_json)
            case SharedInputType.ENTITIES:
                _load("entities", ENTITIES_FILENAME, load_entities_json)
            case SharedInputType.OTHER_RESOURCES:
                _load("other_resources", OTHER_RESOURCES_FILENAME, load_other_resources_json)
            case SharedInputType.MS_EXPORT_ORDER:
                _load("ms_export_order", MS_EXPORT_ORDER_FILENAME, load_ms_export_order_json)
            case SharedInputType.AUDIO_SAMPLES:
                _load("audio_samples", AUDIO_SAMPLES_FILENAME, load_samples_json)
            case SharedInputType.SYMBOLS:
                _load("symbols", self.symbols_filename, read_symbols_file)
            case other:
                raise RuntimeError("Unknown shared input file")


MAPPINGS_FILENAME: Final = "mappings.json"
ENTITIES_FILENAME: Final = "entities.json"
OTHER_RESOURCES_FILENAME: Final = "other-resources.json"
MS_EXPORT_ORDER_FILENAME: Final = "ms-export-order.json"
AUDIO_SAMPLES_FILENAME: Final = "audio/samples.json"


SHARED_INPUT_FILENAME_MAP: Final = {
    MAPPINGS_FILENAME: SharedInputType.MAPPINGS,
    ENTITIES_FILENAME: SharedInputType.ENTITIES,
    OTHER_RESOURCES_FILENAME: SharedInputType.OTHER_RESOURCES,
    MS_EXPORT_ORDER_FILENAME: SharedInputType.MS_EXPORT_ORDER,
    AUDIO_SAMPLES_FILENAME: SharedInputType.AUDIO_SAMPLES,
}

SHARED_INPUT_NAMES: Final = {
    SharedInputType.MAPPINGS: "mappings JSON",
    SharedInputType.ENTITIES: "entities JSON",
    SharedInputType.OTHER_RESOURCES: "other Resources JSON",
    SharedInputType.MS_EXPORT_ORDER: "MetaSprite export order JSON",
    SharedInputType.AUDIO_SAMPLES: "audio Samples JSON",
    SharedInputType.SYMBOLS: "Symbols",
}


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
        self._shared_input: Final = shared_input

    def name_list(self) -> list[Name]:
        return getattr(self._shared_input.mappings, self.rt_name)  # type: ignore[no-any-return]

    # Returns resource id if the filename is used by the compiler
    # ASSUMES: current working directory is the resources directory
    @abstractmethod
    def test_filename_is_resource(self, filename: Filename) -> Optional[tuple[int, Name]]:
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

    def test_filename_is_resource(self, filename: Filename) -> Optional[tuple[int, Name]]:
        if m := MS_SPRITESHEET_FILE_REGEX.match(filename):
            name = m.group(1)
            return self.name_list().index(name), name
        return None

    def compile_resource(self, resource_id: int) -> BaseResourceData:
        assert self._shared_input.ms_export_order

        r_name = self.name_list()[resource_id]
        try:
            ms_dir = os.path.join("metasprites", r_name)
            json_filename = os.path.join(ms_dir, "_metasprites.json")

            ms_input = load_metasprites_json(json_filename)

            data, msfs_entries = convert_spritesheet(ms_input, self._shared_input.ms_export_order, ms_dir)

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

    def test_filename_is_resource(self, filename: Filename) -> Optional[tuple[int, Name]]:
        if m := MT_TILESET_FILE_REGEX.match(filename):
            name = m.group(1)
            return self.name_list().index(name), name
        return None

    def _compile(self, r_name: Name) -> bytes:
        assert self._shared_input.mappings

        filename = os.path.join("metatiles/", r_name + ".tsx")
        return convert_mt_tileset(filename, self._shared_input.mappings)

    def shared_input_changed(self, s_type: SharedInputType) -> bool:
        return False


class OtherResourcesCompiler(SimpleResourceCompiler):
    def __init__(self, r_type: ResourceType, shared_input: SharedInput) -> None:
        super().__init__(r_type, shared_input)
        self.filename_map: dict[Filename, int] = dict()

    @final
    def test_filename_is_resource(self, filename: Filename) -> Optional[tuple[int, Name]]:
        r_id = self.filename_map.get(filename)
        if r_id:
            return r_id, self.name_list()[r_id]
        return None

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
        assert self._shared_input.other_resources

        tiles = self._shared_input.other_resources.tiles

        d = dict()
        for i, n in enumerate(self.name_list()):
            t = tiles.get(n)
            if t:
                d[t.source] = i
        return d

    def _compile(self, r_name: Name) -> bytes:
        assert self._shared_input.other_resources

        t = self._shared_input.other_resources.tiles[r_name]
        return convert_tiles(t)


class BgImageCompiler(OtherResourcesCompiler):
    def __init__(self, shared_input: SharedInput) -> None:
        super().__init__(ResourceType.bg_images, shared_input)

    def build_filename_map(self) -> dict[Filename, int]:
        assert self._shared_input.other_resources

        tiles = self._shared_input.other_resources.bg_images

        d = dict()
        for i, n in enumerate(self.name_list()):
            t = tiles.get(n)
            if t:
                d[t.source] = i
                d[t.palette] = i
        return d

    def _compile(self, r_name: Name) -> bytes:
        assert self._shared_input.other_resources

        bi = self._shared_input.other_resources.bg_images[r_name]
        return convert_bg_image(bi)


class SongCompiler(SimpleResourceCompiler):
    COMMON_DATA_RESOURCE_NAME: Final = "__null__common_data__"
    SFX_FILE: Final = "audio/sound_effects.txt"

    def __init__(self, shared_input: SharedInput) -> None:
        super().__init__(ResourceType.songs, shared_input)

    def test_filename_is_resource(self, filename: Filename) -> Optional[tuple[int, Name]]:
        if filename == self.SFX_FILE:
            return 0, "common data and sound effects"
        return None

    def _compile(self, r_name: Name) -> bytes:
        assert self._shared_input.mappings
        assert self._shared_input.audio_samples

        if r_name == self.COMMON_DATA_RESOURCE_NAME:
            if self.name_list()[0] != self.COMMON_DATA_RESOURCE_NAME:
                raise RuntimeError(f"The first entry in the song resource list MUST BE `{self.COMMON_DATA_RESOURCE_NAME}`")

            sfx_file = load_sfx_file(self.SFX_FILE)
            return build_common_audio_data(self._shared_input.audio_samples, self._shared_input.mappings, sfx_file, self.SFX_FILE)
        else:
            if self.name_list()[0] == r_name:
                raise RuntimeError(f"The first entry in the song resource list MUST BE `{self.COMMON_DATA_RESOURCE_NAME}`")

            raise NotImplementedError("Songs is not yet implemented")

    def shared_input_changed(self, s_type: SharedInputType) -> bool:
        return s_type == SharedInputType.AUDIO_SAMPLES


class RoomCompiler:
    def __init__(self, shared_input: SharedInput) -> None:
        self._shared_input: Final = shared_input

    def compile_room(self, filename: Filename) -> BaseResourceData:
        assert self._shared_input.mappings
        assert self._shared_input.entities

        room_id = -1
        basename = os.path.basename(filename)
        name = os.path.splitext(basename)[0]

        try:
            room_id = extract_room_id(basename)
            filename = os.path.join("rooms", basename)
            data = compile_room(filename, self._shared_input.entities, self._shared_input.mappings)

            return ResourceData(None, room_id, name, data)
        except Exception as e:
            return ResourceError(None, room_id, name, e)

    def shared_input_changed(self, s_type: SharedInputType) -> bool:
        return s_type == SharedInputType.ENTITIES


# THREAD SAFETY: Must only exist on a single thread
class ProjectCompiler:
    def __init__(
        self,
        data_store: DataStore,
        sym_filename: Filename,
        n_processes: Optional[int],
        err_handler: Callable[[Union[ResourceError, Exception]], None],
        message_handler: Callable[[str], None],
    ) -> None:

        self.data_store: Final = data_store

        self.__sym_filename: Final = sym_filename
        self.__shared_input: Final = SharedInput(sym_filename)
        self.__n_processes: Final = n_processes

        self.log_message: Final = message_handler
        self.log_error: Final = err_handler

        # Mark all shared_inputs invalid
        self.__shared_input_valid: bool = False
        self.__shared_inputs_with_errors: Final[set[SharedInputType]] = set(SharedInputType)

        self.__resource_compilers: Final = [
            MetaTileTilesetCompiler(self.__shared_input),
            MsSpritesheetCompiler(self.__shared_input),
            TileCompiler(self.__shared_input),
            BgImageCompiler(self.__shared_input),
            SongCompiler(self.__shared_input),
        ]
        self.__room_compiler: Final = RoomCompiler(self.__shared_input)

        assert len(self.__resource_compilers) == len(ResourceType)

    def is_shared_input_valid(self) -> bool:
        return self.__shared_input_valid

    def file_changed(self, filename: Filename) -> Optional[BaseResourceData | SharedInputType]:
        if filename == self.__shared_input.symbols_filename:
            s_type: Optional[SharedInputType] = SharedInputType.SYMBOLS
        else:
            s_type = SHARED_INPUT_FILENAME_MAP.get(filename)

        if s_type:
            self._shared_input_file_changed(s_type)
            return s_type
        else:
            co = None
            if self.__shared_input_valid:
                # Only compile resources if the shared input is valid
                if filename.endswith(".tmx"):
                    self.log_message(f"Compiling room {filename}")
                    co = self.__room_compiler.compile_room(filename)
                else:
                    for c in self.__resource_compilers:
                        try:
                            res = c.test_filename_is_resource(filename)
                        except Exception as e:
                            self.log_error(e)
                            return None

                        if res:
                            r_id, r_name = res
                            self.log_message(f"Compiling { c.resource_type.name }[{ r_id }] { r_name }")
                            co = c.compile_resource(r_id)
                            break
            if co:
                self.data_store.insert_data(co)
                if isinstance(co, ResourceError):
                    self.log_error(co)
            return co

    def _shared_input_file_changed(self, s_type: SharedInputType) -> None:
        self.log_message(f"Loading { SHARED_INPUT_NAMES[s_type] } file")
        try:
            self.__shared_input.load(s_type)
            self.__shared_inputs_with_errors.discard(s_type)
        except Exception as e:
            self.log_error(e)
            self.__shared_inputs_with_errors.add(s_type)
            self.__shared_input_valid = False
            return

        if s_type == SharedInputType.MAPPINGS:
            if self.__shared_input.mappings:
                self.data_store.reset_data(self.__shared_input.mappings)

        if s_type == SharedInputType.SYMBOLS:
            self.data_store.set_symbols(self.__shared_input.symbols)

        elif s_type == SharedInputType.ENTITIES:
            if self.__shared_input.entities:
                n_entities = len(self.__shared_input.entities.entities)
                self.data_store.set_n_entities(n_entities)

        if self.__shared_input_valid:
            self.__recompile_resources_using_shared_input(s_type)
        else:
            if not self.__shared_inputs_with_errors:
                # All shared_inputs have been fixed
                self.__shared_input_valid = True
                self.__compile_all_resources()

    def __recompile_resources_using_shared_input(self, s_type: SharedInputType) -> None:
        if s_type is SharedInputType.MAPPINGS:
            self.__compile_all_resources()
        else:
            to_recompile: list[Optional[ResourceType]] = list()

            for rc in self.__resource_compilers:
                if rc.shared_input_changed(s_type):
                    self.log_message(f"Compiling all {rc.resource_type.name}")
                    to_recompile.append(rc.resource_type)

            if self.__room_compiler.shared_input_changed(s_type):
                self.log_message("Compiling all rooms")
                to_recompile.append(None)

            self.__compile_resource_lists(to_recompile)

    def __compile_msfs_and_entity_data(self) -> None:
        if not self.__shared_input_valid:
            raise RuntimeError("Shared Input is not valid")

        assert self.__shared_input.mappings
        assert self.__shared_input.entities
        assert self.__shared_input.symbols

        optional_msfs_lists: Final = self.data_store.get_msfs_lists()

        if any(entry_list is None for entry_list in optional_msfs_lists):
            e = ValueError("Cannot compile MsFs data.  There is an error in a MS Spritesheet.")
            self.log_error(e)
            data = MsFsAndEntityOutput(error=e)
        else:
            try:
                msfs_lists = cast(list[list[MsFsEntry]], optional_msfs_lists)
                rom_data, ms_map = build_ms_fs_data(
                    msfs_lists, self.__shared_input.symbols, self.__shared_input.mappings.memory_map.mode
                )
                ms_fs_data = bytes(rom_data.data())
                entity_rom_data = create_entity_rom_data(self.__shared_input.entities, self.__shared_input.symbols, ms_map)

                data = MsFsAndEntityOutput(ms_fs_data, entity_rom_data)
            except Exception as e:
                self.log_error(e)
                data = MsFsAndEntityOutput(error=e)

        self.data_store.insert_msfs_and_entity_data(data)

    def __compile_resource_lists(self, to_recompile: Iterable[Optional[ResourceType]]) -> None:
        # Uses multiprocessing to speed up the compilation

        if not self.__shared_input_valid:
            raise RuntimeError("Shared Input is not valid")

        self.data_store.reset_resources(to_recompile)

        with multiprocessing.Pool(processes=self.__n_processes) as mp:
            co_lists = list()
            for rt in to_recompile:
                if rt is not None:
                    c = self.__resource_compilers[rt]
                    co_lists.append(mp.imap_unordered(c.compile_resource, range(len(c.name_list()))))
                else:
                    room_filenames = get_list_of_tmx_files("rooms")
                    co_lists.append(mp.imap_unordered(self.__room_compiler.compile_room, room_filenames))

            for co_l in co_lists:
                for co in co_l:
                    self.data_store.insert_data(co)
                    if isinstance(co, ResourceError):
                        self.log_error(co)

        self.__compile_msfs_and_entity_data()

    def __compile_all_resources(self) -> None:
        self.log_message("Compiling all resources")

        to_recompile: list[Optional[ResourceType]] = list(ResourceType)
        to_recompile.append(None)

        self.__compile_resource_lists(to_recompile)

    def compile_everything(self) -> None:
        # Load shared inputs
        # After all shared inputs are loaded, then it will trigger a full recompile
        for s_type in SharedInputType:
            self._shared_input_file_changed(s_type)
