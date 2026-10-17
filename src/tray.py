#!/usr/bin/env python3
"""KDE/Linux system-tray battery indicator for Beyerdynamic MMX 200."""

from __future__ import annotations

import json
import os
import sys
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

# Allow running from repo without install
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from backend import EstimateStore, Mmx200Backend  # noqa: E402

CONFIG_DIR = os.path.expanduser("~/.config/mmx200-battery")
STATE_PATH = os.path.join(CONFIG_DIR, "state.json")
MMX_FULL_HOURS = 40.0


def _ensure_config() -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)


def _load_state() -> dict:
    _ensure_config()
    if not os.path.exists(STATE_PATH):
        return {"notify_level": 20}
    try:
        with open(STATE_PATH, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {"notify_level": 20}


def _theme_battery_name(pct: Optional[int], connected: bool) -> str:
    if not connected or pct is None:
        return "battery-missing"
    if pct <= 10:
        return "battery-level-10"
    if pct <= 20:
        return "battery-level-20"
    if pct <= 30:
        return "battery-level-30"
    if pct <= 40:
        return "battery-level-40"
    if pct <= 50:
        return "battery-level-50"
    if pct <= 60:
        return "battery-level-60"
    if pct <= 70:
        return "battery-level-70"
    if pct <= 80:
        return "battery-level-80"
    if pct <= 90:
        return "battery-level-90"
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


class TrayApp(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.state = _load_state()
        self.mmx = Mmx200Backend()
        self.est = EstimateStore(os.path.join(CONFIG_DIR, "estimate"), MMX_FULL_HOURS)
        self._warned = False

        if not QSystemTrayIcon.isSystemTrayAvailable():
            QMessageBox.critical(self, "mmx200-battery", "Kein System-Tray verfügbar.")
            sys.exit(1)

        self.tray = QSystemTrayIcon(self)
        self.tray.setObjectName("mmx200-battery-tray")
        self.menu = QMenu()
        self.menu.setTitle("MMX 200 Battery")
        self.status = QAction("MMX 200: …")
        self.status.setEnabled(False)
        self.menu.addAction(self.status)
        self.menu.addSeparator()

        act_voice = QAction("Ansage kalibrieren…")
        act_voice.triggered.connect(self._voice_calibrate)
        act_full = QAction("Als voll (100 %) markieren")
        act_full.triggered.connect(lambda: self._calibrate(100))
        act_set = QAction("Prozent setzen…")
        act_set.triggered.connect(self._ask_pct)
        tip = QAction("Tipp: Live-% per Bluetooth koppeln")
        tip.setEnabled(False)
        self.menu.addAction(act_voice)
        self.menu.addAction(act_full)
        self.menu.addAction(act_set)
        self.menu.addAction(tip)
        self.menu.addSeparator()
        act_refresh = QAction("Jetzt aktualisieren")
        act_refresh.triggered.connect(self.refresh)
        self.menu.addAction(act_refresh)
        self.menu.addSeparator()
        act_quit = QAction("Beenden")
        act_quit.triggered.connect(QApplication.instance().quit)
        self.menu.addAction(act_quit)

        self.tray.setContextMenu(self.menu)
        self.tray.setToolTip("MMX 200 Battery")
        self.tray.setIcon(_battery_icon(None, False))
        self.tray.show()

        self.timer = QTimer(self)
        self.timer.setInterval(5000)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()

    def _ask_pct(self) -> None:
        val, ok = QInputDialog.getInt(
            self,
            "Akkustand setzen",
            "Prozent (0–100):",
            int(round(self.est.percentage)),
            0,
            100,
            1,
        )
        if ok:
            self._calibrate(val)

    def _voice_calibrate(self) -> None:
        items = ["100 % (voll)", "80 %", "60 %", "40 %", "20 %", "Anderen Wert…"]
        choice, ok = QInputDialog.getItem(
            self,
            "MMX Ansage",
            "Was hat die Akku-Ansage gesagt?\n"
            "(Dongle liefert keinen Live-% — nur BT oder Ansage)",
            items,
            2,
            False,
        )
        if not ok:
            return
        if choice.startswith("Anderen"):
            self._ask_pct()
            return
        self._calibrate(int(choice.split()[0]))

    def _calibrate(self, pct: int) -> None:
        self.est.set_percentage(pct)
        self.mmx.note_activity()
        self.mmx._save_last_bt(pct)
        self._warned = False
        self.refresh()

    def refresh(self) -> None:
        hw = self.mmx.read()
        self.est.sync_from_disk()
        if hw.percentage is not None and hw.source == "hid":
            self.est.set_percentage(hw.percentage)
            pct, src = hw.percentage, "hid"
        else:
            pct = self.est.tick(draining=bool(hw.connected))
            src = "estimate"

        if not hw.dongle:
            text = "MMX 200: kein Dongle"
        else:
            link = "verbunden" if hw.connected else "getrennt"
            tag = "~" if src == "estimate" else ""
            if pct is None:
                text = f"MMX 200: {link} · {hw.detail}"
            else:
                text = f"MMX 200: {tag}{pct}% · {link} · {hw.detail}"

        self.status.setText(text)
        show = pct if hw.connected else None
        self.tray.setIcon(_battery_icon(show, bool(hw.connected and pct is not None)))
        self.tray.setToolTip(
            text
            + "\n\n~ = Schätzung\nLive-% nur per Bluetooth (wie beyerdynamic-App)\n"
            "Link-Status über LL-Dongle HID"
        )

        level = int(self.state.get("notify_level", 20))
        if hw.connected and pct is not None and pct <= level:
            if not self._warned:
                self._warned = True
                self.tray.showMessage(
                    "MMX 200 Akku niedrig",
                    f"{pct}%",
                    QSystemTrayIcon.MessageIcon.Warning,
                    8000,
                )
        elif pct is not None and pct > level + 5:
            self._warned = False


def main() -> None:
    from combined_tray import both_installed, run_combined_host  # noqa: E402

    if both_installed():
        run_combined_host()
        return

    lock = os.path.join(CONFIG_DIR, "tray.lock")
    _ensure_config()
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, str(os.getpid()).encode())
        os.close(fd)
    except FileExistsError:
        try:
            old = int(open(lock, encoding="utf-8").read().strip())
            os.kill(old, 0)
            print("mmx200-battery-tray already running", file=sys.stderr)
            sys.exit(0)
        except (OSError, ValueError):
            os.remove(lock)
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)

    QApplication.setDesktopFileName("mmx200-battery-tray")
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("MMX 200 Battery")
    app.setApplicationDisplayName("MMX 200 Battery")

    def _cleanup() -> None:
        try:
            os.remove(lock)
        except OSError:
            pass

    app.aboutToQuit.connect(_cleanup)
    _ = TrayApp()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
