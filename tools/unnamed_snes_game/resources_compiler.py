# Unnamed SNES Game resource compiler
#
# Copyright (c) 2023, Marcus Rowe <undisbeliever@gmail.com>.
# Distributed under The MIT License, see the LICENSE file for more details.

import re
import os
import os.path
import multiprocessing
from abc import abstractmethod, ABCMeta
from enum import unique, auto, Enum
from typing import cast, final, Any, Callable, Final, Iterable, Optional, Sequence, Set, Union

from .common import ResourceType
from .entity_data import create_entity_rom_data
from .mt_tileset import convert_mt_tileset
from .second_layers import convert_second_layer
from .palette import convert_palette, PaletteResource
from .metasprite import convert_static_spritesheet, convert_dynamic_spritesheet, build_ms_fs_data, MsFsEntry, DynamicMsSpritesheet
from .dungeons import compile_dungeon_header, combine_dungeon_and_room_data
from .rooms import extract_room_position, compile_room, RoomDependencies
from .snes import ConstSmallTileMap
from .other_resources import convert_tiles, convert_bg_image
from .audio import AudioCompiler, COMMON_AUDIO_DATA_RESOURCE_NAME
from .data_store import (
    EngineData,
    BaseResourceData,
    ResourceData,
    MetaSpriteResourceData,
    PaletteResourceData,
    MtTilesetResourceData,
    DungeonResourceData,
    RoomData,
    MsFsAndEntityOutput,
    NonResourceError,
    ResourceError,
    ErrorKey,
    DYNAMIC_METASPRITES_ERROR_KEY,
    DynamicMetaspriteToken,
    DataStore,
    create_resource_error,
)

from .json_formats import load_mappings_json, load_entities_json, load_ms_export_order_json, load_other_resources_json
from .json_formats import load_metasprites_json, load_audio_project, load_dungeons_json
from .json_formats import Name, ScopedName, Filename, Mappings, EntitiesJson
from .json_formats import MsExportOrder, OtherResources, DungeonsJson, AudioProject


@unique
class SharedInputType(Enum):
    MAPPINGS = auto()
    ENTITIES = auto()
    OTHER_RESOURCES = auto()
    MS_EXPORT_ORDER = auto()
    DUNGEONS = auto()
    AUDIO_PROJECT = auto()
    SYMBOLS = auto()

    def rebuild_required(self) -> bool:
        return self in REBUILD_REQUIRED_MAPPINS


def find_all_tmx_files(dungeons: DungeonsJson) -> list[str]:
    tmx_files = list()

    for d in dungeons.dungeons.values():
        for e in os.scandir(d.path):
            if e.is_file() and e.name.startswith("_") is False:
                ext = os.path.splitext(e.name)[1]
                if ext == ".tmx":
                    tmx_files.append(e.path)

    tmx_files.sort()

    return tmx_files


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
        self.dungeons: Optional[DungeonsJson] = None
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
            case SharedInputType.DUNGEONS:
                _load("dungeons", DUNGEONS_JSON_FILENAME, load_dungeons_json)
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
DUNGEONS_JSON_FILENAME: Final = "dungeons.json"
AUDIO_PROJECT_FILENAME: Final = "audio/project.terrificaudio"


SHARED_INPUT_FILENAME_MAP: Final = {
    MAPPINGS_FILENAME: SharedInputType.MAPPINGS,
    ENTITIES_FILENAME: SharedInputType.ENTITIES,
    OTHER_RESOURCES_FILENAME: SharedInputType.OTHER_RESOURCES,
    MS_EXPORT_ORDER_FILENAME: SharedInputType.MS_EXPORT_ORDER,
    DUNGEONS_JSON_FILENAME: SharedInputType.DUNGEONS,
    AUDIO_PROJECT_FILENAME: SharedInputType.AUDIO_PROJECT,
}

