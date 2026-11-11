"""启动恢复测试：上次没处理完的卡片，重启后必须还在。

这条是防回归的：早期实现是「启动就把暂存全删 + pending 全清」，结果是关程序时
没点的卡片会永久消失（水位线已推进，监听器不会再通知），用户只能自己去手机翻。

    ./.venv/Scripts/python.exe tests/restore_test.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _guard import backup, restore  # noqa: E402

backup()

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

from config import CONFIG, staging_dir  # noqa: E402
from store import STATUS_PULLED, STATUS_SAVED, PendingItem, Store  # noqa: E402
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


def make_item(staging: Path, name: str, status: str) -> PendingItem:
    return PendingItem(
        id=uuid.uuid4().hex,
        remote_path=f"/sdcard/Download/{name}",
        filename=name,
        staging=str(staging),
        mtime=int(time.time()) - 60,
        size=staging.stat().st_size if staging.is_file() else 1234,
        status=status,
        created_at=int(time.time()),
    )


def main() -> int:
    qapp = QApplication(sys.argv)
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_restore_"))
    original_archive = CONFIG.get("archive_root")
    CONFIG.set("archive_root", str(tmp / "归档"))

    try:
        store = Store(tmp / "data.db")
        app = App(qapp, store=store, adb=None)   # adb 传 None：这个用例不碰手机

        sd = staging_dir()
        sd.mkdir(parents=True, exist_ok=True)

        # 1) 正常待处理：暂存文件在
        keep = sd / "keep_me.jpg"
        keep.write_bytes(b"x" * 2048)
        app.store.add_pending(make_item(keep, "还没点的照片.jpg", STATUS_PULLED))

        # 2) 记录在、文件没了（上次删了暂存没清库，或者用户手动删了文件）
        app.store.add_pending(make_item(sd / "gone.jpg", "文件已丢失.png", STATUS_PULLED))

        # 3) 孤儿暂存文件（没有任何 pending 记录引用）
        orphan = sd / "orphan.bin"
        orphan.write_bytes(b"y" * 100)

        # 4) 已归档的历史记录，应该在启动时被清掉
        hist = sd / "hist.jpg"
        hist.write_bytes(b"z" * 10)
        app.store.add_pending(make_item(hist, "上周已保存.jpg", STATUS_SAVED))

        before = len(app.store.list_pending(("waiting", "pulling", "pulled")))

        # --- 模拟"重启"：调用启动时的恢复逻辑
        app._cleanup_staging()
        for _ in range(3):
            qapp.processEvents()

        check("重启后卡片被恢复", app.dock.card_count() == 1, f"{app.dock.card_count()} 张")
        check("恢复的正是没处理那张", keep.is_file(), keep.name)
        check("孤儿暂存被清掉", not orphan.exists(), orphan.name)
        check("已归档记录被清掉", not hist.exists(), hist.name)

        rows = app.store.list_pending(("waiting", "pulling", "pulled"))
        check("失效记录没有残留", len(rows) < before, f"{before} → {len(rows)}")
        check("剩下的正是那张待处理",
              len(rows) == 1 and rows[0].filename == "还没点的照片.jpg",
              rows[0].filename if rows else "（空）")
        check("状态归一到 pulled", rows and rows[0].status == STATUS_PULLED,
              rows[0].status if rows else "")

        # 恢复出来的卡片要能正常保存（说明 staging 路径是对的）
        ok = app._save_item(rows[0].id) if rows else False
        check("恢复的卡片能正常归档", ok and app.dock.card_count() == 0)
        archived = list((tmp / "归档").rglob("还没点的照片.jpg"))
        check("归档文件落地", bool(archived), str(archived[0]) if archived else "无")

        app.watcher._running = False
        app.store.close()
    finally:
        CONFIG.set("archive_root", original_archive)
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
