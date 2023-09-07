
METASPRITES_SRC  := $(wildcard metasprites/*/*.aseprite dynamic-metasprites/*.aseprite)
METASPRITES	 := $(patsubst %.aseprite,%.png,$(METASPRITES_SRC))

IMAGES_SRC       := $(wildcard */*.aseprite)
IMAGES           := $(patsubst %.aseprite,%.png,$(IMAGES_SRC))


.PHONY: all
all: $(METASPRITES) $(IMAGES)


%.png: %.aseprite
	aseprite --batch "$<" --save-as "$@"


metasprites/%.png: metasprites/%.aseprite
	aseprite --batch "$<" --sheet-type "rows" --sheet-width 128px --sheet "$@"

dynamic-metasprites/%.png: dynamic-metasprites/%.aseprite
	aseprite --batch "$<" --sheet-type "rows" --sheet-width 128px --sheet "$@"


.PHONY: clean
clean:
	$(RM)	$(METASPRITES) $(IMAGES)


