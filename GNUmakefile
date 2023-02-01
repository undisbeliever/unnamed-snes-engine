
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


COMMON_PYTHON_SCRIPTS = tools/_json_formats.py tools/_snes.py tools/_ansi_color.py tools/_common.py
ALL_PYTHON_SCRIPTS := $(wildcard tools/*.py)

# Python interpreter
# (-bb issues errors on bytes/string comparisons)
PYTHON3  := python3 -bb


.PHONY: all
all: $(BINARY)


$(BINARY): $(INTERMEDIATE_BINARY) tools/insert_resources.py $(ALL_PYTHON_SCRIPTS) $(RESOURCES_SRC)
	$(PYTHON3) tools/insert_resources.py -o '$(BINARY)' '$(RESOURCES_DIR)' '$(INTERMEDIATE_BINARY:.sfc=.sym)' '$(INTERMEDIATE_BINARY)'
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



# Test if the audio driver needs recompiling
__UNUSED2__ := $(shell $(MAKE) --quiet --question -C 'audio')
ifneq ($(.SHELLSTATUS), 0)
  $(INTERMEDIATE_BINARY): audio
endif

.PHONY: audio
audio audio/audio-driver.bin:
	$(MAKE) -C audio

$(INTERMEDIATE_BINARY): audio/audio-driver.bin


gen/interactive-tiles.wiz: resources/mappings.json tools/generate_interactive_tiles_wiz.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_interactive_tiles_wiz.py -o '$@' 'resources/mappings.json'

gen/room-events-function-tables.wiz: resources/mappings.json tools/generate_room_events_function_tables.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_room_events_function_tables.py -o '$@' 'resources/mappings.json'

gen/room-events.wiz: resources/mappings.json tools/generate_room_events_wiz.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_room_events_wiz.py -o '$@' 'resources/mappings.json'

gen/resources.wiz: resources/mappings.json audio/resources/mappings.json tools/generate_resources_wiz.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/generate_resources_wiz.py -o '$@' 'resources/mappings.json' 'audio/resources/mappings.json'

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
	$(MAKE) -C audio clean
	$(RM) $(BINARY) $(INTERMEDIATE_BINARY)
	$(RM) $(GEN_SOURCES)


.PHONY: clean-wiz
clean-wiz:
	$(MAKE) -C wiz clean


