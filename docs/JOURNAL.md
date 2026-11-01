# Development journal — MMX 200 Battery Tray

Extended log of the AI-assisted development (Cursor Agent, 2026-10).

## Context

Request from **Morida**: show battery level and connection state for the
Beyerdynamic MMX 200 on Linux (KDE) via the LL dongle (not day-to-day Bluetooth
headset use).

## Milestones

| Phase | Result |
|---|---|
| First tray app (combined with 8BitDo) | PyQt6 SNI, systemd --user |
| HID feature 0x55 | Unreliable / junk |
| usbmon toggle capture | Report 0x55 link byte found |
| Byte-2 polarity | Fixed: `0x00`=on, `0x01`=off |
| Mic silence proxy | Abandoned (PipeWire/EBUSY) |
| Persistent link | `~/.config/.../link` |
| Battery over dongle | Negative (sweep + vendor cmds) |
| Bluetooth GATT | Positive (real %) |
| Repo split | Standalone project |

## usbmon (short)

Dongle address e.g. `3:003`. Interrupt IN:

```
55 6b 00 48 … "MMX 200 wireless"   → linked
55 6b 01 48 … "MMX 200 wireless"   → unlinked
```

## Known limits

- No dongle battery %
- HID link reports arrive only on power toggle. Until one arrives, the tray uses USB-mic silence (exact zeros = RF down) so a restart does not keep a stale “disconnected”.
- Estimate drifts between BT syncs; re-calibrate via BT or voice announcement
- Runtime learning needs meaningful discharge between syncs (≥3 % / ≥~20 min connected)

## v1.1.0 (2026-10-29)

Self-learning discharge hours from BT anchors using connected `drain_accum`
(not wall clock). See `CHANGELOG.md`.

## AI notice

Entire codebase and docs: **100% AI-generated** (see README).
