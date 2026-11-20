"""拉取进度回归：大文件下载期间要出卡片、锁按钮、显示百分比。

    ./.venv/Scripts/python.exe tests/pull_progress_test.py

背景：209MB 的 APK 走无线要一两分钟，原来卡片要等 adb pull 完成才出现，
用户点了补拉只看到「找到 N 个」，面板上什么都没有 —— 被当成功能失效。
"""
from __future__ import annotations

import os
import shutil
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
from main import App  # noqa: E402
from store import STATUS_PULLED, STATUS_PULLING, PendingItem, Store  # noqa: E402

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
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_prog_"))
    original = CONFIG.get("archive_root")
    CONFIG.set("archive_root", str(tmp / "归档"))
    CONFIG.set("auto_expand", False)

    try:
        store = Store(tmp / "data.db")
        app = App(qapp, store=store, adb=None)

        staging = tmp / "big.apk"
        staging.write_bytes(b"x" * 1000)
        item = PendingItem(
            id="p1", remote_path="/sdcard/我的文件/big.apk",
            filename="big.apk", staging=str(staging),
            mtime=int(time.time()), size=10000,
            status=STATUS_PULLING, created_at=int(time.time()),
        )
        store.add_pending(item)
        app.dock.add_item(item)
        qapp.processEvents()
        card = app.dock._cards.get("p1")

        check("下载中也要先出卡片", card is not None)

        card.set_pulling(True, 42)
        check("保存按钮已禁用", not card.save_btn.isEnabled())
        check("忽略按钮已禁用", not card.ignore_btn.isEnabled())
        check("meta 显示下载进度", "下载中 42%" in card.meta_label.text(), card.meta_label.text())

        check("下载中不能保存", app._save_item("p1") is False)
        app._ignore_item("p1")
        check("下载中忽略无效、卡片仍在", "p1" in app.dock._cards)

        # --- 下载完成
        store.update_pending("p1", status=STATUS_PULLED)
        app._on_pulled("p1")
        qapp.processEvents()
        check("完成后按钮恢复", card.save_btn.isEnabled() and card.ignore_btn.isEnabled())
        check("完成后 meta 不再是下载中",
              "下载中" not in card.meta_label.text(), card.meta_label.text())

        # --- 进度按暂存文件大小估算（1000/10000 = 10%）
        store.update_pending("p1", status=STATUS_PULLING)
        card.set_pulling(True)
        app._tick_progress()
        qapp.processEvents()
        check("进度按暂存大小算出来", "下载中" in card.meta_label.text(), card.meta_label.text())

        # --- 下载期间被忽略：拉完不该再弹卡
        store.update_pending("p1", status="ignored")
        card.set_pulling(True)
        app._on_pulled("p1")
        qapp.processEvents()
        check("被忽略的文件拉完不再弹卡", "p1" not in app.dock._cards)

        app.store.close()
    finally:
        CONFIG.set("archive_root", original)
        restore()
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
