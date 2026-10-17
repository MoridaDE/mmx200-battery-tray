# Entwicklungsjournal — MMX 200 Battery Tray

Ausführlicheres Log der KI-gestützten Entwicklung (Cursor Agent, 2026-10).

## Kontext

Anforderung von Morida: Unter Linux (KDE) Akkustand und Verbindungsstatus für das
Beyerdynamic MMX 200 (LL-Dongle, nicht Bluetooth-Headset-Alltag) sichtbar machen.

## Meilensteine

| Phase | Ergebnis |
|---|---|
| Erste Tray-App (kombiniert mit 8BitDo) | PyQt6 SNI, systemd --user |
| HID Feature 0x55 | Unzuverlässig / Junk |
| usbmon Toggle-Capture | Report 0x55 Link-Byte gefunden |
| Byte-2-Polarität | Korrigiert: 0x00=an, 0x01=aus |
| Mic-Silence-Proxy | Verworfen (PipeWire/EBUSY) |
| Persistenter Link | `~/.config/.../link` |
| Akku über Dongle | Negativ (Sweep + Vendor-Cmds) |
| Bluetooth GATT | Positiv (echter %) |
| Repo-Split | Eigenständiges Projekt |

## usbmon (Kurzform)

Adresse Dongle z. B. `3:003`. Interrupt IN:

```
55 6b 00 48 … "MMX 200 wireless"   → verbunden
55 6b 01 48 … "MMX 200 wireless"   → getrennt
```

## Bekannte Limits

- Kein Dongle-Akku-%
- Ohne erstes HID-Event nach Start: „einmal aus/ein“
- Schätzung driftet; BT oder Ansage neu kalibrieren

## KI-Hinweis

Gesamte Codebasis und Docs: **100 % KI-generiert** (siehe README).