SHARED_INPUT_NAMES: Final = {
    SharedInputType.MAPPINGS: "mappings JSON",
    SharedInputType.ENTITIES: "entities JSON",
    SharedInputType.OTHER_RESOURCES: "other Resources JSON",
    SharedInputType.MS_EXPORT_ORDER: "MetaSprite export order JSON",
    SharedInputType.DUNGEONS: "dungeons JSON",
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
        super().__init__(ResourceType.audio_data, shared_input)

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


class DungeonCompiler(SimpleResourceCompiler):
    def __init__(self, shared_input: SharedInput) -> None:
        super().__init__(ResourceType.dungeons, shared_input)

    SHARED_INPUTS = (
        SharedInputType.DUNGEONS,
        SharedInputType.MAPPINGS,
        SharedInputType.OTHER_RESOURCES,
        SharedInputType.AUDIO_PROJECT,
    )
    USES_PALETTES = False
    EXPORT_ORDER_SI = SharedInputType.DUNGEONS

    def _get_name_list(self) -> list[Name]:
        assert self._shared_input.dungeons
        return list(self._shared_input.dungeons.dungeons.keys())

    def test_filename_is_resource(self, filename: Filename) -> Optional[int]:
        return None

    def compile_resource(self, resource_id: int) -> BaseResourceData:
        assert self._shared_input.dungeons
        assert self._shared_input.mappings
        assert self._shared_input.other_resources
        assert self._shared_input.audio_project

        r_name = self.name_list[resource_id]
        try:
            dungeon = self._shared_input.dungeons.dungeons[r_name]
            header, room_dependencies = compile_dungeon_header(
                dungeon, self._shared_input.mappings, self._shared_input.other_resources, self._shared_input.audio_project
            )
            return DungeonResourceData(
                self.resource_type,
                resource_id,
                r_name,
                header,
                room_dependencies,
                includes_room_data=False,
            )
        except Exception as e:
            return create_resource_error(self.resource_type, resource_id, r_name, e)

    def _compile(self, r_name: Name) -> EngineData:
        raise NotImplementedError()


class RoomCompiler:
    def __init__(self, shared_input: SharedInput) -> None:
        self._shared_input: Final = shared_input
        self.resource_type: Final = None
        self._dungeon_paths: dict[str, int] = dict()
        self._room_dependencies: list[Optional[RoomDependencies]] = list()

    SHARED_INPUTS = (SharedInputType.MAPPINGS, SharedInputType.ENTITIES, SharedInputType.DUNGEONS)
    EXPORT_ORDER_SI = None

    def shared_input_changed(self, s_type: SharedInputType) -> None:
        if s_type == SharedInputType.DUNGEONS:
            self._dungeon_paths.clear()
            if self._shared_input.dungeons:
                for d in self._shared_input.dungeons.dungeons.values():
                    self._dungeon_paths[d.path] = d.id

    def build_room_dependencies(self, data_store: DataStore) -> None:
        assert self._shared_input.dungeons

        dungeons = data_store.get_resource_data_list(ResourceType.dungeons)
        self._room_dependencies = [d.room_dependencies if isinstance(d, DungeonResourceData) else None for d in dungeons]

    def compile_room(self, filename: Filename) -> BaseResourceData:
        assert self._shared_input.mappings
        assert self._shared_input.entities

        try:
            room_id = -1
            dirname, basename = os.path.split(filename)
            name = os.path.splitext(basename)[0]

            dungeon_id = self._dungeon_paths.get(dirname)
            if dungeon_id is None:
                raise RuntimeError(f"Unknown dungeon path: {dirname}")

            position = extract_room_position(basename)
            room_id = (dungeon_id << 16) | ((position[1] & 0xFF) << 8) | (position[0] & 0xFF)

            deps = self._room_dependencies[dungeon_id]
            if deps is None:
                raise RuntimeError("Dependency error")

            data = compile_room(filename, deps, self._shared_input.entities, self._shared_input.mappings)

            return RoomData(None, room_id, name, data, dungeon_id=dungeon_id, position=position)
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
            DungeonCompiler(self.__shared_input),
        )
        self.__room_compiler: Final = RoomCompiler(self.__shared_input)
        self.__dynamic_ms_compiler: Final = DynamicMetaspriteCompiler(self.__shared_input)

        assert len(self.__resource_compilers) == len(ResourceType)

        self.resource_dependencies: Final[dict[Optional[ResourceType], frozenset[Optional[ResourceType]]]] = {
            ResourceType.palettes: frozenset(c.resource_type for c in self.__resource_compilers if c.USES_PALETTES),
            ResourceType.mt_tilesets: frozenset(c.resource_type for c in self.__resource_compilers if c.USES_MT_TILESETS),
            ResourceType.dungeons: frozenset([None]),
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

            for co_l in co_lists:
                for co in co_l:
                    self.data_store.insert_data(co)
                    if isinstance(co, ResourceError):
                        self.log_error(co)

            if None in to_recompile:
                assert self.__shared_input.dungeons

                self.__room_compiler.build_room_dependencies(self.data_store)

                room_filenames = find_all_tmx_files(self.__shared_input.dungeons)
                for co in mp.imap_unordered(self.__room_compiler.compile_room, room_filenames):
                    self.data_store.insert_data(co)
                    if isinstance(co, ResourceError):
                        self.log_error(co)

        if ResourceType.ms_spritesheets in to_recompile:
            self.__compile_dynamic_metasprites()

        self.__compile_msfs_and_entity_data()


def append_room_data_to_dungeons(data_store: DataStore, err_handler: Callable[[Union[ResourceError, Exception, str]], None]) -> None:
    dungeons = data_store.get_resource_data_list(ResourceType.dungeons)

    for d_id, d in enumerate(dungeons):
        if isinstance(d, DungeonResourceData):
            assert d.includes_room_data is False
            try:
                rooms = data_store.get_dungeon_rooms(d_id)
                data = combine_dungeon_and_room_data(d.resource_name, d.data, rooms)

                data_store.insert_data(
                    ResourceData(
                        resource_type=d.resource_type,
                        resource_id=d.resource_id,
                        resource_name=d.resource_name,
                        data=data,
                    )
                )

            except Exception as e:
                err_handler(e)
                data_store.insert_data(create_resource_error(d.resource_type, d.resource_id, d.resource_name, e))
