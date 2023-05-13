unnamed-snes-game Code Conventions
==================================


Naming Conventions
==================

 * namespaces: lower snake_case (ie, `namespace interactive_tiles`)
 * namespace for a data table: Upper CamelCase
 * Constant data: UpperCamelCase (ie, `CosineTable`)
 * Constant vales: SCREAMING SNAKE CASE (ie, `MAP_WIDTH`)
 * Labels: UpperCamelCase (ie, `ZeroX:`)
 * Structs: UpperCamelCase (ie, `struct RoomData`)
 * Enums: UpperCamelCase (ie, `enum ScrollDirection : u8`)
 * Variables: lower camelCase (ie, `frameCounter`)
 * Functions: lower snake_case (ie, `func sort_active_entities()`)
    * Functions that must be called during Force-Blank must be suffixed with `__forceblank`
    * Functions that must be called during V-Blank (or Force-Blank) must be suffixed with `__vblank`
    * Some complex inline functions are suffixed with `__inline`.  This declares the function should
      only be called from a single location.
    * Some functions are called in different contexts or states.  A double-underscore (`__`) suffix
      is used to describe the context/state (ie, `set_state__walking()`, `set_state__attacking()`,
      `process__walking()`, `process__attacking()`).


Names that start with a single underscore (`_`) are private and should not be read, modified or
called outside of the scope or file they are declared in.

Names that start with a double-underscore (`__`) are considered ultra-private and **must not** be
read, modified or called outside the scope or file they are declared in.  Only ultra-private
functions are allowed to override the calling conventions listed below, so long as the infringement
is clearly documented in the function's preceding comments.



Calling Conventions
===================

The `A`, `X`, and `Y` registers are caller-saved, unless

 * The function argument is `entityId` in the `Y` register and the function is expected to be called
   in the entity-loop.  The `Y` register (`entityId`) will be saved by the callee.  The function
   should be tagged with a `KEEP: ` comment, listing the `Y` register as unmodified.

 * The function is a private function (prefixed with an underscore) and the function does not modify
   a register.  These functions must be labelled with a `KEEP:` tag, listing the register that is
   guaranteed to not be modified by the function.  The function must not be called outside of the
   source file it is defined in.


The Data Bank (`DB`) and Direct Page (`DP`) registers are callee-saved.



Entity Loop
-----------

Functions that are intended to be called inside the entity-loop should be aliased in the `entities`
namespace.  All aliased functions in the `entities` namespace that do not preserve (clobber) the
Y-register must be suffixed with `__clobbers_y`.



Register Sizes
--------------

All functions should be prefixed with a `mem` and idx` attribute to declare the register sizes.  Any
function that does not declare register size attributes can be called with 8 and/or 16 bit sized
registers.

Functions must return with the same register sizes they were called in.

There is no strict convention for function register sizes.  The following list is the most common
register sizes for the various contexts within the engine:
 * Main Loop: `#[mem8, idx8]`
 * Entity Loop: `#[mem8, idx8]`
 * MetaSprite rendering: `#[mem8, idx16]`
 * Setup (force-blank): `#[mem8, idx16]`
 * VBlank: `#[mem8, idx16]`



Data Bank
---------

The value of the Data Bank (`DB`) register depends on the current context.

 * Main-loop: `DB = 0x7e`
 * Setup (Force-Blank): `DB = 0x80`
 * VBlank: `DB = 0x80`

Functions must return with the same DB value they were called in.

Functions must be prefixed with a comment declaring the expected value of the `DB` register.
```
// DB = 0x7e
#[mem8, idx8]
func f() {
}
```

Whenever the `DB` register is changed, the next line must start with a comment detailing the new
value of the Data Bank register:
```
    push8(a = 0);
    data_bank = pop8();
// DB = 0
```

Some functions require the `DB` register to access low-RAM variables.  There functions are tagged
with `DB = lowram`, and can be called with a `DB` with a value of `0x00`-`0x3f`, `0x7e`, or
`0x80`-`0xbf`.

If the indented value of the `DB` register is unknown, a function can be tagged with `DB unknown`.
These functions must either:
 * Immediately set the `DB` register using the stack
 * Only access variables and constants using long addressing.



Direct Page
-----------

The Direct Page (`DP`) register must always be `0` when a function is called or returned.

Whenever the `DP` register is changed, the next line must start with a comment detailing the new
value of the Data Bank register:
```
    direct_page = aa = 0;
// DP = 0
```


