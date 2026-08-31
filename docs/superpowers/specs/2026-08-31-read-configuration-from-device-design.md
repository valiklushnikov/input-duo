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

A device that answers has its configuration read and shown. The operator sees
what is actually running on the hardware without pressing anything. When no
device answers, the program opens empty and the status bar says so.

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

## 3. Attach order

The device wins. This is a deliberate choice with a cost, taken because the
question the operator asks on opening the program is "what is my device doing?"

1. The window is shown and attaches to a device by itself, as it already does.
2. If a device answered, its configuration is read and becomes the session.
   The session has no file path — it came from hardware, not from disk — so
   the first save asks where to put it.
3. If no device answered, the program opens empty, and the status bar says a
   device was not found and names the way back in: load a copy from a file, or
   attach a board.

**Step 2 belongs to every attach, not only the one at startup.** A board
plugged in an hour after the window opened, or swapped for a different one, is
just as much a board whose configuration nobody has seen, and there is no
control anywhere that asks on the operator's behalf. Issuing the read is
unconditional and costs a few chunks over the wire. What it costs beyond that
is paid where the answer lands, not where the request goes out.

**Two things can refuse the answer**, and both are decided when it arrives.

*The board already holds what is on screen.* If the package the device sends is
byte-for-byte what the project compiles to, adoption is skipped: the board has
nothing to teach the project, and decoding its answer would cost detail the
binary cannot carry (section 2). Only the baseline is re-agreed.

*The session has unsaved local edits.* The read is asynchronous — several
chunks over a serial link — and an operator who started typing during it must
not have their work discarded by an answer that arrived late. In that case the
device's configuration is dropped and a line in the status bar says so.

**There is one guard, not two.** Text an editor page is still holding — a name
typed but not yet committed, because the name fields turn text into a command
on `editingFinished` — is committed before the answer is weighed, so it becomes
an edit the dirty guard can see rather than a field the repaint overwrites.
Committing is not a second refusal of its own: it gives the single dirty guard
something to look at, and that guard then decides. Every page is asked before
any one page's answer is applied, because applying one repaints them all.

**What later changes replaced.** As approved, this section had two more steps:
if no device answered the last project file opened, and the autosave recovery
was offered afterwards. Both are gone, and so is the startup-only read.
*The device is the document*
(`2026-08-31-the-device-is-the-document-design.md`) removed the last-project
memory and autosave outright — a file is a copy of the device, not the thing
being edited — and its section 5 is where the every-attach read, the identity
skip and the commit-first step were settled. They are written out here so the
read feature's own spec stops describing behaviour the program no longer has.

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

**The attach order.** A device present means its configuration is shown; a
device absent means the program opens empty and says so; a session with unsaved
edits is not replaced by a late read; a board that arrives after startup is
read like any other; a board holding exactly what is on screen is not adopted
at all; and a name still being typed when a board attaches is committed first,
so the dirty guard sees it.

**On real hardware.** Write a known project to the board, restart the
configurator, and confirm the same project comes back.

## 6. Risks

**The parse disagrees with the writer.** The round trip catches this, which is
why it is the primary test rather than an afterthought.

**A device configuration replaces work the operator wanted.** Mitigated by the
unsaved-edits protection in section 3 — which, with autosave gone (see *What
later changes replaced*), is the whole of the mitigation now. This is the cost
of "the device wins", accepted deliberately.

**Text macros silently lose their source.** Mitigated by marking the step in
the editor rather than letting it look like an ordinary text step.

## 7. Open questions

None. The conflict rule, the text-macro treatment and the scope are settled.
