
BINARY := game.sfc


.DELETE_ON_ERROR:
.SUFFIXES:
MAKEFLAGS += --no-builtin-rules


rwildcard_all = $(foreach d,$(wildcard $(addsuffix /*,$(1))),$d $(call rwildcard_all, $d))


SOURCES	  := $(call rwildcard_all, src)
RESOURCES :=



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



$(BINARY): $(RESOURCES)


.PHONY: resources
resources: $(RESOURCES)

$(RESOURCES): gen/
gen/:
	mkdir gen


.PHONY: clean
clean:
	$(RM) $(BINARY)
	$(RM) $(RESOURCES)


.PHONY: clean-wiz
clean-wiz:
	$(MAKE) -C wiz clean


