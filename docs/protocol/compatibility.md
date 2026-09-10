# CDC v1 compatibility contract

The configuration endpoint exists only on U1 (MAIN) through its PC1 CDC ACM interface. U2
has no CDC interface, configuration store, or configuration endpoint. All multibyte application
fields below are little-endian. CDC transport framing, limits, flags, CRC, and message IDs come
from `protocol/schema.json` and the generated codecs.

## Version and capability negotiation

`HELLO` carries `client_capabilities:u32`. A valid response is `DEVICE_INFO` with:

```text
error:u8, protocol_major:u8, protocol_minor:u8,
negotiated_capabilities:u32, active_generation:u32, active_profile:u8,
active_sha256:bytes[32]
```

The active hash is all zeroes and generation is zero when no configuration is installed.
Different protocol major versions are incompatible: U1 returns `INCOMPATIBLE_MAJOR` and does
not permit state-changing operations in that session. Minor-version peers use only the bitwise
intersection of client and U1 capabilities. A command whose capability was not negotiated
returns `UNSUPPORTED_CAPABILITY` without changing state.

## Sequences and retries

The first valid request sequence may be any `u16`. Each new request must then use the previous
accepted sequence plus one modulo 65536. A response uses its request sequence. An exact retry
of the immediately previous encoded request receives the byte-identical cached response and
does not repeat side effects. Reusing that sequence for different bytes, or sending any other
stale/out-of-order sequence, returns `BAD_SEQUENCE` and increments the diagnostic counter.

An unsolicited `CAPTURE_EVENT` uses the next sequence, advances sequence state, and ends the
capture. A one-shot timeout consumes and caches the request normally but suppresses that
response; an exact retry obtains the cached result without applying the operation twice.

## Direct reply payloads

Every direct reply begins with `error:u8`. Except for `HELLO` → `DEVICE_INFO`, a reply uses the
request type and sequence; request/reply direction is contextual because v1 flags are zero.
`DEVICE_INFO` and `CAPTURE_EVENT` are output-only, and requests using either type return
`INVALID_REQUEST`.

| Error | Value | Meaning |
|---|---:|---|
| `OK` | 0 | Operation accepted |
| `INVALID_REQUEST` | 1 | Wrong direction or malformed application payload |
| `INCOMPATIBLE_MAJOR` | 2 | Protocol major cannot interoperate |
| `UNSUPPORTED_CAPABILITY` | 3 | Required capability was not negotiated |
| `BAD_SEQUENCE` | 4 | Request is stale, reordered, or conflicts with the retry cache |
| `BUSY` | 5 | An exclusive operation is already active |
| `BAD_STATE` | 6 | Required session/transaction/device state is absent |
| `BAD_SIZE` | 7 | Declared size is outside the allowed range or incomplete |
| `BAD_CHUNK` | 8 | Chunk offset/extent is not the next valid sequential chunk |
| `BAD_HASH` | 9 | Staged bytes do not match the declared SHA-256 |
| `INVALID_CONFIG` | 10 | The complete package fails binary configuration validation |
| `PHYSICAL_CONFIRMATION_REQUIRED` | 11 | Factory reset lacks live physical confirmation |

Successful or shape-preserving direct payloads are:

| Request | Reply bytes after `error` |
|---|---|
| `GET_STATUS` | `active_profile:u8, capture_active:u8, staging_active:u8, release_all_count:u32` |
| `GET_ACTIVE_CONFIG_INFO`, `READ_CONFIG_BEGIN` | `generation:u32, length:u32, sha256:bytes[32]` |
| `READ_CONFIG_CHUNK` | `offset:u32, bytes` |
| `WRITE_CHUNK` | `accepted_next_offset:u32` |
| `GET_DIAGNOSTICS` | five `u32`: bad CRC, disconnect, timeout, bad sequence, aborted staging; then the link state (`answering:u8, endpoint_usb:u8, frames_sent:u32, crc_errors:u32, echoed_frames:u32`), the endpoint report (`drops:u8, release_ms:u16`), `dropped_commands:u32`, and `runtime_fault:u8` |
| `GET_HID_DESCRIPTOR_CAPTURE` | format version `u8`, flags `u8` (`present`, `truncated`), `vendor_id:u16`, `product_id:u16`, `interface_number:u8`, `original_size:u16`, `captured_size:u16`, then exactly `captured_size` descriptor bytes (maximum 256) |
| `GET_HID_REPORT_SETS` | format version `u8`, source count `u8`, then each source's identity and bounded accepted/rejected parsed report entries |
| `PING` | the request payload unchanged (at most 1023 bytes so the error prefix fits) |

