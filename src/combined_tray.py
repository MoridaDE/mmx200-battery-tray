#!/usr/bin/env python3
"""Unified tray UI when both MMX 200 and 8BitDo 2C trays are installed.

Repos stay separate; this module merges the StatusNotifier icon at runtime.
MMX is the preferred host process; the 8BitDo service exits quietly.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QInputDialog,
    QMenu,
    QMessageBox,
    QSystemTrayIcon,
    QWidget,
)

MMX_SHARE = Path(os.path.expanduser("~/.local/share/mmx200-battery-tray"))
PAD_SHARE = Path(os.path.expanduser("~/.local/share/8bitdo-2c-battery-tray"))
COMBINED_CONFIG = Path(os.path.expanduser("~/.config/device-battery-combined"))
LOCK_PATH = COMBINED_CONFIG / "tray.lock"

MMX_FULL_HOURS = 40.0
PAD_FULL_HOURS = 18.0
PAD_CHARGE_HOURS = 2.0


def both_installed() -> bool:
    return (MMX_SHARE / "backend.py").is_file() and (PAD_SHARE / "backend.py").is_file()


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    mod = importlib.util.module_from_spec(spec)
    # dataclasses on Python 3.14 look up cls.__module__ in sys.modules
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _theme_battery_name(pct: Optional[int], connected: bool) -> str:
    if not connected or pct is None:
        return "battery-missing"
    bands = (10, 20, 30, 40, 50, 60, 70, 80, 90)
    for b in bands:
        if pct <= b:
            return f"battery-level-{b}"
    return "battery-level-100"


def _battery_icon(pct: Optional[int], connected: bool) -> QIcon:
    for name in (
        _theme_battery_name(pct, connected),
        "battery",
        "battery-good",
        "preferences-system-power-management",
    ):
        icon = QIcon.fromTheme(name)
        if not icon.isNull():
            return icon
    pm = QPixmap(64, 64)
    pm.fill(QColor("#1a1a1a"))
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    color = QColor("#5db87a") if connected and pct and pct > 20 else QColor("#e3b35d")
    if not connected:
        color = QColor("#888888")
    p.setBrush(color)
    p.setPen(QColor("#ffffff"))
    p.drawRoundedRect(10, 18, 36, 28, 4, 4)
    p.drawRoundedRect(46, 26, 6, 12, 2, 2)
    font = p.font()
    font.setBold(True)
    font.setPixelSize(16)
    p.setFont(font)
    p.drawText(pm.rect(), int(Qt.AlignmentFlag.AlignCenter), "—" if pct is None else str(pct))
    p.end()
    return QIcon(pm)


class CombinedTrayApp(QWidget):
    def __init__(self, mmx_mod, pad_mod) -> None:
        super().__init__()
        self.mmx_mod = mmx_mod
        self.pad_mod = pad_mod
        self.mmx = mmx_mod.Mmx200Backend()
        self.pad = pad_mod.EightBitDo2CBackend()
        mmx_cfg = os.path.expanduser("~/.config/mmx200-battery")
        pad_cfg = os.path.expanduser("~/.config/8bitdo-2c-battery")
        self.mmx_est = mmx_mod.EstimateStore(os.path.join(mmx_cfg, "estimate"), MMX_FULL_HOURS)
        ch = PAD_CHARGE_HOURS
        try:
            ch = float(open(os.path.join(pad_cfg, "charge_hours"), encoding="utf-8").read().split()[0])
            if not 0.5 <= ch <= 6.0:
                ch = PAD_CHARGE_HOURS
        except (OSError, ValueError, IndexError):
            pass
        self.pad_est = pad_mod.EstimateStore(
            os.path.join(pad_cfg, "estimate"), PAD_FULL_HOURS, charge_hours=ch
        )
        self._warned: set[str] = set()
        self._full_warned = False
        self.state = {"notify_level": 20}
        try:
            st = json.loads(
                open(COMBINED_CONFIG / "state.json", encoding="utf-8").read()
            )
            self.state.update(st)
        except (OSError, json.JSONDecodeError):
            pass

        if not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.critical(self, "Device Battery", "No system tray available.")
            sys.exit(1)

        self.tray = QSystemTrayIcon(self)
        self.tray.setObjectName("device-battery-combined-tray")
        self.menu = QMenu()
        self.menu.setTitle("Device Battery")
        self.status_mmx = QAction("MMX 200: …")
        self.status_mmx.setEnabled(False)
        self.status_pad = QAction("8BitDo 2C: …")
        self.status_pad.setEnabled(False)
        self.menu.addAction(self.status_mmx)
        self.menu.addAction(self.status_pad)
        self.menu.addSeparator()

        act_mmx_voice = QAction("MMX: calibrate from announcement…")
        act_mmx_voice.triggered.connect(self._mmx_voice)
        act_mmx_full = QAction("MMX: mark full (100%)")
        act_mmx_full.triggered.connect(lambda: self._calibrate("mmx", 100))
        act_mmx_set = QAction("MMX: set percent…")
        act_mmx_set.triggered.connect(lambda: self._ask("mmx"))
        act_pad_full = QAction("8BitDo: mark full (100%)")
        act_pad_full.triggered.connect(lambda: self._calibrate("pad", 100))
        act_pad_set = QAction("8BitDo: set percent…")
        act_pad_set.triggered.connect(lambda: self._ask("pad"))
        for a in (act_mmx_voice, act_mmx_full, act_mmx_set, act_pad_full, act_pad_set):
            self.menu.addAction(a)
        self.menu.addSeparator()
        act_refresh = QAction("Refresh now")
        act_refresh.triggered.connect(self.refresh)
        self.menu.addAction(act_refresh)
        self.menu.addSeparator()
        act_quit = QAction("Quit")
        act_quit.triggered.connect(QApplication.instance().quit)
        self.menu.addAction(act_quit)

        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip("Device Battery (MMX + 8BitDo)")
        self.tray.setIcon(_battery_icon(None, False))
        self.tray.show()

        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def _ask(self, which: str) -> None:
        store = self.mmx_est if which == "mmx" else self.pad_est
        val, ok = QInputDialog.getInt(
            self, "Set battery %", "Percent (0–100):", int(round(store.percentage)), 0, 100, 1
        )
        if ok:
            self._calibrate(which, val)

    def _mmx_voice(self) -> None:
        items = ["100 % (full)", "80 %", "60 %", "40 %", "20 %", "Other…"]
        choice, ok = QInputDialog.getItem(
            self,
            "MMX announcement",
            "What did the battery announcement say?\n(Dongle has no live % — BT or voice only)",
            items,
            2,
            False,
        )
        if not ok:
            return
        if choice.startswith("Other"):
            self._ask("mmx")
            return
        self._calibrate("mmx", int(choice.split()[0]))

    def _calibrate(self, which: str, pct: int) -> None:
        if which == "mmx":
            self.mmx_est.set_percentage(pct)
            self.mmx.note_activity()
            self.mmx._save_last_bt(pct)
        else:
            self.pad_est.set_percentage(pct)
        self._warned.discard(which)
        self.refresh()

    def refresh(self) -> None:
        mmx_hw = self.mmx.read()
        pad_hw = self.pad.read()
        self.mmx_est.sync_from_disk()
        self.pad_est.sync_from_disk()

        if mmx_hw.percentage is not None and mmx_hw.source == "hid":
            self.mmx_est.set_percentage(mmx_hw.percentage)
            mmx_pct, mmx_src = mmx_hw.percentage, "hid"
        else:
            mmx_pct = self.mmx_est.tick(draining=bool(mmx_hw.connected))
            mmx_src = "estimate"

        if pad_hw.percentage is not None and pad_hw.source == "hid":
            self.pad_est.set_percentage(pad_hw.percentage)
            pad_pct, pad_src = pad_hw.percentage, "hid"
            self._full_warned = False
        elif pad_hw.charging and pad_hw.percentage is None:
            pad_pct = self.pad_est.tick(charging=True)
            pad_src = "estimate"
            ch = self.pad_est.charge_hours or PAD_CHARGE_HOURS
            rem = max(0.0, 100.0 - pad_pct)
            eta = (rem / 100.0) * ch * 60.0
            if pad_pct < 100:
                pad_hw = self.pad_mod.DeviceSnapshot(
                    name=pad_hw.name,
                    dongle=pad_hw.dongle,
                    connected=pad_hw.connected,
                    percentage=pad_hw.percentage,
                    charging=pad_hw.charging,
                    source=pad_hw.source,
                    detail=f"{pad_hw.detail} · ETA ~{eta:.0f} min",
                )
        else:
            pad_pct = self.pad_est.tick(
                draining=bool(pad_hw.connected and not pad_hw.charging)
            )
            pad_src = "estimate"
            self._full_warned = False

        def fmt(name: str, hw, pct: Optional[int], src: str) -> str:
            if not hw.dongle and not hw.connected:
                return f"{name}: offline · {hw.detail}"
            if not hw.dongle:
                return f"{name}: no dongle"
            link = "connected" if hw.connected else "disconnected"
            tag = "~" if src == "estimate" else ""
            charge = " ⚡" if hw.charging else ""
            if pct is None:
                return f"{name}: {link} · {hw.detail}"
            return f"{name}: {tag}{pct}%{charge} · {link} · {hw.detail}"

        self.status_mmx.setText(fmt("MMX 200", mmx_hw, mmx_pct, mmx_src))
        self.status_pad.setText(fmt("8BitDo 2C", pad_hw, pad_pct, pad_src))

        candidates: list[int] = []
        if mmx_hw.connected and mmx_pct is not None:
            candidates.append(mmx_pct)
        if pad_hw.connected and pad_pct is not None:
            candidates.append(pad_pct)
        show = min(candidates) if candidates else None
        self.tray.setIcon(_battery_icon(show, bool(candidates)))
        self.tray.setToolTip(
            "\n".join(
                [
                    self.status_mmx.text(),
                    self.status_pad.text(),
                    "",
                    "~ = estimate",
                    "Combined UI (both trays installed)",
                ]
            )
        )

        level = int(self.state.get("notify_level", 20))
        for key, pct, active, charging in (
            ("mmx", mmx_pct, mmx_hw.connected, False),
            ("pad", pad_pct, pad_hw.connected, bool(pad_hw.charging)),
        ):
            if active and pct is not None and pct <= level and not charging:
                if key not in self._warned:
                    self._warned.add(key)
                    label = "MMX 200" if key == "mmx" else "8BitDo 2C"
                    self.tray.showMessage(
                        "Low battery",
                        f"{label}: {pct}%",
                        QSystemTrayIcon.MessageIcon.Warning,
                        8000,
                    )
            elif pct is not None and pct > level + 5:
                self._warned.discard(key)

        if pad_hw.charging and pad_pct is not None and pad_pct >= 100 and not self._full_warned:
            self._full_warned = True
            self.tray.showMessage(
                "8BitDo likely full",
                "Charge estimate ~100% (no USB full-signal — check pad LED)",
                QSystemTrayIcon.MessageIcon.Information,
                8000,
            )


def run_combined_host() -> None:
    """Run combined tray (call from MMX process when both installed)."""
    COMBINED_CONFIG.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        try:
            old = int(open(LOCK_PATH, encoding="utf-8").read().strip())
            os.kill(old, 0)
            print("Combined device-battery tray already running", file=sys.stderr)
            sys.exit(0)
        except (OSError, ValueError):
            os.remove(LOCK_PATH)
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)

    mmx_mod = _load_module("mmx200_backend", MMX_SHARE / "backend.py")
    pad_mod = _load_module("pad2c_backend", PAD_SHARE / "backend.py")

    QApplication.setDesktopFileName("mmx200-battery-tray")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("Device Battery")
    app.setApplicationDisplayName("Device Battery")

    def _cleanup() -> None:
        try:
            os.remove(LOCK_PATH)
        except OSError:
            pass

    app.aboutToQuit.connect(_cleanup)
    _ = CombinedTrayApp(mmx_mod, pad_mod)
    sys.exit(app.exec())
