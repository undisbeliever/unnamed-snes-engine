# Unnamed SNES Game data store
#
# Copyright (c) 2023, Marcus Rowe <undisbeliever@gmail.com>.
# Distributed under The MIT License, see the LICENSE file for more details.

import struct
import threading
from collections import OrderedDict
from dataclasses import dataclass
from typing import Final, Iterable, NamedTuple, Optional, Union

from .enums import ResourceType
from .snes import ConstSmallTileMap
from .json_formats import Name, ScopedName, Mappings

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .resources_compiler import SharedInputType
    from .palette import PaletteResource
    from .metasprite import MsFsEntry, DynamicMsSpritesheet
    from .ms_palettes import MsPalette
    from .dungeons import DungeonIntermediate


class FixedSizedData:
    "Engine Data with a fixed size"

    def __init__(self, data: bytes):
        if len(data) > 0xFFFF:
            raise RuntimeError("data is too large")
        self._data: Final = data

    def size(self) -> int:
        return len(self._data)

    def data(self) -> bytes:
        return self._data


# ::TODO compress dynamic data::
class DynamicSizedData:
    "Engine data with an unknown size"

    def __init__(self, data: bytes):
        if len(data) > 0xFFFF:
            raise RuntimeError("data is too large")
        self._data: Final = struct.pack("<H", len(data)) + data

    def size(self) -> int:
        return len(self._data)

    def data(self) -> bytes:
        return self._data


class EngineData:
    def __init__(self, ram_data: Optional[Union[FixedSizedData, DynamicSizedData]], ppu_data: Optional[DynamicSizedData]):
        self.ram_data: Final = ram_data
        self.ppu_data: Final = ppu_data

        if self.size() > 0xFFFF:
            raise RuntimeError("data is too large")

    def size(self) -> int:
        size = 0
        if self.ram_data is not None:
            size += self.ram_data.size()
        if self.ppu_data is not None:
            size += self.ppu_data.size()
        return size

    def to_rou2s_data(self) -> bytes:
        if self.ram_data and self.ppu_data:
            return self.ram_data.data() + self.ppu_data.data()
        elif self.ram_data:
            return self.ram_data.data()
        elif self.ppu_data:
            return self.ppu_data.data()
        else:
            raise RuntimeError("No data")

    def ram_and_ppu_size(self) -> tuple[int, int]:
        if self.ram_data is not None and self.ppu_data is not None:
            return self.ram_data.size(), self.ppu_data.size()
        elif self.ram_data is not None:
            return self.ram_data.size(), 0
        elif self.ppu_data is not None:
            return 0, self.ppu_data.size()
        else:
            raise RuntimeError("No data")


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
class MsPaletteResourceData(ResourceData):
    palette: "MsPalette"


@dataclass(frozen=True)
class MetaSpriteResourceData(ResourceData):
    msfs_entries: list["MsFsEntry"]


@dataclass(frozen=True)
class PaletteResourceData(ResourceData):
    palette: "PaletteResource"


@dataclass(frozen=True)
class MtTilesetResourceData(ResourceData):
    tile_map: "ConstSmallTileMap"


@dataclass(frozen=True)
class SecondLayerResourceData(ResourceData):
    n_tiles: int


@dataclass(frozen=True)
class DungeonResourceData(ResourceData):
    header: "DungeonIntermediate"
    includes_room_data: bool


@dataclass(frozen=True)
class RoomData(ResourceData):
    dungeon_id: int
    position: tuple[int, int]


class MsFsAndEntityOutput(NamedTuple):
    msfs_data: Optional[bytes] = None
    entity_rom_data: Optional[bytes] = None
    error: Optional[Exception] = None


