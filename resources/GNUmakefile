
METASPRITES_SRC  := $(wildcard metasprites/*/*.aseprite)
METASPRITES	 := $(patsubst %.aseprite,%.png,$(METASPRITES_SRC))


.PHONY: all
all: $(METASPRITES)


metasprites/%.png: metasprites/%.aseprite
	aseprite --batch "$<" --sheet-type "horizontal" --sheet-rows 8 --sheet "$@"


.PHONY: clean
clean:
	$(RM)	$(METASPRITES)


