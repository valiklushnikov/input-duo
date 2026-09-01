# The Keychron M3 receiver and its side button — an unfinished investigation

**Status: fixed and bench-verified on 2026-09-02.** The historical sections
below retain the failed hypotheses; see *Resolution — 2026-09-02* for the
confirmed cause and repair.

Written 2026-09-01, against the working tree as it stands: the previous agent's
uncommitted composite-receiver work, plus the changes listed under *What is in
the tree* below.

## The complaint

A Keychron M3's 2.4 GHz receiver on one CH375, an Aula F75's receiver on the
other. The cursor moves; pressing the mouse's side button — bound to switching
the mouse between PC1 and PC2 — freezes the cursor, it never appears on PC2,
and pressing again does not bring it back.

## What is proven

Each of these was measured, and the measurement is named so it can be repeated.

**The side button does not travel on the mouse endpoint.** Twelve hundred
samples at twenty a second across a run containing several presses, and the
buttons byte of the mouse report was never once non-zero. Only Report ID 3
arrives on endpoint 2.

**The receiver stops answering on the mouse endpoint, permanently.** In the
failed state the channel stays in `Ready`, `polls` climbs at its normal rate,
and every single transaction comes back a failure — a NAK. Ninety seconds of
it were recorded while the operator moved the mouse continuously. `succ` was
zero on every line.

**It survives everything but losing power.** A U1 reflash, a USB bus reset and
a complete re-enumeration all leave it dead: after one, `life=1/0/1` with
`reports=0` against 27937 polls. Unplugging the receiver and plugging it back
in is the only thing that revives it. So whatever is wedged is in the
receiver's own firmware, not in U1's state and not in the CH375.

**U1 loses nothing of what it does receive.** While the mouse works, `success`
and `reports` rise in exact step - 105/105, 128/128, 130/130. Nothing is being
discarded between the controller and the input pipeline.

**Nothing downstream of U1's pipeline is involved.** In the failed state the
keyboard on the other CH375 goes on working and its counter goes on rising, so
Core 1, the pipeline, the output runtime and the link are all alive. `U2` is
answering (`endpoint_answering` true) throughout, so "it never appears on PC2"
is not U2 being absent.

**U1 has no recovery from this and never will have.** `kDeviceLostUs` requires
`failed_polls_ > 0`, and a NAK counts as an answer and clears it - so a device
that NAKs for ever is indistinguishable from an idle one. Worth knowing, but
**not worth fixing for this symptom**: the wedge survives re-enumeration, so
tearing the device down and bringing it back would not cure it either. An
earlier draft of this record proposed that repair; it would not have worked.

## What is refuted

Listed because each cost time, and two of them were mine.

**Not the poll rate on its own.** The receiver's `bInterval` is 1 ms - it asks
to be polled faster than anything U1 does. And 74 polls a second killed it in
one configuration and spared it in another.

**Not the data toggle, and not the CH375.** The wedge survives a full
re-enumeration, which resets both ends' toggles, and the CH375 reports a clean
detach and re-attach when the receiver is pulled.

**Not `SET_IDLE`.** Suspected on the keyboard side earlier the same day and
refuted there too: twenty seconds and 2417 polls of an untouched keyboard
produced `reports +0`, so no idle timer is running on either receiver.

**Not "drain the service endpoint on a deadline".** Mine. Ten milliseconds
between visits, derived from the two rates that had bracketed it, and the
receiver died anyway.

**Not "one input poll between visits".** Also mine, and the more careful of the
two: strict alternation, which is exactly the shape that had survived. It died.
The surviving run alternated at 148 polls a second; the same alternation at 500
did not survive.

## The six runs

| input polls/s | service polls/s | total | outcome |
|---|---|---|---|
| 148 | ~19 (only after a NAK) | 167 | died |
| 148 | 0 | 148 | died |
| 74 | 0 | 74 | died |
| **74** | **74** | **148** | **lived** |
| 420 | 100 (every 10 ms) | 520 | died |
| 250 | 250 (alternating) | 500 | died |

Two things follow, and they do not sit together comfortably. Draining the
service endpoint is **necessary** - rows three and four differ only in that.
And it is **not sufficient** - row six keeps the same proportion and dies.

