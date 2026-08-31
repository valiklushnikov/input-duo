# Reading the configuration back from the device — design

**Date:** 2026-08-31
**Status:** approved for planning
**Scope:** `configurator/` only. No firmware and no protocol changes.

## Why

The configurator can write a configuration to the device and it can save one
to a file. It cannot read one back. The consequence showed up the hard way:
a project file was overwritten, the settings were still running on the board,
and there was no way to recover them — the bytes were right there and nothing
could turn them into a project.

The binary format already carries everything needed. It stores profile names,
macro names, colours, routes, layouts, bindings and macro steps, and its header
carries the offsets and record sizes to find them. Only the reader is missing.

## What this changes

At startup, a device that answers has its configuration read and shown. The
operator sees what is actually running on the hardware without pressing
anything. When no device answers, the last project file opens instead, exactly
as it does today.

**Explicitly out of scope.** No "Read from device" button: the whole point is
that it happens on its own. No firmware change, no protocol change — this reads
the format that already ships. No attempt to recover the source text of macro
steps (see *What cannot be recovered*).

## 1. The reader

A new module, `duo_input/domain/config_reader.py`, mirroring the existing
`config_binary.py` that writes the format. Two public functions:

```python
def parse_device_config(package: bytes) -> DeviceConfig
def binary_to_project(package: bytes) -> DeviceProject
```

The format is self-describing: the header holds the profile count, the active
profile, the descriptor size, the table offsets and the string-blob offset, and
each descriptor holds its own counts, offsets and record sizes. Nothing has to
be guessed.

`protocol/config_format.md` already states what a reader must do, and this one
follows it rather than inventing rules:

> Readers validate every `offset + count * record_size` and `offset + length`
> using checked arithmetic before reading. They reject incorrect
> magic/major/flags/reserved/CRC, noncanonical or unaligned order, incorrect
> record sizes and counts, invalid UTF-8/enums/references/duplicates, overlap,
> padding, trailing data, or truncation.

Every rejection raises `ProjectError`, the exception the domain already uses for
a configuration that cannot be trusted. A malformed package is a thing to report
in the status bar, never a reason for the program to fall over: this input comes
off a wire.

## 2. What cannot be recovered

One thing does not survive the round trip, and the format says so plainly:

> TEXT contains one or more `(modifier u8, nonzero keyboard usage u8)` pairs;
> source Unicode is never stored.

A macro step typed as text is compiled to keystrokes before it is written, and
the string is not kept. Reading it back yields the keystrokes, which are what
the device actually performs — the behaviour is identical, the editing
affordance is not.

The model already expresses this: `MacroStep` carries `source_text` beside its
compiled `payload`, and a step read from the device has the payload but no
source text. That is the marker — no new field is needed. Today the macro
editor renders such a step as `step.source_text or ""`, an empty box that looks
like a bug; it must instead say the step came from the device and show its
keystrokes, so the operator is told why they can no longer edit it as a
sentence. Guessing the text back from keystrokes is rejected: the
mapping is not one-to-one, and a wrong guess would rewrite the macro on the next
save without anyone noticing.

Everything else is exact: profile names, colours, routes, layout, bindings,
macro names and every other step type.

## 3. Startup order

The device wins. This is a deliberate choice with a cost, taken because the
question the operator asks on opening the program is "what is my device doing?"

1. The window is shown and attaches to a device by itself, as it already does.
2. If a device answered, its configuration is read and becomes the session.
   The session has no file path — it came from hardware, not from disk — so
   the first save asks where to put it.
3. If no device answered, the last project file opens, as it does today.
4. The autosave recovery is offered afterwards, unchanged. This is the
   safeguard for edits that were never written to the device: they live in the
   autosave and are offered back.

**One protection beyond that.** If the session already has unsaved local edits
by the time the read finishes, the read does not replace it. The read is
asynchronous — several chunks over a serial link — and an operator who started
typing during it must not have their work discarded by an answer that arrived
late. In that case the device's configuration is dropped and a line in the
status bar says so.

## 4. What already exists

`DeviceService.read_config()` is implemented and unused by the interface. It
requests the package, reads it in chunks, tracks progress and verifies the
SHA-256 digest against what the device declared before reporting success. The
package arrives through `operation_succeeded`.

So the device side needs no work. What is missing is the parse, and the wiring
at startup.

## 5. How this is verified

**The round trip is the main test.** Three vectors already ship —
`valid_full.bin`, `valid_minimal.bin`, `valid_wide_usages.bin`. For each:
parse it, compile the result, and compare the bytes to the original. Identical
bytes prove the parse is complete and lossless. `valid_full.bin` is the strong
case: it carries Cyrillic and emoji in names, a macro with nine steps, and the
maximum macro ID.

**Malformed input must not crash.** Truncation at every length, a corrupted
CRC, a bad magic, an offset pointing past the end, a count that overflows its
table: each raises `ProjectError` and nothing else.

**The startup order.** A device present means its configuration is shown; a
device absent means the last file opens; a session with unsaved edits is not
replaced by a late read.

**On real hardware.** Write a known project to the board, restart the
configurator, and confirm the same project comes back.

## 6. Risks

**The parse disagrees with the writer.** The round trip catches this, which is
why it is the primary test rather than an afterthought.

**A device configuration replaces work the operator wanted.** Mitigated by the
autosave recovery and by the unsaved-edits protection in section 3. This is the
cost of "the device wins", accepted deliberately.

**Text macros silently lose their source.** Mitigated by marking the step in
the editor rather than letting it look like an ordinary text step.

## 7. Open questions

None. The conflict rule, the text-macro treatment and the scope are settled.
