"""暗夜模式测试：样式产出、面板切换、设置窗口跟随、配置持久化。

    ./.venv/Scripts/python.exe tests/theme_test.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _guard import backup, restore  # noqa: E402

backup()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from config import CONFIG  # noqa: E402
from dock import SideDock  # noqa: E402
from store import PendingItem  # noqa: E402

from ui_theme import dock_style, is_dark, settings_style, theme_icon  # noqa: E402

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
    CONFIG.set("theme", "light")

    # --- 样式产出
    light = dock_style(0.94, False)
    dark = dock_style(0.94, True)
    check("浅色样式含白底", "rgba(255, 255, 255, 0.94)" in light)
    from ui_theme import _DOCK_DARK, _DOCK_LIGHT, _SETTINGS_DARK, _SETTINGS_LIGHT
    check("暗色样式含深底", "rgba(23, 30, 46, 0.94)" in dark)
    check("暗色正文用令牌色", f"color: {_DOCK_DARK['title']}" in dark)
    check("暗色不含白色面板底", "rgba(255, 255, 255, 0.94)" not in dark)
    check("设置样式两套都产出", _SETTINGS_DARK["dlg_bg"] in settings_style(True)
          and _SETTINGS_LIGHT["dlg_bg"] in settings_style(False))
    check("主题图标两态都能画", not theme_icon(False).isNull() and not theme_icon(True).isNull())

    # --- 面板切换（透明度取当前配置，别写死 0.94 —— 用户可能调过滑杆）
    alpha = float(CONFIG.get("panel_alpha", 0.94))
    dark_bg = f"rgba(23, 30, 46, {alpha:.2f})"
    light_bg = f"rgba(255, 255, 255, {alpha:.2f})"
    CONFIG.set("theme", "light")
    dock = SideDock()
    dock.show()
    qapp.processEvents()
    check("初始浅色", not is_dark() and not dock._dark)

    # 真点击按钮必须能切换（曾经 clicked 把 checked=False 传进 set_dark 导致点不动）
    dock.theme_btn.click()
    qapp.processEvents()
    check("点击月亮按钮 → 进入暗夜", dock._dark and CONFIG.get("theme") == "dark")
    dock.theme_btn.click()
    qapp.processEvents()
    check("再点太阳按钮 → 回浅色", not dock._dark and CONFIG.get("theme") == "light")

    dock.set_dark(True)
    qapp.processEvents()
    sheet = dock.styleSheet()
    check("切到暗夜：面板样式变深", dark_bg in sheet, dark_bg)
    check("切到暗夜：配置持久化", CONFIG.get("theme") == "dark")
    check("切到暗夜：预览弹窗同步", _DOCK_DARK["preview_bg"] in dock._preview.styleSheet())

    # 有卡片时也要跟着换底色
    item = PendingItem(id="t1", remote_path="/sdcard/a.jpg", filename="a.jpg",
                       staging="", mtime=1, size=1, status="pulled", created_at=0)
    dock.add_item(item)
    qapp.processEvents()
    card = next(iter(dock._cards.values()))
    check("暗夜下卡片缩略图底色变深", _DOCK_DARK["thumb_bg"] in card.thumb_label.styleSheet(),
          card.thumb_label.styleSheet())

    dock.set_dark(False)
    qapp.processEvents()
    check("切回浅色：配置与样式还原", CONFIG.get("theme") == "light"
          and light_bg in dock.styleSheet(), light_bg)
    check("切回浅色：卡片缩略图还原", _DOCK_LIGHT["thumb_bg"] in card.thumb_label.styleSheet())
    dock.remove_item("t1")

    # 重启保持：配置里是 dark 时新面板直接是暗的
    CONFIG.set("theme", "dark")
    dock2 = SideDock()
    check("重启后保持暗夜", dock2._dark and dark_bg in dock2.styleSheet(), dark_bg)
    CONFIG.set("theme", "light")
    dock.close()
    dock2.close()
    qapp.processEvents()

    # --- 设置窗口跟随主题（样式由 ui_theme.settings_style 按 CONFIG 产出）
    CONFIG.set("theme", "dark")
    check("暗色下设置窗口样式为深色", _SETTINGS_DARK["dlg_bg"] in settings_style(is_dark()))
    CONFIG.set("theme", "light")

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
