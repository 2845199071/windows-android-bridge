"""卡片拖出测试：拖拽内容构造 + 拖出时自动归档 + 拖完卡片移除。

    ./.venv/Scripts/python.exe tests/drag_out_test.py
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

from config import CONFIG  # noqa: E402
from main import App  # noqa: E402
from store import STATUS_SAVED, PendingItem, Store  # noqa: E402

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
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_drag_"))
    archive_root = tmp / "归档"
    original_archive = CONFIG.get("archive_root")
    CONFIG.set("archive_root", str(archive_root))
    CONFIG.set("auto_expand", False)

    try:
        store = Store(tmp / "data.db")
        app = App(qapp, store=store, adb=None)

        staging_dir = archive_root / ".staging"
        staging_dir.mkdir(parents=True, exist_ok=True)
        staging_file = staging_dir / "abc123.jpg"
        staging_file.write_bytes(b"photo-bytes")

        item = PendingItem(
            id="d1", remote_path="/sdcard/DCIM/Camera/IMG_20261109.jpg",
            filename="IMG_20261109.jpg", staging=str(staging_file),
            mtime=int(time.time()), size=11, status="pulled", created_at=int(time.time()),
        )
        store.add_pending(item)
        app._inflight.add(item.remote_path)
        app.dock.add_item(item)
        qapp.processEvents()
        card = app.dock._cards["d1"]

        # --- 拖拽内容构造（不真的进入拖拽循环）
        mime, pixmap = card.build_drag_mime()
        check("拖拽内容已生成", mime is not None)
        urls = mime.urls() if mime else []
        local = urls[0].toLocalFile() if urls else ""
        check("带文件 uri-list", bool(urls) and urls[0].isLocalFile(), local)
        path = urls[0].toLocalFile() if urls else ""
        check("交付的是归档后的路径（不是暂存）",
              "归档" in path and ".staging" not in path, path)
        check("归档文件真的存在", Path(path).is_file())
        check("暂存文件已移走", not staging_file.exists())
        check("库里已标记 saved", store.get_pending("d1").status == STATUS_SAVED)
        check("archive 表有记录", store.archive_count() == 1, str(store.archive_count()))

        # --- 拖拽结束 → 卡片移除
        app._on_card_dragged("d1")
        qapp.processEvents()
        check("拖完卡片从面板移除", app.dock.card_count() == 0, str(app.dock.card_count()))

        # --- 兜底：文件已不在暂存（比如被清理），拖拽要安全失败
        item2 = PendingItem(
            id="d2", remote_path="/sdcard/DCIM/Gone.jpg", filename="Gone.jpg",
            staging=str(staging_dir / "missing.jpg"), mtime=1, size=1,
            status="pulled", created_at=1,
        )
        store.add_pending(item2)
        app.dock.add_item(item2)
        qapp.processEvents()
        got = app._save_for_drag("d2")
        check("暂存缺失时拖拽失败但不崩", got is None)
        check("失败时卡片仍在（不误删）", "d2" in app.dock._cards)

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
