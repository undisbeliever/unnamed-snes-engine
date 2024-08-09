# Unnamed SNES Game resource compiler and resource data store
#
# Copyright (c) 2023, Marcus Rowe <undisbeliever@gmail.com>.
# Distributed under The MIT License, see the LICENSE file for more details.

import re
import os.path
import threading
import multiprocessing
from abc import abstractmethod, ABCMeta
from collections import OrderedDict
from enum import unique, auto, Enum
from dataclasses import dataclass
from typing import cast, final, Any, Callable, Final, Iterable, NamedTuple, Optional, Sequence, Set, Union

from .common import ResourceType, EngineData
from .entity_data import create_entity_rom_data
from .mt_tileset import convert_mt_tileset
from .second_layers import convert_second_layer
from .palette import convert_palette, PaletteResource
from .metasprite import convert_static_spritesheet, convert_dynamic_spritesheet, build_ms_fs_data, MsFsEntry, DynamicMsSpritesheet
from .rooms import get_list_of_tmx_files, extract_room_id, compile_room, RoomDependencies
from .snes import ConstSmallTileMap
from .other_resources import convert_tiles, convert_bg_image
from .audio import AudioCompiler, COMMON_AUDIO_DATA_RESOURCE_NAME

from .json_formats import load_mappings_json, load_entities_json, load_ms_export_order_json, load_other_resources_json
from .json_formats import load_metasprites_json, load_audio_project
from .json_formats import Name, ScopedName, Filename, Mappings, EntitiesJson
from .json_formats import MsExportOrder, OtherResources, AudioProject


@unique
class SharedInputType(Enum):
    MAPPINGS = auto()
    ENTITIES = auto()
    OTHER_RESOURCES = auto()
    MS_EXPORT_ORDER = auto()
    AUDIO_PROJECT = auto()
    SYMBOLS = auto()

    def rebuild_required(self) -> bool:
        return self in REBUILD_REQUIRED_MAPPINS


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
        if self.resource_type is not None:
            return f"{ self.resource_type.name }[{ self.resource_id }] { self.resource_name }"
        else:
            return f"room[{ self.resource_id }] { self.resource_name }"


@dataclass(frozen=True)
class ResourceData(BaseResourceData):
    data: EngineData


@dataclass(frozen=True)
class MetaSpriteResourceData(ResourceData):
    msfs_entries: list[MsFsEntry]


@dataclass(frozen=True)
class PaletteResourceData(ResourceData):
    palette: PaletteResource


@dataclass(frozen=True)
class MtTilesetResourceData(ResourceData):
    tile_map: ConstSmallTileMap


class MsFsAndEntityOutput(NamedTuple):
    msfs_data: Optional[bytes] = None
    entity_rom_data: Optional[bytes] = None
    error: Optional[Exception] = None


# Used to track errors in the DataStore
class ErrorKey(NamedTuple):
    r_type: Optional[ResourceType]
    r_id: Union[int, SharedInputType]


DYNAMIC_METASPRITES_ERROR_KEY: Final = ErrorKey(None, -1)
MS_FS_AND_ENTITY_ERROR_KEY: Final = ErrorKey(None, -2)


class NonResourceError(NamedTuple):
    error_key: ErrorKey
    name: str
    error: Exception

    def res_string(self) -> str:
        return self.name


@dataclass(frozen=True)
class ResourceError(BaseResourceData):
    error_key: ErrorKey
    error: Exception


def create_resource_error(r_type: Optional[ResourceType], r_id: int, r_name: Name, error: Exception) -> ResourceError:
    return ResourceError(r_type, r_id, r_name, ErrorKey(r_type, r_id), error)


