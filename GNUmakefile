
BINARY := game.sfc

INTERMEDIATE_BINARY := gen/game-no-resources.sfc

RESOURCES_DIR := resources


.DELETE_ON_ERROR:
.SUFFIXES:
MAKEFLAGS += --no-builtin-rules


SOURCES	  := $(wildcard src/*.wiz src/*/*.wiz)

GEN_SOURCES  := gen/resources.wiz
GEN_SOURCES  += gen/interactive-tiles.wiz
GEN_SOURCES  += gen/room-events-function-tables.wiz
GEN_SOURCES  += gen/room-events.wiz
GEN_SOURCES  += gen/entities.wiz
GEN_SOURCES  += gen/death-functions-table.wiz
GEN_SOURCES  += gen/ms-drawing-functions.wiz
GEN_SOURCES  += gen/arctan-table.wiz
GEN_SOURCES  += gen/cosine-tables.wiz

RESOURCES_SRC := $(wildcard $(RESOURCES_DIR)/*.json $(RESOURCES_DIR)/* $(RESOURCES_DIR)/*/* $(RESOURCES_DIR)/*/*/*)


AUDIO_DRIVER_SRC := $(wildcard audio-driver/*.wiz)
AUDIO_DRIVER_BINARIES := gen/audio-loader.bin gen/audio-driver.bin
AUDIO_DRIVER_DATA     := gen/audio-blank-song.bin

MLB_FILE  := $(patsubst %.sfc,%.mlb,$(BINARY))
SYM_FILES := $(patsubst %.sfc,%.sym,$(INTERMEDIATE_BINARY)) $(patsubst %.bin,%.sym,$(AUDIO_DRIVER_BINARIES))

COMMON_PYTHON_SCRIPTS = $(wildcard tools/unnamed_snes_game/*.py tools/unnamed_snes_game/*/*.py)

# Python interpreter
# (-bb issues errors on bytes/string comparisons)
PYTHON3  := python3 -bb


.PHONY: all
all: $(BINARY) $(MLB_FILE)


$(BINARY): $(INTERMEDIATE_BINARY) tools/insert_resources.py $(COMMON_PYTHON_SCRIPTS) $(RESOURCES_SRC)
	$(PYTHON3) tools/insert_resources.py -o '$(BINARY)' '$(RESOURCES_DIR)' '$(INTERMEDIATE_BINARY:.sfc=.sym)' '$(INTERMEDIATE_BINARY)'


$(MLB_FILE): $(INTERMEDIATE_BINARY) $(AUDIO_DRIVER_BINARIES) tools/sym_to_mlb.py
	$(PYTHON3) tools/sym_to_mlb.py -o '$@' --hirom $(SYM_FILES)


$(INTERMEDIATE_BINARY): wiz/bin/wiz $(SOURCES) $(GEN_SOURCES) $(AUDIO_DRIVER_BINARIES) $(AUDIO_DRIVER_DATA)
	wiz/bin/wiz -s wla -I src src/main.wiz -o $(INTERMEDIATE_BINARY)



.PHONY: audio-driver
audio-driver: $(AUDIO_DRIVER_BINARIES)

gen/audio-loader.bin: audio-driver/loader.wiz audio-driver/common_memmap.wiz audio-driver/io-commands.wiz
	wiz/bin/wiz --system=spc700 -s wla audio-driver/loader.wiz -o '$@'

gen/audio-blank-song.bin: audio-driver/blank-song.wiz audio-driver/data-formats.wiz
	wiz/bin/wiz --system=spc700 audio-driver/blank-song.wiz -o '$@'

gen/audio-driver.bin: $(AUDIO_DRIVER_SRC)
	wiz/bin/wiz --system=spc700 -s wla audio-driver/audio-driver.wiz -o '$@'



.PHONY: wiz
wiz wiz/bin/wiz:
	$(MAKE) -C wiz


# Test if wiz needs recompiling
__UNUSED__ := $(shell $(MAKE) --quiet --question -C 'wiz' bin/wiz)
ifneq ($(.SHELLSTATUS), 0)
  $(BINARY): wiz
  $(AUDIO_DRIVER_BINARIES): wiz
endif


gen/interactive-tiles.wiz: resources/mappings.json tools/generate_interactive_tiles_wiz.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_interactive_tiles_wiz.py -o '$@' 'resources/mappings.json'

gen/room-events-function-tables.wiz: resources/mappings.json tools/generate_room_events_function_tables.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_room_events_function_tables.py -o '$@' 'resources/mappings.json'

gen/room-events.wiz: resources/mappings.json tools/generate_room_events_wiz.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_room_events_wiz.py -o '$@' 'resources/mappings.json'

gen/resources.wiz: resources/mappings.json tools/generate_resources_wiz.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_resources_wiz.py -o '$@' 'resources/mappings.json'

gen/ms-drawing-functions.wiz: resources/ms-export-order.json tools/generate_ms_drawing_functions.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_ms_drawing_functions.py -o '$@' 'resources/ms-export-order.json'

gen/entities.wiz: resources/entities.json resources/ms-export-order.json tools/generate_entities_wiz.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_entities_wiz.py -o '$@' 'resources/entities.json' 'resources/ms-export-order.json'

gen/death-functions-table.wiz: resources/entities.json tools/generate_death_functions_table_wiz.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_death_functions_table_wiz.py -o '$@' 'resources/entities.json'

gen/arctan-table.wiz: tools/generate_arctan_table.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_arctan_table.py -o '$@'

gen/cosine-tables.wiz: tools/generate_cosine_tables.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_cosine_tables.py -o '$@'


$(GEN_SOURCES): gen/
$(INTERMEDIATE_BINARY): gen/
gen/:
	mkdir 'gen/'


.PHONY: clean
clean:
	$(RM) $(BINARY) $(INTERMEDIATE_BINARY) $(AUDIO_DRIVER_BINARIES) $(AUDIO_DRIVER_DATA)
	$(RM) $(MLB_FILE) $(SYM_FILES)
	$(RM) $(GEN_SOURCES)


.PHONY: clean-wiz
clean-wiz:
	$(MAKE) -C wiz clean


