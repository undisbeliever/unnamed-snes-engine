# -*- coding: utf-8 -*-
# vim: set fenc=utf-8 ai ts=4 sw=4 sts=4 et:


import re
import json
import os.path
from collections import OrderedDict
from typing import Any, Callable, Final, Generator, Literal, NamedTuple, NoReturn, Optional, Type, TypeVar, Union

from .memory_map import MemoryMapMode
from .errors import FileError


Name = str
ScopedName = str
RoomName = str

Filename = str

MAX_RESOURCE_ITEMS: Final = 254


class JsonError(FileError):
    pass


class Position(NamedTuple):
    x: int
    y: int


class _Helper:
    """
    A helper class to help parse the output of `json.load()` into structured data.

    This class will also recursively track the position within the `json.load()` output to improve error messages.
    """

    # _Helper class or subclass of _Helper
    _Self = TypeVar("_Self", bound="_Helper")

    _T = TypeVar("_T")
    _U = TypeVar("_U")

    NAME_REGEX: Final = re.compile(r"[a-zA-Z0-9_]+$")
    NAME_WITH_DOT_REGEX: Final = re.compile(r"[a-zA-Z0-9_]+\.[a-zA-Z0-9_]+$")
    ROOM_NAME_REGEX: Final = re.compile(r"[a-zA-Z0-9_-]+$")

    def __init__(self, d: dict[str, Any], *path: str):
        if not isinstance(d, dict):
            raise JsonError("Expected a dict", path)

        self.__dict: Final = d
        self.__path: Final = path

    def _raise_error(self, e: Union[str, Exception], *location: str) -> NoReturn:
        if isinstance(e, Exception):
            e = f"{ type(e).__name__ }: { e }"
        raise JsonError(e, self.__path + location) from None

    def _raise_missing_field_error(self, key: str, *location: str) -> NoReturn:
        raise JsonError(f"Missing JSON field: { key }", self.__path + location)

    def contains(self, key: str) -> bool:
        return key in self.__dict

    def _get(self, key: str, _type: Type[_T]) -> _T:
        assert _type != dict and _type != OrderedDict

        v = self.__dict.get(key)
        if v is None:
            self._raise_missing_field_error(key)
        if not isinstance(v, _type):
            self._raise_error(f"Expected a { _type.__name__ }", key)
        return v

    def _get2(self, key: str, type_a: Type[_T], type_b: Type[_U]) -> Union[_T, _U]:
        assert type_a != dict and type_a != OrderedDict
        assert type_b != dict and type_b != OrderedDict

        v = self.__dict.get(key)
        if v is None:
            self._raise_missing_field_error(key)
        if not isinstance(v, type_a) and not isinstance(v, type_b):
            self._raise_error(f"Expected a { type_a.__name__ } or { type_b.__name__ }", key)
        return v

    def _optional_get(self, key: str, _type: Type[_T]) -> Optional[_T]:
        assert _type != dict and _type != OrderedDict

        v = self.__dict.get(key)
        if v is None:
            return None
        if not isinstance(v, _type):
            self._raise_error(f"Expected a { _type.__name__ }", key)
        return v

    def _optional_get2(self, key: str, type_a: Type[_T], type_b: Type[_U]) -> Optional[Union[_T, _U]]:
        assert type_a != dict and type_a != OrderedDict
        assert type_b != dict and type_b != OrderedDict

        v = self.__dict.get(key)
        if v is None:
            return None
        if not isinstance(v, type_a) and not isinstance(v, type_b):
            self._raise_error(f"Expected a { type_a.__name__ } or { type_b.__name__ }", key)
        return v

    def get_optional_dict(self: _Self, key: str) -> Optional[_Self]:
        cls = type(self)

        d = self.__dict.get(key)
        if d is None:
            return None
        if not isinstance(d, dict):
            self._raise_error("Expected a JSON dict type", key)

        return cls(d, *self.__path, key)

    def get_dict(self: _Self, key: str) -> _Self:
        cls = type(self)

        d = self.__dict.get(key)
        if not isinstance(d, dict):
            self._raise_error("Expected a JSON dict type", key)

        return cls(d, *self.__path, key)

    def iterate_list_of_dicts(self: _Self, key: str) -> Generator[_Self, None, None]:
        cls = type(self)

        for i, item in enumerate(self._get(key, list)):
            if not isinstance(item, dict):
                self._raise_error("Expected a dict", key)

            yield cls(item, *self.__path, key, str(i))

    def iterate_dict_of_dicts(self: _Self, key: str) -> Generator[tuple[Name, _Self], None, None]:
        cls = type(self)

        d = self.__dict.get(key)
        if not isinstance(d, dict):
            self._raise_error("Expected a JSON dict type", key)

        for name, item in d.items():
            name = self._test_name(name, key)

            if not isinstance(item, dict):
                self._raise_error("Expected a dict", key, name)

            yield name, cls(item, *self.__path, key)

    def iterate_dict(self, key: str, _type: Type[_T]) -> Generator[tuple[Name, _T], None, None]:
        assert _type != dict or _type != OrderedDict

        d = self.__dict.get(key)
        if not isinstance(d, dict):
            self._raise_error("Expected a JSON dict type", key)

        for name, item in d.items():
            name = self._test_name(name, key)

            if not isinstance(item, _type):
                self._raise_error(f"Expected a { _type.__name__ }", key, name)

            yield name, item

    def iterate_str_dict(self, key: str, _type: Type[_T]) -> Generator[tuple[str, _T], None, None]:
        assert _type != dict or _type != OrderedDict

        d = self.__dict.get(key)
        if not isinstance(d, dict):
            self._raise_error("Expected a JSON dict type", key)

        for name, item in d.items():
            assert isinstance(name, str)

            if not isinstance(item, _type):
                self._raise_error(f"Expected a { _type.__name__ }", key, name)

            yield name, item

    # `self.__dict` MUST NOT be accessed below this line
    # --------------------------------------------------

    def get_string(self, key: str) -> str:
        return self._get(key, str)

    def get_optional_string(self, key: str) -> Optional[str]:
        return self._optional_get(key, str)

    def get_filename(self, key: str) -> Filename:
        return self._get(key, str)

    def get_int(self, key: str) -> int:
        v = self._get2(key, str, int)
        if isinstance(v, int):
            return v
        else:
            try:
                return int(v)
            except ValueError:
                self._raise_error("Expected an integer", key)

    def get_optional_int(self, key: str) -> Optional[int]:
        v = self._optional_get2(key, str, int)
        if v is None or isinstance(v, int):
            return v
        else:
            try:
                return int(v)
            except ValueError:
                self._raise_error("Expected an integer", key)

    def get_float(self, key: str) -> float:
        return self._get2(key, int, float)

    def get_hex_or_int(self, key: str) -> int:
        v = self._get2(key, int, str)
        if isinstance(v, int):
            return v
        else:
            try:
                return int(v, 16)
            except ValueError:
                self._raise_error(f"Expected hex string: { v }", key)

    def get_bool(self, key: str) -> bool:
        return self._get(key, bool)

    def get_int1(self, key: str) -> bool:
        i = self.get_int(key)
        if i == 0:
            return False
        elif i == 1:
            return True
        self._raise_error(f"Expected a 1 or a 0: { i }", key)

    def get_int_range(self, key: str, min_: int, max_: int) -> int:
        i = self.get_int(key)
        if i < min_ or i > max_:
            self._raise_error(f"Integer out of range: { i } (min: {min_}, max:{max_}", key)
        return i

    def get_optional_u8_position(self, key: str) -> Optional[Position]:
        v = self._optional_get2(key, str, list)
        if v is None:
            return None

        if isinstance(v, str):
            v = v.split()

        if len(v) != 2:
            self._raise_error("u8 position requires 2 integers", key)

        try:
            v1 = int(v[0], 0)
            v2 = int(v[1], 0)
        except ValueError:
            self._raise_error("A u8 position requires 2 integers", key)

        if v1 < 0 or v1 > 0xFF or v2 < 0 or v2 > 0xFF:
            self._raise_error(f"u8 position out of bounds: {v1} {v2}", key)

        return Position(v1, v2)

    def get_object_size(self, key: str) -> Literal[8, 16]:
        i = self.get_int(key)
        if i == 8:
            return 8
        elif i == 16:
            return 16
        else:
            self._raise_error(f"Invalid Object Size: { i }", key)

    def get_name(self, key: str) -> Name:
        s = self.get_string(key)
        if self.NAME_REGEX.match(s):
            return s
        else:
            self._raise_error(f"Invalid name: {s}", key)

    def get_name_with_dot(self, key: str) -> ScopedName:
        s = self.get_string(key)
        if self.NAME_WITH_DOT_REGEX.match(s):
            return s
        else:
            self._raise_error(f"Invalid name with dot: {s}", key)

    def get_optional_name(self, key: str) -> Optional[Name]:
        s = self.get_optional_string(key)
        if not s:
            return None
        if self.NAME_REGEX.match(s):
            return s
        else:
            self._raise_error(f"Invalid name with dot: {s}", key)

    def get_room_name(self, key: str) -> RoomName:
        s = self.get_string(key)
        if self.ROOM_NAME_REGEX.match(s):
            return s
        else:
            self._raise_error(f"Invalid room name: {s}", key)

    def get_name_list(self, key: str) -> list[Name]:
        l = self._get(key, list)

        for i, s in enumerate(l):
            if not isinstance(s, str):
                self._raise_error("Expected a string", key, str(i))
            if not self.NAME_REGEX.match(s):
                self._raise_error(f"Invalid name: {s}", key, str(i))
        return l

    def get_name_list_mapping(self, key: str, max_items: Optional[int] = None) -> OrderedDict[Name, int]:
        l = self.get_name_list(key)

        out: OrderedDict[Name, int] = OrderedDict()

        for i, s in enumerate(l):
            if s in out:
                self._raise_error(f"Duplicate name: { s }", key, str(i))
            out[s] = i

        return out

    def get_parameter_dict(self, key: str) -> dict[Name, str]:
        """Returns a callback parameter dictionary."""

        d = self.__dict.get(key)
        if not isinstance(d, dict):
            self._raise_error("Expected a JSON dict type", key)

        out = OrderedDict()
        for name, value in d.items():
            name = self._test_name(name, key)

            if isinstance(value, str):
                out[name] = value
            elif isinstance(value, int) or isinstance(value, float):
                out[name] = str(value)
            else:
                self._raise_error("Only strings or numbers are allowed in parameters", key, name)

        return out

    def get_optional_parameter_dict(self, key: str) -> Optional[dict[Name, str]]:
        if key in self.__dict:
            return self.get_parameter_dict(key)
        else:
            return None

    def _test_name(self, s: Any, *path: str) -> Name:
        if not isinstance(s, str):
            self._raise_error("Expected a string", *path)
        if not self.NAME_REGEX.match(s):
            self._raise_error(f"Invalid name: {s}", *path)
        return s

    def _test_name_list(self, l: Any, *path: str) -> list[Name]:
        if not isinstance(l, list):
            self._raise_error("Expected a list of names", *path)

        for i, s in enumerate(l):
            if not isinstance(s, str):
                self._raise_error("Expected a string", *path, str(i))
            if not self.NAME_REGEX.match(s):
                self._raise_error(f"Invalid name: {s}", *path, str(i))
        return l

    def build_dict_from_dict(
        self: _Self, key: str, _type: Type[_T], max_elements: int, func: Callable[[_Self, Name], _T]
    ) -> dict[Name, _T]:
        out: dict[Name, _Helper._T] = dict()

        for item_name, d in self.iterate_dict_of_dicts(key):
            # item_name has been checked by `iterate_dict_of_dicts`

            if item_name in out:
                self._raise_error(f"Duplicate { _type.__name__ } name: { item_name }", key)

            try:
                item = func(d, item_name)
            except JsonError:
                raise
            except Exception as ex:
                self._raise_error(ex, key, item_name)

            assert item_name not in out
            out[item_name] = item

        if len(out) > max_elements:
            self._raise_error(f"Too many items ({ len(out) }, max: { max_elements })", key)

        return out

    def build_ordered_dict_from_list(
        self: _Self, key: str, _type: Type[_T], max_elements: int, func: Callable[[_Self, Name, int], _T]
    ) -> OrderedDict[Name, _T]:
        cls = type(self)

        out: OrderedDict[Name, _Helper._T] = OrderedDict()

        for i, d in enumerate(self._get(key, list)):
            if not isinstance(d, dict):
                self._raise_error("Expected a JSON dict", key, str(i))

            # Testing 'name' here improves the error messages in the child `cls` instance.
            item_name: Optional[Name] = d.get("name")
            if item_name is None:
                self._raise_missing_field_error("name", key)
            if not isinstance(item_name, str):
                self._raise_error("Expected a string", key, str(i), "name")
            if not self.NAME_REGEX.match(item_name):
                self._raise_error(f"Invalid name: { item_name }", key, str(i), "name")

            if item_name in out:
                self._raise_error(f"Duplicate { _type.__name__ } name: { item_name }", key, str(i))

            item_index_str = f"{i} ({item_name})"
            try:
                item = func(cls(d, *self.__path, key, item_index_str), item_name, i)
            except JsonError:
                raise
            except Exception as ex:
                self._raise_error(ex, key, item_index_str)

            assert item_name not in out
            out[item_name] = item

        if len(out) > max_elements:
            self._raise_error(f"Too many items ({ len(out) }, max: { max_elements })", key)

        return out