# Used to signal the dynamic metasprites were recompiled
class DynamicMetaspriteToken(NamedTuple):
    pass


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

            self._errors: OrderedDict[ErrorKey, Union[ResourceError, NonResourceError]] = OrderedDict()

            self._dynamic_ms_data: Optional[DynamicMsSpritesheet] = None
            self._msfs_lists: list[Optional[list[MsFsEntry]]] = list()

            self._msfs_and_entity_data: Optional[MsFsAndEntityOutput] = None
            self._msfs_and_entity_data_valid: bool = False

            # Incremented if the resource type is not ROOM
            self._not_room_counter: int = 0

    # Must be called before data is inserted
    def reset_data(self, r_type: ResourceType, n_items: int) -> None:
        with self._lock:
            assert n_items >= 0

            self._resources[r_type] = [None] * n_items

            if r_type == ResourceType.ms_spritesheets:
                self._msfs_lists = [None] * n_items
                self._dynamic_ms_data = None
                self._msfs_and_entity_data = None
                self._msfs_and_entity_data_valid = False

    def reset_resources(self, rt_to_reset: Iterable[Optional[ResourceType]]) -> None:
        with self._lock:
            for rt in rt_to_reset:
                if rt is not None:
                    n_resources = len(self._resources[rt])
                    self._resources[rt] = [None] * n_resources
                else:
                    self._rooms = [None] * self.ROOMS_PER_WORLD

                if rt == ResourceType.ms_spritesheets:
                    self._dynamic_ms_data = None

            self._msfs_and_entity_data = None
            self._msfs_and_entity_data_valid = False

    def set_mappings(self, mappings: Optional[Mappings]) -> None:
        with self._lock:
            self._mappings = mappings

    def set_symbols(self, symbols: Optional[dict[ScopedName, int]]) -> None:
        with self._lock:
            self._symbols = symbols

    def set_n_entities(self, n_entities: int) -> None:
        with self._lock:
            self._n_entities = n_entities

    def add_non_resource_error(self, e: NonResourceError) -> None:
        with self._lock:
            self._errors[e.error_key] = e

    def clear_shared_input_error(self, s_type: SharedInputType) -> None:
        with self._lock:
            key: Final = ErrorKey(None, s_type)
            self._errors.pop(key, None)

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

            if isinstance(c, ResourceError):
                self._errors[c.error_key] = c
            else:
                e_key: Final = ErrorKey(c.resource_type, c.resource_id)
                self._errors.pop(e_key, None)

    def set_dyanamic_ms_data(self, ms: Union[DynamicMsSpritesheet, NonResourceError]) -> None:
        with self._lock:
            if isinstance(ms, DynamicMsSpritesheet):
                self._dynamic_ms_data = ms
                self._errors.pop(DYNAMIC_METASPRITES_ERROR_KEY, None)
            else:
                assert ms.error_key == DYNAMIC_METASPRITES_ERROR_KEY
                self._dynamic_ms_data = None
                self._errors[ms.error_key] = ms
            self._msfa_and_entity_rom_data = None
            self._msfs_and_entity_data_valid = False

    def set_msfs_and_entity_data(self, me: Optional[MsFsAndEntityOutput]) -> None:
        with self._lock:
            self._msfs_and_entity_data = me
            self._not_room_counter += 1
            if me:
                if me.error:
                    e = NonResourceError(MS_FS_AND_ENTITY_ERROR_KEY, "MsFs and entity data", me.error)
                    self._errors[e.error_key] = e
                else:
                    self._errors.pop(MS_FS_AND_ENTITY_ERROR_KEY, None)

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

    def get_dynamic_ms_data(self) -> Optional[DynamicMsSpritesheet]:
        with self._lock:
            return self._dynamic_ms_data

    def get_msfs_and_entity_data(self) -> Optional[MsFsAndEntityOutput]:
        with self._lock:
            return self._msfs_and_entity_data

    def get_resource_data(self, r_type: ResourceType, r_id: int) -> Optional[BaseResourceData]:
        with self._lock:
            return self._resources[r_type][r_id]

    def get_room_data(self, room_id: int) -> Optional[BaseResourceData]:
        with self._lock:
            return self._rooms[room_id]

    def get_errors(self) -> list[Union[ResourceError, NonResourceError]]:
        with self._lock:
            return list(self._errors.values())

    # Assumes no errors in the DataStore
    def get_all_data_for_type(self, r_type: ResourceType) -> list[EngineData]:
        with self._lock:
            return [r.data for r in self._resources[r_type]]  # type: ignore

    # Assumes no errors in the DataStore
    def get_data_for_all_rooms(self) -> list[Optional[EngineData]]:
        with self._lock:
            return [r.data if isinstance(r, ResourceData) else None for r in self._rooms]


#
# Shared Input
# ============


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
# THREAD SAFETY: Must only be edited by the ProjectCompiler classes
class SharedInput:
    def __init__(self, sym_filename: Filename) -> None:
        self.symbols_filename: Final[Filename] = sym_filename

        self.mappings: Optional[Mappings] = None
        self.entities: Optional[EntitiesJson] = None
        self.other_resources: Optional[OtherResources] = None
        self.ms_export_order: Optional[MsExportOrder] = None
        self.audio_project: Optional[AudioProject] = None
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
            case SharedInputType.AUDIO_PROJECT:
                _load("audio_project", AUDIO_PROJECT_FILENAME, load_audio_project)
            case SharedInputType.SYMBOLS:
                _load("symbols", self.symbols_filename, read_symbols_file)
            case _:
                raise RuntimeError("Unknown shared input file")


MAPPINGS_FILENAME: Final = "mappings.json"
ENTITIES_FILENAME: Final = "entities.json"
OTHER_RESOURCES_FILENAME: Final = "other-resources.json"
MS_EXPORT_ORDER_FILENAME: Final = "ms-export-order.json"
AUDIO_PROJECT_FILENAME: Final = "audio/project.terrificaudio"


SHARED_INPUT_FILENAME_MAP: Final = {
    MAPPINGS_FILENAME: SharedInputType.MAPPINGS,
    ENTITIES_FILENAME: SharedInputType.ENTITIES,
    OTHER_RESOURCES_FILENAME: SharedInputType.OTHER_RESOURCES,
    MS_EXPORT_ORDER_FILENAME: SharedInputType.MS_EXPORT_ORDER,
    AUDIO_PROJECT_FILENAME: SharedInputType.AUDIO_PROJECT,
}

