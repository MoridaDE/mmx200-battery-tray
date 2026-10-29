# Changelog

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
