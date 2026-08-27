# The CH375 command set, as WCH documents it

Every constant this firmware sends to a CH375 is written down here first, with
the section of the manufacturer's datasheet it came from. Nothing was copied
from an Arduino library or a forum post: those disagree with each other about
several of these values, and a wrong command byte on a USB host controller
does not fail loudly - it returns a plausible number.

## The documents

| Short name | Title | Where the copy came from |
|---|---|---|
| **DS1** | *USB Bus Interface Chip CH375 Datasheet (I)*, 20 pages | WCH, `wch-ic.com` download 13 (`CH375DS1.PDF`) |
| **DS2** | *CH375 Datasheet (II)*, Version 4, 6 pages | WCH, `CH375DS2.PDF` |

DS1 covers the chip, the interfaces and the general commands. DS2 covers the
host-mode transaction and control-transfer commands, and says so itself: DS1
page 4 ends its table with *"Please refer to Datasheet (II) for execution of
transaction commands and commonly used control transmission commands."*

## How the chip is addressed over a serial port

**DS1 section 6.2.2.** The serial interface exists only in USB host mode,
which is the mode this device uses.

> The serial data format of CH375 is 1 start bit, **9 data bits**, and 1 stop
> bit, in which the first 8 data bits are 1 byte of data and the last data bit
> is a command flag bit. When the bit 9 is 0, the data of the first 8 bits are
> written to CH375; when the bit 9 is 1, the first 8 bits are written to CH375
> as command codes.

So "send a command" and "send a data byte" are the same eight bits with a
different ninth bit, which is why the transport interface has two separate
operations rather than one. The default rate is 9600 bps after reset.

**This does not fit an RP2040's hardware UART**, which supports five to eight
data bits and no more. Driving these chips needs a nine-bit serial port built
from PIO, or the stick-parity trick on the PL011, or different hardware. The
choice belongs to the transport implementation; nothing above this line
depends on it.

### Baud rates (DS1 section 5.2)

`SET_BAUDRATE` takes two bytes, a division coefficient and a division
constant:

| Coefficient | Constant | Rate | Error |
|---|---|---|---|
| 02H | B2H | 9600 | 0.16% |
| 03H | CCH | 115200 | 0.16% |
| 03H | C4H | 100000 | 0% |
| 03H | FAH | 1000000 | 0% |
| 03H | FDH | 2000000 | 0% |

Formula, from the same table: coefficient 02H gives `750000 / (256 - constant)`
and coefficient 03H gives `6000000 / (256 - constant)`.

The chip takes about 1 ms to change rate and *replies at the new rate*, so the
host has to switch its own port immediately after sending the command.

## General commands (DS1)

| Code | Name | Input | Output | Section |
|---|---|---|---|---|
| 01H | `GET_IC_VER` | — | version | 5.1 |
| 02H | `SET_BAUDRATE` | coefficient, constant | status | 5.2 |
| 03H | `ENTER_SLEEP` | — | — | 5.3 |
| 05H | `RESET_ALL` | — | — (wait 40 ms) | 5.4 |
| 06H | `CHECK_EXIST` | any byte | bitwise NOT of it | 5.5 |
| 15H | `SET_USB_MODE` | mode code | status (within 20 µs) | 5.9 |
| 16H | `TEST_CONNECT` | — | connection status (within 2 µs) | 5.10 |
| 17H | `ABORT_NAK` | — | — | 5.11 |
| 22H | `GET_STATUS` | — | interrupt status | 5.12 |
| 28H | `RD_USB_DATA` | — | length, then that many bytes | 5.13 |
| 2BH | `WR_USB_DATA7` | length, then that many bytes | — | 5.14 |

`GET_IC_VER` (5.1): bit 7 of the reply is 1 and bits 5-0 are the version, so a
reply of B7H means version 37H.

`CHECK_EXIST` (5.5): the datasheet's own example is 57H in, A8H out. It also
notes that a freshly reset chip reads 00H before it has been given a command.

Data blocks (5.13, 5.14) are length-prefixed and the length is **0 to 64**.
Anything larger means the port is out of step with the chip, not that a longer
packet arrived.

## Host-mode commands (DS2)

| Code | Name | Input | Output | Section |
|---|---|---|---|---|
| 04H | `SET_USB_SPEED` | bus speed | — | 1.1 |
| 0AH | `GET_DEV_RATE` | 07H | rate type | 1.2 |
| 0BH | `SET_RETRY` | 25H, retry byte | — | 1.3 |
| 0FH | `DELAY_100US` | — | delay status | 1.4 |
| 13H | `SET_USB_ADDR` | address | — | 1.5 |
| 1CH | `SET_ENDP6` | working mode | — (within 3 µs) | 1.6 |
| 1DH | `SET_ENDP7` | working mode | — (within 3 µs) | 1.7 |
| 27H | `RD_USB_DATA0` | — | length, then that many bytes | 1.8 |
| 41H | `CLR_STALL` | endpoint address | interrupt | 1.9 |
| 45H | `SET_ADDRESS` | address | interrupt | 1.10 |
| 46H | `GET_DESCR` | descriptor type | interrupt | 1.11 |
| 49H | `SET_CONFIG` | configuration value | interrupt | 1.12 |
| 4DH | `AUTO_SETUP` | — | interrupt | 1.13 |
| 4EH | `ISSUE_TKN_X` | sync flag, transaction attribute | interrupt | 1.14 |
| 4FH | `ISSUE_TOKEN` | transaction attribute | interrupt | 1.15 |

