"""全局热键测试：注册候选链 + 真按键（keybd_event）触发面板切换。

注意：Ctrl+Alt+M 在很多机器上被其他软件占用（本机就是），所以走候选链，
实际生效哪个组合由运行时决定，测试断言的是「选中的那个组合能切换面板」。

    ./.venv/Scripts/python.exe tests/hotkey_test.py
"""
from __future__ import annotations

import ctypes
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _guard import backup, restore  # noqa: E402

backup()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QCursor  # noqa: E402
from PySide6.QtCore import QPoint  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import main as app_main  # noqa: E402
from hotkey import (  # noqa: E402
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    VK_J,
    VK_K,
    VK_M,
    GlobalHotKeys,
)

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
    app = app_main.App(qapp, adb=None)
    fired: list[int] = []
    hk = GlobalHotKeys()
    chosen = hk.add_first_available(
        [
            (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_M, "Ctrl+Alt+M"),
            (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_J, "Ctrl+Alt+J"),
            (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_K, "Ctrl+Alt+K"),
        ],
        lambda: (fired.append(1), app._toggle_dock()),
    )
    qapp.installNativeEventFilter(hk)

    check("至少注册成功一个候选组合", bool(chosen), chosen or "全部被占用")
    if not chosen:
        restore()
        return 1

    # 鼠标挪离面板：折叠瞬间若鼠标悬停会立刻触发 enterEvent 展开回去
    QCursor.setPos(QPoint(300, 700))
    app.dock.show()
    for _ in range(3):
        qapp.processEvents()
        time.sleep(0.05)
    before = app.dock.isVisible() and not app.dock.is_collapsed()
    check("初始展开", before)

    user32 = ctypes.windll.user32
    vk = {"M": 0x4D, "J": 0x4A, "K": 0x4B}[chosen[-1]]
    VK_CTRL, VK_MENU = 0x11, 0x12

    def press() -> None:
        user32.keybd_event(VK_CTRL, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(vk, 0, 0, 0)
        user32.keybd_event(vk, 0, 2, 0)
        user32.keybd_event(VK_MENU, 0, 2, 0)
        user32.keybd_event(VK_CTRL, 0, 2, 0)

    def wait_fires(count: int) -> bool:
        deadline = time.monotonic() + 3
        while len(fired) < count and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.03)
        for _ in range(5):
            qapp.processEvents()
            time.sleep(0.03)
        return len(fired) >= count

    press()
    ok1 = wait_fires(1)
    check("第 1 次按键触发回调", ok1)
    check("第 1 次按键收起面板", app.dock.is_collapsed())

    press()
    ok2 = wait_fires(2)
    after = app.dock.isVisible() and not app.dock.is_collapsed()
    check("第 2 次按键触发回调", ok2)
    check("第 2 次按键展开面板", after)

    hk.release()
    app.store.close()
    restore()

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
