#!/usr/bin/env python3
"""Beyerdynamic MMX 200 wireless — link state + battery estimate/BT."""

from __future__ import annotations

import glob
import os
import select
import struct
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
    PID = 0x002F
    REPORT_LINK = 0x55
    REPORT_BUTTON = 0x07
    LINK_PATH = os.path.expanduser("~/.config/mmx200-battery/link")
    LAST_BT_PATH = os.path.expanduser("~/.config/mmx200-battery/last_bt")

    def __init__(self) -> None:
        self._last_button = 0.0
        self._hid_linked: Optional[bool] = None
        self._hid_ts = 0.0
        self._hid_lock = threading.Lock()
        self._stop = threading.Event()
        self._load_link()
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

    def _load_link(self) -> None:
        try:
            raw = open(self.LINK_PATH, encoding="utf-8").read().strip().split()
            linked = raw[0] in ("1", "true", "on")
            ts = float(raw[1]) if len(raw) > 1 else time.time()
        except (OSError, ValueError, IndexError):
            return
        with self._hid_lock:
            self._hid_linked = linked
            self._hid_ts = ts

    def _persist_link(self, linked: bool) -> None:
        try:
            os.makedirs(os.path.dirname(self.LINK_PATH), exist_ok=True)
            with open(self.LINK_PATH, "w", encoding="utf-8") as fh:
                fh.write(f"{1 if linked else 0} {time.time():.3f}\n")
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
        self._set_linked(data[2] == 0, persist=True)

    def _hidraw_loop(self) -> None:
        """Listen for unsolicited HID link/button reports."""
        while not self._stop.is_set():
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
                        self._set_linked(True, persist=True)
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
                                self._set_linked(True, persist=True)
                                break
            finally:
                for fd in fds:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            self._stop.wait(0.3)

    def _save_last_bt(self, pct: int) -> None:
        try:
            os.makedirs(os.path.dirname(self.LAST_BT_PATH), exist_ok=True)
            with open(self.LAST_BT_PATH, "w", encoding="utf-8") as fh:
                fh.write(f"{pct} {time.time():.3f}\n")
            est = os.path.expanduser("~/.config/mmx200-battery/estimate")
            with open(est, "w", encoding="utf-8") as fh:
                fh.write(f"{float(pct):.4f} {time.time():.3f}\n")
        except OSError:
            pass

    def _load_last_bt(self) -> Optional[tuple[int, float]]:
        try:
            pct_s, ts_s = open(self.LAST_BT_PATH, encoding="utf-8").read().split()
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

    def read(self) -> DeviceSnapshot:
        bt = self.bluetooth_battery()
        dongle = self.dongle_present()
        last = self._load_last_bt()

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
            why = "HID-Link"
            connected = True
        elif hid is False:
            why = "HID: getrennt"
            connected = False
        else:
            connected = False
            why = "HID: einmal aus/ein schalten"

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


class EstimateStore:
    """Persisted coulomb-counter estimate when hardware % is unavailable.

    drain_hours: empty→full discharge runtime
    charge_hours: empty→full charge time (USB), optional
    """

    def __init__(
        self,
        path: str,
        drain_hours: float,
        charge_hours: Optional[float] = None,
    ) -> None:
        self.path = path
        self.full_hours = drain_hours
        self.charge_hours = charge_hours if charge_hours and charge_hours > 0 else None
        self.percentage = 70.0
        self.last_ts = time.time()
        self._load()

    def _load(self) -> None:
        if not os.path.exists(self.path):
            return
        try:
            data = open(self.path, encoding="utf-8").read().strip().split()
            self.percentage = float(data[0])
            self.last_ts = float(data[1])
        except (OSError, ValueError, IndexError):
            pass

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as fh:
            fh.write(f"{self.percentage:.4f} {time.time():.3f}\n")

    def set_percentage(self, pct: int) -> None:
        self.percentage = max(0.0, min(100.0, float(pct)))
        self.last_ts = time.time()
        self.save()

    def sync_from_disk(self) -> None:
        """Pick up calibrations written by backends (BT GATT) without restart."""
        if not os.path.exists(self.path):
            return
        try:
            data = open(self.path, encoding="utf-8").read().strip().split()
            file_pct = float(data[0])
            file_ts = float(data[1])
        except (OSError, ValueError, IndexError):
            return
        if file_ts > self.last_ts + 0.05:
            self.percentage = max(0.0, min(100.0, file_pct))
            self.last_ts = file_ts

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
            self.percentage = max(
                0.0, self.percentage - (dt / 3600.0) * (100.0 / self.full_hours)
            )
        self.save()
        return int(round(self.percentage))