def _load_json_file(filename: Filename, cls: Type[_Helper._Self]) -> _Helper._Self:
    with open(filename, "r") as fp:
        j = json.load(fp)

    return cls(j, os.path.basename(filename))


# entities.json
# =============


class EfParameter(NamedTuple):
    type: str
    values: Optional[list[Name]]


class EntityFunction(NamedTuple):
    name: Name
    id: int
    is_enemy: bool
    ms_export_order: Name
    parameter: Optional[EfParameter]
    uses_process_function_from: Optional[Name]


class EntityVision(NamedTuple):
    a: int
    b: int


class Entity(NamedTuple):
    name: Name
    id: int
    code: EntityFunction
    metasprites: ScopedName
    death_function: Name
    zpos: int
    vision: Optional[EntityVision]
    health: int
    attack: int


class EntitiesJson(NamedTuple):
    death_functions: list[Name]
    entity_functions: OrderedDict[Name, EntityFunction]
    entities: OrderedDict[Name, Entity]


class _Entities_Helper(_Helper):
    def get_entity_vision(self, key: str) -> Optional[EntityVision]:
        s = self.get_optional_string(key)
        if not s:
            return None
        v = s.split()
        if len(v) != 2:
            self._raise_error("Expected a string containing two integers", key)

        try:
            return EntityVision(int(v[0]), int(v[1]))
        except ValueError:
            self._raise_error("Expected a string containing two integers", key)

    def get_ef_parameter(self, key: str) -> Optional[EfParameter]:
        p = self.get_optional_dict(key)
        if p is None:
            return None

        t = p.get_string("type")

        if t == "enum":
            return EfParameter("enum", p.get_name_list("values"))
        elif t == "global_flag":
            return EfParameter(t, None)
        elif t == "dungeon_flag":
            return EfParameter(t, None)
        elif t == "u8":
            return EfParameter(t, None)
        else:
            self._raise_error(f"Unknown function parameter type: { t }", key)


