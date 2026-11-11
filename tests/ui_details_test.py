"""UI 细节测试：类型角标、文件名字宽自适应、面板宽度切换、收起条状态点、
全部忽略二次确认的超时复位。

    ./.venv/Scripts/python.exe tests/ui_details_test.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _guard import backup, restore  # noqa: E402

backup()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from config import CONFIG  # noqa: E402
from dock import SideDock, _kind_of  # noqa: E402
from store import PendingItem  # noqa: E402

PASS = 0
FAIL = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"[PASS] {label}" + (f" — {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"[FAIL] {label}" + (f" — {detail}" if detail else ""))


def main() -> int:
    qapp = QApplication(sys.argv)

    # --- 类型角标规则
    check("视频有「视」角标", _kind_of("a.mp4")[0] == "视")
    check("音频有「音」角标", _kind_of("b.M4A")[0] == "音")
    check("文档有「文」角标", _kind_of("c.pdf")[0] == "文")
    check("压缩包有「包」角标", _kind_of("d.apk")[0] == "包")
    check("图片不打角标（缩略图自己说话）", _kind_of("e.jpg")[0] == "")

    tmp = Path(tempfile.mkdtemp(prefix="s2pc_ui_"))
    CONFIG.set("dock_width", 340)
    CONFIG.set("auto_expand", False)
    dock = SideDock()
    dock.show()
    qapp.processEvents()

    f = tmp / "x.mp4"
    f.write_bytes(b"x" * 16)
    item = PendingItem(id="k1", remote_path="/sdcard/VID_20261109.mp4",
                       filename="VID_20260909_123456_long_name_sample.mp4",
                       staging=str(f), mtime=1, size=2048, status="pulled", created_at=1)
    dock.add_item(item)
    qapp.processEvents()
    card = dock._cards["k1"]

    check("卡片带类型角标", hasattr(card, "kind_badge") and card.kind_badge.text() == "视")
    check("角标是缩略图的子控件", card.kind_badge.parent() is card.thumb_label)

    narrow = card.name_label.width()
    check("窄面板文件名宽度合理（≥120）", narrow >= 120, str(narrow))

    # --- 宽度切换
    dock.set_panel_width(420)
    qapp.processEvents()
    wide = card.name_label.width()
    check("宽面板文件名变宽", wide > narrow, f"{narrow} → {wide}")
    check("宽度已写入配置", CONFIG.get("dock_width") == 420)
    # offscreen 平台屏幕只有 800px 宽，_place 会按屏幕夹一道，这里只断言"变宽了"
    check("面板实际变宽", dock._expanded_width > 340, str(dock._expanded_width))
    dock.set_panel_width(340)

    # --- 收起条状态点
    dock.set_device_status("无线已连接", "#F59E0B")
    check("收起条状态点跟随连接色", "F59E0B" in dock.strip_dot.styleSheet(),
          dock.strip_dot.styleSheet())
    dock.set_device_status("未连接", "#EF4444")
    check("未连接时红点", "EF4444" in dock.strip_dot.styleSheet())

    # --- 二次确认超时复位（把 3 秒缩短成 50ms 验证）
    dock.ignore_all_btn.click()
    qapp.processEvents()
    check("第一次点是确认态", dock._ignore_confirm)
    dock._confirm_timer.stop()
    dock._confirm_timer.start(50)
    t0 = time.time()
    while time.time() - t0 < 1.0:
        qapp.processEvents()
        time.sleep(0.02)
        if not dock._ignore_confirm:
            break
    check("超时后自动复位", not dock._ignore_confirm
          and dock.ignore_all_btn.text() == "全部忽略", dock.ignore_all_btn.text())

    dock.close()
    restore()
    import shutil

    shutil.rmtree(tmp, ignore_errors=True)

    print()
    if FAIL:
        print(f"失败 {FAIL} 项，通过 {PASS} 项")
        return 1
    print(f"全部通过（{PASS} 项）")
    return 0


if __name__ == "__main__":
    sys.stdout.flush()
    code = main()
    sys.stdout.flush()
    os._exit(code)
