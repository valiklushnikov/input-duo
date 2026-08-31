# The device is the document — design

**Date:** 2026-08-31
**Status:** approved for planning
**Scope:** `configurator/` only. No firmware, no protocol, no binary format changes.

## Why

The configurator now reads its configuration from the device at startup. That
made an older assumption false: the project file is no longer where the truth
lives, but the program still treats it as an open document. It asks "save
before closing?" about work that will be read back off the board next time
anyway. It reports "local changes: unsaved" against a file the operator may
never have created. The two stories contradict each other, and the operator is
left to work out which one to believe.

The device is the document. A file is a copy of it — useful when the board
dies, when a second board needs the same setup, or when someone wants to keep
a known-good version. It is not the thing being edited.

## What changes, in one sentence

"Changed" stops meaning "differs from the file" and starts meaning "differs
from the device".

Everything else in this spec follows from that.

## 1. The reference point moves

`ProjectSession` already has the machinery. `baseline` is the state the project
is compared against, and `dirty` is `project != baseline`. Today `baseline` is
set in exactly one place: `ProjectSession.save`, when a file is written.

After this change, `baseline` is set when the project and the device agree:

- when a configuration is read from the device and adopted, and
- when a project is successfully written to the device.

Saving a copy to a file no longer touches it. `device_matches` — which compares
the compiled project against the device's hash — stays as it is; it answers the
same question by a different route and remains the authority for whether a
write is needed.

## 2. What is removed

**Autosave**, entirely: `configurator/src/duo_input/persistence/autosave.py`
(189 lines), the timer in `MainWindow.__init__`, `autosave_now`,
`offer_recovery`, `_ask_recovery`, and the `Recovery` type. It exists to
protect unsaved edits between runs — a concept that disappears with the
document. It is also already broken: `open_project` calls `autosave.discard()`
before `offer_recovery` can read it, so the prompt is unreachable whenever a
last-project file exists. Deleting it retires that defect rather than fixing it.

**The last-project memory**: `reopen_last_project`, `last_project_path`,
`_remember_project` and the `projects/last` settings key. Startup gets its
configuration from the device.

**The close prompt**: `_confirm_close` and the save branch of `closeEvent`.
There is no unsaved document to lose. `closeEvent` keeps only what it needs to
tear down cleanly.

**The file chip** in the state strip, and the file name in the window title.

## 3. What remains, renamed

Two explicit actions, neither of which remembers anything:

- **Save a copy…** — always asks where. Writes the current configuration to a
  `.duoinput.json` file. Does not change `baseline`, does not set a "current
  file", does not affect what happens at startup.
- **Load a copy…** — always asks which. Replaces the current configuration with
  the file's contents. It does **not** touch `baseline`, so whether the result
  reads as changed follows from the truth: a copy that happens to match what
  the board holds reads as unchanged, and one that differs reads as needing a
  write. Nothing is asserted about the file; the comparison is with the device,
  as everywhere else.

`Write to device` is unchanged and becomes the only action that commits.

## 4. The state strip

Three chips become one, because after the reference point moves they were all
answering the same question:

| Now | After |
|---|---|
| `Local changes: unsaved / none` | removed — it now duplicates the chip below |
| `Project file: …` | removed |
| `Written to device: matches / differs / no link` | the only one left |

The old pair could report "local changes: none" and "written to device:
differs" at the same time. Both were true and the combination told the operator
nothing. With `baseline` set from the device, "changed" and "differs from the
device" are the same fact, and it is stated once.

The surviving chip keeps its three states, and they cover what the removed one
carried: *matches the device*, *differs from the device* (which is now also
what "you have unwritten edits" means), and *no link* — the case where nothing
can be compared because no board answered.

## 5. Attaching, after this change

1. The window opens.
2. It attaches to a device by itself, as it already does.
3. If a device answers, its configuration is read and becomes the project, with
   `baseline` set to it — so nothing reads as changed.
4. If no device answers, the program opens empty, and the status bar says no
   device was found and a copy can be loaded from a file.

Point 4 is a real loss and is accepted deliberately: today the last file would
have opened. Without a device and without an explicit load, there is nothing to
show, and inventing something would be the confusion this change exists to
remove.

**Step 3 belongs to every attach, not only to the one at startup.** The
operator asked for this in these words — *«до этого же мы читали плату при
старте! делаем без кнопки как и сначала было оговорено!»* — and the reason it
has to be every attach is that a board plugged in an hour after startup, or
swapped for a different one, is just as much a board whose configuration
nobody has seen. There is no control anywhere that asks on the operator's
behalf, so a board that arrived late was simply never read: the window went on
showing the previous board's configuration, marked the project as pending, and
offered nothing but Write — which overwrites the board that had just arrived —
to clear the warning.

What that costs is paid where the answer lands, not where the request goes
out. Issuing the read is unconditional and costs a few chunks over the wire;
adopting it is refused while the session is dirty, and refused while an editor
page still holds text that has not been committed — the pages are asked to
commit first, so a name being typed when a board attaches becomes an edit the
dirty guard can see rather than a field that gets overwritten. And when the
package the board sends is byte-for-byte what the project already compiles to,
adoption is skipped entirely: the board has nothing to teach the project, while
decoding its answer would cost detail the binary cannot carry — a TEXT macro
travels as keystrokes and never as the Unicode it was compiled from, so
adopting a board's echo of what was just written to it would throw away the
text the operator typed.

## 6. What this costs

**A board that dies takes its configuration with it**, unless a copy was saved.
The program no longer keeps one automatically. This is the reason "Save a
copy…" stays rather than files being removed outright, and the reason the
status bar should mention it when a device is not found.

**Edits are lost on a crash.** With autosave gone, a program that dies with
unwritten changes loses them. The mitigation is that the window of exposure is
small — the operator's next action after editing is to write to the device —
and that autosave did not actually work today.

## 7. How this is verified

- `dirty` is false right after a device read and right after a successful
  write, and true after any edit in between.
- Saving a copy does not make a changed project read as unchanged.
- Loading a copy that differs from the device reads as needing a write; one
  that matches what the board holds reads as matching. The file itself is
  never what the comparison is against.
- Closing the window with unwritten changes does not prompt.
- Starting with no device leaves an empty project and says so.
- Starting with a device shows the device's configuration, unchanged.
- No test references `autosave`, `offer_recovery`, or `projects/last` when the
  work is done.

## 8. Risks

**Removing autosave removes a safety net that might one day work.** Accepted:
it does not work now, the concept it protects is being deleted, and keeping a
broken net is worse than having none, because it invites trust.

**An operator who expects their file to reopen will find an empty program.**
Mitigated by the status bar message naming "Load a copy…" explicitly.

**`dirty` is load-bearing elsewhere**: it guards against a device read
overwriting edits in progress. Moving its reference point must not weaken that
guard — a read that lands while the operator is editing must still be refused.

## 9. Open questions

None.