def load_entities_json(filename: Filename) -> EntitiesJson:
    jh = _load_json_file(filename, _Entities_Helper)

    entity_functions = jh.build_ordered_dict_from_list(
        "entity_functions",
        EntityFunction,
        256,
        lambda ef, name, i: EntityFunction(
            name=name,
            id=i,
            is_enemy=ef.get_bool("is_enemy"),
            ms_export_order=ef.get_name("ms-export-order"),
            parameter=ef.get_ef_parameter("parameter"),
            uses_process_function_from=ef.get_optional_name("uses-process-function-from"),
        ),
    )

    entities = jh.build_ordered_dict_from_list(
        "entities",
        Entity,
        254,
        lambda e, name, i: Entity(
            name=name,
            id=i,
            code=entity_functions[e.get_name("code")],
            metasprites=e.get_name_with_dot("metasprites"),
            death_function=e.get_name("death_function"),
            zpos=e.get_int("zpos"),
            vision=e.get_entity_vision("vision"),
            health=e.get_int("health"),
            attack=e.get_int("attack"),
        ),
    )

    return EntitiesJson(death_functions=jh.get_name_list("death_functions"), entity_functions=entity_functions, entities=entities)


# ms-export-order.json
# ====================


class MsPatternObject(NamedTuple):
    xpos: int
    ypos: int
    size: Literal[8, 16]


class MsPattern(NamedTuple):
    name: Name
    id: int
    objects: list[MsPatternObject]


class MsAnimationExportOrder(NamedTuple):
    name: Name
    animations: list[Name]


class MseoDynamicMsFsSettings(NamedTuple):
    first_tile_id: int
    n_large_tiles: int


class MsExportOrder(NamedTuple):
    patterns: OrderedDict[Name, MsPattern]
    shadow_sizes: OrderedDict[Name, int]
    animation_lists: OrderedDict[Name, MsAnimationExportOrder]
    dynamic_metasprites: OrderedDict[Name, MseoDynamicMsFsSettings]


