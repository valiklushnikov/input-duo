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
| `PING` | the request payload unchanged (at most 1023 bytes so the error prefix fits) |

All other successful direct replies contain only `error=OK`. Malformed fixed-size requests
return `INVALID_REQUEST` and perform no state mutation. Read and write chunks are at most 512
bytes and write chunks are strictly sequential.

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
  root_port_connects:u16, core1_passes:u32`. Its length byte plays the same role the backend
  block's count does: an image with no host stack sends zero, which is a different fact from an
  older firmware that sends no block at all.

  `init_flags` bit 0 says the host stack was already active before the input core's own bring-up
  ran, which makes bits 1-3 (the configure result, the init result, and `tuh_inited()` after
  both) meaningless as evidence - they report success for calls that did nothing. The two clock
  readings must match: the PIO dividers are computed once from the first and never recomputed, so
  a bus brought up at one clock and running at another is off by the ratio between them.
  `root_port_state` packs `initialized`, `connected`, `suspended` and `is_fullspeed` as bits 0-3.
  `sof_frame_count` is raw root-port activity below the host stack: zero and static means the bus
  is not being driven at all, climbing while every backend counter is still zero means it is being
  driven and nothing on it answers. `core1_passes` unchanged across two reads twenty seconds apart
  means the input core stopped. `dropped_commands` counts input the device produced
and could not deliver - a nonzero value means what a computer is holding no longer matches
what the operator did. `runtime_fault` says what the output runtime is doing about its queue
at this instant: `0` no fault, `1` a queue that is refusing commands. It is not a latch. The
runtime releases everything once when it notices the loss and resumes after a pass in which
nothing was refused, so a host that reads `1` is looking at a burst still in progress, where
a nonzero `dropped_commands` only says one happened at some point since boot.
