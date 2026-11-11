"""连接方式切换不能卡界面：adb server 重启（实测 2.3s）必须在后台线程。

    ./.venv/Scripts/python.exe tests/mode_switch_test.py
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

from adb import AdbChannel  # noqa: E402
from config import CONFIG  # noqa: E402
from main import App  # noqa: E402
from store import Store  # noqa: E402

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


class SlowAdb(AdbChannel):
    """模拟真实 server 重启耗时（实测 kill+start ≈ 2.3s）。"""

    DELAY = 0.4

    def __init__(self) -> None:
        super().__init__()
        self.mdns_auto = None

    def set_mdns_auto_connect(self, auto, restart=None):  # noqa: D102
        time.sleep(self.DELAY)
        self.mdns_auto = bool(auto)
        self._env["ADB_MDNS_AUTO_CONNECT"] = "1" if auto else "0"


def main() -> int:
    qapp = QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_mode_"))
    original_archive = CONFIG.get("archive_root")
    CONFIG.set("archive_root", str(tmp / "归档"))
    CONFIG.set("connect_mode", "manual")

    try:
        fake = SlowAdb()
        store = Store(tmp / "data.db")
        app = App(qapp, store=store, adb=fake)

        # 切到自动：必须立刻返回（后台线程干活）
        CONFIG.set("connect_mode", "auto")      # 真实顺序：设置页先写配置再回调
        t0 = time.perf_counter()
        app._on_setting_live("connect_mode", "auto", "manual")
        elapsed = time.perf_counter() - t0
        check("切换立即返回（不卡界面）", elapsed < 0.15, f"{elapsed*1000:.0f} ms")
        check("立刻给出「切换中」反馈",
              "切换中" in app.dock.status_label.text(), app.dock.status_label.text())

        deadline = time.time() + 5
        while time.time() < deadline and app._mode_thread.isRunning():
            qapp.processEvents()
            time.sleep(0.02)
        qapp.processEvents()
        check("后台完成：mDNS 自动回连开启", fake.mdns_auto is True, str(fake.mdns_auto))
        check("worker 线程已结束", not app._mode_thread.isRunning())

        # 再切回手动
        CONFIG.set("connect_mode", "manual")
        app._on_setting_live("connect_mode", "manual", "auto")
        deadline = time.time() + 5
        while time.time() < deadline and app._mode_thread.isRunning():
            qapp.processEvents()
            time.sleep(0.02)
        check("切回手动：自动回连关闭", fake.mdns_auto is False, str(fake.mdns_auto))

        app.store.close()
    finally:
        CONFIG.set("archive_root", original_archive)
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