SHARED_INPUT_NAMES: Final = {
    SharedInputType.MAPPINGS: "mappings JSON",
    SharedInputType.ENTITIES: "entities JSON",
    SharedInputType.OTHER_RESOURCES: "other Resources JSON",
    SharedInputType.MS_EXPORT_ORDER: "MetaSprite export order JSON",
    SharedInputType.AUDIO_PROJECT: "audio Samples JSON",
    SharedInputType.SYMBOLS: "Symbols",
}


#
# Resource Compilers
# ==================

MT_TILESET_FILE_REGEX: Final = re.compile(r"^metatiles/(\w+)(\.tsx|-.+)$")
MS_SPRITESHEET_FILE_REGEX: Final = re.compile(r"^metasprites/(\w+)/")


class BaseResourceCompiler(metaclass=ABCMeta):
    # The SharedInputType that contains the export order
    EXPORT_ORDER_SI: Optional[SharedInputType]
    # The Shared inputs that are used by the compiler
    SHARED_INPUTS: Sequence[SharedInputType]
    USES_PALETTES: bool
    USES_MT_TILESETS: bool = False

    def __init__(self, r_type: ResourceType, shared_input: SharedInput) -> None:
        # All fields in a BaseResourceCompiler MUST be final
        self.resource_type: Final = r_type
        self.rt_name: Final = r_type.name
        self._shared_input: Final = shared_input

        self.name_list: list[Name] = list()
        self.name_map: dict[Name, int] = dict()

    # Called when mappings.json is successfully loaded
    @final
    def update_name_list(self) -> None:
        self.name_list = self._get_name_list()
        self.name_map = dict((n, i) for i, n in enumerate(self.name_list))

    @final
    def n_items(self) -> int:
        return len(self.name_list)

    # Called when a shared input is successfully loaded
    def shared_input_changed(self, s_type: SharedInputType) -> None:
        pass

    @abstractmethod
    def _get_name_list(self) -> list[Name]:
        pass

    # Returns resource id if the filename is used by the compiler
    # ASSUMES: current working directory is the resources directory
    @abstractmethod
    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        pass

    @abstractmethod
    def compile_resource(self, resource_id: int) -> BaseResourceData:
        pass


class SimpleResourceCompiler(BaseResourceCompiler):
    def compile_resource(self, resource_id: int) -> BaseResourceData:
        r_name = self.name_list[resource_id]
        try:
            data = self._compile(r_name)
            return ResourceData(self.resource_type, resource_id, r_name, data)
        except Exception as e:
            return create_resource_error(self.resource_type, resource_id, r_name, e)

    @abstractmethod
    def _compile(self, r_name: Name) -> EngineData:
        pass


class OtherResourcesCompiler(SimpleResourceCompiler):
    def __init__(self, r_type: ResourceType, shared_input: SharedInput) -> None:
        super().__init__(r_type, shared_input)
        self.filename_map: dict[Filename, int] = dict()

    @final
    def shared_input_changed(self, s_type: SharedInputType) -> None:
        if s_type == SharedInputType.OTHER_RESOURCES or s_type == SharedInputType.MAPPINGS:
            if self._shared_input.other_resources:
                self.filename_map = self.build_filename_map(self._shared_input.other_resources)
            else:
                self.filename_map = dict()

    @abstractmethod
    def build_filename_map(self, other_resources: OtherResources) -> dict[Filename, int]:
        pass

    @final
    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        return self.filename_map.get(filename)


class MsSpritesheetCompiler(BaseResourceCompiler):
    def __init__(self, shared_input: SharedInput) -> None:
        super().__init__(ResourceType.ms_spritesheets, shared_input)

    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        if m := MS_SPRITESHEET_FILE_REGEX.match(filename):
            return self.name_map.get(m.group(1))
        return None

    SHARED_INPUTS = (SharedInputType.MS_EXPORT_ORDER,)
    USES_PALETTES = False

    EXPORT_ORDER_SI = SharedInputType.MAPPINGS

    def _get_name_list(self) -> list[Name]:
        assert self._shared_input.mappings
        return self._shared_input.mappings.ms_spritesheets.copy()

    def compile_resource(self, resource_id: int) -> BaseResourceData:
        assert self._shared_input.ms_export_order

        r_name = self.name_list[resource_id]
        try:
            ms_dir = os.path.join("metasprites", r_name)
            json_filename = os.path.join(ms_dir, "_metasprites.json")

            ms_input = load_metasprites_json(json_filename)

            data, msfs_entries = convert_static_spritesheet(ms_input, self._shared_input.ms_export_order, ms_dir)

            return MetaSpriteResourceData(self.resource_type, resource_id, r_name, data, msfs_entries)
        except Exception as e:
            return create_resource_error(self.resource_type, resource_id, r_name, e)


