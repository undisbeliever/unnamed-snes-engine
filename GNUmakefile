
BINARY := game.sfc


.DELETE_ON_ERROR:
.SUFFIXES:
MAKEFLAGS += --no-builtin-rules



SOURCES	  := $(wildcard src/*.wiz src/*/*.wiz)
RESOURCES :=

8BPP_TILES_SRC	 := $(wildcard resources/*/*8bpp-tiles.png)
4BPP_TILES_SRC	 := $(wildcard resources/*/*4bpp-tiles.png)
2BPP_TILES_SRC	 := $(wildcard resources/*/*2bpp-tiles.png)

RESOURCES  += $(patsubst resources/%.png,gen/%.tiles, $(8BPP_TILES_SRC))
RESOURCES  += $(patsubst resources/%.png,gen/%.tiles, $(4BPP_TILES_SRC))
RESOURCES  += $(patsubst resources/%.png,gen/%.tiles, $(2BPP_TILES_SRC))

RESOURCES  += $(patsubst resources/%.png,gen/%.pal, $(8BPP_TILES_SRC))
RESOURCES  += $(patsubst resources/%.png,gen/%.pal, $(4BPP_TILES_SRC))
RESOURCES  += $(patsubst resources/%.png,gen/%.pal, $(2BPP_TILES_SRC))


ROOM_SRC   := $(wildcard resources/rooms/*.tmx)
ROOM_BINS  := $(sort $(patsubst resources/rooms/%.tmx,gen/rooms/%.bin, $(ROOM_SRC)))
RESOURCES  += $(ROOM_BINS)


METATILE_TILESETS = dungeon
RESOURCES  += $(patsubst %,gen/metatiles/%.bin, $(METATILE_TILESETS))

RESOURCES  += gen/resource-lists.wiz
RESOURCES  += gen/entities.wiz
RESOURCES  += gen/entity-data.wiz
RESOURCES  += gen/ms-patterns-table.wiz
RESOURCES  += gen/rooms.wiz


METASPRITE_SPRITESETS = common dungeon
RESOURCES  += $(patsubst %,gen/metasprites/%.bin, $(METASPRITE_SPRITESETS))
RESOURCES  += $(patsubst %,gen/metasprites/%.wiz, $(METASPRITE_SPRITESETS))


COMMON_PYTHON_SCRIPTS = tools/_json_formats.py tools/_snes.py

# Python interpreter
# (-bb issues errors on bytes/string comparisons)
PYTHON3  := python3 -bb


.PHONY: all
all: $(BINARY)

$(BINARY): wiz/bin/wiz $(SOURCES)
	wiz/bin/wiz -s wla -I src src/main.wiz -o $(BINARY)


.PHONY: wiz
wiz wiz/bin/wiz:
	$(MAKE) -C wiz


# Test if wiz needs recompiling
__UNUSED__ := $(shell $(MAKE) --quiet --question -C "wiz" bin/wiz)
ifneq ($(.SHELLSTATUS), 0)
  $(BINARY): wiz
endif



gen/%-2bpp-tiles.tiles gen/%-2bpp-tiles.pal &: resources/%-2bpp-tiles.png tools/png2snes.py tools/_snes.py
	$(PYTHON3) tools/png2snes.py -f 2bpp -t gen/$*-2bpp-tiles.tiles -p gen/$*-2bpp-tiles.pal $<

gen/%-4bpp-tiles.tiles gen/%-4bpp-tiles.pal &: resources/%-4bpp-tiles.png tools/png2snes.py tools/_snes.py
	$(PYTHON3) tools/png2snes.py -f 4bpp -t gen/$*-4bpp-tiles.tiles -p gen/$*-4bpp-tiles.pal $<

gen/%-8bpp-tiles.tiles gen/%-8bpp-tiles.pal &: resources/%-8bpp-tiles.png tools/png2snes.py tools/_snes.py
	$(PYTHON3) tools/png2snes.py -f 8bpp -t gen/$*-8bpp-tiles.tiles -p gen/$*-8bpp-tiles.pal $<

RESOURCES += $(2BPP_TILES) $(2BPP_PALETTES)
RESOURCES += $(4BPP_TILES) $(4BPP_PALETTES)
RESOURCES += $(8BPP_TILES) $(8BPP_PALETTES)


gen/metatiles/%.bin: resources/metatiles/%-tiles.png resources/metatiles/%-palette.png resources/metatiles/%.tsx tools/convert-tileset.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/convert-tileset.py -o "$@" "resources/metatiles/$*-tiles.png" "resources/metatiles/$*-palette.png" "resources/metatiles/$*.tsx"


gen/rooms/%.bin: resources/rooms/%.tmx resources/mappings.json resources/entities.json tools/convert-room.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/convert-room.py -o "$@" "resources/rooms/$*.tmx" "resources/mappings.json" "resources/entities.json"


gen/resource-lists.wiz: resources/mappings.json tools/generate-resource-lists.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate-resource-lists.py -o "$@" "resources/mappings.json"

gen/rooms.wiz: resources/mappings.json $(ROOM_BINS) tools/generate-rooms-table.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate-rooms-table.py -o "$@" "resources/mappings.json" $(ROOM_BINS)

gen/entity-data.wiz: resources/entities.json tools/generate-entity-data.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate-entity-data.py -o "$@" "resources/entities.json"

gen/ms-patterns-table.wiz: resources/ms-export-order.json tools/generate-ms-patterns-table-wiz.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate-ms-patterns-table-wiz.py -o "$@" "resources/ms-export-order.json"

gen/entities.wiz: resources/entities.json resources/ms-export-order.json tools/generate-entities-wiz.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate-entities-wiz.py -o "$@" "resources/entities.json" "resources/ms-export-order.json"

gen/metasprites/%.wiz gen/metasprites/%.bin: resources/metasprites/%/_metasprites.json resources/ms-export-order.json tools/convert-metasprite.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/convert-metasprite.py --ppu-output "gen/metasprites/$*.bin" --wiz-output "gen/metasprites/$*.wiz" "resources/metasprites/$*/_metasprites.json" "resources/ms-export-order.json"


define __update_metasprite_dependencies
gen/metasprites/$(firstword $1).wiz gen/metasprites/$(firstword $1).bin: $2
endef

$(foreach d, $(METASPRITE_SPRITESETS), $(eval $(call __update_metasprite_dependencies, $d, $(wildcard resources/metasprites/$d/*.png))))




.PHONY: resources
resources: $(RESOURCES)
$(BINARIES): $(RESOURCES)


$(BINARY): $(RESOURCES)


.PHONY: resources
resources: $(RESOURCES)

DIRS := $(sort $(dir $(RESOURCES)))

$(RESOURCES): $(DIRS)
$(DIRS):
	mkdir -p "$@"


.PHONY: clean
clean:
	$(RM) $(BINARY)
	$(RM) $(RESOURCES)


.PHONY: clean-wiz
clean-wiz:
	$(MAKE) -C wiz clean