All other successful direct replies contain only `error=OK`. Malformed fixed-size requests
return `INVALID_REQUEST` and perform no state mutation. Read and write chunks are at most 512
bytes and write chunks are strictly sequential.

`GET_HID_DESCRIPTOR_CAPTURE` requires negotiated capability
`HID_DESCRIPTOR_DIAGNOSTICS` (`2048`) and has an empty request. Including its
error byte, the reply is 12 bytes when no observation is present and at most
268 bytes. It is intentionally separate from `GET_DIAGNOSTICS`, whose maximum
remains 1020 bytes. Therefore new hosts omit the request against old firmware,
and old hosts negotiate no new capability or command against new firmware.

`GET_HID_REPORT_SETS` (command `23`) requires negotiated capability
`HID_REPORT_SET_DIAGNOSTICS` (`4096`) and also has an empty request. Its
successful little-endian reply starts with `error:u8, version:u8 (=1),
source_count:u8`. Each source then carries `device_address:u8, vendor_id:u16,
product_id:u16, interface_number:u8, accepted_count:u8, rejected_count:u8,
rejected_overflow:u8`; each accepted entry is `role:u8, report_id:u8,
minimum_body_bytes:u8`, and each rejected entry is `role:u8, report_id:u8,
reason:u8`. With eight sources and eight accepted plus eight rejected entries
per source, the reply is at most 459 bytes. This separate reply leaves the
`GET_DIAGNOSTICS` maximum exactly 1020 bytes.

Compatibility works in both directions. A new host connected to old firmware
does not negotiate capability 4096, sends no command 23, and records the report
set state as unknown while existing diagnostics remain usable. An old host
connected to new firmware does not offer capability 4096, so U1 does not
negotiate it and all existing commands and reply shapes remain unchanged. This
addition changes neither the 8-byte `CAPTURE_EVENT` payload nor saved
configuration and binding formats.

Runtime HID dispatch uses the parsed accepted `role` and Report ID entries for
each interface. VID/PID values identify and qualify a source for diagnostics,
capture, and bindings; there is no VID/PID allowlist in report dispatch.

## Transactional configuration guarantees

U1 owns two configuration slots, A and B. On power-up it selects the valid slot with the highest
generation. `WRITE_BEGIN` fixes the total size and SHA-256; `WRITE_CHUNK` appends only at the
next offset; `WRITE_VERIFY` checks length, hash, and package validity. `WRITE_COMMIT` validates
the complete package again with the device configuration decoder, writes the inactive slot, and
writes its generation last before making it active.

Until successful commit, the active slot never changes. Abort, disconnect, timeout, bad CRC,
bad chunk, bad hash, invalid configuration, and power loss therefore leave the previous active
configuration intact. Factory reset clears both slots only after a successful arm and commit
while physical confirmation remains asserted. `STOP_AND_RELEASE_ALL` remains a safety command:
it increments its counter and clears capture and staging state. So do `HELLO` and
`FACTORY_RESET_COMMIT`: a handshake starts a new owner of the session and a reset erases
everything the question was about, and in both cases a capture left running would go on
swallowing the operator's input until its ten-second timeout.

The groups after the five counters in `GET_DIAGNOSTICS` were appended in that order and
each is optional: a reply that stops after any group is still a valid reply, so a host reads
what it recognises and leaves the rest. After `runtime_fault` come four more, in this order,
each self-delimiting so the one behind it can always be found:

