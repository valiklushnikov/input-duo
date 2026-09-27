# Phase E — post-materialization File Provider read boundary (2026-09-27)

Diagnostic only; nothing changed. Source: B-mixed-22 generation e2254a07… in the File
Provider domain, all 100 items materialized (0 dataless, 42,158,741 B). Control: the
byte-identical ordinary APFS folder ~/Desktop/fp-bench/D0. Same APFS volume (disk3s5,
same st_dev). Host log: 0 fp_fetch_started / FILE_READ during all tests.

## Direct clone evidence (not inferred from throughput)
- `clonefile(2)` and `copyfile(COPYFILE_CLONE_FORCE)` (no fallback) from the
  materialized FP items: 100/100 OK (same as from APFS).
- Shared physical extents (`fcntl F_LOG2PHYS_EXT`, `struct log2phys` pack(4)) of
  copy vs source, files ≥ 4 KiB (67) / ≥ 1 MiB (7):
  | source | clonefile | cp -c | ditto | ditto --noclone | Finder (AppleScript) | Finder Cmd+V |
  |---|---|---|---|---|---|---|
  | FP materialized | 67/67 shared | 67/67 | 67/67 | 0/67 (distinct) | 67/67 | 67/67 (also the Phase D paste dest) |
  | APFS | 67/67 | 67/67 | 67/67 | 0/67 (distinct) | 67/67 | 67/67 |
  `ditto --noclone` producing distinct extents validates the discriminator.

## Timing (s)
| op | APFS | FP materialized |
|---|---|---|
| cp -c FULL (100 / 42.16 MB) | 0.024 | 0.023 |
| ditto FULL | 0.038 | 0.030 |
| ditto --noclone FULL | 0.098 | 0.086 |
| cp -c SMALL (93 / 6.02 MB) | 0.032 | 0.031 |
| ditto SMALL (per-file) | 0.395 | 0.376 |
| cp -c LARGE (8.54 MB) | 0.008 | 0.004 |
| Finder AppleScript LARGE | 0.272 | 0.362 |
| Finder AppleScript FULL | 2.102 | 3.136 |
| **Finder Cmd+V FULL (manual)** | **0.666** | **1.036** |
Finder AppleScript SMALL (`whose size ≤`) is invalid (9–18 s spent evaluating the
filter before copying). Finder AppleScript is slower than Cmd+V in absolute terms.

## fileproviderd participation (live log stream)
- Finder from the FP domain: one NSFileCoordinator `provideItemAtURL` transaction per
  item via fileproviderd ("began/finished providing", "fast path"): Cmd+V 100 in
  0.79 s; AppleScript 200 (p50 1.15 ms each, sum 255 ms, serial ~7.6 ms apart).
- Finder from APFS: 0. cp -c / ditto from FP: 0 provide transactions (only FSEvents
  noise). Extension: 0 fetch events.

## Conclusion
- The data path is a clone for every tool and source: File Provider / VFS does NOT
  force byte reads or block the APFS clone path (not FILEPROVIDER_BOUNDARY).
- Tools take materially different paths: CLI copies bypass coordination (FP ≈ APFS);
  Finder coordinates each FP item through fileproviderd, costing ≈ +0.37 s on 100
  materialized files (×1.56 vs APFS Cmd+V) → CLASSIFICATION = MIXED.
- Phase D's 2.76 s first-byte→stable tail is NOT reproduced by a copy from the
  already-materialized domain (1.04 s): ≈ 1.7 s of it is specific to a Finder paste
  that STARTED on dataless items (Phase D: large files landed 0.3–0.7 s apart after
  the last materialization). That part is not yet attributed.