class PaletteCompiler(OtherResourcesCompiler):
    def __init__(self, shared_input: SharedInput) -> None:
        super().__init__(ResourceType.palettes, shared_input)

    def build_filename_map(self, other_resources: OtherResources) -> dict[Filename, int]:
        palettes: Final = other_resources.palettes

        d = dict()
        for i, n in enumerate(self.name_list):
            p = palettes.get(n)
            if p:
                d[p.source] = i
        return d

    SHARED_INPUTS = (SharedInputType.OTHER_RESOURCES,)
    USES_PALETTES = False
    EXPORT_ORDER_SI = SharedInputType.MAPPINGS

    def _get_name_list(self) -> list[Name]:
        assert self._shared_input.mappings
        return self._shared_input.mappings.palettes.copy()

    def compile_resource(self, resource_id: int) -> BaseResourceData:
        assert self._shared_input.other_resources

        r_name = self.name_list[resource_id]
        try:
            pin = self._shared_input.other_resources.palettes[r_name]
            data, pal = convert_palette(pin, resource_id)
            return PaletteResourceData(self.resource_type, resource_id, r_name, data, pal)
        except Exception as e:
            return create_resource_error(self.resource_type, resource_id, r_name, e)

    def _compile(self, r_name: Name) -> EngineData:
        raise NotImplementedError()


class MetaTileTilesetCompiler(SimpleResourceCompiler):
    def __init__(self, shared_input: SharedInput, palettes: dict[Name, PaletteResource]) -> None:
        super().__init__(ResourceType.mt_tilesets, shared_input)
        self.__palettes = palettes

    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        if m := MT_TILESET_FILE_REGEX.match(filename):
            return self.name_map.get(m.group(1))
        return None

    SHARED_INPUTS = (SharedInputType.MAPPINGS,)
    USES_PALETTES = True
    EXPORT_ORDER_SI = SharedInputType.MAPPINGS

    def _get_name_list(self) -> list[Name]:
        assert self._shared_input.mappings
        return self._shared_input.mappings.mt_tilesets.copy()

    def compile_resource(self, resource_id: int) -> BaseResourceData:
        assert self._shared_input.mappings

        r_name = self.name_list[resource_id]
        try:
            filename = os.path.join("metatiles/", r_name + ".tsx")
            data, tile_map = convert_mt_tileset(filename, self._shared_input.mappings, self.__palettes)

            return MtTilesetResourceData(self.resource_type, resource_id, r_name, data, tile_map)
        except Exception as e:
            return create_resource_error(self.resource_type, resource_id, r_name, e)

    def _compile(self, r_name: Name) -> EngineData:
        raise NotImplementedError()


class SecondLayerCompiler(OtherResourcesCompiler):
    def __init__(
        self, shared_input: SharedInput, palettes: dict[Name, PaletteResource], mt_tileset_tiles: dict[Name, ConstSmallTileMap]
    ) -> None:
        super().__init__(ResourceType.second_layers, shared_input)
        self.__palettes: Final = palettes
        self.__mt_tileset_tiles: Final = mt_tileset_tiles

    def build_filename_map(self, other_resources: OtherResources) -> dict[Filename, int]:
        second_layers: Final = other_resources.second_layers

        d = dict()
        for i, n in enumerate(self.name_list):
            b = second_layers.get(n)
            if b:
                d[b.source] = i
                d[b.palette] = i
        return d

    SHARED_INPUTS = (
        SharedInputType.MAPPINGS,
        SharedInputType.OTHER_RESOURCES,
    )
    USES_PALETTES = True
    USES_MT_TILESETS = True
    EXPORT_ORDER_SI = SharedInputType.MAPPINGS

    def _get_name_list(self) -> list[Name]:
        assert self._shared_input.mappings
        return self._shared_input.mappings.second_layers.copy()

    def _compile(self, r_name: Name) -> EngineData:
        assert self._shared_input.mappings
        assert self._shared_input.other_resources

        sli = self._shared_input.other_resources.second_layers[r_name]
        return convert_second_layer(sli, self.__palettes, self.__mt_tileset_tiles, self._shared_input.mappings)


class TileCompiler(OtherResourcesCompiler):
    def __init__(self, shared_input: SharedInput) -> None:
        super().__init__(ResourceType.tiles, shared_input)

    def build_filename_map(self, other_resources: OtherResources) -> dict[Filename, int]:
        tiles: Final = other_resources.tiles

        d = dict()
        for i, n in enumerate(self.name_list):
            t = tiles.get(n)
            if t:
                d[t.source] = i
        return d

    SHARED_INPUTS = (SharedInputType.OTHER_RESOURCES,)
    USES_PALETTES = False
    EXPORT_ORDER_SI = SharedInputType.MAPPINGS

    def _get_name_list(self) -> list[Name]:
        assert self._shared_input.mappings
        return self._shared_input.mappings.tiles.copy()

    def _compile(self, r_name: Name) -> EngineData:
        assert self._shared_input.other_resources

        t = self._shared_input.other_resources.tiles[r_name]
        return convert_tiles(t)


