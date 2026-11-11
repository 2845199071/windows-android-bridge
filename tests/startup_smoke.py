"""启动冒烟测试：真实跑一遍 App.start() 完整启动链，任何异常都算失败。

背景：暗夜模式的 TTL 定时器写成 QTimer(self)（App 不是 QObject），打包版一启动
就崩出 PyInstaller 报错框，而单测全绿——因为没有任何测试真正走过 start()。
本测试就是补这个洞：必须真实执行 start()，不能只调零件。

    ./.venv/Scripts/python.exe tests/startup_smoke.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _guard import backup, restore  # noqa: E402

backup()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from config import CONFIG  # noqa: E402
from main import App  # noqa: E402

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
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_smoke_"))
    original_archive = CONFIG.get("archive_root")
    CONFIG.set("archive_root", str(tmp / "归档"))

    try:
        from store import Store

        store = Store(tmp / "data.db")
        app = App(qapp, store=store, adb=None)
        app.start()          # ← 完整启动链：恢复卡片/迁移/定时器/线程，一处崩就炸
        for _ in range(5):
            qapp.processEvents()

        check("start() 无异常走完", True)
        check("TTL 定时器已创建并激活",
              app._ttl_timer is not None and app._ttl_timer.isActive())
        check("监听线程已启动", app.watcher.isRunning())
        check("拉取线程已启动", app.puller.isRunning())

        # 卡片时效清理可手动触发（无过期卡片 → 0）
        check("时效巡检可安全执行", app._expire_stale_cards() == 0)

        # 优雅关停（同样必须无异常：线程停止/会话关闭/库关闭）
        app.shutdown()
        for _ in range(5):
            qapp.processEvents()
        check("shutdown() 无异常走完", True)
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
