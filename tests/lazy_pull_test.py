"""按需下载回归：大文件先出卡片不下载，点保存才拉、拉完自动归档。

    ./.venv/Scripts/python.exe tests/lazy_pull_test.py

用户要求：像大文件先显示名字和文件类型，点保存时才下载，下载时卡片下方有进度条。
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
from store import STATUS_IDLE, STATUS_PULLED, STATUS_WAITING, Store  # noqa: E402

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
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_lazy_"))
    archive = tmp / "归档"
    original = CONFIG.get("archive_root")
    CONFIG.set("archive_root", str(archive))
    CONFIG.set("auto_expand", False)

    try:
        store = Store(tmp / "data.db")
        app = App(qapp, store=store, adb=None)
        now = int(time.time())

        # --- 1. 大文件：只出卡片，不下载
        app._on_new_files([("/sdcard/我的文件/big.apk", now, 60 * 1024 * 1024)])
        qapp.processEvents()
        big = store.list_pending((STATUS_IDLE,))[0]
        card = app.dock._cards.get(big.id)
        check("大文件状态是 idle（未下载）", big.status == STATUS_IDLE, big.status)
        check("大文件也先出卡片", card is not None)
        check("保存按钮写「下载并保存」", card.save_btn.text() == "下载并保存", card.save_btn.text())
        check("meta 标明未下载", "未下载" in card.meta_label.text(), card.meta_label.text())
        # 离屏模式下父窗口不可见，isVisible() 恒为 False，只能看 isHidden()
        check("进度条默认隐藏", card.progress.isHidden())

        # --- 2. 点保存：开始下载，进度条出现，记下"下完自动保存"
        check("点保存不报错", app._save_item(big.id) is True)
        qapp.processEvents()
        mid = store.get_pending(big.id)
        check("开始下载后状态变 waiting", mid.status == STATUS_WAITING, mid.status)
        check("已登记下完自动保存", big.id in app._pending_save)
        check("下载中进度条可见", not card.progress.isHidden())
        check("下载中按钮变「下载中…」", card.save_btn.text() == "下载中…", card.save_btn.text())
        check("下载中按钮禁用", not card.save_btn.isEnabled())

        # --- 3. 模拟下载完成 -> 自动归档、卡片消失
        staged = tmp / "big_staging.apk"
        staged.write_bytes(b"x" * 2048)
        store.update_pending(big.id, status=STATUS_PULLED, staging=str(staged))
        app._on_pulled(big.id)
        qapp.processEvents()
        final = store.get_pending(big.id)
        check("下载完自动变 saved", final.status == "saved", final.status)
        check("卡片已从面板移除", big.id not in app.dock._cards)
        check("自动保存登记已清理", big.id not in app._pending_save)
        archived = list(archive.rglob("big.apk"))
        check("文件已进归档目录", bool(archived), str(archived[0]) if archived else "")

        # --- 4. 小文件：仍然预先下载（保证缩略图和秒开）
        app._on_new_files([("/sdcard/DCIM/Camera/small.jpg", now, 1024 * 1024)])
        qapp.processEvents()
        waiting = [x for x in store.list_pending((STATUS_WAITING,)) if x.filename == "small.jpg"]
        check("小文件直接进下载队列", bool(waiting), str([x.status for x in waiting]))

        # --- 5. 重启恢复：idle 卡片不能当成「暂存丢失」
        app._on_new_files([("/sdcard/我的文件/another.apk", now, 60 * 1024 * 1024)])
        qapp.processEvents()
        idle2 = [x for x in store.list_pending((STATUS_IDLE,))
                 if x.filename == "another.apk"][0]
        app.dock.remove_item(idle2.id)          # 模拟重启：面板先清空
        app._cleanup_staging()
        qapp.processEvents()
        again = store.get_pending(idle2.id)
        check("重启后 idle 卡片恢复而不是标失败",
              again is not None and again.status == STATUS_IDLE,
              str(again.status if again else "None"))
        check("重启后卡片重新出现", idle2.id in app.dock._cards)

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
