
BINARY         := audio-driver.bin
LOADER_BIN     := loader.bin

WIZ            := ../wiz/bin/wiz


.DELETE_ON_ERROR:
.SUFFIXES:
MAKEFLAGS      += --no-builtin-rules


SOURCES	       := $(wildcard src/*.wiz src/*/*.wiz)


AUDIO_SAMPLES   = $(wildcard samples/*.wav)

RESOURCES      := gen/common_data.bin


# Python interpreter
# (-bb issues errors on bytes/string comparisons)
PYTHON3  := python3 -bb

COMMON_PYTHON_SCRIPTS = $(wildcard tools/_*.py) tools/wav2brr.py



.PHONY: all
all: $(BINARY) $(LOADER_BIN)

$(LOADER_BIN): src/loader.wiz src/common_memmap.wiz
	'$(WIZ)' -s wla -I src --system=spc700 src/loader.wiz -o '$@'

$(BINARY): $(SOURCES) $(RESOURCES)
	'$(WIZ)' -s wla -I src --system=spc700 src/audio-driver.wiz -o '$(BINARY)'


gen/common_data.bin: samples/_samples.json resources/mappings.json resources/sound_effects.txt $(AUDIO_SAMPLES) tools/compile_common_data.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/compile_common_data.py -o '$@' samples/_samples.json resources/mappings.json resources/sound_effects.txt



# Build gen/ directory if it doesn't exist
$(RESOURCES): gen/
gen/:
	mkdir -p '$@'


.PHONY: resources
resources: $(RESOURCES)
$(BINARIES): $(RESOURCES)


.PHONY: clean
clean:
	$(RM) $(BINARY)
	$(RM) $(RESOURCES)


