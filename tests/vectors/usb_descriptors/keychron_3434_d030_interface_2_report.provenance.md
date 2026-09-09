# Keychron 3434:D030 interface 2 HID report descriptor

- Captured from U1 PIO USB diagnostics on 2026-09-09.
- USB identity: vendor `0x3434`, product `0xD030`, interface `2`.
- Original size: 164 bytes.
- Captured size: 164 bytes.
- Truncated: no.
- SHA-256: `3e7a5226173a4fbe03c98934c6686d8ccd0d3ae321b5b1a266b24c4b18cddb28`.
- Source export: `C:\Users\Valentyn\Downloads\duo-input-report\diagnostics.json`, exported 2026-09-09 10:07 local time.

The side-button reports captured from the same receiver are:

```text
press   01 01 00 4F 00 00 00 00 03
release 01 00 00 00 00 00 00 00 03
```

The press is Report ID 1. Its body contains Left Ctrl in the modifier bitmap
and HID keyboard usage `0x4F` (Right Arrow). The final byte is the sixth array
slot, not another Report ID; usage `0x03` is ErrorUndefined and must not be
emitted as a key.
