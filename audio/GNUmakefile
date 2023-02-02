
BINARY         := audio-driver.bin
LOADER_BIN     := loader.bin

WIZ            := ../wiz/bin/wiz


.DELETE_ON_ERROR:
.SUFFIXES:
MAKEFLAGS      += --no-builtin-rules


SOURCES	       := $(wildcard src/*.wiz src/*/*.wiz)



.PHONY: all
all: $(BINARY) $(LOADER_BIN)

$(LOADER_BIN): src/loader.wiz src/common_memmap.wiz
	'$(WIZ)' -s wla -I src --system=spc700 src/loader.wiz -o '$@'

$(BINARY): $(SOURCES)
	'$(WIZ)' -s wla -I src --system=spc700 src/audio-driver.wiz -o '$(BINARY)'


.PHONY: clean
clean:
	$(RM) $(BINARY)

