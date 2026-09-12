# Duo Input binary configuration format

All integers are unsigned little-endian. Records are serialized field-by-field; no native
structure layout is part of the ABI. Version 1 packages are canonical: profiles appear in ID
order, strings and data records appear in the order described below, all reserved bytes and
alignment padding are zero, and no region may overlap or leave trailing bytes.

The maximum package size is the generated `BINARY_CONFIG_MAX_BYTES` value (368640 bytes).
Schema-major mismatches are incompatible. Schema-minor 1 is written by this version; it assigns
the binding record's former reserved bytes to the source qualifier. Readers accept any minor while
all flags remain zero, so minor-0 packages with zero-filled binding source bytes remain compatible.

## Stable values

The generated `MacroStepType` values are the sole ABI source for step types: KEY_TAP=1,
KEY_DOWN=2, KEY_UP=3, CONSUMER_TAP=4, TEXT=5, DELAY=6,
SET_KEYBOARD_ROUTE=7, SET_MOUSE_ROUTE=8, SET_PROFILE=9.

All stable binary-config u8 enums are generated from `protocol/schema.json`. Python domain
models re-export the generated types and C++ `config/format.hpp` aliases them:

| Type | Values |
| --- | --- |
| KeyboardRoute | PC1=1, PC2=2, BOTH=3 |
| MouseRoute | PC1=1, PC2=2 |
| TargetMode | INHERIT=0, PC1=1, PC2=2, BOTH=3 |
| MouseRouteCommand | PC1=1, PC2=2, TOGGLE=3 |
| TextLayout | US=1, RU=2, UA=3 |
| TriggerKind | KEYBOARD_USAGE=1, MOUSE_BUTTON=2 |
| BindingMode | REPLACE=1, ADD=2 |
| ActionKind | RUN_MACRO=1, TOGGLE_KEYBOARD_ROUTE=2, SET_KEYBOARD_ROUTE=3, TOGGLE_MOUSE_ROUTE=4, SET_MOUSE_ROUTE=5, SET_PROFILE=6 |

Profile IDs are exactly 1..8 in ascending descriptor order. Macro IDs are 1..255 and unique
within a profile. Keyboard usage codes are 1..255. Mouse buttons are 1..5 and have zero
modifiers. Every route, target, command, and layout field uses its context-specific type above.

## Package header (64 bytes)

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 4 | ASCII magic `DUOC` |
| 4 | 1 | schema major (generated `SCHEMA_VERSION_MAJOR`) |
| 5 | 1 | schema minor (generated `SCHEMA_VERSION_MINOR`) |
| 6 | 1 | flags: bit 0 `SYNCHRONISED_CONTROL`, bits 1..7 zero |
| 7 | 1 | reserved, zero |
| 8 | 4 | exact total package length |
| 12 | 4 | CRC-32/IEEE |
| 16 | 1 | profile count, generated `PROFILES` (8) |
| 17 | 1 | active profile ID, 1..8 |
| 18 | 1 | profile descriptor size, 36 |
| 19 | 1 | reserved, zero |
| 20 | 4 | profile table offset, exactly 64 |
| 24 | 4 | string blob offset, exactly profile-table end |
| 28 | 4 | string blob length |
| 32 | 4 | data blob offset, aligned-up string-blob end |
| 36 | 4 | data blob length, ending exactly at total length |
| 40 | 24 | reserved, zero |

CRC-32 uses the reflected IEEE polynomial and the same result as `zlib.crc32`. Coverage is the
entire declared package with bytes 12..15 treated as zero. Length and region checks precede CRC
access. The string blob is followed by zero bytes to the next four-byte boundary.

`SYNCHRONISED_CONTROL` makes every route change move the keyboard and the
mouse to the same computer. A reader that does not know a flag rejects the
package: a refusal is visible, while running the configuration without the
flag would look like working hardware that switches only one device.

