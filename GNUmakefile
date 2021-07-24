
BINARY := game.sfc


.DELETE_ON_ERROR:
.SUFFIXES:
MAKEFLAGS += --no-builtin-rules


rwildcard_all = $(foreach d,$(wildcard $(addsuffix /*,$(1))),$d $(call rwildcard_all, $d))


SOURCES	  := $(call rwildcard_all, src)
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


MAP_SRC    := $(wildcard resources/maps/*.tmx)
RESOURCES  += $(patsubst resources/maps/%.tmx,gen/maps/%.bin, $(MAP_SRC))


METATILE_TILESETS = dungeon
RESOURCES  += $(patsubst %,gen/metatiles/%.bin, $(METATILE_TILESETS))



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



gen/%-2bpp-tiles.tiles gen/%-2bpp-tiles.pal &: resources/%-2bpp-tiles.png
	python3 tools/png2snes.py -f 2bpp -t gen/$*-2bpp-tiles.tiles -p gen/$*-2bpp-tiles.pal $<

gen/%-4bpp-tiles.tiles gen/%-4bpp-tiles.pal &: resources/%-4bpp-tiles.png
	python3 tools/png2snes.py -f 4bpp -t gen/$*-4bpp-tiles.tiles -p gen/$*-4bpp-tiles.pal $<

gen/%-8bpp-tiles.tiles gen/%-8bpp-tiles.pal &: resources/%-8bpp-tiles.png
	python3 tools/png2snes.py -f 8bpp -t gen/$*-8bpp-tiles.tiles -p gen/$*-8bpp-tiles.pal $<

RESOURCES += $(2BPP_TILES) $(2BPP_PALETTES)
RESOURCES += $(4BPP_TILES) $(4BPP_PALETTES)
RESOURCES += $(8BPP_TILES) $(8BPP_PALETTES)


gen/metatiles/%.bin: resources/metatiles/%-tiles.png resources/metatiles/%-palette.png resources/metatiles/%.tsx tools/convert-tileset.py
	python3 tools/convert-tileset.py -o "$@" "resources/metatiles/$*-tiles.png" "resources/metatiles/$*-palette.png" "resources/metatiles/$*.tsx"


gen/maps/%.bin:	resources/maps/%.tmx tools/convert-map.py
	python3 tools/convert-map.py -o "$@" "resources/maps/$*.tmx"



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