# Used to track errors in the DataStore
class ErrorKey(NamedTuple):
    r_type: Optional[ResourceType]
    r_id: Union[int, "SharedInputType"]


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
            self._rooms: list[dict[tuple[int, int], RoomData]] = list()

            self._resource_name_map: list[dict[Name, Optional[BaseResourceData]]] = [dict() for rt in ResourceType]

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

            if r_type == ResourceType.dungeons:
                self._rooms = [dict() for i in range(n_items)]

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
                    self._resource_name_map[rt].clear()
                else:
                    for r in self._rooms:
                        r.clear()

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

    def clear_shared_input_error(self, s_type: "SharedInputType") -> None:
        with self._lock:
            key: Final = ErrorKey(None, s_type)
            self._errors.pop(key, None)

    def insert_data(self, c: BaseResourceData) -> None:
        with self._lock:
            if isinstance(c, RoomData):
                self._rooms[c.dungeon_id][c.position] = c
            elif c.resource_type is not None:
                self._resources[c.resource_type][c.resource_id] = c
                self._not_room_counter += 1

            if isinstance(c, MetaSpriteResourceData):
                assert c.resource_type == ResourceType.ms_spritesheets
                self._msfs_lists[c.resource_id] = c.msfs_entries
                self._msfa_and_entity_rom_data = None
                self._msfs_and_entity_data_valid = False

            if c.resource_type is not None:
                self._resource_name_map[c.resource_type][c.resource_name] = c

            if isinstance(c, ResourceError):
                self._errors[c.error_key] = c
            else:
                e_key: Final = ErrorKey(c.resource_type, c.resource_id)
                self._errors.pop(e_key, None)

    def set_dyanamic_ms_data(self, ms: Union["DynamicMsSpritesheet", NonResourceError]) -> None:
        with self._lock:
            if not isinstance(ms, NonResourceError):
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

    def get_msfs_lists(self) -> list[Optional[list["MsFsEntry"]]]:
        with self._lock:
            return self._msfs_lists

    def is_msfs_and_entity_data_valid(self) -> bool:
        with self._lock:
            return self._msfs_and_entity_data_valid

    def mark_msfs_and_entity_data_valid(self) -> None:
        with self._lock:
            self._msfs_and_entity_data_valid = True

    def get_dynamic_ms_data(self) -> Optional["DynamicMsSpritesheet"]:
        with self._lock:
            return self._dynamic_ms_data

    def get_msfs_and_entity_data(self) -> Optional[MsFsAndEntityOutput]:
        with self._lock:
            return self._msfs_and_entity_data

    def get_resource_data(self, r_type: ResourceType, r_id: int) -> Optional[BaseResourceData]:
        with self._lock:
            return self._resources[r_type][r_id]

    def get_room_data(self, dungeon_id: int, room_x: int, room_y: int) -> Optional[BaseResourceData]:
        with self._lock:
            return self._rooms[dungeon_id].get((room_x, room_y))

    def get_dungeon_rooms(self, dungeon_id: int) -> dict[tuple[int, int], RoomData]:
        with self._lock:
            return self._rooms[dungeon_id].copy()

    def get_errors(self) -> list[Union[ResourceError, NonResourceError]]:
        with self._lock:
            return list(self._errors.values())

    # Assumes no errors in the DataStore
    def get_n_resources(self) -> int:
        with self._lock:
            return sum(len(self._resources[r_type]) for r_type in ResourceType)

    # Assumes no errors in the DataStore
    def get_resource_data_list(self, r_type: ResourceType) -> list[Optional[BaseResourceData]]:
        with self._lock:
            return self._resources[r_type].copy()

    # Assumes no errors in the DataStore
    def get_all_data_for_type(self, r_type: ResourceType) -> list[EngineData]:
        with self._lock:
            return [r.data for r in self._resources[r_type]]  # type: ignore

    # Assumes no errors in the DataStore
    def get_data_for_all_rooms(self) -> list[Optional[EngineData]]:
        with self._lock:
            return [r.data if isinstance(r, ResourceData) else None for r in self._rooms]

    def get_palette(self, name: Name) -> Optional[PaletteResourceData]:
        with self._lock:
            co = self._resource_name_map[ResourceType.palettes].get(name)
            if isinstance(co, PaletteResourceData):
                return co
            else:
                return None

    def get_ms_palette(self, name: Name) -> Optional[MsPaletteResourceData]:
        with self._lock:
            co = self._resource_name_map[ResourceType.ms_palettes].get(name)
            if isinstance(co, MsPaletteResourceData):
                return co
            else:
                return None

    def get_mt_tileset(self, name: Name) -> Optional[MtTilesetResourceData]:
        with self._lock:
            co = self._resource_name_map[ResourceType.mt_tilesets].get(name)
            if isinstance(co, MtTilesetResourceData):
                return co
            else:
                return None

    def get_second_layer(self, name: Name) -> Optional[SecondLayerResourceData]:
        with self._lock:
            co = self._resource_name_map[ResourceType.second_layers].get(name)
            if isinstance(co, SecondLayerResourceData):
                return co
            else:
                return None
