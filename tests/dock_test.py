"""面板拖动 + 透明度测试。

运行：``.venv\\Scripts\\python.exe tests/dock_test.py``
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from PySide6.QtCore import QPointF, Qt  # noqa: E402
from PySide6.QtGui import QEnterEvent, QGuiApplication  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from _guard import backup as guard_backup, restore as guard_restore  # noqa: E402
from config import CONFIG  # noqa: E402
from dock import SideDock  # noqa: E402
from store import PendingItem  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main() -> int:
    qapp = QApplication(sys.argv)
    guard_backup()

    try:
        CONFIG.set("dock_pos", [])
        dock = SideDock()
        dock.show()
        qapp.processEvents()

        geo = QGuiApplication.primaryScreen().availableGeometry()
        check("默认贴右侧", abs(dock.x() + dock.width() + dock.MARGIN - geo.right()) <= 2,
              f"x={dock.x()} w={dock.width()} right={geo.right()}")

        # --- 拖动后位置持久化
        dock.move(120, 260)
        dock._save_pos()
        check("拖动后坐标已保存", CONFIG.get("dock_pos") == [120, 260], str(CONFIG.get("dock_pos")))

        dock.move(0, 0)
        dock._place()
        check("重新布局回到保存位置", (dock.x(), dock.y()) == (120, 260), f"{dock.x()},{dock.y()}")

        # --- 位置失效时回默认
        CONFIG.set("dock_pos", [99999, 99999])
        dock._place()
        check("屏幕外的位置被丢弃", dock.x() != 99999, f"x={dock.x()}")
        check("丢弃后回到贴右", abs(dock.x() + dock.width() + dock.MARGIN - geo.right()) <= 2)

        # --- 收起态右边缘不动
        CONFIG.set("dock_pos", [300, 200])
        dock._place()
        qapp.processEvents()
        right_before = dock.x() + dock.width()
        dock.set_collapsed(True)
        qapp.processEvents()
        right_after = dock.x() + dock.width()
        check("收起时右边缘不动", abs(right_before - right_after) <= 2,
              f"{right_before} → {right_after}")
        check("收起后变宽为小方点",
              dock.width() == dock.STRIP_SIZE and dock.height() == dock.STRIP_SIZE,
              f"{dock.width()}x{dock.height()}")

        dock.set_collapsed(False)
        qapp.processEvents()
        check("展开后回到原位置", abs(dock.x() - 300) <= 2, f"x={dock.x()}")

        # --- 鼠标移上来（收起态）应该自动展开
        dock.set_collapsed(True)
        qapp.processEvents()
        check("先确认已收起", dock.is_collapsed())
        enter = QEnterEvent(QPointF(10.0, 10.0), QPointF(10.0, 10.0), QPointF(10.0, 10.0))
        dock.enterEvent(enter)
        qapp.processEvents()
        check("鼠标移上来就展开", not dock.is_collapsed())

        # --- 自动收起：有文件时不收，处理完才收
        item = PendingItem(
            id="auto1",
            remote_path="/sdcard/DCIM/Camera/auto1.jpg",
            filename="auto1.jpg",
            staging="",
            mtime=1793329400,
            size=1024,
            status="pulled",
            created_at=0,
        )
        dock.add_item(item)
        qapp.processEvents()
        dock._maybe_collapse()
        check("有未处理文件时不自动收起", not dock.is_collapsed(),
              f"卡片数={dock.card_count()}")

        dock.remove_item("auto1")
        qapp.processEvents()
        dock._maybe_collapse()
        check("文件处理完后自动收起", dock.is_collapsed())

        dock.set_collapsed(False)
        qapp.processEvents()

        # --- 卡片很多时高度必须封顶，多出来的靠滚动
        dock.set_collapsed(False)
        for _ in range(3):
            qapp.processEvents()

        many = []
        for i in range(20):
            extra = PendingItem(
                id=f"many{i}", remote_path=f"/sdcard/DCIM/Camera/many{i}.jpg",
                filename=f"many{i}.jpg", staging="", mtime=1793329400,
                size=100 * (i + 1), status="pulled", created_at=0,
            )
            dock.add_item(extra)
            many.append(extra)
        for _ in range(5):
            qapp.processEvents()

        # 20 张卡 × 114px = 2280px，远超封顶高度，窗口不该跟着长到那么大
        max_h = SideDock.CHROME + SideDock.MAX_CARD_AREA + 16
        check("20 张卡时窗口高度封顶", dock.height() <= max_h, f"{dock.height()} <= {max_h}")
        check("封顶后不超过屏幕四成半", dock.height() <= int(qapp.primaryScreen().availableGeometry().height() * 0.45),
              f"{dock.height()} px / 屏高 {qapp.primaryScreen().availableGeometry().height()}")
        check("超出部分靠滚动查看", dock.scroll.verticalScrollBar().maximum() > 0,
              f"可滚动像素 {dock.scroll.verticalScrollBar().maximum()}")

        for extra in many:
            dock.remove_item(extra.id)
        qapp.processEvents()

        # --- 透明度
        for value, expect in ((0.5, "0.50"), (0.8, "0.80"), (1.0, "1.00")):
            dock.set_alpha(value)
            sheet = dock.styleSheet()
            check(f"透明度 {value} 生效",
                  f"rgba(255, 255, 255, {expect})" in sheet and abs(CONFIG.get("panel_alpha") - value) < 1e-6,
                  f"配置={CONFIG.get('panel_alpha')}")

        dock.set_alpha(0.05)
        check("透明度过低被夹到 0.35", abs(CONFIG.get("panel_alpha") - 0.35) < 1e-6,
              str(CONFIG.get("panel_alpha")))
        dock.set_alpha(0.5)

        # --- 复位
        dock.reset_position()
        check("复位清空坐标", CONFIG.get("dock_pos") == [])
        dock._place()
        check("复位后贴右", abs(dock.x() + dock.width() + dock.MARGIN - geo.right()) <= 2)

        # --- 📌 固定显示（置顶开关）：默认不置顶，别挡别的 App
        CONFIG.set("panel_pinned", False)
        pinned_dock = SideDock()
        pinned_dock.show()
        qapp.processEvents()
        f0 = pinned_dock.windowHandle().flags()
        check("默认不置顶", not (f0 & Qt.WindowStaysOnTopHint))

        pinned_dock.set_pinned(True)
        qapp.processEvents()
        f1 = pinned_dock.windowHandle().flags()
        check("点固定 → 置顶", bool(f1 & Qt.WindowStaysOnTopHint))
        check("固定状态已持久化", CONFIG.get("panel_pinned") is True)
        check("固定后窗口仍然可见", pinned_dock.isVisible(),
              "setWindowFlag 会隐藏窗口，必须 show 回去")

        pinned_dock.set_pinned(False)
        qapp.processEvents()
        f2 = pinned_dock.windowHandle().flags()
        check("取消固定 → 不置顶", not (f2 & Qt.WindowStaysOnTopHint))
        check("取消后状态已持久化", CONFIG.get("panel_pinned") is False)
        check("取消后窗口仍然可见", pinned_dock.isVisible())

        CONFIG.set("panel_pinned", True)
        pinned_dock2 = SideDock()
        check("重启后保持固定",
              bool(pinned_dock2.windowFlags() & Qt.WindowStaysOnTopHint)
              and pinned_dock2.pin_btn.isChecked())
        CONFIG.set("panel_pinned", False)
        pinned_dock.close()
        pinned_dock2.close()
        qapp.processEvents()

        dock.close()
    finally:
        guard_restore()

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    _code = main()
    sys.stdout.flush()
    os._exit(_code)
