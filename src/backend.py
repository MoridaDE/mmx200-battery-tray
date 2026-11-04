#!/usr/bin/env python3
"""Beyerdynamic MMX 200 wireless — link state + battery estimate/BT."""

from __future__ import annotations

import glob
import os
import select
import struct
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import Optional


def _find_hidraw(vid: int, pid: Optional[int] = None) -> Optional[str]:
    base = "/sys/class/hidraw"
    try:
        names = sorted(os.listdir(base))
    except OSError:
        return None
    for name in names:
        try:
            with open(f"{base}/{name}/device/uevent", encoding="utf-8") as fh:
                text = fh.read()
        except OSError:
            continue
        if f"HID_ID=0003:{vid:08X}:" not in text:
            continue
        if pid is not None and f":{pid:08X}" not in text:
            continue
        return f"/dev/{name}"
    return None


def _usb_ids() -> list[tuple[int, int, str]]:
    out: list[tuple[int, int, str]] = []
    for path in glob.glob("/sys/bus/usb/devices/*/idVendor"):
        root = os.path.dirname(path)
        try:
            vid = int(open(f"{root}/idVendor", encoding="utf-8").read().strip(), 16)
            pid = int(open(f"{root}/idProduct", encoding="utf-8").read().strip(), 16)
            product = ""
            prod_path = f"{root}/product"
            if os.path.exists(prod_path):
                product = open(prod_path, encoding="utf-8", errors="replace").read().strip()
            out.append((vid, pid, product))
        except (OSError, ValueError):
            continue
    return out


@dataclass
class DeviceSnapshot:
    name: str
    dongle: bool
    connected: bool
    percentage: Optional[int]
    charging: Optional[bool]
    source: str  # "hid" | "estimate" | "none"
    detail: str