class _MSEO_Helper(_Helper):
    def get_pattern_objects(self, key: str) -> list[MsPatternObject]:
        objs = list()

        for o in self.iterate_list_of_dicts(key):
            objs.append(MsPatternObject(xpos=o.get_int("x"), ypos=o.get_int("y"), size=o.get_object_size("size")))

        return objs

    def get_animation_eo_lists(self, key: str) -> OrderedDict[Name, MsAnimationExportOrder]:
        out = OrderedDict()

        for name, al in self.iterate_dict(key, list):
            eo = MsAnimationExportOrder(
                name=name,
                animations=self._test_name_list(al, key, name),
            )

            if eo.name in out:
                self._raise_error(f"Duplicate name: { eo.name }", key)
            out[eo.name] = eo

        return out

    def get_dynamic_metasprites(self, key: str) -> OrderedDict[Name, MseoDynamicMsFsSettings]:
        out = OrderedDict()

        for name, jh in self.iterate_dict_of_dicts(key):
            item = MseoDynamicMsFsSettings(
                first_tile_id=jh.get_int("first_tile_id"),
                n_large_tiles=jh.get_int("n_large_tiles"),
            )

            if name in out:
                self._raise_error(f"Duplicate name: { name }", key)
            out[name] = item

        return out


def load_ms_export_order_json(filename: Filename) -> MsExportOrder:
    jh = _load_json_file(filename, _MSEO_Helper)

    patterns = jh.build_ordered_dict_from_list(
        "patterns", MsPattern, 256, lambda p, name, i: MsPattern(name=name, id=i * 2, objects=p.get_pattern_objects("objects"))
    )

    return MsExportOrder(
        patterns=patterns,
        shadow_sizes=jh.get_name_list_mapping("shadow_sizes"),
        animation_lists=jh.get_animation_eo_lists("animation_lists"),
        dynamic_metasprites=jh.get_dynamic_metasprites("dynamic_metasprites"),
    )


# mappings.json
# =============

MAX_N_CALLBACKS = 128
MAX_ROOM_EVENT_PARAMETERS = 4
MAX_SL_CALLBACK_PARAMETERS = 8
MAX_SL_ROOM_PARAMETERS = 2
MAX_MS_PALETTE_CALLBACK_PARAMETERS = 2

MAX_GAMESTATE_SIZE: Final = 4096
MAX_GAMESTATE_ARRAY_BYTE_SIZE: Final = 256
MAX_GAMESTATE_FLAGS: Final = 256

# GAME_MODES > 128 mean the next game mode is unchanged.
MAX_GAME_MODES = 128


class MemoryMap(NamedTuple):
    mode: MemoryMapMode
    first_resource_bank: int
    n_resource_banks: int


class GameStateVar(NamedTuple):
    var_index: int
    comment: Optional[str]


class GameState(NamedTuple):
    identifier: str
    cart_ram_size: int
    n_save_slots: int
    n_save_copies: int
    # ::TODO add names::
    version: int
    u8_array_len: int
    u16_array_len: int
    # Global flags.  Named in `gen/enums.wiz`.
    global_flags: OrderedDict[Name, GameStateVar]
    # Non-global flags.  Not named in `gen/enums.wiz`
    # ::MAYDO multiple instances of dungeon_flags, swapped out when a dungeon is loaded::
    dungeon_flags: OrderedDict[Name, GameStateVar]
    u8_vars: OrderedDict[Name, GameStateVar]
    u16_vars: OrderedDict[Name, GameStateVar]


class GameMode(NamedTuple):
    name: Name
    source: str


class CallbackParameter(NamedTuple):
    name: Name
    comment: str
    type: Name
    default_value: Optional[str]


class RoomEvent(NamedTuple):
    name: Name
    id: int
    source: str
    parameters: list[CallbackParameter]


class SecondLayerCallback(NamedTuple):
    name: Name
    id: int
    source: str
    sl_parameters: list[CallbackParameter]
    room_parameters: list[CallbackParameter]
    # ::TODO add world parameters::


class MsPaletteCallback(NamedTuple):
    name: Name
    id: int
    source: str
    parameters: list[CallbackParameter]


Callback = Union[RoomEvent, SecondLayerCallback, MsPaletteCallback]
CallbackDict = Union[OrderedDict[Name, RoomEvent], OrderedDict[Name, SecondLayerCallback], OrderedDict[Name, MsPaletteCallback]]


class Mappings(NamedTuple):
    game_title: str
    gamestate: GameState
    mt_tilesets: list[Name]
    second_layers: list[Name]
    ms_spritesheets: list[Name]
    palettes: list[Name]
    tiles: list[Name]
    bg_images: list[Name]
    interactive_tile_functions: list[Name]
    gamemodes: list[GameMode]
    room_transitions: list[Name]
    room_events: OrderedDict[Name, RoomEvent]
    sl_callbacks: OrderedDict[Name, SecondLayerCallback]
    ms_palette_callbacks: OrderedDict[Name, MsPaletteCallback]
    memory_map: MemoryMap

    # Location of the directory containing tad-compiler
    tad_binary_directory: Filename