class BgImageCompiler(OtherResourcesCompiler):
    def __init__(self, shared_input: SharedInput, palettes: dict[Name, PaletteResource]) -> None:
        super().__init__(ResourceType.bg_images, shared_input)
        self.__palettes = palettes

    def build_filename_map(self, other_resources: OtherResources) -> dict[Filename, int]:
        bg_images: Final = other_resources.bg_images

        d = dict()
        for i, n in enumerate(self.name_list):
            b = bg_images.get(n)
            if b:
                d[b.source] = i
                d[b.palette] = i
        return d

    SHARED_INPUTS = (SharedInputType.OTHER_RESOURCES,)
    USES_PALETTES = True
    EXPORT_ORDER_SI = SharedInputType.MAPPINGS

    def _get_name_list(self) -> list[Name]:
        assert self._shared_input.mappings
        return self._shared_input.mappings.bg_images.copy()

    def _compile(self, r_name: Name) -> EngineData:
        assert self._shared_input.other_resources

        bi = self._shared_input.other_resources.bg_images[r_name]
        return convert_bg_image(bi, self.__palettes)


class SongCompiler(SimpleResourceCompiler):
    def __init__(self, shared_input: SharedInput) -> None:
        super().__init__(ResourceType.songs, shared_input)

        self.compiler: Optional[AudioCompiler] = None

        # Map of filenames to resource index
        self._file_map: Final[dict[Filename, int]] = dict()

    @final
    def shared_input_changed(self, s_type: SharedInputType) -> None:
        if s_type == SharedInputType.MAPPINGS:
            if self._shared_input.mappings:
                if self.compiler is None:
                    self.compiler = AudioCompiler(self._shared_input.mappings, AUDIO_PROJECT_FILENAME)
            else:
                self.compiler = None

        if s_type == SharedInputType.AUDIO_PROJECT or s_type == SharedInputType.MAPPINGS:
            audio_project = self._shared_input.audio_project

            self._file_map.clear()

            if audio_project:
                # Map sound effects to 0 (common_audio_data)
                self._file_map[audio_project.sound_effect_file] = 0

                # Map sample sources to 0 (common_audio_data)
                for filename in audio_project.instrument_sources:
                    self._file_map[filename] = 0

                for s in audio_project.songs.values():
                    i = self.name_map.get(s.name)
                    if i:
                        self._file_map[s.source] = i

    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        return self._file_map.get(filename)

    SHARED_INPUTS = (SharedInputType.MAPPINGS, SharedInputType.AUDIO_PROJECT)
    USES_PALETTES = False
    EXPORT_ORDER_SI = SharedInputType.AUDIO_PROJECT

    def _get_name_list(self) -> list[Name]:
        assert self._shared_input.audio_project
        return [COMMON_AUDIO_DATA_RESOURCE_NAME] + list(self._shared_input.audio_project.songs)

    def _compile(self, r_name: Name) -> EngineData:
        assert self.compiler
        assert self._shared_input.audio_project

        if r_name == COMMON_AUDIO_DATA_RESOURCE_NAME:
            if self.name_list[0] != COMMON_AUDIO_DATA_RESOURCE_NAME:
                raise RuntimeError(f"The first entry in the exported song name list MUST BE `{COMMON_AUDIO_DATA_RESOURCE_NAME}`")
            return self.compiler.compile_common_audio_data()
        else:
            if self.name_list[0] == r_name:
                raise RuntimeError(f"The first entry in the song resource list MUST BE `{COMMON_AUDIO_DATA_RESOURCE_NAME}`")
            return self.compiler.compile_song(r_name)


class RoomCompiler:
    def __init__(self, shared_input: SharedInput) -> None:
        self._shared_input: Final = shared_input
        self.resource_type: Final = None
        self._room_dependencies: Optional[RoomDependencies] = None

    SHARED_INPUTS = (SharedInputType.MAPPINGS, SharedInputType.ENTITIES, SharedInputType.OTHER_RESOURCES)
    USES_PALETTES = False
    EXPORT_ORDER_SI = None

    def shared_input_changed(self, s_type: SharedInputType) -> None:
        if s_type == SharedInputType.MAPPINGS or s_type == SharedInputType.OTHER_RESOURCES:
            self._room_dependencies = None
            if self._shared_input.mappings and self._shared_input.other_resources:
                # ::TODO get second-layers from dungeon::
                sl = next(iter(self._shared_input.other_resources.second_layers.values()))

                if sl.callback:
                    sl_callback = self._shared_input.mappings.sl_callbacks.get(sl.callback)
                    if sl_callback is None:
                        raise RuntimeError(f"Unknown second-layer callback: {sl.callback}")
                else:
                    sl_callback = None

                self._room_dependencies = RoomDependencies(sl, sl_callback)

    def compile_room(self, filename: Filename) -> BaseResourceData:
        assert self._shared_input.mappings
        assert self._shared_input.entities

        room_id = -1
        basename = os.path.basename(filename)
        name = os.path.splitext(basename)[0]

        try:
            if self._room_dependencies is None:
                raise RuntimeError("Dependency error")

            room_id = extract_room_id(basename)
            filename = os.path.join("rooms", basename)
            data = compile_room(filename, self._room_dependencies, self._shared_input.entities, self._shared_input.mappings)

            return ResourceData(None, room_id, name, data)
        except Exception as e:
            return create_resource_error(None, room_id, name, e)