class Mmx200Backend:
    """Beyerdynamic MMX 200 via LL Adapter (2e50:002f).

    Link (dongle): HID 0x55 byte2 0x00=linked / 0x01=unlinked; 0x07 buttons.
    Battery: dongle has no % (beyerdynamic app uses Bluetooth). Brief BT connect
    writes mmx.last_bt and calibrates the drain estimate for dongle-only use.
    """

    VID = 0x2E50
    PID = 0x002F  # LL dongle only. The USB-C cable enumerates the headset itself.
    # Empty→full while the USB cable is on the PC. The manual does not publish
    # a charge time; this only drives the estimate until a live % appears.
    CHARGE_HOURS_DEFAULT = 2.5
    REPORT_LINK = 0x55
    REPORT_BUTTON = 0x07
    LINK_PATH = os.path.expanduser("~/.config/mmx200-battery/link")
    LAST_BT_PATH = os.path.expanduser("~/.config/mmx200-battery/last_bt")
    ESTIMATE_PATH = os.path.expanduser("~/.config/mmx200-battery/estimate")
    DRAIN_HOURS_PATH = os.path.expanduser("~/.config/mmx200-battery/drain_hours")
    DRAIN_SAMPLES_PATH = os.path.expanduser("~/.config/mmx200-battery/drain_samples.json")
    # Spec default ~40 h; learned values stay inside these bounds
    DRAIN_HOURS_DEFAULT = 40.0
    DRAIN_HOURS_BOUNDS = (15.0, 70.0)

    def __init__(self, watch: bool = True) -> None:
        self._last_button = 0.0
        self._hid_linked: Optional[bool] = None
        self._hid_ts = 0.0
        self._hid_seen_live = False
        self._link_via = ""
        self._last_mic_probe = 0.0
        self._mic_quiet = 0
        self._mic_settled = False
        self._hid_lock = threading.Lock()
        self._stop = threading.Event()
        self._watch = watch
        self._bt_connect_next = 0.0
        self._bt_connect_lock = threading.Lock()
        self._load_link()
        if not watch:
            return
        self._evdev_thread = threading.Thread(
            target=self._evdev_loop, name="mmx-evdev", daemon=True
        )
        self._hid_thread = threading.Thread(
            target=self._hidraw_loop, name="mmx-hidraw", daemon=True
        )
        self._evdev_thread.start()
        self._hid_thread.start()

    def note_activity(self) -> None:
        """Called when user calibrates / presses a known control."""
        self._last_button = time.time()
        self._set_linked(True, persist=True)

    def dongle_present(self) -> bool:
        return any(v == self.VID and p == self.PID for v, p, _ in _usb_ids())

    def usb_headset(self) -> Optional[tuple[int, str]]:
        """Headset on the USB-C cable, not the LL dongle.

        Windows treats that cable as a separate device. On Linux it is any
        other ``2e50`` USB id, or a sound card named MMX that is not the
        LL Adapter.
        """
        for vid, pid, name in _usb_ids():
            if vid == self.VID and pid != self.PID:
                return pid, name or "MMX 200"
        try:
            cards = open("/proc/asound/cards", encoding="utf-8", errors="replace").read()
        except OSError:
            return None
        for line in cards.splitlines():
            low = line.lower()
            if "mmx" not in low:
                continue
            if "adapter" in low or "ll adapter" in low:
                continue
            return 0, "MMX 200"
        return None

    def _usb_capacity(self, pid: int) -> Optional[int]:
        """Live % if the USB headset exposes a kernel power supply."""
        needle = f"{self.VID:04X}"
        pid_s = f"{pid:04X}" if pid else ""
        for root in glob.glob("/sys/class/power_supply/*"):
            real = os.path.realpath(root).upper()
            blob = real
            uevent = os.path.join(root, "device", "uevent")
            try:
                blob += open(uevent, encoding="utf-8", errors="replace").read().upper()
            except OSError:
                pass
            if needle not in blob or "002F" in blob:
                continue
            if pid_s and pid_s not in blob and needle in real:
                continue
            try:
                cap = int(open(os.path.join(root, "capacity"), encoding="utf-8").read().strip())
            except (OSError, ValueError):
                continue
            if 0 <= cap <= 100:
                return cap
        return None

    def _load_link(self) -> None:
        try:
            raw = open(self.LINK_PATH, encoding="utf-8").read().strip().split()
            linked = raw[0] in ("1", "true", "on")
            ts = float(raw[1]) if len(raw) > 1 else time.time()
            via = raw[2] if len(raw) > 2 else "hid"
        except (OSError, ValueError, IndexError):
            return
        # The dongle only emits a link report on power toggle. A value from
        # a previous session is not the current RF state.
        if time.time() - ts > 120:
            return
        with self._hid_lock:
            self._hid_linked = linked
            self._hid_ts = ts
            self._link_via = via

    def _persist_link(self, linked: bool) -> None:
        try:
            os.makedirs(os.path.dirname(self.LINK_PATH), exist_ok=True)
            with self._hid_lock:
                via = self._link_via or "hid"
            with open(self.LINK_PATH, "w", encoding="utf-8") as fh:
                fh.write(f"{1 if linked else 0} {time.time():.3f} {via}\n")
        except OSError:
            pass

    def _set_linked(self, linked: bool, persist: bool = True) -> None:
        with self._hid_lock:
            self._hid_linked = linked
            self._hid_ts = time.time()
        if persist:
            self._persist_link(linked)

    def _clear_linked(self) -> None:
        with self._hid_lock:
            self._hid_linked = None
            self._hid_ts = 0.0
        try:
            os.remove(self.LINK_PATH)
        except OSError:
            pass

    def _apply_link_report(self, data: bytes) -> None:
        """Parse interrupt payload 0x55 … MMX 200 wireless."""
        if len(data) < 4 or data[0] != self.REPORT_LINK:
            return
        if len(data) >= 20 and b"MMX" not in data and data[1] != 0x6B:
            return
        # byte2: 0x00 = RF up, 0x01 = RF down (confirmed live)
        self._note_hid_link(data[2] == 0)

    def _hidraw_loop(self) -> None:
        """Listen for unsolicited HID link/button reports."""
        while not self._stop.is_set():
            self._maybe_mic_probe()
            path = _find_hidraw(self.VID, self.PID)
            if not path:
                self._clear_linked()
                self._stop.wait(2.0)
                continue
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
            except OSError:
                self._stop.wait(1.0)
                continue
            try:
                while not self._stop.is_set():
                    if _find_hidraw(self.VID, self.PID) != path:
                        break
                    ready, _, _ = select.select([fd], [], [], 1.0)
                    if not ready:
                        self._maybe_mic_probe()
                        continue
                    try:
                        data = os.read(fd, 64)
                    except OSError:
                        break
                    if not data:
                        break
                    if data[0] == self.REPORT_LINK:
                        self._apply_link_report(data)
                    elif data[0] == self.REPORT_BUTTON:
                        # Buttons only arrive while RF-linked
                        self._last_button = time.time()
                        self._note_hid_link(True)
            finally:
                try:
                    os.close(fd)
                except OSError:
                    pass
            self._stop.wait(0.3)

    def hid_link_status(self) -> Optional[bool]:
        """Cached link from last 0x55/0x07 report (or persisted)."""
        with self._hid_lock:
            return self._hid_linked

    def link_detail(self, connected: bool) -> str:
        with self._hid_lock:
            via = self._link_via
            known = self._hid_linked
        if known is None:
            return "HID: einmal aus/ein schalten"
        if connected:
            return "HID-Link" if via == "hid" else "Link (Mikrofon)"
        return "HID: getrennt" if via == "hid" else "getrennt (Mikrofon still)"

    def _note_hid_link(self, linked: bool) -> None:
        with self._hid_lock:
            self._hid_seen_live = True
            self._link_via = "hid"
        self._set_linked(linked, persist=True)

    def _maybe_mic_probe(self) -> None:
        """Steady-state link when no 0x55 edge has arrived this session.

        The LL dongle emits the HID link report only on power toggle. Exact
        digital silence on the USB mic means RF down; a noise floor means the
        headset codec is up. Opening that source is what the desktop shows as
        the microphone in use, so this runs only until the session has a
        decision: one noisy reading, or three quiet ones. Later on/off comes
        from HID and does not open the mic again.
        """
        with self._hid_lock:
            if self._hid_seen_live or self._mic_settled:
                return
        now = time.time()
        if now - self._last_mic_probe < 8.0:
            return
        self._last_mic_probe = now
        linked = self._mic_linked()
        if linked is None:
            self._last_mic_probe = now - 6.0
            return
        if linked:
            self._mic_quiet = 0
        else:
            self._mic_quiet += 1
            if self._mic_quiet < 3:
                return
        with self._hid_lock:
            self._link_via = "mic"
            self._mic_settled = True
        self._set_linked(linked, persist=True)

    def _mmx_source(self) -> Optional[str]:
        try:
            out = subprocess.check_output(
                ["pactl", "list", "short", "sources"],
                text=True,
                timeout=2,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        for line in out.splitlines():
            lower = line.lower()
            if "beyerdynamic" not in lower or "monitor" in lower:
                continue
            parts = line.split()
            if len(parts) >= 2:
                return parts[1]
        return None

    def _mic_linked(self) -> Optional[bool]:
        source = self._mmx_source()
        if not source:
            return None
        try:
            proc = subprocess.run(
                [
                    "parec",
                    f"--device={source}",
                    "--format=s16le",
                    "--rate=48000",
                    "--channels=1",
                    "--latency-msec=40",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                timeout=0.6,
            )
            data = proc.stdout
        except subprocess.TimeoutExpired as exc:
            data = exc.stdout or b""
        except (OSError, subprocess.SubprocessError):
            return None
        if len(data) < 4800:
            return None
        peak = 0
        limit = min(len(data) - 1, 24000)
        for index in range(0, limit, 2):
            sample = int.from_bytes(data[index : index + 2], "little", signed=True)
            mag = sample if sample >= 0 else -sample
            if mag > peak:
                peak = mag
            if peak >= 8:
                return True
        return peak >= 3

    def _evdev_loop(self) -> None:
        while not self._stop.is_set():
            nodes: list[str] = []
            for name_path in glob.glob("/sys/class/input/input*/name"):
                try:
                    name = open(name_path, encoding="utf-8").read().strip().lower()
                except OSError:
                    continue
                if "beyer" not in name and "ll adapter" not in name:
                    continue
                base = os.path.dirname(name_path)
                for event in glob.glob(f"{base}/event*"):
                    nodes.append(f"/dev/input/{os.path.basename(event)}")
            fds: list[int] = []
            try:
                for node in nodes:
                    try:
                        fds.append(os.open(node, os.O_RDONLY | os.O_NONBLOCK))
                    except OSError:
                        continue
                if not fds:
                    self._stop.wait(2.0)
                    continue
                while not self._stop.is_set():
                    ready, _, _ = select.select(fds, [], [], 1.0)
                    for fd in ready:
                        try:
                            data = os.read(fd, 24 * 16)
                        except OSError:
                            continue
                        off = 0
                        while off + 24 <= len(data):
                            _sec, _nsec, typ, _code, _val = struct.unpack(
                                "llHHi", data[off : off + 24]
                            )
                            off += 24
                            if typ != 0:
                                self._last_button = time.time()
                                self._note_hid_link(True)
                                break
            finally:
                for fd in fds:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            self._stop.wait(0.3)

    def _save_last_bt(self, pct: int) -> None:
        """Commit a real BT/% calibration anchor; may learn drain_hours from prior use."""
        prev = self._load_last_bt()
        now = time.time()
        # Debounce identical GATT polls while BT stays linked
        if prev is not None and abs(prev[0] - pct) < 1 and (now - prev[1]) < 600:
            return
        learn_drain_from_bt_anchor(
            estimate_path=self.ESTIMATE_PATH,
            drain_hours_path=self.DRAIN_HOURS_PATH,
            samples_path=self.DRAIN_SAMPLES_PATH,
            prev=prev,
            new_pct=pct,
            default_hours=self.DRAIN_HOURS_DEFAULT,
            bounds=self.DRAIN_HOURS_BOUNDS,
        )
        try:
            os.makedirs(os.path.dirname(self.LAST_BT_PATH), exist_ok=True)
            with open(self.LAST_BT_PATH, "w", encoding="utf-8") as fh:
                fh.write(f"{pct} {now:.3f}\n")
            with open(self.ESTIMATE_PATH, "w", encoding="utf-8") as fh:
                # pct ts drain_accum_s — accum resets after a fresh BT anchor
                fh.write(f"{float(pct):.4f} {now:.3f} 0.000\n")
        except OSError:
            pass

    def _load_last_bt(self) -> Optional[tuple[int, float]]:
        try:
            pct_s, ts_s = open(self.LAST_BT_PATH, encoding="utf-8").read().split()[:2]
            pct = int(float(pct_s))
            if 1 <= pct <= 100:
                return pct, float(ts_s)
        except (OSError, ValueError):
            pass
        return None

    def bluetooth_battery(self) -> Optional[tuple[int, bool, str]]:
        """Real % via BlueZ Battery1 (same path as the official beyerdynamic app)."""
        try:
            import dbus

            bus = dbus.SystemBus()
            obj = bus.get_object("org.bluez", "/")
            mgr = dbus.Interface(obj, "org.freedesktop.DBus.ObjectManager")
            for _path, ifaces in mgr.GetManagedObjects().items():
                dev = ifaces.get("org.bluez.Device1")
                batt = ifaces.get("org.bluez.Battery1")
                if not dev:
                    continue
                name = str(dev.get("Name", "") or "")
                alias = str(dev.get("Alias", "") or "")
                blob = f"{name} {alias}".lower()
                if not any(k in blob for k in ("beyer", "mmx 200", "mmx200")):
                    continue
                connected = bool(dev.get("Connected", False))
                if batt is not None:
                    pct = int(batt.get("Percentage", 0))
                    if 1 <= pct <= 100:
                        self._save_last_bt(pct)
                        return pct, connected, "Bluetooth GATT"
                if connected:
                    return None
        except Exception:
            pass
        return None

    def _request_bt_connect(self) -> None:
        """Ask BlueZ to connect while the USB cable is attached.

        The GATT battery is readable in that mode. Connect can block, so it
        runs off the tray timer and is not retried more than once a minute.
        """
        if not self._watch:
            return
        now = time.time()
        with self._bt_connect_lock:
            if now < self._bt_connect_next:
                return
            self._bt_connect_next = now + 60.0
        threading.Thread(
            target=self._connect_bt_blocking, name="mmx-bt-connect", daemon=True
        ).start()

    def _connect_bt_blocking(self) -> None:
        try:
            import dbus

            bus = dbus.SystemBus()
            mgr = dbus.Interface(
                bus.get_object("org.bluez", "/"),
                "org.freedesktop.DBus.ObjectManager",
            )
            for path, ifaces in mgr.GetManagedObjects().items():
                dev = ifaces.get("org.bluez.Device1")
                if not dev:
                    continue
                blob = f"{dev.get('Name', '')} {dev.get('Alias', '')}".lower()
                if not any(k in blob for k in ("beyer", "mmx 200", "mmx200")):
                    continue
                if bool(dev.get("Connected", False)):
                    return
                dbus.Interface(
                    bus.get_object("org.bluez", path), "org.bluez.Device1"
                ).Connect()
                return
        except Exception:
            pass

    def read(self) -> DeviceSnapshot:
        bt = self.bluetooth_battery()
        dongle = self.dongle_present()
        usb = self.usb_headset()
        last = self._load_last_bt()

        if usb is not None:
            pid, uname = usb
            cap = self._usb_capacity(pid)
            ident = f"USB {self.VID:04x}:{pid:04x}" if pid else "USB"
            bt_pct = bt[0] if bt is not None and bt[1] else None
            if cap is None and bt_pct is None:
                self._request_bt_connect()
            if cap is not None:
                detail = f"{ident} · {uname} · battery {cap}%"
                source = "usb"
                percentage = cap
            elif bt_pct is not None:
                detail = f"{ident} · {uname} · Bluetooth GATT {bt_pct}%"
                source = "hid"
                percentage = bt_pct
            else:
                detail = f"{ident} · {uname} · charging (no live %)"
                source = "none"
                percentage = None
            return DeviceSnapshot(
                name="MMX 200",
                dongle=dongle,
                connected=True,
                percentage=percentage,
                charging=True,
                source=source,
                detail=detail,
            )

        if bt is not None:
            pct, bt_conn, detail = bt
            if bt_conn:
                self._set_linked(True, persist=True)
                if dongle:
                    detail = f"{detail} · Dongle da"
                return DeviceSnapshot(
                    name="MMX 200",
                    dongle=dongle or True,
                    connected=True,
                    percentage=pct,
                    charging=None,
                    source="hid",
                    detail=detail,
                )

        if not dongle:
            self._clear_linked()
            detail = "Kein Dongle"
            if last:
                detail += f" · zuletzt BT {last[0]}%"
            return DeviceSnapshot(
                name="MMX 200",
                dongle=False,
                connected=False,
                percentage=None,
                charging=None,
                source="none",
                detail=detail,
            )

        hid = self.hid_link_status()
        if hid is True:
            connected = True
        elif hid is False:
            connected = False
        else:
            connected = False
        why = self.link_detail(connected)

        if last:
            age_h = (time.time() - last[1]) / 3600.0
            if age_h < 48:
                detail = f"{why} · Schätzung (BT-Kalibrierung {last[0]}%)"
            else:
                detail = f"{why} · Schätzung (BT {last[0]}% · {age_h:.0f}h alt)"
        else:
            detail = f"{why} · kein Live-% (kurz BT koppeln oder Ansage)"

        return DeviceSnapshot(
            name="MMX 200",
            dongle=True,
            connected=connected,
            percentage=None,
            charging=None,
            source="none",
            detail=detail,
        )


def _read_drain_accum(estimate_path: str) -> float:
    try:
        data = open(estimate_path, encoding="utf-8").read().strip().split()
        if len(data) >= 3:
            return max(0.0, float(data[2]))
    except (OSError, ValueError, IndexError):
        pass
    return 0.0


def load_drain_hours(path: str, default: float, bounds: tuple[float, float]) -> float:
    lo, hi = bounds
    try:
        h = float(open(path, encoding="utf-8").read().split()[0])
        if lo <= h <= hi:
            return h
    except (OSError, ValueError, IndexError):
        pass
    return default


def learn_drain_from_bt_anchor(
    *,
    estimate_path: str,
    drain_hours_path: str,
    samples_path: str,
    prev: Optional[tuple[int, float]],
    new_pct: int,
    default_hours: float,
    bounds: tuple[float, float],
    min_drop_pct: float = 3.0,
    min_drain_hours: float = 0.35,
    ema_alpha: float = 0.35,
) -> Optional[float]:
    """Update full→empty hours from connected drain time between two BT anchors.

    Uses only seconds the estimate was *draining* (dongle linked / in use), not
    wall-clock idle time. Returns the new hours value when a sample was applied.
    """
    if prev is None:
        return None
    prev_pct, _prev_ts = prev
    drop = float(prev_pct - new_pct)
    if drop < min_drop_pct:
        return None
    accum_s = _read_drain_accum(estimate_path)
    hours_used = accum_s / 3600.0
    if hours_used < min_drain_hours:
        return None
    implied = 100.0 * hours_used / drop
    lo, hi = bounds
    if not (lo <= implied <= hi):
        return None
    current = load_drain_hours(drain_hours_path, default_hours, bounds)
    learned = (1.0 - ema_alpha) * current + ema_alpha * implied
    learned = max(lo, min(hi, learned))
    try:
        os.makedirs(os.path.dirname(drain_hours_path) or ".", exist_ok=True)
        with open(drain_hours_path, "w", encoding="utf-8") as fh:
            fh.write(f"{learned:.4f} {time.time():.3f} implied={implied:.2f} {prev_pct}->{new_pct}\n")
        import json

        hist: list[dict] = []
        if os.path.exists(samples_path):
            try:
                hist = json.loads(open(samples_path, encoding="utf-8").read())
            except (OSError, json.JSONDecodeError, TypeError):
                hist = []
        hist.append(
            {
                "ts": time.time(),
                "prev_pct": prev_pct,
                "new_pct": new_pct,
                "drop": drop,
                "drain_hours_used": round(hours_used, 4),
                "implied_full_hours": round(implied, 3),
                "learned_full_hours": round(learned, 3),
            }
        )
        with open(samples_path, "w", encoding="utf-8") as fh:
            json.dump(hist[-40:], fh, indent=2)
    except OSError:
        return learned
    return learned


class EstimateStore:
    """Persisted coulomb-counter estimate when hardware % is unavailable.

    drain_hours: empty→full discharge runtime (default or learned from BT anchors)
    charge_hours: empty→full charge time (USB), optional
    File format: ``percentage timestamp [drain_accum_seconds]``
    """

    def __init__(
        self,
        path: str,
        drain_hours: float,
        charge_hours: Optional[float] = None,
        drain_hours_path: Optional[str] = None,
        drain_bounds: tuple[float, float] = (10.0, 80.0),
    ) -> None:
        self.path = path
        self.default_hours = drain_hours
        self.drain_hours_path = drain_hours_path
        self.drain_bounds = drain_bounds
        self.full_hours = (
            load_drain_hours(drain_hours_path, drain_hours, drain_bounds)
            if drain_hours_path
            else drain_hours
        )
        self.charge_hours = charge_hours if charge_hours and charge_hours > 0 else None
        self.percentage = 70.0
        self.last_ts = time.time()
        self.drain_accum_s = 0.0
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            data = open(self.path, encoding="utf-8").read().strip().split()
            self.percentage = float(data[0])
            self.last_ts = float(data[1])
            if len(data) >= 3:
                self.drain_accum_s = max(0.0, float(data[2]))
        except (OSError, ValueError, IndexError):
            pass

    def reload_drain_hours(self) -> None:
        if self.drain_hours_path:
            self.full_hours = load_drain_hours(
                self.drain_hours_path, self.default_hours, self.drain_bounds
            )

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(
                f"{self.percentage:.4f} {time.time():.3f} {self.drain_accum_s:.3f}\n"
            )

    def set_percentage(self, pct: int) -> None:
        self.percentage = max(0.0, min(100.0, float(pct)))
        self.last_ts = time.time()
        self.save()

    def sync_from_disk(self) -> None:
        """Pick up calibrations written by backends (BT GATT) without restart."""
        self.reload_drain_hours()
        if not os.path.exists(self.path):
            return
        try:
            data = open(self.path, encoding="utf-8").read().strip().split()
            file_pct = float(data[0])
            file_ts = float(data[1])
            file_accum = float(data[2]) if len(data) >= 3 else None
        except (OSError, ValueError, IndexError):
            return
        if file_ts > self.last_ts + 0.05:
            self.percentage = max(0.0, min(100.0, file_pct))
            self.last_ts = file_ts
            if file_accum is not None:
                self.drain_accum_s = max(0.0, file_accum)

    def tick(self, draining: bool = False, charging: bool = False) -> int:
        now = time.time()
        dt = max(0.0, now - self.last_ts)
        self.last_ts = now
        if charging and self.charge_hours:
            self.percentage = min(
                100.0,
                self.percentage + (dt / 3600.0) * (100.0 / self.charge_hours),
            )
        elif draining and self.full_hours > 0:
            self.drain_accum_s += dt
            self.percentage = max(
                0.0, self.percentage - (dt / 3600.0) * (100.0 / self.full_hours)
            )
        self.save()
        return int(round(self.percentage))
