
METASPRITES_SRC  := $(wildcard metasprites/*/*.aseprite)
METASPRITES	 := $(patsubst %.aseprite,%.png,$(METASPRITES_SRC))


.PHONY: all
all: $(METASPRITES)


metasprites/%.png: metasprites/%.aseprite
	aseprite --batch "$<" --sheet-type "rows" --sheet-width 128px --sheet "$@"


.PHONY: clean
clean:
	$(RM)	$(METASPRITES)


