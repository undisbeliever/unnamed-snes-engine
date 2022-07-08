
BINARY := game.sfc

INTERMEDIATE_BINARY := game-no-resources.sfc


.DELETE_ON_ERROR:
.SUFFIXES:
MAKEFLAGS += --no-builtin-rules



SOURCES	  := $(wildcard src/*.wiz src/*/*.wiz)


RESOURCES_DOT_BIN_SOURCES := $(wildcard resources/*/*8bpp-tiles.png)

RESOURCES  := gen/other-resources.bin

ROOMS_DIR  := resources/rooms
ROOMS_SRC  := $(wildcard $(ROOMS_DIR)/*.tmx)
RESOURCES  += gen/rooms.bin


METATILE_TILESETS = dungeon
RESOURCES  += $(patsubst %,gen/metatiles/%.bin, $(METATILE_TILESETS))

GEN_SOURCES  := gen/resources.wiz
GEN_SOURCES  += gen/interactive-tiles.wiz
GEN_SOURCES  += gen/entities.wiz
GEN_SOURCES  += gen/ms-patterns-table.wiz
GEN_SOURCES  += gen/arctan-table.wiz
GEN_SOURCES  += gen/cosine-tables.wiz


METASPRITE_SPRITESETS = common dungeon
RESOURCES  += $(patsubst %,gen/metasprites/%.bin, $(METASPRITE_SPRITESETS))
RESOURCES  += $(patsubst %,gen/metasprites/%.txt, $(METASPRITE_SPRITESETS))


COMMON_PYTHON_SCRIPTS = tools/_json_formats.py tools/_snes.py tools/_ansi_color.py tools/_common.py

# Python interpreter
# (-bb issues errors on bytes/string comparisons)
PYTHON3  := python3 -bb


.PHONY: all
all: $(BINARY)


$(BINARY): $(INTERMEDIATE_BINARY) tools/insert_resources.py tools/convert_metasprite.py tools/convert_other_resources.py tools/_entity_data.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/insert_resources.py -o $(BINARY) resources/mappings.json resources/entities.json gen/other-resources.bin $(INTERMEDIATE_BINARY:.sfc=.sym) $(INTERMEDIATE_BINARY)
	cp $(INTERMEDIATE_BINARY:.sfc=.sym) $(BINARY:.sfc=.sym)


$(INTERMEDIATE_BINARY): wiz/bin/wiz $(SOURCES) $(GEN_SOURCES)
	wiz/bin/wiz -s wla -I src src/main.wiz -o $(INTERMEDIATE_BINARY)


.PHONY: wiz
wiz wiz/bin/wiz:
	$(MAKE) -C wiz


# Test if wiz needs recompiling
__UNUSED__ := $(shell $(MAKE) --quiet --question -C 'wiz' bin/wiz)
ifneq ($(.SHELLSTATUS), 0)
  $(BINARY): wiz
endif


gen/metatiles/%.bin: resources/metatiles/%-tiles.png resources/metatiles/%-palette.png resources/metatiles/%.tsx resources/mappings.json tools/convert_mt_tileset.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/convert_mt_tileset.py -o '$@' 'resources/mappings.json' 'resources/metatiles/$*.tsx'


gen/interactive-tiles.wiz: resources/mappings.json tools/generate_interactive_tiles_wiz.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_interactive_tiles_wiz.py -o '$@' 'resources/mappings.json'


gen/other-resources.bin: resources/other-resources.json resources/mappings.json tools/convert_other_resources.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/convert_other_resources.py -o '$@' 'resources/mappings.json' 'resources/other-resources.json'

gen/rooms.bin: $(ROOMS_DIR) resources/mappings.json resources/entities.json tools/convert_rooms.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/convert_rooms.py -o '$@' 'resources/mappings.json' 'resources/entities.json' '$(ROOMS_DIR)'


gen/resources.wiz: resources/mappings.json tools/generate_resources_wiz.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_resources_wiz.py -o '$@' 'resources/mappings.json'

gen/ms-patterns-table.wiz: resources/ms-export-order.json tools/generate_ms_patterns_table_wiz.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_ms_patterns_table_wiz.py -o '$@' 'resources/ms-export-order.json'

gen/entities.wiz: resources/entities.json resources/ms-export-order.json tools/generate_entities_wiz.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_entities_wiz.py -o '$@' 'resources/entities.json' 'resources/ms-export-order.json'

gen/arctan-table.wiz: tools/generate_arctan_table.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_arctan_table.py -o '$@'

gen/cosine-tables.wiz: tools/generate_cosine_tables.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_cosine_tables.py -o '$@'

gen/metasprites/%.wiz gen/metasprites/%.txt: resources/metasprites/%/_metasprites.json resources/ms-export-order.json tools/convert_metasprite.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/convert_metasprite.py --bin-output 'gen/metasprites/$*.bin' --msfs-output 'gen/metasprites/$*.txt' 'resources/metasprites/$*/_metasprites.json' 'resources/ms-export-order.json'


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
	mkdir -p '$@'


.PHONY: clean
clean:
	$(RM) $(BINARY) $(INTERMEDIATE_BINARY)
	$(RM) $(GEN_SOURCES)
	$(RM) $(RESOURCES)


.PHONY: clean-wiz
clean-wiz:
	$(MAKE) -C wiz clean