Three of these carry warnings worth repeating where they will be read:

**`DELAY_100US` only works over the parallel port** (1.4). This device talks
to the chip over a serial port, so the command is unusable here and is listed
only so nobody reaches for it.

**`GET_DESCR` cannot return a descriptor longer than 64 bytes** (1.11): the
chip's control-transfer buffer is that size, and it answers `USB_INT_BUF_OVER`
instead. Real keyboards routinely have configuration descriptors longer than
that, so enumeration cannot rely on this command alone - the same section
directs the caller to `ISSUE_TOKEN` or `ISSUE_TKN_X` for those.

**`SET_USB_ADDR` is not `SET_ADDRESS`** (1.5, 1.10). `SET_ADDRESS` asks the
*device* to take a new address; `SET_USB_ADDR` tells the *CH375* which address
it should now be talking to. Both are needed, in that order, and swapping them
leaves a device that has moved and a host still calling its old address.

### Endpoint synchronisation (1.6, 1.7)

The data toggle is set by hand. `SET_ENDP6` configures the receiver, `SET_ENDP7`
the transmitter. Working mode 80H means DATA0; for the transmitter, C0H means
DATA1. `ISSUE_TKN_X` (1.14) folds this into the transaction: its first byte has
bit 7 as the receiver's toggle and bit 6 as the transmitter's, bits 5-0 zero.

### Transaction attributes (1.15)

One byte: the low four bits are the token PID, the high four bits are the
device endpoint number.

| PID | Name | Meaning |
|---|---|---|
| 0DH | `DEF_USB_PID_SETUP` | begin a control transfer, send setup data |
| 01H | `DEF_USB_PID_OUT` | OUT transaction, send data |
| 09H | `DEF_USB_PID_IN` | IN transaction, receive data |

The datasheet's examples: 09H receives from endpoint 0, 21H sends to endpoint
2, 29H receives from endpoint 2.

Order matters. For SETUP and OUT, write the payload with `WR_USB_DATA7` first
and then issue the token; for IN, issue the token first and read with
`RD_USB_DATA` after it succeeds.

## USB working modes (DS1 section 5.9)

| Code | Meaning |
|---|---|
| 00H | device mode, disabled (the state after power-on or reset) |
| 01H | device mode, external firmware |
| 02H | device mode, built-in firmware |
| 04H | host mode, disabled |
| 05H | host mode, enabled, no SOF packets |
| 06H | host mode, enabled, SOF packets generated automatically |
| 07H | host mode, enabled, USB bus held in reset |

"Disabled" and "enabled" refer to whether the chip watches for a device being
plugged in by itself. Enabled, it raises an interrupt on connect and
disconnect; disabled, the MCU has to ask.

The datasheet gives the sequence plainly: *"It is recommended to use mode 5
when there is no USB device. After the USB device is plugged, enter mode 7
first and then switch to mode 6."* Mode 7 holds the bus in reset and **stays
there until the mode is changed**, so it is a step, never a resting state.

## What the chip answers

### Operation status (DS1 section 5, table above 5.1)

| Code | Name | Meaning |
|---|---|---|
| 51H | `CMD_RET_SUCCESS` | operated successfully |
| 5FH | `CMD_RET_ABORT` | operation failure |

These two are the whole set. A third value means the port has lost step with
the chip, and this firmware treats it as a failure rather than guessing.

### Interrupt status (DS1 section 5.12)

The byte is banded:

| Range | Meaning |
|---|---|
| 00H-0FH | device-mode statuses (see the CH372 datasheet; unused here) |
| 10H-1FH | ordinary host-mode statuses |
| 20H-3FH | host-mode operation failures, encoding why |

| Code | Name | Meaning |
|---|---|---|
| 14H | `USB_INT_SUCCESS` | the transaction succeeded |
| 15H | `USB_INT_CONNECT` | a device was plugged in |
| 16H | `USB_INT_DISCONNECT` | a device was unplugged |
| 17H | `USB_INT_BUF_OVER` | the transfer was wrong, or too long for the buffer |
| 1DH | `USB_INT_DISK_READ` | storage-device read request |
| 1EH | `USB_INT_DISK_WRITE` | storage-device write request |
| 1FH | `USB_INT_DISK_ERR` | storage-device operation failed |

A failure status is not a value to look up but a small structure:

| Bits | Meaning |
|---|---|
| 7-6 | always 00 |
| 5 | always 1 - this is what marks the byte as a failure |
| 4 | for IN transactions, 0 means the data packet was out of sync and may be invalid |
| 3-0 | what the device answered: 1010 NAK, 1110 STALL, XX00 timeout, otherwise the PID |

This is why the transport carries an unrecognised status byte through instead
of rejecting it: the byte *is* the diagnosis, and there are 32 of them.

## Retries (DS2 section 1.3)

`SET_RETRY` takes 25H and then a retry byte. Bits 7 and 6 decide what happens
on NAK: 10 retries forever (abandonable with `ABORT_NAK`), 11 retries for
roughly 200 ms to 2 s, and 0 reports the NAK to the MCU instead. Bits 5-0 are
how many times to retry after a timeout.

The default after reset is 85H - retry NAK forever, five retries after a
timeout. Forever is the wrong default for this device: a keyboard that stops
answering must not hold the firmware inside one command, so this is set
explicitly rather than left alone.
