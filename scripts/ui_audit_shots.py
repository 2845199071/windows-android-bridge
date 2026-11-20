"""UI 审查截图脚本：面板（多卡/空态/收起）+ 设置窗口，浅色与暗色各一套。

    ./.venv/Scripts/python.exe scripts/ui_audit_shots.py
输出到 %TEMP%\\s2pc_ui\\*.png
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tests"))

from _guard import backup, restore  # noqa: E402

backup()

from PySide6.QtCore import QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPixmap,
)
from PySide6.QtWidgets import QApplication  # noqa: E402

from config import CONFIG  # noqa: E402
from dock import SideDock  # noqa: E402
from store import PendingItem  # noqa: E402

OUT = Path(tempfile.gettempdir()) / "s2pc_ui"
OUT.mkdir(exist_ok=True)


def fake_photo(path: Path, kind: str) -> None:
    pm = QPixmap(320, 240)
    if kind == "photo":
        g = QLinearGradient(0, 0, 0, 240)
        g.setColorAt(0, QColor("#8EC5F2"))
        g.setColorAt(1, QColor("#D9ECFA"))
        p = QPainter(pm)
        p.fillRect(pm.rect(), g)
        p.setBrush(QColor("#FBD38D"))
        p.setPen(Qt.NoPen)
        p.drawEllipse(QRectF(220, 30, 60, 60))
        p.setBrush(QColor("#6B7F94"))
        p.drawPolygon([QPointF(0, 240), QPointF(90, 120), QPointF(170, 240)])
        p.drawPolygon([QPointF(120, 240), QPointF(210, 140), QPointF(320, 240)])
        p.end()
    else:
        pm.fill(QColor("#263238"))
        p = QPainter(pm)
        p.setBrush(QColor("#4DD0E1"))
        p.setPen(Qt.NoPen)
        p.drawRoundedRect(QRectF(90, 80, 140, 80), 8, 8)
        p.end()
    pm.save(str(path))


def grab(widget, name: str) -> None:
    app = QApplication.instance()
    for _ in range(8):
        app.processEvents()
        time.sleep(0.05)
    g = widget.geometry()
    img = QGuiApplication.primaryScreen().grabWindow(0).toImage()
    crop = img.copy(g.x() - 8, g.y() - 8, g.width() + 16, g.height() + 16)
    crop.save(str(OUT / f"{name}.png"))
    print("shot:", name)


def main() -> None:
    app = QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_ui_"))
    CONFIG.set("dock_pos", [1480, 120])

    for theme in ("light", "dark"):
        CONFIG.set("theme", theme)
        dock = SideDock()
        dock.show()
        files = []
        for i, (name, kind) in enumerate([
            ("IMG_20261109_014812385.jpg", "photo"),
            ("VID_20261109_014203120.mp4", "video"),
            ("Screenshot_20261109-014501.png", "photo"),
        ]):
            f = tmp / f"{i}.jpg"
            fake_photo(f, kind)
            files.append(f)
            item = PendingItem(
                id=f"a{i}", remote_path=f"/sdcard/DCIM/Camera/{name}",
                filename=name, staging=str(f), mtime=int(time.time()) - i * 600,
                size=1_350_000 + i * 220_000, status="pulled",
                created_at=int(time.time()) - i * 600,
            )
            dock.add_item(item)
        dock.set_device_status("无线已连接", "#F59E0B")
        grab(dock, f"panel_3cards_{theme}")

        # 空态
        for i in range(3):
            dock.remove_item(f"a{i}")
        grab(dock, f"panel_empty_{theme}")

        # 收起态
        dock.set_collapsed(True)
        grab(dock, f"panel_collapsed_{theme}")
        dock.set_collapsed(False)
        dock.close()

    # 设置窗口
    from adb import AdbChannel
    from settings_dialog import SettingsDialog

    for theme in ("light", "dark"):
        CONFIG.set("theme", theme)
        dlg = SettingsDialog(AdbChannel(), None)
        dlg.resize(dlg.width(), 860)
        dlg.show()
        grab(dlg, f"settings_{theme}")
        dlg.close()

    restore()
    print("done ->", OUT)


if __name__ == "__main__":
    main()
