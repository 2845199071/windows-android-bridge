"""拉取失败重试测试：断连失败的文件在设备恢复后必须自动重试（限次）。

背景：大文件拉到一半 adb 断连 → 卡片从没出现过 → 水位线已推进 →
原实现只记一条日志，文件从此不再提醒（还在手机上但程序不再管）。

    ./.venv/Scripts/python.exe tests/pull_retry_test.py
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

from adb import Device  # noqa: E402
from config import CONFIG  # noqa: E402
from main import App  # noqa: E402
from store import STATUS_FAILED, STATUS_WAITING, PendingItem, Store  # noqa: E402

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


def make_item(store: Store, name: str) -> PendingItem:
    import time
    import uuid

    item = PendingItem(
        id=uuid.uuid4().hex,
        remote_path=f"/sdcard/DCIM/Camera/{name}",
        filename=name,
        staging="",
        mtime=int(time.time()),
        size=1024,
        status=STATUS_FAILED,
        created_at=int(time.time()),
        error="device offline",
    )
    store.add_pending(item)
    return item


def main() -> int:
    qapp = QApplication(sys.argv)
    tmp = Path(__import__("tempfile").mkdtemp(prefix="s2pc_retry_"))
    original_archive = CONFIG.get("archive_root")
    CONFIG.set("archive_root", str(tmp / "归档"))

    try:
        store = Store(tmp / "data.db")
        app = App(qapp, store=store, adb=None)
        app._was_connected = False

        # 场景：拉取失败的文件挂在 failed 状态（真实流程里 inflight 已占位）
        item = make_item(store, "大视频.mp4")
        app._inflight.add(item.remote_path)

        # 第一次失败：通知 + 保留重试资格（inflight 不摘除）
        app._on_pull_failed(item.id, "device offline")
        check("失败后有重试资格", app._pull_retries.get(item.id) == 1)
        check("失败后仍占位 inflight", item.remote_path in app._inflight)

        # 设备恢复 → 自动重新入队（状态回 waiting）
        dev = Device(serial="abc", state="device", transport="wifi")
        app._on_device_changed(dev)
        qapp.processEvents()
        row = store.get_pending(item.id)
        check("设备恢复 → 状态回到 waiting", row.status == STATUS_WAITING, row.status)
        check("重试已入队", app.puller._queue.qsize() == 1, str(app.puller._queue.qsize()))

        # 真实时序：拉取线程拿到任务 → 失败 → 状态置回 failed → 回调
        import queue as _q

        for attempt in range(2, 4):   # 第 2、3 次重试
            try:
                while True:
                    app.puller._queue.get_nowait()
            except _q.Empty:
                pass
            store.update_pending(item.id, status=STATUS_FAILED, error="device offline")
            app._on_pull_failed(item.id, "device offline")

        # 第 3 次重试也失败后再恢复 → 达到上限，放弃
        app._was_connected = False
        app._on_device_changed(dev)
        qapp.processEvents()
        check("超过 3 次 → 保持放弃（不再重试）",
              store.get_pending(item.id).status == STATUS_FAILED
              and app.puller._queue.qsize() == 0)
        check("放弃后 inflight 摘除", item.remote_path not in app._inflight)
        check("重试计数已清", item.id not in app._pull_retries)

        # 成功路径清计数
        item2 = make_item(store, "照片.jpg")
        app._on_pull_failed(item2.id, "x")
        app._on_pulled(item2.id)
        check("成功后重试计数清零", item2.id not in app._pull_retries)

        app.watcher._running = False
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
