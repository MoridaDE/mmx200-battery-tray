# MMX 200 Battery Tray (Linux / KDE)

Plasma-/StatusNotifier-Tray für das **Beyerdynamic MMX 200 wireless** über den
**LL-USB-Dongle** (`2e50:002f`): Verbindungsstatus (Funk-Link) und kalibrierbare
Akku-Schätzung. Live-Prozent nur über Bluetooth GATT (wie die offizielle
beyerdynamic-App).

> ## 100 % KI-generiert
>
> **Entwicklung, Implementierung, Protokollanalyse und diese Dokumentation wurden
> vollständig von einer KI (Cursor Agent) erstellt.** Es gab keinen manuellen
> Anwendungscode von Menschenhand — nur Anforderungen, Tests und Feedback durch
> Morida. Bitte entsprechend einordnen (Review, Security, Support).

## Features

- Tray-Icon (Freedesktop-Battery-Theme, Fallback gezeichnet)
- **HID-Link** über Interrupt-Report `0x55` (Byte 2: `0x00` = verbunden, `0x01` = getrennt; verifiziert per usbmon)
- **Bluetooth GATT** (`org.bluez.Battery1`) für echten Akkustand
- Schätzung mit Entladekurve (~40 h), kalibrierbar per Ansage / Menü / BT
- CLI: `mmx200-battery-status`
- systemd-user-Service + Autostart-Desktop-Datei

## Voraussetzungen

- Linux mit PipeWire/Pulse optional (nicht nötig für HID-Link)
- Python 3.11+, PyQt6, dbus-python
- KDE Plasma / StatusNotifier-Host (andere Desktops mit SNI möglich)
- udev `uaccess` für hidraw (siehe `packaging/`)

## Installation

```bash
git clone <dieses-repo>
cd mmx200-battery-tray
pip install --user -r requirements.txt   # oder Distro-Pakete: python-pyqt6, python-dbus
./install.sh
sudo cp packaging/71-mmx200-hidraw.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

```bash
mmx200-battery-status
systemctl --user status mmx200-battery-tray.service
```

Konfiguration: `~/.config/mmx200-battery/` (`estimate`, `link`, `last_bt`, `state.json`).

## Nutzung

| Situation | Anzeige |
|---|---|
| Dongle + Headset an (HID) | verbunden + `~`Schätzung |
| Kurz per Bluetooth gekoppelt | Live-% (GATT), kalibriert Schätzung |
| Headset aus | HID: getrennt |
| Rechtsklick → Ansage kalibrieren | setzt Schätzung nach Sprachansage |

**Wichtig:** Der 2,4‑GHz-Dongle liefert **keinen** Live-Akku-%. Das ist das gleiche
Ergebnis wie bei der MMX‑150-Forschung und dem Verhalten der beyerdynamic-App
(Akku über Bluetooth).

## Protokoll-Journal (Entwicklung)

Chronologischer Auszug der Analyse auf echter Hardware (CachyOS / Kernel 7.x,
Dongle Bus 3):

1. **Ziel:** Akkustand und Link im Linux-Tray, ohne Windows-Tool.
2. **HID-Scan:** Feature `0x55` / `GET_FEATURE` oft Junk (`0x80bb` / später `0x3d ff 01…`).
3. **usbmon** (Text-Capture `/sys/kernel/debug/usb/usbmon/3u`) während Power-Toggle:
   - Interrupt IN Report `0x55`, Payload mit ASCII `"MMX 200 wireless"`.
   - Byte 2: **`0x00` = RF up / `0x01` = RF down** (anfangs falsch herum interpretiert, live korrigiert).
   - Byte 3 (`0x48`) ist **kein** Akku (kurz fälschlich als 72 % gelesen).
4. **Mic-Proxy** (digitale Stille) als Fallback verworfen — PipeWire hält das Gerät belegt, False-Positives.
5. **Persistenz:** Letzter HID-Link in `link`; nach Restart einmal toggle oder Taste nötig, wenn noch kein Event.
6. **Akku über Dongle:** Feature-Sweep 0x00–0xFF, Vendor-Kommandos auf `0x55`/`0x3d` — kein Live-%.
7. **Bluetooth:** Gerät `MMX 200 wireless`, `Battery1.Percentage` (z. B. 60 %) → kalibriert `estimate`.
8. **Abspaltung:** Aus einem kombinierten „device-battery“-Tray als eigenes Repo extrahiert.

Verwandte Arbeit (MMX 150, anderer PID): Dongle-Report `0x3a` nur Link, kein Akku —
siehe öffentliche Analyse von `mmx-battery` GNOME-Extension (3dns).

## Architektur

```
src/backend.py   # HID-Listener, BlueZ, EstimateStore
src/tray.py      # PyQt6 StatusNotifierItem
src/status.py    # JSON-CLI
packaging/       # udev, systemd --user, .desktop
install.sh
```

## Lizenz

MIT — siehe `LICENSE`.

## Haftungsausschluss

Keine offizielle beyerdynamic-Software. HID/Bluetooth-Verhalten kann sich mit
Firmware ändern. Nutzung auf eigene Gefahr.
