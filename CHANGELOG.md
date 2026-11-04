# Changelog

## Unreleased

### Added
- USB-C cable mode is the headset itself, `2e50:002d` (“MMX 200 wireless”),
  not the LL dongle `002f`. While that device is present the tray shows
  charging and the estimate counts up (~2.5 h empty→full) instead of draining.
- udev `uaccess` for the cable device (`002d`) as well as the dongle.

### Notes
- The cable device itself has no kernel `power_supply` and its vendor HID
  channel has no percentage. Bluetooth still answers with the cable
  attached: a connected GATT reading is shown as the live % while charging.
  Otherwise the estimate counts up.

## v1.2.0 — 2026-11-01

### Fixed
- **Quit tray** opens in a normal window on tray click. Plasma only shows
  the status tooltip for that click, and a popup menu is rejected on
  Wayland. CLI: `mmx200-battery-tray --quit`.
- Headset link no longer stays on a stale “disconnected” after restart.
  Until a live HID toggle arrives, the tray reads the USB-mic noise floor
  (silence = RF down).

## v1.1.0 — 2026-10-29

### Added
- **Self-learning discharge runtime** from Bluetooth sync anchors.
  Between two real BT `%` readings, the tray measures how long the headset
  was actually *connected/draining* (not wall-clock idle time). From the
  `%` drop it updates a learned full→empty curve (`drain_hours`), smoothed
  with EMA and clamped to sane bounds (15–70 h; default still 40 h).
- Persisted learning artifacts: `drain_hours`, `drain_samples.json`.
- Estimate file now stores drain accumulator seconds
  (`percentage timestamp drain_accum_s`).
- Tooltip shows the active Entladekurve (`Standard` vs `gelernt`).

### Notes
- Frequent short BT reconnects still only *reset* the live `%`. Runtime
  learning needs ≥ ~3 % drop and ≥ ~20 min of connected use between anchors.
- Live `%` over the LL dongle remains an open goal (unchanged).

## v1.0.0 — 2026-10-17

Initial release: HID link + BT GATT battery + coulomb-counter estimate.