## Profile descriptor (36 bytes)

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | profile ID |
| 1 | 1 | keyboard route (`KeyboardRoute`) |
| 2 | 1 | mouse route (`MouseRoute`) |
| 3 | 1 | text layout (`TextLayout`) |
| 4 | 3 | RGB color |
| 7 | 1 | reserved, zero |
| 8 | 4 | absolute UTF-8 name offset |
| 12 | 2 | UTF-8 name byte length |
| 14 | 2 | binding count, at most generated limit 128 |
| 16 | 4 | absolute binding table offset |
| 20 | 2 | binding record size, 12 |
| 22 | 2 | macro count, at most generated limit 32 |
| 24 | 4 | absolute macro table offset |
| 28 | 2 | macro record size, 24 |
| 30 | 2 | reserved, zero |
| 32 | 4 | reserved, zero |

Names contain at most 48 Unicode code points, are strict UTF-8, and contain no NUL. The string
blob contains first the eight profile names, then macro names in profile and macro table order,
with no separators, padding, overlap, or trailing bytes.

## Data records

For each profile, the data blob contains its binding table, macro table, then for each macro its
step table and aligned payloads. The next profile follows immediately. Every table starts on a
four-byte boundary. A zero-count table has its canonical cursor as its offset.

### Binding record (12 bytes)

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | trigger kind |
| 1 | 1 | trigger code |
| 2 | 1 | keyboard modifiers; zero for mouse |
| 3 | 1 | binding mode |
| 4 | 1 | action kind |
| 5 | 1 | action argument |
| 6 | 2 | source USB vendor ID |
| 8 | 2 | source USB product ID |
| 10 | 1 | source USB interface number |
| 11 | 1 | reserved, zero |

The all-zero `(vendor ID, product ID, interface number)` source means any source. Otherwise both
vendor ID and product ID must be nonzero; interface zero is valid. Exact
`(kind, code, modifiers, source)` trigger duplicates are invalid within a profile. RUN_MACRO refers
to an existing macro ID. Toggle actions require argument zero. SET_KEYBOARD_ROUTE uses
KeyboardRoute. SET_MOUSE_ROUTE uses MouseRoute and therefore rejects value 3; mouse toggle has
its own action kind. SET_PROFILE refers to profile 1..8.

### Macro descriptor (24 bytes)

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | macro ID |
| 1 | 1 | target mode (`TargetMode`) |
| 2 | 2 | reserved, zero |
| 4 | 4 | absolute UTF-8 name offset |
| 8 | 2 | name byte length |
| 10 | 2 | step count, at most generated limit 64 |
| 12 | 4 | absolute step table offset |
| 16 | 2 | step record size, 12 |
| 18 | 2 | reserved, zero |
| 20 | 4 | reserved, zero |

### Step descriptor (12 bytes)

| Offset | Size | Field |
| ---: | ---: | --- |
| 0 | 1 | generated MacroStepType |
| 1 | 1 | reserved, zero |
| 2 | 2 | payload byte length |
| 4 | 4 | absolute payload offset, four-byte aligned |
| 8 | 4 | reserved, zero |

Payloads follow the step table in step order, with zero alignment padding. KEY_TAP contains
exactly `(modifier u8, nonzero keyboard usage u8)`. KEY_DOWN and KEY_UP each contain exactly one
nonzero keyboard usage u8 and no modifier byte. CONSUMER_TAP contains a nonzero u16 usage. TEXT
contains one or more `(modifier u8, nonzero keyboard usage u8)` pairs; source
Unicode is never stored, and compiled length is bounded only by the u16 record and package
limits. DELAY contains `(minimum_ms u16, maximum_ms u16)`, with minimum <= maximum <= the
generated 60000 ms limit. SET_KEYBOARD_ROUTE contains one KeyboardRoute byte;
SET_MOUSE_ROUTE contains one MouseRouteCommand byte, including TOGGLE=3. SET_PROFILE contains one
profile ID byte. Unknown step types and every other payload shape are invalid.

## Reader requirements

Readers validate every `offset + count * record_size` and `offset + length` using checked
arithmetic before reading. They reject incorrect magic/major/CRC, reserved bytes that are not
zero, or header flags this build does not know, noncanonical or unaligned order, incorrect record
sizes and counts, invalid UTF-8/enums/references/duplicates, overlap, padding, trailing data, or
truncation. A borrowed firmware `ConfigView` may only be constructed by successful full
validation.
