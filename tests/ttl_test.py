"""卡片时效测试：超过 3 天没处理的卡片自动忽略 + 清暂存；新卡片不受影响。

    ./.venv/Scripts/python.exe tests/ttl_test.py
"""
from __future__ import annotations

import os
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

from config import CONFIG  # noqa: E402
from main import App  # noqa: E402
from store import STATUS_IGNORED, PendingItem, Store  # noqa: E402

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
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_ttl_"))
    original_archive = CONFIG.get("archive_root")
    CONFIG.set("archive_root", str(tmp / "归档"))

    try:
        store = Store(tmp / "data.db")
        app = App(qapp, store=store, adb=None)

        staging = CONFIG  # noqa: F841  (占位，实际目录由 staging_dir() 决定)
        from config import staging_dir

        sd = staging_dir()
        sd.mkdir(parents=True, exist_ok=True)

        def make(name: str, age_days: float) -> PendingItem:
            staging_file = sd / f"{uuid.uuid4().hex}.jpg"
            staging_file.write_bytes(b"x" * 512)
            item = PendingItem(
                id=uuid.uuid4().hex,
                remote_path=f"/sdcard/DCIM/Camera/{name}",
                filename=name,
                staging=str(staging_file),
                mtime=int(time.time()) - 60,
                size=512,
                status="pulled",
                created_at=int(time.time() - age_days * 86400),
            )
            store.add_pending(item)
            app._inflight.add(item.remote_path)
            app._on_pulled(item.id)   # 生成卡片
            return item

        old1 = make("四天前的照片.jpg", 4.0)
        old2 = make("五天前的视频.mp4", 5.0)
        fresh = make("刚拍的照片.jpg", 0.1)
        check("初始有 3 张卡片", app.dock.card_count() == 3, str(app.dock.card_count()))

        # 执行时效清理
        expired = app._expire_stale_cards()
        for _ in range(5):
            qapp.processEvents()
        app._deleter_pool.waitForDone(5000)
        qapp.processEvents()

        check("识别出 2 张过期卡片", expired == 2, str(expired))
        check("过期卡片从面板移除", app.dock.card_count() == 1, str(app.dock.card_count()))
        check("过期卡片状态 → ignored", store.get_pending(old1.id).status == STATUS_IGNORED)
        check("忽略表已记录（不会误伤新版本）",
              store.is_ignored(old1.remote_path, old1.mtime))
        check("过期暂存文件已删", not old1.staging and not Path(old2.staging).exists()
              if False else not Path(old1.staging).exists() and not Path(old2.staging).exists())
        check("新卡片不受影响", store.get_pending(fresh.id).status == "pulled"
              and Path(fresh.staging).is_file())
        check("新卡片仍在面板", app.dock.card_count() == 1)
        check("inflight 已清（允许重新检测）",
              old1.remote_path not in app._inflight and fresh.remote_path in app._inflight)

        # --- 一键忽略：剩余卡片全部清空（暂存删除 + 忽略表 + 卡片移除）
        all_ids = list(app.dock.item_ids())
        app.dock.ignore_all_btn.click()          # 第一次：进入确认态
        qapp.processEvents()
        check("全部忽略需二次确认",
              app.dock._ignore_confirm and "确认忽略" in app.dock.ignore_all_btn.text()
              and app.dock.card_count() == len(all_ids),
              app.dock.ignore_all_btn.text())
        app.dock.ignore_all_btn.click()          # 第二次：真执行
        for _ in range(5):
            qapp.processEvents()
        app._deleter_pool.waitForDone(5000)
        qapp.processEvents()
        check("一键忽略 → 卡片清空", app.dock.card_count() == 0, str(app.dock.card_count()))
        check("一键忽略 → 状态全 ignored",
              all(store.get_pending(i).status == STATUS_IGNORED for i in all_ids))
        check("一键忽略 → 暂存全删", not any(Path(i.staging).exists() for i in (fresh,)))
        check("一键忽略 → 忽略表记录", store.is_ignored(fresh.remote_path, fresh.mtime))

        # 新卡片 3 天内不做清理：把时效调成 0 天？最小 1 天，所以只验证 fresh 存活
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
