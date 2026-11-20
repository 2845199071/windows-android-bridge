"""补拉复用本地文件回归：电脑上已经存过的，不再重复下载。

    ./.venv/Scripts/python.exe tests/archived_reuse_test.py

用户要求：拉过的文件路径不会变，所以判重只需要在 D:\\手机归档 里找（含子文件夹）。
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
from store import STATUS_LOCAL, STATUS_WAITING, Store  # noqa: E402

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
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_reuse_"))
    archive = tmp / "归档"
    original = CONFIG.get("archive_root")
    CONFIG.set("archive_root", str(archive))
    CONFIG.set("auto_expand", False)

    try:
        store = Store(tmp / "data.db")
        app = App(qapp, store=store, adb=None)
        now = int(time.time())

        # --- 1. 存过、文件还在：直接标「已在电脑上」，不下载
        saved = archive / "图片" / "2026-11-15" / "saved.jpg"
        saved.parent.mkdir(parents=True)
        saved.write_bytes(b"x" * 100)
        store.add_archive("a1", "/sdcard/DCIM/saved.jpg", str(saved))
        app._on_new_files([("/sdcard/DCIM/saved.jpg", now, 100)])
        qapp.processEvents()
        item = [x for x in store.list_pending((STATUS_LOCAL,))
                if x.filename == "saved.jpg"][0]
        card = app.dock._cards[item.id]
        check("存过的文件标为 local", item.status == STATUS_LOCAL, item.status)
        check("指向电脑上那份文件", item.local_path == str(saved), item.local_path)
        check("按钮变「打开位置」", card.save_btn.text() == "打开位置", card.save_btn.text())
        check("meta 标「已在电脑上」", "已在电脑上" in card.meta_label.text(),
              card.meta_label.text())
        check("拖出去直接给本地路径", app._save_for_drag(item.id) == str(saved))

        # --- 2. 归档记录里的路径失效（文件被挪走），但归档目录子文件夹里还有
        moved = archive / "备份" / "2026-11-15" / "moved.jpg"
        moved.parent.mkdir(parents=True)
        moved.write_bytes(b"y" * 100)
        store.add_archive("a2", "/sdcard/DCIM/moved.jpg",
                          str(archive / "已删除" / "moved.jpg"))
        app._on_new_files([("/sdcard/DCIM/moved.jpg", now, 100)])
        qapp.processEvents()
        item2 = [x for x in store.list_pending((STATUS_LOCAL,))
                 if x.filename == "moved.jpg"][0]
        check("记录失效时按文件名在子目录里找回",
              item2.local_path == str(moved), item2.local_path)

        # --- 3. 归档里根本没有：照常走下载队列
        app._on_new_files([("/sdcard/DCIM/brand_new.jpg", now, 100)])
        qapp.processEvents()
        item3 = [x for x in store.list_pending((STATUS_WAITING,))
                 if x.filename == "brand_new.jpg"][0]
        check("没存过的照常下载", item3.status == STATUS_WAITING)

        # --- 4. local 卡片忽略：只从列表移掉，不能写忽略表（否则手机端新版本也不弹）
        mtime = item.mtime
        app._ignore_item(item.id)
        qapp.processEvents()
        check("local 卡片忽略后消失", item.id not in app.dock._cards)
        check("local 忽略不写忽略表",
              not store.is_ignored("/sdcard/DCIM/saved.jpg", mtime))

        app._progress_timer.stop()
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