**The one survival is a single observation, from a single press.** It may be
luck. Nothing should be built on it until it has been reproduced.

## What is in the tree

Three changes, and only the first is proven on its own terms.

**Removed the probe build's deliberate interface swap** (`hid_parser.cpp`).
Under `DUO_CH375_PROBE` the parser used to select the *unknown* interface so
that its report descriptor would be fetched and printed. It did its job - the
Keychron vendor descriptor is captured - but it meant the probe and release
images routed different endpoints, so every measurement taken on the probe
described a device nobody was repairing. It also left the probe reading vendor
packets as boot mouse reports: `54 E2 01 02` arrives as buttons 0x54 held down.
**Keep this.**

**The poll interval is taken from the largest report a device has actually
sent** rather than from `wMaxPacketSize` (`device.cpp`, `device.hpp`).
The Keychron endpoint declares 64 bytes for reports of 8, which costed its
polls four and a half times too slowly - 145 a second where the wire affords
500. The reasoning is sound and independent of this bug, and there is a test
with a mutation behind it. **But it makes this symptom worse**, because the
rate it unlocks is on the far side of whatever the receiver tolerates. Do not
ship it until the rate question is settled.

**The service endpoint is polled every other poll** rather than only after the
input endpoint NAKs (`device.cpp`, `device.hpp`). The old rule offered it only
the polls a moving mouse never declines, which is why the fault needs a hand on
the mouse to appear. This is the change that is *necessary but not sufficient*.
**Unproven as a repair.**

Tests added along the way, which are worth keeping whatever happens to the
code: the fake can now model a receiver that wedges irreversibly when a service
packet is ignored (`wedges_if_auxiliary_ignored`), can report a mouse on every
poll rather than one per test slice (`always_reports`), and counts tokens per
endpoint (`tokens_to`). Without those three the defect could not be expressed
in a test at all - the previous agent's test for the same feature passes on
firmware that dies in six seconds, because it models a receiver that recovers.

## How to measure it again

`scratchpad/mouse_watch.py COM18 <seconds>` prints a line a second - polls,
successes, reports, state, and the bytes of the last report - and an extra line
whenever the buttons byte is non-zero. It needs the probe build. There is no
window: a frozen pointer cannot click a Finish button, which spoiled two runs
before this existed.

The reading: `polls` climbing with `succ` at zero and the state still `Ready` is
the failure. `succ` and `reports` rising together is health.

Two earlier instruments were useless and are recorded so nobody rebuilds them:
one asked Qt for mouse-move events, which only arrive while the pointer is over
its own window; another printed only when the report bytes changed, which makes
a dead channel and an idle one look identical.

## The next step

Reproduce the one configuration that survived - alternation with the interval
still taken from the declared packet size, about 74 polls a second each way -
and press the button several times, twice over. If it survives both, that is a
repair worth having even at 74 Hz on the pointer, and the rate can then be
raised a step at a time to find the ceiling. If it does not, the single
survival was luck and the model has to start again.

A candidate image for exactly that configuration is built and waiting.

## Resolution — 2026-09-02

The receiver was not a two-endpoint device. Its 91-byte configuration
descriptor declares three interrupt-IN endpoints: mouse EP2, service EP4 and
keyboard EP1. Polling both auxiliary endpoints stopped the receiver wedge;
prior attempts which drained only EP4 could not do that.

The side button is reported on EP1 as a keyboard-shaped shortcut and is mapped
to runtime mouse button code 3 (the configurator's Button 4). A complete
hardware capture finally exposed the intermittent part which the original
eight-byte probe had hidden:

```
press:   01 01 00 4F 00 00 00 00 03
release: 01 00 00 00 00 00 00 00 03
```

The receiver also emits the same pair with a zero ninth byte, and can change
the modifier and usage in separate frames. The first decoder required a zero
tail, so it silently rejected the `03` variant; controlled trials showed six
EP1 packets received but only one Down/Up pair recognised. The final decoder
accepts usage 03 only in the ninth byte, recognises the side key by usage 4F
independently of the modifier transition, and suppresses repeated states.

Bench verification after the fix:

- 10–20 successive Keychron side-button route changes: all successful;
- cursor remained live and at normal speed;
- wired Trust GTX105 still moved and clicked correctly when substituted on
  the same CH375.