class _Mappings_Helper(_Helper):
    def get_memory_map(self, key: str) -> MemoryMap:
        mm = self.get_dict(key)

        mode_str = mm.get_string("mode")
        try:
            mode = MemoryMapMode[mode_str.upper()]
        except ValueError:
            self._raise_error(f"Unknown memory mapping mode: { mode_str }", key)

        return MemoryMap(
            mode=mode,
            first_resource_bank=mm.get_hex_or_int("first_resource_bank"),
            n_resource_banks=mm.get_int("n_resource_banks"),
        )

    def get_gamestate_identifier(self, key: str) -> str:
        identifier = self.get_string("identifier")
        try:
            if len(identifier.encode("ASCII")) != 4:
                self._raise_error("GameState identifier must be 4 ASCII characters", key)
        except UnicodeEncodeError:
            self._raise_error("GameState identifier must be ASCII", key)
        return identifier

    def get_gamestate_vars(self, key: str, array_len: int, element_size: int) -> OrderedDict[Name, GameStateVar]:
        var_list = self._get(key, list)

        if len(var_list) > array_len:
            self._raise_error(f"Too many items in list ({len(var_list)}, max: {array_len})", key)

        out: OrderedDict[Name, GameStateVar] = OrderedDict()

        for i, s in enumerate(var_list):
            if s is None:
                # dummied out
                pass
            if not isinstance(s, str):
                self._raise_error("Expected a string", key, str(i))
            else:
                sl = s.split(None, 1)
                name = sl[0]
                comment = None

                if len(sl) == 2:
                    comment = sl[1]

                if not self.NAME_REGEX.match(name):
                    self._raise_error(f"Invalid name: {name}", key, str(i))

                out[name] = GameStateVar(i * element_size, comment)

        return out

    def get_gamestate(self, key: str) -> GameState:
        gs = self.get_dict(key)

        identifier = gs.get_gamestate_identifier("identifier")

        cart_ram_size = gs.get_int_range("cart_ram_size", 1024, 8192)
        n_save_slots = gs.get_int_range("n_save_slots", 1, 8)
        n_save_copies = gs.get_int_range("n_save_copies", 2, 8)

        version = gs.get_int_range("version", 0, 0xFF)
        u8_array_len = gs.get_int_range("u8_array_len", 4, MAX_GAMESTATE_ARRAY_BYTE_SIZE)
        u16_array_len = gs.get_int_range("u16_array_len", 4, MAX_GAMESTATE_ARRAY_BYTE_SIZE // 2)

        return GameState(
            identifier=identifier,
            cart_ram_size=cart_ram_size,
            n_save_slots=n_save_slots,
            n_save_copies=n_save_copies,
            version=version,
            u8_array_len=u8_array_len,
            u16_array_len=u16_array_len,
            global_flags=gs.get_gamestate_vars("global_flags", MAX_GAMESTATE_FLAGS, 1),
            dungeon_flags=gs.get_gamestate_vars("dungeon_flags", MAX_GAMESTATE_FLAGS, 1),
            u8_vars=gs.get_gamestate_vars("u8_vars", u8_array_len, 1),
            u16_vars=gs.get_gamestate_vars("u16_vars", u16_array_len, 2),
        )

    def get_gamemodes(self, key: str) -> list[GameMode]:
        out = list()

        for p in self.iterate_list_of_dicts(key):
            out.append(
                GameMode(
                    name=p.get_name("name"),
                    source=p.get_string("source"),
                )
            )

        if len(out) > MAX_GAME_MODES:
            self._raise_error(f"Too many gamemodes, max: { MAX_GAME_MODES }", key)
        return out

    def get_callback_parameters(self, key: str, max_parameters: int) -> list[CallbackParameter]:
        out = list()

        for p in self.iterate_list_of_dicts(key):
            out.append(
                CallbackParameter(
                    name=p.get_name("name"),
                    comment=p.get_string("comment"),
                    type=p.get_name("type"),
                    default_value=p.get_optional_string("default"),
                )
            )

        if len(out) > MAX_ROOM_EVENT_PARAMETERS:
            self._raise_error(f"Too many {key} parameters, max: { max_parameters }", key)
        return out

    def get_room_events(self, key: str) -> OrderedDict[Name, RoomEvent]:
        return self.build_ordered_dict_from_list(
            key,
            RoomEvent,
            MAX_N_CALLBACKS,
            lambda rj, name, i: RoomEvent(
                name=name,
                id=i,
                source=rj.get_string("source"),
                parameters=rj.get_callback_parameters("parameters", MAX_ROOM_EVENT_PARAMETERS),
            ),
        )

    def get_sl_callbacks(self, key: str) -> OrderedDict[Name, SecondLayerCallback]:
        callbacks = self.build_ordered_dict_from_list(
            key,
            SecondLayerCallback,
            MAX_N_CALLBACKS,
            lambda rj, name, i: SecondLayerCallback(
                name=name,
                id=i + 1,  # 0 is null
                source=rj.get_string("source"),
                sl_parameters=rj.get_callback_parameters("sl_parameters", MAX_SL_CALLBACK_PARAMETERS),
                room_parameters=rj.get_callback_parameters("room_parameters", MAX_SL_ROOM_PARAMETERS),
            ),
        )
        # ::TODO detect duplicates in callback parameters::
        return callbacks

    def get_ms_palette_callbacks(self, key: str) -> OrderedDict[Name, MsPaletteCallback]:
        callbacks = self.build_ordered_dict_from_list(
            key,
            MsPaletteCallback,
            MAX_N_CALLBACKS,
            lambda rj, name, i: MsPaletteCallback(
                name=name,
                id=i + 1,  # 0 is null
                source=rj.get_string("source"),
                parameters=rj.get_callback_parameters("parameters", MAX_MS_PALETTE_CALLBACK_PARAMETERS),
            ),
        )
        # ::TODO detect duplicates in callback parameters::
        return callbacks


def load_mappings_json(filename: Filename) -> Mappings:
    jh = _load_json_file(filename, _Mappings_Helper)

    return Mappings(
        game_title=jh.get_string("game_title"),
        gamestate=jh.get_gamestate("gamestate"),
        mt_tilesets=jh.get_name_list("mt_tilesets"),
        second_layers=jh.get_name_list("second_layers"),
        ms_spritesheets=jh.get_name_list("ms_spritesheets"),
        palettes=jh.get_name_list("palettes"),
        tiles=jh.get_name_list("tiles"),
        bg_images=jh.get_name_list("bg_images"),
        interactive_tile_functions=jh.get_name_list("interactive_tile_functions"),
        room_transitions=jh.get_name_list("room_transitions"),
        room_events=jh.get_room_events("room_events"),
        sl_callbacks=jh.get_sl_callbacks("sl_callbacks"),
        ms_palette_callbacks=jh.get_ms_palette_callbacks("ms_palette_callbacks"),
        memory_map=jh.get_memory_map("memory_map"),
        gamemodes=jh.get_gamemodes("gamemodes"),
        tad_binary_directory=jh.get_string("tad_binary_directory"),
    )


# Terrific Audio Driver project.json
# ==================================

MAX_SONGS = 254


class Song(NamedTuple):
    name: Name
    id: int
    source: Filename


class SfxExportOrder(NamedTuple):
    export_order: list[Name]
    n_high_priority_sfx: int
    first_low_priority_sfx: int


class AudioProject(NamedTuple):
    instrument_sources: list[Filename]
    songs: OrderedDict[Name, Song]
    sound_effects: SfxExportOrder
    sound_effect_file: Filename


def load_audio_project(filename: Filename) -> AudioProject:
    jh = _load_json_file(filename, _Helper)

    dirname = os.path.dirname(filename)

    songs = jh.build_ordered_dict_from_list(
        "songs",
        Song,
        MAX_SONGS,
        lambda sj, name, i: Song(
            name=name,
            id=i + 1,
            source=os.path.join(dirname, sj.get_string("source")),
        ),
    )

    instrument_sources = [os.path.join(dirname, j.get_string("source")) for j in jh.iterate_list_of_dicts("instruments")]

    hp_sfx = jh.get_name_list("high_priority_sound_effects")
    np_sfx = jh.get_name_list("sound_effects")
    lp_sfx = jh.get_name_list("low_priority_sound_effects")

    return AudioProject(
        instrument_sources=instrument_sources,
        songs=songs,
        sound_effects=SfxExportOrder(
            export_order=hp_sfx + np_sfx + lp_sfx,
            n_high_priority_sfx=len(hp_sfx),
            first_low_priority_sfx=len(hp_sfx) + len(np_sfx),
        ),
        sound_effect_file=os.path.join(dirname, jh.get_string("sound_effect_file")),
    )


#
# ms-palettes.json
#


class MsPaletteInput(NamedTuple):
    id: int
    name: Name
    parent: Optional[Name]
    spritesheet_uses_parent: bool
    source: Filename
    starting_row: int
    n_rows: int
    n_frames: Optional[int]
    rows_per_frame: Optional[int]
    callback: Optional[Name]
    parameters: Optional[dict[Name, str]]


class MsPalettesJson(NamedTuple):
    ms_palettes: OrderedDict[Name, MsPaletteInput]


def load_ms_palettes_json(filename: Filename) -> MsPalettesJson:
    jh = _load_json_file(filename, _Helper)

    dirname = os.path.dirname(filename)

    ms_palettes = jh.build_ordered_dict_from_list(
        "ms_palettes",
        MsPaletteInput,
        MAX_RESOURCE_ITEMS,
        lambda sj, name, i: MsPaletteInput(
            name=name,
            id=i,
            parent=sj.get_optional_name("parent"),
            spritesheet_uses_parent=sj.get_bool("spritesheet_uses_parent"),
            source=os.path.join(dirname, sj.get_string("source")),
            starting_row=sj.get_int("starting_row"),
            n_rows=sj.get_int("n_rows"),
            n_frames=sj.get_optional_int("n_frames"),
            rows_per_frame=sj.get_optional_int("rows_per_frame"),
            callback=sj.get_optional_name("callback"),
            parameters=sj.get_optional_parameter_dict("parameters"),
        ),
    )

    return MsPalettesJson(ms_palettes)


# metasprites.json
# ================


class Aabb(NamedTuple):
    x: int
    y: int
    width: int
    height: int


class AabbOverride(NamedTuple):
    start: Name
    end: Optional[Name]
    value: Aabb


class MsLayout(NamedTuple):
    pattern: Name
    x_offset: int
    y_offset: int


class MsLayoutOverride(NamedTuple):
    start: Name
    end: Optional[Name]
    value: MsLayout


class TileHitbox(NamedTuple):
    half_width: int
    half_height: int


class MsAnimation(NamedTuple):
    name: Name
    loop: bool
    delay_type: str
    fixed_delay: Optional[Union[float, int]]
    frames: list[Name]
    frame_delays: Optional[list[Union[float, int]]]


class MsClone(NamedTuple):
    name: Name
    source: Name
    flip: Optional[str]


class MsFrameset(NamedTuple):
    name: Name
    source: Filename
    frame_width: int
    frame_height: int
    x_origin: int
    y_origin: int
    shadow_size: str
    tilehitbox: TileHitbox
    default_hitbox: Optional[Aabb]
    default_hurtbox: Optional[Aabb]
    default_layout: Optional[MsLayout]
    ms_export_order: Name
    order: int
    frames: list[Name]
    layout_overrides: list[MsLayoutOverride]
    hitbox_overrides: list[AabbOverride]
    hurtbox_overrides: list[AabbOverride]
    clones: list[MsClone]
    animations: dict[Name, MsAnimation]


class MsPaletteSwap(NamedTuple):
    name: Name
    copies: Name
    palette: int


class MsSpritesheet(NamedTuple):
    name: Name
    palette: Name
    first_tile: int
    end_tile: int
    framesets: OrderedDict[Name, MsFrameset]
    palette_swaps: OrderedDict[Name, MsPaletteSwap]


class _Ms_Helper(_Helper):
    def get_tilehitbox(self, key: str) -> TileHitbox:
        s = self.get_string(key)

        v = s.split()
        if len(v) != 2:
            self._raise_error("Expected a string containing two integers (TileHitbox)", key)

        try:
            return TileHitbox(int(v[0]), int(v[1]))
        except ValueError:
            self._raise_error("Expected a string containing two integers (TileHitbox)", key)

    def get_animation_frames__no_fixed_delay(self, key: str) -> tuple[list[Name], list[Union[int, float]]]:
        l = self._get(key, list)

        if len(l) % 2 != 0:
            self._raise_error("Expected a list of `frame, delay, frame, delay, frame, delay, ...`", key)

        # off indexes
        frames = l[0::2]
        frame_delays = l[1::2]

        for index, s in enumerate(frames):
            if not isinstance(s, str):
                self._raise_error("Expected a str", str(index * 2))

        for index, delay in enumerate(frame_delays):
            if not isinstance(delay, float) and not isinstance(delay, int):
                self._raise_error("Expected a float containing the delay time", str(index * 2 + 1))

        return frames, frame_delays

    def __convert_aabb(self, s: str, *path: str) -> Aabb:
        v = s.split()
        if len(v) != 4:
            self._raise_error("Expected a string containing four integers (Aabb)", *path)
        try:
            return Aabb(int(v[0]), int(v[1]), int(v[2]), int(v[3]))
        except ValueError:
            self._raise_error("Expected a string containing four integers (Aabb)", *path)

    def get_aabb(self, key: str) -> Aabb:
        s = self._get(key, str)
        return self.__convert_aabb(s, key)

    def get_optional_aabb(self, key: str) -> Optional[Aabb]:
        s = self._optional_get(key, str)
        if s is None:
            return None
        return self.__convert_aabb(s, key)

    def __convert_layout(self, s: str, *path: str) -> MsLayout:
        v = s.split()
        if len(v) != 3:
            self._raise_error("Expected a string in the following format `pattern int int", *path)
        try:
            return MsLayout(v[0], int(v[1]), int(v[2]))
        except ValueError:
            self._raise_error("Expected a string in the following format `pattern int int", *path)

    def get_optional_layout(self, key: str) -> Optional[MsLayout]:
        s = self._optional_get(key, str)
        if s is None:
            return None
        return self.__convert_layout(s, key)

    RANGE_REGEX: Final = re.compile(r"([a-zA-Z0-9_]+) *- *([a-zA-Z0-9_]+)")

    def __convert_range(self, s: str, key: str) -> tuple[Name, Optional[Name]]:
        m = self.RANGE_REGEX.match(s)
        if m:
            return m.group(1), m.group(2)
        elif self.NAME_REGEX.match(s):
            return s, None
        else:
            self._raise_error(f"Invalid range: {s}", key)

    def get_aabb_overrides(self, key: str) -> list[AabbOverride]:
        out: list[AabbOverride] = list()

        if self.contains(key):
            for range_, s in self.iterate_str_dict(key, str):
                start, end = self.__convert_range(range_, key)

                out.append(AabbOverride(start=start, end=end, value=self.__convert_aabb(s, key, range_)))

        return out

    def get_layout_overrides(self, key: str) -> list[MsLayoutOverride]:
        out: list[MsLayoutOverride] = list()

        if self.contains(key):
            for range_, s in self.iterate_str_dict(key, str):
                start, end = self.__convert_range(range_, key)

                out.append(MsLayoutOverride(start=start, end=end, value=self.__convert_layout(s, key, range_)))

        return out

    VALID_FLIPS: Final = ("hflip", "vflip", "hvflip")

    def get_clones(self, key: str) -> list[MsClone]:
        out: list[MsClone] = list()

        if self.contains(key):
            for name, clone_str in self.iterate_dict(key, str):
                v = clone_str.split()

                if len(v) == 1:
                    flip = None
                elif len(v) == 2:
                    flip = v[1]
                    if flip not in self.VALID_FLIPS:
                        self._raise_error(f"Unknown flip: { flip }", key, name)
                else:
                    self._raise_error("Invalid clone format (expected `name` or `name flip`)", key, name)

                out.append(MsClone(name=name, source=v[0], flip=flip))

        return out


def __read_ms_animation(a: _Ms_Helper, name: Name) -> MsAnimation:
    if a.contains("fixed-delay"):
        fixed_delay = a.get_float("fixed-delay")
        frames = a.get_name_list("frames")
        frame_delays = None
    else:
        fixed_delay = None
        frames, frame_delays = a.get_animation_frames__no_fixed_delay("frames")

    return MsAnimation(
        name=name,
        loop=a.get_bool("loop"),
        delay_type=a.get_name("delay-type"),
        fixed_delay=fixed_delay,
        frames=frames,
        frame_delays=frame_delays,
    )


# IF `skip_animations` is true, then no animations will be loaded and any errors in the animations will be ignored
def __read_ms_frameset(jh: _Ms_Helper, name: Name, i: int, skip_animations: Optional[bool] = None) -> MsFrameset:
    return MsFrameset(
        name=name,
        source=jh.get_filename("source"),
        frame_width=jh.get_int("frameWidth"),
        frame_height=jh.get_int("frameHeight"),
        x_origin=jh.get_int("xorigin"),
        y_origin=jh.get_int("yorigin"),
        shadow_size=jh.get_name("shadowSize"),
        tilehitbox=jh.get_tilehitbox("tilehitbox"),
        default_hitbox=jh.get_optional_aabb("defaultHitbox"),
        default_hurtbox=jh.get_optional_aabb("defaultHurtbox"),
        ms_export_order=jh.get_name("ms-export-order"),
        order=jh.get_int("order"),
        default_layout=jh.get_optional_layout("defaultLayout"),
        frames=jh.get_name_list("frames"),
        hitbox_overrides=jh.get_aabb_overrides("hitboxes"),
        hurtbox_overrides=jh.get_aabb_overrides("hurtboxes"),
        layout_overrides=jh.get_layout_overrides("layouts"),
        clones=jh.get_clones("clones"),
        animations=(
            jh.build_dict_from_dict("animations", MsAnimation, 254, __read_ms_animation) if not skip_animations else OrderedDict()
        ),
    )


def _load_metasprites(jh: _Ms_Helper) -> MsSpritesheet:
    palette_swaps = jh.build_ordered_dict_from_list(
        "palette_swaps",
        MsPaletteSwap,
        256,
        lambda j, name, i: MsPaletteSwap(
            name=name,
            copies=j.get_name("copies"),
            palette=j.get_int("palette"),
        ),
    )

    return MsSpritesheet(
        name=jh.get_name("name"),
        palette=jh.get_filename("palette"),
        first_tile=jh.get_int("firstTile"),
        end_tile=jh.get_int("endTile"),
        framesets=jh.build_ordered_dict_from_list("framesets", MsFrameset, 256, __read_ms_frameset),
        palette_swaps=palette_swaps,
    )


# IF `skip_animations` is true, then no animations will be loaded and any errors in the animations will be ignored
def load_metasprite_frameset_from_dict(fs_name: Name, d: dict[str, Any], skip_animations: bool) -> MsFrameset:
    jh = _Ms_Helper(d, fs_name)
    return __read_ms_frameset(jh, fs_name, 0, skip_animations)


def load_metasprites_json(filename: Filename) -> MsSpritesheet:
    jh = _load_json_file(filename, _Ms_Helper)
    return _load_metasprites(jh)


def load_metasprites_string(text: str) -> MsSpritesheet:
    return _load_metasprites(_Ms_Helper(json.loads(text)))


#
# other-resources.json
#


class PaletteInput(NamedTuple):
    name: Name
    source: Filename
    n_rows: int


class TilesInput(NamedTuple):
    name: Name
    format: str
    source: Filename


class BackgroundImageInput(NamedTuple):
    name: Name
    format: str
    source: Filename
    palette: Name
    tile_priority: bool


class SecondLayerInput(NamedTuple):
    name: Name
    source: Filename
    palette: Name
    tile_priority: bool
    above_metatiles: bool
    mt_tileset: Optional[Name]  # If defined, the second-layer will reuse tiles in a MetaTile Tileset
    part_of_room: bool
    callback: Optional[Name]
    parameters: Optional[dict[Name, str]]


class OtherResources(NamedTuple):
    palettes: dict[Name, PaletteInput]
    second_layers: dict[Name, SecondLayerInput]
    tiles: dict[Name, TilesInput]
    bg_images: dict[Name, BackgroundImageInput]


def load_other_resources_json(filename: Filename) -> OtherResources:
    jh = _load_json_file(filename, _Helper)

    dirname = os.path.dirname(filename)

    palettes = jh.build_dict_from_dict(
        "palettes",
        PaletteInput,
        256,
        lambda j, name: PaletteInput(
            name=name,
            source=os.path.join(dirname, j.get_filename("source")),
            n_rows=j.get_int("n_rows"),
        ),
    )

    tiles = jh.build_dict_from_dict(
        "tiles",
        TilesInput,
        256,
        lambda t, name: TilesInput(
            name=name,
            format=t.get_string("format"),
            source=os.path.join(dirname, t.get_filename("source")),
        ),
    )

    bg_images = jh.build_dict_from_dict(
        "bg_images",
        BackgroundImageInput,
        256,
        lambda t, name: BackgroundImageInput(
            name=name,
            format=t.get_string("format"),
            source=os.path.join(dirname, t.get_filename("source")),
            palette=t.get_name("palette"),
            tile_priority=t.get_int1("tile_priority"),
        ),
    )

    second_layers = jh.build_dict_from_dict(
        "second_layers",
        SecondLayerInput,
        256,
        lambda t, name: SecondLayerInput(
            name=name,
            source=os.path.join(dirname, t.get_filename("source")),
            palette=t.get_name("palette"),
            tile_priority=t.get_int1("tile_priority"),
            above_metatiles=t.get_bool("above_metatiles"),
            mt_tileset=t.get_optional_name("mt_tileset"),
            part_of_room=t.get_bool("part_of_room"),
            callback=t.get_optional_name("callback"),
            parameters=t.get_optional_parameter_dict("parameters"),
        ),
    )

    return OtherResources(
        palettes=palettes,
        tiles=tiles,
        bg_images=bg_images,
        second_layers=second_layers,
    )


#
# dungeons.json
#


class DungeonInput(NamedTuple):
    name: Name
    id: int
    path: Filename
    infinite: bool
    width: int
    height: int
    default_room: str
    palette: Name
    tileset: Name
    second_layer: Optional[Name]
    ms_palette: Name
    ms_spritesheet: Name
    song: Optional[Name]


class DungeonsJson(NamedTuple):
    dungeons: dict[Name, DungeonInput]


MAX_DUNGEONS: Final = 254


def load_dungeons_json(filename: Filename) -> DungeonsJson:
    jh = _load_json_file(filename, _Helper)

    dirname = os.path.dirname(filename)

    dungeons = jh.build_ordered_dict_from_list(
        "dungeons",
        DungeonInput,
        MAX_DUNGEONS,
        lambda sj, name, i: DungeonInput(
            name=name,
            id=i,
            path=os.path.join(dirname, sj.get_string("path")),
            infinite=sj.get_bool("infinite"),
            width=sj.get_int("width"),
            height=sj.get_int("height"),
            default_room=sj.get_string("default_room"),
            palette=sj.get_name("palette"),
            tileset=sj.get_name("tileset"),
            second_layer=sj.get_optional_name("second_layer"),
            ms_palette=sj.get_name("ms_palette"),
            ms_spritesheet=sj.get_name("ms_spritesheet"),
            song=sj.get_optional_name("song"),
        ),
    )

    return DungeonsJson(dungeons)
