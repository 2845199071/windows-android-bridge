"""保存/忽略的性能剖析：把各阶段单独计时，找出真正的瓶颈。

不需要手机，用假数据造 staging 文件。
运行：``.venv\\Scripts\\python.exe tests/perf_save.py``
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
from pathlib import Path

# 默认 offscreen 不干扰桌面；设 S2PC_PERF_REAL=1 用真实窗口，
# 才能量出「半透明无边框窗口 resize」的真实代价。
if not os.environ.get("S2PC_PERF_REAL"):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from archiver import archive_file  # noqa: E402
from config import CONFIG, staging_dir  # noqa: E402
from store import STATUS_PULLED, PendingItem, Store  # noqa: E402

N = 20
SIZE = 512 * 1024


def make_items(count: int) -> list[PendingItem]:
    folder = staging_dir()
    items = []
    for i in range(count):
        path = folder / f"perf_{i}.jpg"
        path.write_bytes(os.urandom(SIZE))
        items.append(
            PendingItem(
                id=f"perf{i}",
                remote_path=f"/sdcard/DCIM/Camera/perf_{i}.jpg",
                filename=f"perf_{i}.jpg",
                staging=str(path),
                mtime=int(time.time()),
                size=SIZE,
                status=STATUS_PULLED,
                created_at=int(time.time()),
            )
        )
    return items


def main() -> int:
    qapp = QApplication(sys.argv)
    tmp_root = Path(tempfile.mkdtemp(prefix="s2pc_perf_"))
    db_path = tmp_root / "data.db"

    # 真实场景里 staging 在 C 盘，归档在 D 盘 —— 跨盘移动等于复制+删除，慢得多。
    # 默认按真实情况测 D 盘；设 S2PC_PERF_SAMEDISK=1 可以对比同盘的情况。
    if os.environ.get("S2PC_PERF_SAMEDISK"):
        archive_root = tmp_root / "归档"
    else:
        archive_root = Path(r"D:\_s2pc_perf_test") / "归档"
        shutil.rmtree(archive_root.parent, ignore_errors=True)

    original_archive = CONFIG.get("archive_root")
    CONFIG.set("archive_root", str(archive_root))

    try:
        print(f"暂存目录: {staging_dir()}")
        print(f"归档目录: {archive_root}")
        print()
        store = Store(db_path)
        items = make_items(N)

        # ---------- 1. 纯后端：归档 + 两次 DB 事务
        t_arch = t_db = 0.0
        for item in items:
            t0 = time.perf_counter()
            dest = archive_file(item.staging, item.filename, item.mtime)
            t_arch += time.perf_counter() - t0

            t0 = time.perf_counter()
            store.add_archive(item.id, item.remote_path, str(dest))
            store.update_pending(item.id, status="saved")
            t_db += time.perf_counter() - t0

        print(f"归档文件移动  × {N}: 共 {t_arch*1000:.0f} ms，单个 {t_arch/N*1000:.1f} ms")
        print(f"两次 DB 事务  × {N}: 共 {t_db*1000:.0f} ms，单个 {t_db/N*1000:.1f} ms")

        # ---------- 2. 忽略路径的后端部分
        items2 = make_items(N)
        for item in items2:
            store.add_pending(item)
        t_unlink = t_db2 = 0.0
        for item in items2:
            t0 = time.perf_counter()
            Path(item.staging).unlink(missing_ok=True)
            t_unlink += time.perf_counter() - t0

            t0 = time.perf_counter()
            store.mark_ignored(item.remote_path, item.mtime)
            store.update_pending(item.id, status="ignored")
            t_db2 += time.perf_counter() - t0

        print(f"删除暂存文件  × {N}: 共 {t_unlink*1000:.0f} ms，单个 {t_unlink/N*1000:.1f} ms")
        print(f"忽略的 DB 事务 × {N}: 共 {t_db2*1000:.0f} ms，单个 {t_db2/N*1000:.1f} ms")

        # ---------- 3. UI：移除卡片（含窗口 resize）
        from dock import SideDock

        dock = SideDock()
        dock.show()
        qapp.processEvents()

        items3 = make_items(N)
        for item in items3:
            dock.add_item(item)
        qapp.processEvents()

        t_remove = 0.0
        for item in items3:
            t0 = time.perf_counter()
            dock.remove_item(item.id)
            qapp.processEvents()
            t_remove += time.perf_counter() - t0

        print(f"移除卡片+重排 × {N}: 共 {t_remove*1000:.0f} ms，单个 {t_remove/N*1000:.1f} ms")

        total = (t_arch + t_db + t_remove) / N * 1000
        print()
        print(f"单次「保存」主线程耗时合计约 {total:.0f} ms（不含托盘通知）")
        print("参考：人眼可感知的卡顿阈值约 100 ms")

        store.close()
    finally:
        CONFIG.set("archive_root", original_archive)
        shutil.rmtree(tmp_root, ignore_errors=True)
        if not os.environ.get("S2PC_PERF_SAMEDISK"):
            shutil.rmtree(Path(r"D:\_s2pc_perf_test"), ignore_errors=True)

    return 0


if __name__ == "__main__":
    _code = main()
    sys.stdout.flush()
    os._exit(_code)