- the **latency block** - `bucket_count:u8`, `bucket_count - 1` `u32` edges, then two streams of
  `count:u32, max_us:u32` and `bucket_count` `u32` buckets (keyboard, then mouse). It carries its
  own edges so a host can never disagree with the device about what a bucket means.
- the **peripheral block** - two fixed records of
  `attached:u8, ready:u8, kind:u8, vid:u16, pid:u16, buttons:u8, descriptor_bytes:u16, sha256:bytes[32]`,
  the keyboard role slot then the mouse one. Both always: an empty slot is a fact about the run.
- the **backend block** - `backend:u8, counter_count:u8`, then that many `u32` counters. The count
  is what lets a backend publish none of them: CH375 keeps none of these host-stack figures and
  sends a count of zero rather than twelve zeros a reader would take for measurements.
- the **host block** - `field_bytes:u8`, then that many bytes:
  `init_flags:u8, clk_hz_at_begin:u32, clk_hz_now:u32, sof_frame_count:u32, root_port_state:u8,
  root_port_connects:u16, core1_passes:u32, mount_events:u16, umount_events:u16,
  hid_mount_events:u16, ep_slots_opened:u8, ep_max_failed_count:u8, max_pass_gap_us:u32,
  max_sof_gap:u16, root_port_resets:u16, hub_mount_events:u16, ep_slot_map:u32,
  host_event_counts:u32, enum_progress_mask:u32, long_pass_count:u32,
  long_pass_total_ms:u32, core1_min_sp:u32, ep_transfer_flags:u32,
  xfer_completions_at_attach:u32, enum_stall_recoveries:u32`. Its length byte plays the same
  role the backend
  block's count does: an image with no host stack sends zero, which is a different fact from an
  older firmware that sends no block at all.

  `init_flags` bit 0 says the host stack was already active before the input core's own bring-up
  ran, which makes bits 1-3 (the configure result, the init result, and `tuh_inited()` after
  both) meaningless as evidence - they report success for calls that did nothing. **Bit 0 is the
  only field that separates a host started on the wrong core from a healthy one.**

  `clk_hz_at_begin` keeps its historical wire name but now means `clk_sys` when Core 1 began.
  The PIO build selects and settles 120 MHz as the first work in `main()`, before peripherals or
  Core 1, so a current healthy image reports 120 MHz here and in `clk_hz_now`. Older valid images
  report 125/120; readers must not turn that version-dependent pair into a fault verdict.
  `clk_hz_now` is the divider clock whenever bit 0 is clear.

  `root_port_state` packs `initialized`, `connected`, `suspended` and `is_fullspeed` as bits 0-3.
  `sof_frame_count` is raw root-port activity below the host stack: zero and static means the bus
  is not being driven at all, climbing while every backend counter is still zero means it is being
  driven and nothing on it answers. `root_port_connects` is a **lower bound**, not a total:
  nothing below the host stack reports an attach edge, so the device polls the line once per
  input-core pass, and an attach and detach that both fall between two passes leaves no trace -
  zero is strong evidence that nothing attached rather than proof of it. `core1_passes` unchanged
  across two reads twenty seconds apart means the input core stopped.

  `mount_events`, `umount_events`, and `hid_mount_events` are saturating callback counts. TinyUSB
  deliberately excludes hub addresses from its application mount and unmount callbacks, so
  `hub_mount_events` separately counts rising edges from `tuh_mounted(CFG_TUH_DEVICE_MAX + 1)`.
  It is a saturating, polled lower bound; a nonzero value proves the hub configured even when
  `mount_events` remains zero. The
  endpoint fields are high-water readings from Pico-PIO-USB's fixed endpoint pool: a nonzero
  `ep_slots_opened` proves endpoint open ran, and `ep_max_failed_count == 3` means the pinned
  host exhausted its transaction retry limit. `max_pass_gap_us` measures blocking between input
  passes. `max_sof_gap` is the largest SOF-frame advance between consecutive
  Core-1 service passes; large values can be normal while `tuh_task()` blocks
  during enumeration and do not mean the SOF ISR was starved. `root_port_resets` is a saturating,
  polled lower bound over connected suspended-to-running cycles.

  The last nine fields read inside the window where enumeration stops. Everything in front of
  them says whether the host stack started and whether anything ever attached; by the time a
  board reaches this window both answers are yes, and none of the fields above can say which
  step stopped it.

  `ep_slot_map` packs one byte per host endpoint-pool slot 0-3, slot 0 in the LOW byte: device
  address in bits 7-5, an **open** bit in bit 4, direction (1 = IN) in bit 3 and endpoint number
  in bits 2-0. A byte of zero means the slot is closed. The open bit is load-bearing rather than
  decorative: the endpoint this field exists to find is device address 0's control endpoint,
  whose address, direction and endpoint number are all zero, and without that bit it would
  encode as zero and read as an empty slot. The map is a **live** reading and follows the pool
  back down when an endpoint closes; `ep_slots_opened` above it stays the high-water count, so
  the pair says both how far enumeration ever got and where it stands now.

  `host_event_counts` packs every event the device's host stack has queued since boot: accepted
  attaches in bits 0-7, removals in bits 8-15 and completed transfers in bits 16-31, each
  saturating. Two attaches means a device behind the hub was accepted as well as the hub itself.
  An event the host stack's own queue **dropped** is not counted, by construction - the count is
  what the stack accepted, not what the hardware raised.

  `enum_progress_mask` says how far each device address got and is **sticky**, so an address that
  reached a state for one service pass and lost it before the request arrived is still reported.
  For address `a` in 1..5, bit `a - 1` says it reached the configured state and bit `8 + a - 1`
  says its device descriptor was read - which is true only once the address has been assigned.
  Addresses 1-4 are devices; 5 is the hub. An address nothing was plugged into reads as zero in
  both halves, and that is NORMAL.

  `long_pass_count` and `long_pass_total_ms` are the input core's blocking budget: passes that
  blocked for more than 20 ms, and the sum of those gaps in whole milliseconds, both saturating.
  **Neither is a fault reading.** A healthy board produces several: the host stack blocks inside
  its service call for about 500 ms enumerating the root port and another 450 ms for each device
  behind a hub, so a count of two totalling roughly 950 ms is what a board that started
  enumerating one device behind a hub is expected to show. Zero would mean no enumeration was
  ever attempted.

  `core1_min_sp` is the lowest stack pointer the input core was ever seen at while its host stack
  was queueing an event. **Zero means no sample was ever taken** - no host event has been queued
  - and is not a stack that reached address zero. On the shipping RP2040 image the input core's
  stack runs from `__StackOneBottom` to `__StackOneTop`, 2 KB placed at a fixed address by the
  linker script, and a reading below that bottom is an overflow. The exact addresses come from
  the linked image rather than from this document.

  `ep_transfer_flags` is a **live**, non-sticky byte per endpoint-pool slot 0-3, slot 0 in the
  LOW byte. A closed slot is zero. The bits are the firmware's own `kEpXfer*` constants, and a
  guard derives this list from `firmware/u1_main/pio_usb/backend.hpp` rather than trusting it:

  | bit | firmware constant |
  |---:|---|
  | 0 | `kEpXferOpen` |
  | 1 | `kEpXferHasTransfer` |
  | 2 | `kEpXferHostOut` |
  | 3 | `kEpXferData1` |
  | 4 | `kEpXferNeedPre` |
  | 5 | `kEpXferSetupStaged` |
  | 6 | `kEpXferStalled` |
  | 7 | `kEpXferAborted` |

  What a byte READS AS is the spelling the configurator's report prints, and every example below
  is checked against that decoder rather than written out beside it. Between them these bytes set
  every bit above, so no bit's spelling is unguarded:

  | byte | reads as |
  |---|---|
  | `0x00` | `closed` |
  | `0x01` | `open idle` |
  | `0x03` | `open active DATA0 host-in` |
  | `0x07` | `open active DATA0 host-out` |
  | `0x0B` | `open active DATA1 host-in` |
  | `0x27` | `open active SETUP host-out` |
  | `0x11` | `open idle PRE` |
  | `0xC1` | `open idle stalled aborted` |

  Direction and PID are deliberately suppressed when the endpoint is idle, because Pico-PIO-USB
  retains the preceding stage in `is_tx` and `data_id` after a transfer completes and an idle
  endpoint reading as one mid-transfer is the exact confusion this field removes. `need_pre`,
  `stalled` and `transfer_aborted` are true live endpoint properties and are reported either way.

  Because this reading is live, take it TWICE, twenty seconds apart, like every other
  procedure in this file. A single `active` sample can be the ordinary instant between a
  transfer being queued and the next SOF servicing it; the same `active` sample twice is a
  transfer that is not progressing. Which of the two it is matters: a SETUP the device refuses
  cannot stay staged, because the library errors it out after three consecutive frames and
  `ep_max_failed_count` rises to 3 - so `active SETUP` on both reads beside
  `ep_max_failed_count == 0` means the endpoint was never serviced at all rather than serviced
  and refused. A DATA or status stage is different: a NAK is retried for ever and never touches
  `failed_count`, so `active DATA1` twice beside a zero failure count is a device that is
  answering the token and refusing the data.

  `xfer_completions_at_attach` snapshots the saturated transfer-completion total at the latest
  accepted attach event. Subtracting it from bits 16-31 of `host_event_counts` gives the control
  stages that completed **after that attach**, cancelling every hub-control transfer before it -
  including one `SET_FEATURE(PORT_POWER)` per hub port, which is why the reading needs to know
  nothing about the hub in front of the device. **The configurator does that subtraction and
  prints it as its own row**, so nothing below asks anybody to do arithmetic; the two raw numbers
  are still reported beside it. Both saturate at 65535, and a saturated total makes the
  difference a fiction rather than a small number - the subtracted row says so instead of
  printing one. A snapshot taken before any attach was accepted is a zero, not a baseline, and
  that row says that too.

  For the stable wedge this pair was added to decide, the next read is interpreted as follows.
  First confirm the board is in that state at all - `attach 2`, `enum_progress_mask` showing
  `dev5 configured+descriptor` with `dev1`-`dev4` all `nothing`, and `ep_slot_map` slot 2 holding
  **`dev0 ep0`, in either direction**. The direction bit of a control endpoint tracks whichever
  stage last ran on it and is not part of the identity; gating on it would throw away one of the
  states this table exists to name.

  Then read the verdict off the slot-2 byte of `ep_transfer_flags` and the subtracted row.
  `PRE` may be set in any of these patterns and only says the downstream device is low speed:

  | Slot-2 byte | reads as | completions since the latest attach | verdict |
  |---|---|---:|---|
  | `0x01` | `open idle` | 5 | The first request to address 0 never reached the wire: `tuh_descriptor_get_device()` returned false before `hcd_setup_send`. |
  | `0x27` | `open active SETUP host-out` | 5 | The SETUP was accepted by the endpoint layer and is still staged. On one read this can be the instant before the next SOF; on two reads twenty seconds apart, with `ep_max_failed_count` still 0, the endpoint is never being serviced. |
  | `0x0B` | `open active DATA1 host-in` | 6 | The SETUP completed; the eight-byte device-descriptor DATA-IN stage is outstanding. |
  | `0x0F` | `open active DATA1 host-out` | 7 | The SETUP and the DATA-IN stage completed; the zero-length status-OUT stage is outstanding. |
  | `0x01` | `open idle` | 8 or more | The whole three-stage request completed and the stop is further along; read `enum_progress_mask` and `ep_max_failed_count` instead. |
  | anything else | - | - | **Not in this table. Record both rows verbatim and stop.** A pair this table does not list - a `SETUP` byte beside a delta of 7, say - is not a reading to interpret, and guessing which half to believe is how the previous four rounds each cost a bench trip. |

  Part of that delta is fixed and belongs to the hub rather than the device: after the host stack
  accepts a port attach it finishes the port's own paperwork before the downstream device is
  asked anything at all.

  | hub work after an accepted port attach | control stages |
  |---|---:|
  | `GET_PORT_STATUS` | 3 |
  | `CLEAR_FEATURE(C_PORT_RESET)` | 2 |
  | **total, already included in every delta above** | 5 |

  So the smallest delta any of the rows above can carry is that total, and it means the first
  request to the device produced nothing. The subtracted row's own label carries the same number,
  and a guard ties the table, the label and the firmware-side constant together.

  A delta of 5 or more with slot 2 CLOSED, or `ep_slot_map` showing an address other than `dev0`
  in it, means the board is no longer in this state at all and the table does not apply.

  `enum_stall_recoveries` counts only **synthetic duplicate-attach recovery requests submitted**
  by this backend, not every suspected stall and not successful enumeration restarts. It is
  saturating and **zero is not a health verdict**: the watchdog can be inapplicable,
  safety-suppressed while any downstream address 1-4 is mounted, or capped. A rise proves that
  this bounded recovery request repeated, **not its cause**. For this bench role it submits at
  most two requests while one address-0 transfer stays continuously outstanding, with a new
  two-second observation window after every request, cap, or safety suppression. One retry can
  clear a transient wedge and the second distinguishes that from a repeatable one; a third adds
  no new binary outcome while every hub-side enumeration can block Core 1 for about 475 ms.
  The cap resets only when the address-0 predicate goes quiet, so a later independent enumeration
  is eligible without an autonomous retry loop.

  The pool is sampled before `tuh_task()`: a transfer that starts inside host service begins its
  watchdog window on the next pass, rather than being charged the host stack's legitimate blocking
  waits. A request is refused unless the published HCD topology names this image's hub-backed
  port (`rhport=1`, hub address 5, nonzero hub port); that is the honest boundary available without
  reading TinyUSB's private `_dev0.enumerating`. In particular it never manufactures a root-port
  attach that could start a fresh enumeration and reset the working hub. It also never calls
  `tuh_deinit`: that API exists, but calls `process_removing_device(rhport, 0, 0)` and would tear
  down the hub and every working child.

  `ep_max_failed_count == 0` over the observed frames already excludes the broad no-response/no-
  bytes case: a zero byte reaches Pico-PIO-USB's error path and increments `failed_count`; a real
  NAK resets it. The pinned `usb_out_transaction` still discards the validated handshake result,
  so a repeated valid-byte misclassification remains a dependency defect but is probably not the
  root cause: that case would need the same framing slip to look like a valid NAK every time.
  `ep_max_failed_count == 0` therefore strongly favours a **real NAK**. No inert firmware field
  can discriminate:
  the RX buffer is cleared before the ISR returns and `transfer_started` is visible only while that
  ISR owns Core 1. The remaining discriminators are a GP0/GP1 logic analyser or the pinned
  upstream example against the same downstream device and status stage.

  `clk_hz_now`, `sof_frame_count` and `root_port_state` are sampled once per device main-loop
  pass and held until the request arrives, so a reading can be up to one pass old - far below the
  second that the "read it twice" procedures need. `dropped_commands` counts input the device produced
and could not deliver - a nonzero value means what a computer is holding no longer matches
what the operator did. `runtime_fault` says what the output runtime is doing about its queue
at this instant: `0` no fault, `1` a queue that is refusing commands. It is not a latch. The
runtime releases everything once when it notices the loss and resumes after a pass in which
nothing was refused, so a host that reads `1` is looking at a burst still in progress, where
a nonzero `dropped_commands` only says one happened at some point since boot.