class DynamicMetaspriteCompiler:
    def __init__(self, shared_input: SharedInput) -> None:
        self._shared_input: Final = shared_input
        self.ms_dir: Final = "dynamic-metasprites"
        self.ms_dir_slash: Final = self.ms_dir + os.path.sep
        self.json_filename: Final = os.path.join(self.ms_dir, "_metasprites.json")

    def test_filename_is_resource(self, filename: Filename) -> bool:
        return filename.startswith(self.ms_dir_slash)

    def compile(self) -> Union[DynamicMsSpritesheet, NonResourceError]:
        assert self._shared_input.mappings
        assert self._shared_input.ms_export_order

        try:
            ms_input = load_metasprites_json(self.json_filename)
            return convert_dynamic_spritesheet(
                ms_input,
                self._shared_input.ms_export_order,
                self.ms_dir,
                self._shared_input.mappings.memory_map.mode,
            )
        except Exception as e:
            return NonResourceError(DYNAMIC_METASPRITES_ERROR_KEY, "Dynamic Metasprites", e)


def _build_st_rt_map(
    *compilers: Union[BaseResourceCompiler, RoomCompiler]
) -> dict[SharedInputType, frozenset[Optional[ResourceType]]]:
    out = dict()

    for s_type in SharedInputType:
        s = list()
        for rc in compilers:
            if s_type in rc.SHARED_INPUTS:
                s.append(rc.resource_type)
            if s_type == rc.EXPORT_ORDER_SI:
                s.append(rc.resource_type)
        out[s_type] = frozenset(s)

    return out


