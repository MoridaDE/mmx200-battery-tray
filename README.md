# MMX 200 Battery Tray (Linux / KDE)

StatusNotifier / Plasma tray for the **Beyerdynamic MMX 200 wireless** over the
**LL USB dongle** (`2e50:002f`): RF link state plus a calibratable battery
estimate. **Live percentage only via Bluetooth GATT** (same path as the official
beyerdynamic app).

> ## 100% AI-generated
>
> **Development, implementation, protocol analysis, and this documentation were
> created entirely by an AI (Cursor Agent).** There is no hand-written application
> code — only requirements, testing, and feedback by **Morida**. Please treat the
> project accordingly (review, security, support).

<details>
<summary>Deutsch (Kurzfassung)</summary>

Tray für MMX 200 über LL-Dongle: HID-Link + Akku-Schätzung; Live-% nur per
Bluetooth. Vollständig KI-generiert (Cursor Agent), Feedback durch Morida.
</details>

## Features

- Tray icon (Freedesktop battery theme, painted fallback)
- **HID link** via interrupt report `0x55` (byte 2: `0x00` = linked, `0x01` = unlinked; verified with usbmon)
- **Bluetooth GATT** (`org.bluez.Battery1`) for real battery %
- Discharge estimate (~40 h runtime), calibrate via voice prompt / menu / BT
- CLI: `mmx200-battery-status`
- systemd user service + autostart `.desktop`
- **Combined tray:** if [`8bitdo-2c-battery-tray`](https://github.com/MoridaDE/8bitdo-2c-battery-tray) is also installed, this service hosts **one** StatusNotifier icon for both devices (repos stay separate)

## Requirements

- Linux (PipeWire/Pulse optional; not required for HID link)
- Python 3.11+, PyQt6, dbus-python
- KDE Plasma / any StatusNotifier host
- udev `uaccess` for hidraw (see `packaging/`)

## Installation

```bash
git clone https://github.com/MoridaDE/mmx200-battery-tray.git
cd mmx200-battery-tray
pip install --user -r requirements.txt   # or distro packages: python-pyqt6, python-dbus
./install.sh
sudo cp packaging/71-mmx200-hidraw.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

```bash
mmx200-battery-status
systemctl --user status mmx200-battery-tray.service
```

Config: `~/.config/mmx200-battery/` (`estimate`, `link`, `last_bt`, `state.json`).

## Usage

| Situation | Display |
|---|---|
| Dongle + headset on (HID) | connected + `~`estimate |
| Briefly paired over Bluetooth | live % (GATT), calibrates estimate |
| Headset off | HID: disconnected |
| Right-click → calibrate from announcement | sets estimate from voice prompt |

**Important:** The 2.4 GHz dongle does **not** expose a live battery %. That matches
MMX 150 research and the beyerdynamic app (battery over Bluetooth).

## Development journal (protocol)

Hardware notes (CachyOS / kernel 7.x, dongle on bus 3):

1. **Goal:** Battery + link in a Linux tray without Windows tools.
2. **HID scan:** Feature `0x55` / `GET_FEATURE` often junk (`0x80bb` / later `0x3d ff 01…`).
3. **usbmon** text capture during power toggle:
   - Interrupt IN report `0x55`, payload contains ASCII `"MMX 200 wireless"`.
   - Byte 2: **`0x00` = RF up / `0x01` = RF down** (initially inverted, corrected live).
   - Byte 3 (`0x48`) is **not** battery (briefly misread as 72%).
4. **Mic silence proxy** abandoned — PipeWire holds the device; false positives.
5. **Persistence:** last HID link in `link`; after restart, one power toggle or button may be needed.
6. **Battery over dongle:** feature sweep + vendor commands on `0x55`/`0x3d` — no live %.
7. **Bluetooth:** device `MMX 200 wireless`, `Battery1.Percentage` → calibrates `estimate`.
8. **Split:** extracted from a combined “device-battery” tray into this repo.

Related (MMX 150, different PID): dongle report `0x3a` is link-only — see the public
`mmx-battery` GNOME extension analysis (3dns).

More detail: [`docs/JOURNAL.md`](docs/JOURNAL.md).

## Layout

```
src/backend.py        # HID listener, BlueZ, EstimateStore
src/tray.py           # PyQt6 StatusNotifierItem (solo or hosts combined)
src/combined_tray.py  # Unified UI when both sister apps are installed
src/status.py         # JSON CLI
packaging/            # udev, systemd --user, .desktop
install.sh
```

## Sister project

8BitDo Ultimate 2C: [`8bitdo-2c-battery-tray`](https://github.com/MoridaDE/8bitdo-2c-battery-tray).
Install both → one combined tray icon (this unit hosts it; pad unit is disabled).

## License

MIT — see `LICENSE`.

## Disclaimer

Not official beyerdynamic software. HID/Bluetooth behaviour may change with
firmware. Use at your own risk.
