
BINARY         := audio-driver.bin

WIZ            := ../wiz/bin/wiz


.DELETE_ON_ERROR:
.SUFFIXES:
MAKEFLAGS      += --no-builtin-rules


SOURCES	       := $(wildcard src/*.wiz src/*/*.wiz)


AUDIO_SAMPLES   = $(wildcard samples/*.wav)

RESOURCES	= $(patsubst samples/%.wav,gen/%.brr, $(AUDIO_SAMPLES))


# Python interpreter
# (-bb issues errors on bytes/string comparisons)
PYTHON3  := python3 -bb

COMMON_PYTHON_SCRIPTS =



.PHONY: all
all: $(BINARY)

$(BINARY): $(SOURCES) $(RESOURCES)
	'$(WIZ)' -s wla -I src --system=spc700 src/audio-driver.wiz -o '$(BINARY)'


gen/%.brr: samples/%.wav tools/wav2brr.py $(COMMON_PYTHON_SCRIPTS)
	$(PYTHON3) tools/wav2brr.py --loop -o '$@' 'samples/$*.wav'



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


