"""连接方式（自动/手动）测试。

覆盖四件事：
1. 默认配置是 auto；
2. ensure_device(allow_connect=False) 是纯被动——设备不在线时绝不发起 adb connect；
3. allow_connect=True（点「连接」按钮的路径）会主动尝试连接；
4. 面板按钮可见性规则：手动+未连接=显示；已连接=隐藏；自动=永远隐藏；
   点击会发信号；set_connecting 期间防连点。

不需要真机（adb 行为用替身拦截）。

    ./.venv/Scripts/python.exe tests/connect_mode_test.py
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

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from adb import AdbChannel, Device  # noqa: E402
from config import CONFIG  # noqa: E402
from dock import SideDock  # noqa: E402
from settings_dialog import SettingsDialog  # noqa: E402

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


class FakeAdb(AdbChannel):
    """devices() 永远返回空（模拟手机不在线），connect 记录调用次数。"""

    def __init__(self) -> None:
        super().__init__()
        self.connect_calls = 0

    def devices(self):  # noqa: N802
        return []

    def connect(self, endpoint: str, timeout: float = 12) -> bool:
        self.connect_calls += 1
        return True   # 假装连上了

    def _mdns_endpoints(self):  # noqa: N802
        return ["192.168.1.200:5555"] if self.connect_calls == 0 else []

    def set_mdns_auto_connect(self, auto, restart=None):  # noqa: D102
        # 测试里不能真去 kill/start adb server（会打断前台应用），只记环境与状态
        self.mdns_auto = bool(auto)
        self._env["ADB_MDNS_AUTO_CONNECT"] = "1" if auto else "0"


def test_default_and_passive() -> None:
    from config import DEFAULTS

    check("DEFAULTS 里 connect_mode = auto", DEFAULTS.get("connect_mode") == "auto")
    CONFIG.set("connect_mode", "auto")   # 显式归位，避免其他用例/泄漏影响判断

    fake = FakeAdb()
    got = fake.ensure_device(allow_connect=False)
    check("被动模式：手机不在线就不连接", got is None)
    check("被动模式：一次 connect 都没发", fake.connect_calls == 0, f"{fake.connect_calls} 次")

    got2 = fake.ensure_device(allow_connect=True)
    check("主动模式：会尝试连接", got2 is not None or fake.connect_calls > 0,
          f"connect {fake.connect_calls} 次")

    # --- server 自动回连开关（手动模式的真正关键：adb server 会自己回连已配对设备）
    fake.set_mdns_auto_connect(False, restart=False)
    check("手动模式：server 环境禁用自动回连",
          fake._env.get("ADB_MDNS_AUTO_CONNECT") == "0")
    fake.set_mdns_auto_connect(True, restart=False)
    check("自动模式：server 环境恢复默认",
          fake._env.get("ADB_MDNS_AUTO_CONNECT") == "1")


def test_dock_button() -> None:
    qapp = QApplication.instance() or QApplication(sys.argv)
    CONFIG.set("connect_mode", "auto")
    dock = SideDock()
    dock.show()
    qapp.processEvents()

    # 自动模式：即使未连接也不亮按钮
    dock.set_device_status("未连接", "#EF4444")
    check("自动模式：不显示连接按钮", not dock.connect_btn.isVisible())

    # 切手动：未连接 → 亮
    dock.set_manual_mode(True)
    check("切手动 + 未连接 → 显示按钮", dock.connect_btn.isVisible())
    check("按钮文案是「连接」", dock.connect_btn.text() == "连接")

    # 连上 → 藏
    dock.set_device_status("无线已连接", "#F59E0B")
    check("连接成功 → 隐藏按钮", not dock.connect_btn.isVisible())

    # 掉线 → 亮回来
    dock.set_device_status("未连接", "#EF4444")
    check("掉线 → 按钮回来", dock.connect_btn.isVisible())

    # 点击发信号
    fired = []
    dock.connect_requested.connect(lambda: fired.append(1))
    dock.connect_btn.click()
    qapp.processEvents()
    check("点击发出连接信号", len(fired) == 1)

    # set_connecting：防连点
    dock.set_connecting(True)
    check("连接中禁用按钮", not dock.connect_btn.isEnabled()
          and dock.connect_btn.text() == "连接中…")
    dock.set_connecting(False)
    check("结束后恢复", dock.connect_btn.isEnabled() and dock.connect_btn.text() == "连接")

    # 切回自动：藏
    dock.set_manual_mode(False)
    check("切回自动 → 按钮消失", not dock.connect_btn.isVisible())

    # 配置持久化往返：设为 manual → 重建 dock 后仍保持手动语义（配置里能读到）
    CONFIG.set("connect_mode", "manual")
    check("手动模式已写入配置", CONFIG.get("connect_mode") == "manual")
    CONFIG.set("connect_mode", "auto")


def test_settings_roundtrip() -> None:
    qapp = QApplication.instance() or QApplication(sys.argv)
    CONFIG.set("connect_mode", "manual")
    dlg = SettingsDialog.__new__(SettingsDialog)   # 只测 _load/values，不弹窗
    # 手工构造需要的控件（offscreen 下不做完整 exec）
    from PySide6.QtWidgets import QCheckBox, QComboBox, QLineEdit

    class _W:
        pass

    dlg.send_edit, dlg.archive_edit = QLineEdit(), QLineEdit()
    dlg.fast_combo, dlg.slow_combo, dlg.collapse_combo = QComboBox(), QComboBox(), QComboBox()
    dlg.width_combo = QComboBox()
    dlg.autostart_check, dlg.shell_check = QCheckBox(), QCheckBox()
    dlg.auto_check = QCheckBox()
    dlg.dark_check = QCheckBox()
    dlg._load()
    check("配置 manual → 设置页复选框未勾选", not dlg.auto_check.isChecked())

    CONFIG.set("connect_mode", "auto")
    dlg._load()
    check("配置 auto → 复选框勾选", dlg.auto_check.isChecked())

    dlg.auto_check.setChecked(False)
    vals = dlg.values()
    check("取消勾选 → connect_mode=manual", vals.get("connect_mode") == "manual")
    dlg.auto_check.setChecked(True)
    check("勾选 → connect_mode=auto", dlg.values().get("connect_mode") == "auto")
    CONFIG.set("connect_mode", "auto")


def main() -> int:
    try:
        test_default_and_passive()
        test_dock_button()
        test_settings_roundtrip()
    finally:
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