# THREAD SAFETY: Must only exist on a single thread
class ProjectCompiler:
    # A set containing all resource lists
    ALL_RESOURCE_LISTS: Final = frozenset(ResourceType) | frozenset([None])

    def __init__(
        self,
        data_store: DataStore,
        sym_filename: Filename,
        n_processes: Optional[int],
        err_handler: Callable[[Union[ResourceError, Exception, str]], None],
        message_handler: Callable[[str], None],
    ) -> None:
        self.data_store: Final = data_store

        self.__sym_filename: Final = sym_filename
        self.__shared_input: Final = SharedInput(sym_filename)
        self.__n_processes: Final = n_processes

        self.log_message: Final = message_handler
        self.log_error: Final = err_handler

        self.__palettes: Final[dict[Name, PaletteResource]] = dict()
        self.__mt_tileset_tiles: Final[dict[Name, ConstSmallTileMap]] = dict()

        self.__resource_compilers: Final = (
            PaletteCompiler(self.__shared_input),
            MetaTileTilesetCompiler(self.__shared_input, self.__palettes),
            SecondLayerCompiler(self.__shared_input, self.__palettes, self.__mt_tileset_tiles),
            MsSpritesheetCompiler(self.__shared_input),
            TileCompiler(self.__shared_input),
            BgImageCompiler(self.__shared_input, self.__palettes),
            SongCompiler(self.__shared_input),
        )
        self.__room_compiler: Final = RoomCompiler(self.__shared_input)
        self.__dynamic_ms_compiler: Final = DynamicMetaspriteCompiler(self.__shared_input)

        assert len(self.__resource_compilers) == len(ResourceType)

        self.resource_dependencies: Final[dict[Optional[ResourceType], frozenset[Optional[ResourceType]]]] = {
            ResourceType.palettes: frozenset(c.resource_type for c in self.__resource_compilers if c.USES_PALETTES),
            ResourceType.mt_tilesets: frozenset(c.resource_type for c in self.__resource_compilers if c.USES_MT_TILESETS),
        }

        self.ST_RT_MAP: Final = _build_st_rt_map(*self.__resource_compilers, self.__room_compiler)

        # Mark all shared_inputs invalid
        self.__shared_inputs_with_errors: Final[set[SharedInputType]] = set(SharedInputType)

        # This field serves two purposes:
        #  * Keeps track of the resource lists to compile when a shared input has changed.
        #    (If a shared input has an error, the compilation has to be delayed until the errors are fixed)
        #  * Keeps track of which compilers cannot be used (as they rely on a shared input that is invalid)
        #
        # Mark all compilers as unusable.
        # All compilers need a recompile when the shared inputs are loaded successfully.
        self.__res_lists_waiting_on_shared_input: Final[set[Optional[ResourceType]]] = set(self.ALL_RESOURCE_LISTS)

    def is_shared_input_valid(self) -> bool:
        return not self.__shared_inputs_with_errors

    def compile_everything(self) -> None:
        # Mark all shared inputs as invalid.
        # Prevents multiple calls to `__compile_resource_lists()`
        self.__shared_inputs_with_errors.update(SharedInputType)
        self.__res_lists_waiting_on_shared_input.update(self.ALL_RESOURCE_LISTS)

        # Load shared inputs
        # After all shared inputs are loaded, then it will trigger a full recompile
        for s_type in SharedInputType:
            self._shared_input_file_changed(s_type)

    def file_changed(self, filename: Filename) -> Optional[BaseResourceData | DynamicMetaspriteToken | SharedInputType]:
        if filename == self.__shared_input.symbols_filename:
            s_type: Optional[SharedInputType] = SharedInputType.SYMBOLS
        else:
            s_type = SHARED_INPUT_FILENAME_MAP.get(filename)

        if s_type:
            self._shared_input_file_changed(s_type)
            return s_type
        else:
            co = None
            if filename.endswith(".tmx"):
                # Test if the room can be compiled (ie, all required shared inputs are valid)
                if None not in self.__res_lists_waiting_on_shared_input:
                    self.log_message(f"Compiling room {filename}")
                    co = self.__room_compiler.compile_room(filename)
                else:
                    self._log_cannot_compile_si_error(filename)

            elif self.__dynamic_ms_compiler.test_filename_is_resource(filename):
                self.__compile_dynamic_metasprites()
                return DynamicMetaspriteToken()

            else:
                for c in self.__resource_compilers:
                    r_id = c.test_filename_is_resource(filename)

                    if r_id is not None:
                        # Test if the compiler can compile the resource (ie, all required shared inputs are valid)
                        if c.resource_type not in self.__res_lists_waiting_on_shared_input:
                            self.log_message(f"Compiling { c.rt_name }[{ r_id }] { c.name_list[r_id] }")
                            co = c.compile_resource(r_id)
                        else:
                            self._log_cannot_compile_si_error(f"{ c.rt_name }[{ r_id }] { c.name_list[r_id] }")
                        break
            if co:
                self.data_store.insert_data(co)
                if isinstance(co, ResourceError):
                    self.log_error(co)
                if co.resource_type == ResourceType.palettes:
                    self._palette_changed(co)
            return co

    # MUST NOT be called by `self.__compile_resource_lists()`
    def _palette_changed(self, co: BaseResourceData) -> None:
        assert co.resource_type == ResourceType.palettes

        if isinstance(co, PaletteResourceData):
            self.__palettes[co.resource_name] = co.palette
        else:
            self.__palettes.pop(co.resource_name)

        # Recompile the resource that uses palettes
        # ::TODO only recompile the resources that use the changed palette::
        self.__compile_resource_lists(set(self.resource_dependencies[ResourceType.palettes]))

    # MUST NOT be called by `self.__compile_resource_lists()`
    def _mt_tileset_changed(self, co: BaseResourceData) -> None:
        assert co.resource_type == ResourceType.mt_tilesets

        if isinstance(co, MtTilesetResourceData):
            self.__mt_tileset_tiles[co.resource_name] = co.tile_map
        else:
            self.__mt_tileset_tiles.pop(co.resource_name)

        # Recompile the resources that depend on mt_tilesets
        # ::TODO only recompile the resources that use the changed mt_tileset::
        self.__compile_resource_lists(set(self.resource_dependencies[ResourceType.mt_tilesets]))

    def _log_cannot_compile_si_error(self, res_name: str) -> None:
        si_list = [SHARED_INPUT_NAMES[s_type] for s_type in self.__shared_inputs_with_errors]
        self.log_error(f"Cannot compile {res_name}: Errors in shared inputs ({ ', '.join(si_list) })")

    def _shared_input_file_changed(self, s_type: SharedInputType) -> None:
        to_recompile: Final = self.ST_RT_MAP[s_type]

        # Remove resources that depend on the shared input from `data_store`.
        if s_type != SharedInputType.MAPPINGS:
            self.data_store.reset_resources(to_recompile)

        # Mark all compilers that rely on the shared input for later recompilation
        self.__res_lists_waiting_on_shared_input.update(to_recompile)

        self.log_message(f"Loading { SHARED_INPUT_NAMES[s_type] } file")
        try:
            self.__shared_input.load(s_type)
            self.__shared_inputs_with_errors.discard(s_type)
            self.data_store.clear_shared_input_error(s_type)
        except Exception as e:
            self.log_error(e)
            self.__shared_inputs_with_errors.add(s_type)

            error = NonResourceError(ErrorKey(None, s_type), SHARED_INPUT_NAMES[s_type], e)
            self.data_store.add_non_resource_error(error)
            return

        for c in self.__resource_compilers:
            if s_type == c.EXPORT_ORDER_SI:
                c.update_name_list()
                self.data_store.reset_data(c.resource_type, c.n_items())

        if s_type == SharedInputType.MAPPINGS:
            self.data_store.set_mappings(self.__shared_input.mappings)
        elif s_type == SharedInputType.SYMBOLS:
            self.data_store.set_symbols(self.__shared_input.symbols)
        elif s_type == SharedInputType.ENTITIES:
            if self.__shared_input.entities:
                n_entities = len(self.__shared_input.entities.entities)
                self.data_store.set_n_entities(n_entities)

        # Must be called after `c.update_name_list()`
        for c in self.__resource_compilers:
            try:
                c.shared_input_changed(s_type)
            except Exception as e:
                self.log_error(e)
                self.__shared_inputs_with_errors.add(s_type)

        try:
            self.__room_compiler.shared_input_changed(s_type)
        except Exception as e:
            self.log_error(e)
            self.__shared_inputs_with_errors.add(s_type)

        if not self.__shared_inputs_with_errors:
            # No shared inputs have errors.
            # Compile all resource lists that need recompiling
            self.__compile_resource_lists(self.__res_lists_waiting_on_shared_input)
            self.__res_lists_waiting_on_shared_input.clear()

    def __compile_dynamic_metasprites(self) -> None:
        self.log_message("Compiling dynamic metasprites")
        d = self.__dynamic_ms_compiler.compile()
        if isinstance(d, NonResourceError):
            self.log_error(d.error)
        self.data_store.set_dyanamic_ms_data(d)

    def __compile_msfs_and_entity_data(self) -> None:
        if self.__shared_input.mappings is None or self.__shared_input.entities is None or self.__shared_input.symbols is None:
            self._log_cannot_compile_si_error("MsFs and Entity Data")
            self.data_store.set_msfs_and_entity_data(None)
            return

        dynamic_ms_data: Final = self.data_store.get_dynamic_ms_data()
        optional_msfs_lists: Final = self.data_store.get_msfs_lists()

        if dynamic_ms_data is None or any(entry_list is None for entry_list in optional_msfs_lists):
            e = ValueError("Cannot compile MsFs data.  There is an error in a MS Spritesheet.")
            self.log_error(e)
            data = MsFsAndEntityOutput(error=e)
        else:
            try:
                static_msfs_lists = cast(list[list[MsFsEntry]], optional_msfs_lists)
                rom_data, ms_map = build_ms_fs_data(
                    dynamic_ms_data, static_msfs_lists, self.__shared_input.symbols, self.__shared_input.mappings.memory_map.mode
                )
                ms_fs_data = bytes(rom_data.data())
                entity_rom_data = create_entity_rom_data(self.__shared_input.entities, self.__shared_input.symbols, ms_map)

                data = MsFsAndEntityOutput(ms_fs_data, entity_rom_data)
            except Exception as e:
                self.log_error(e)
                data = MsFsAndEntityOutput(error=e)

        self.data_store.set_msfs_and_entity_data(data)

    def __compile_resource_lists(self, to_recompile: Set[Optional[ResourceType]]) -> None:
        # Uses multiprocessing to speed up the compilation

        if self.__shared_inputs_with_errors:
            self._log_cannot_compile_si_error("resources")
            return

        for rt, deps in self.resource_dependencies.items():
            if rt in to_recompile:
                to_recompile |= deps

        if to_recompile == self.ALL_RESOURCE_LISTS:
            self.log_message("Compiling all resources")
        else:
            for rt in to_recompile:
                if rt is not None:
                    self.log_message(f"Compiling all {rt.name}")
                else:
                    self.log_message("Compiling all rooms")

        with multiprocessing.Pool(processes=self.__n_processes) as mp:
            co_lists: list[Iterable[BaseResourceData]] = list()

            if ResourceType.palettes in to_recompile:
                to_recompile.remove(ResourceType.palettes)

                c = self.__resource_compilers[ResourceType.palettes]
                pal_l = list(mp.imap_unordered(c.compile_resource, range(len(c.name_list))))

                co_lists.append(pal_l)

                self.__palettes.clear()
                self.__palettes.update((co.resource_name, co.palette) for co in pal_l if isinstance(co, PaletteResourceData))

            if ResourceType.mt_tilesets in to_recompile:
                to_recompile.remove(ResourceType.mt_tilesets)

                c = self.__resource_compilers[ResourceType.mt_tilesets]
                mt_l = list(mp.imap_unordered(c.compile_resource, range(len(c.name_list))))

                co_lists.append(mt_l)

                self.__mt_tileset_tiles.clear()
                self.__mt_tileset_tiles.update((co.resource_name, co.tile_map) for co in mt_l if isinstance(co, MtTilesetResourceData))

            for rt in to_recompile:
                if rt is not None:
                    c = self.__resource_compilers[rt]
                    co_lists.append(mp.imap_unordered(c.compile_resource, range(len(c.name_list))))
                else:
                    room_filenames = get_list_of_tmx_files("rooms")
                    co_lists.append(mp.imap_unordered(self.__room_compiler.compile_room, room_filenames))

            for co_l in co_lists:
                for co in co_l:
                    self.data_store.insert_data(co)
                    if isinstance(co, ResourceError):
                        self.log_error(co)

        if ResourceType.ms_spritesheets in to_recompile:
            self.__compile_dynamic_metasprites()

        self.__compile_msfs_and_entity_data()
