"""端到端测试：真机 → pull → 卡片 → 归档。

用临时数据库和临时归档目录，不碰用户的真实数据。
运行：``.venv\\Scripts\\python.exe tests\\e2e.py``
"""
from __future__ import annotations

import os
import sys
import tempfile
import time
import uuid
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from PySide6.QtWidgets import QApplication  # noqa: E402

from adb import AdbChannel  # noqa: E402
from config import CONFIG  # noqa: E402
from main import App  # noqa: E402
from store import STATUS_PULLED, STATUS_PULLING, STATUS_WAITING, PendingItem, Store  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def pump(qapp: QApplication, seconds: float, until=None) -> bool:
    """跑事件循环直到条件满足或超时。"""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        qapp.processEvents()
        if until is not None and until():
            return True
        time.sleep(0.05)
    return until() if until is not None else True


def main() -> int:
    qapp = QApplication(sys.argv)

    # 挑一个手机上的小文件（截图目录里最近的 jpg）
    adb = AdbChannel()
    device = adb.ensure_device()
    if device is None:
        print("没有设备，跳过")
        return 1

    listing = adb.shell(
        "find /sdcard/DCIM/Screenshots -maxdepth 1 -type f -name '*.jpg' -printf '%s|%T@|%p\\n' "
        "2>/dev/null | sort -t'|' -k2 -rn | head -3",
        timeout=30,
    )
    candidates = []
    for line in listing.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) == 3:
            try:
                candidates.append((int(parts[0]), int(float(parts[1])), parts[2]))
            except ValueError:
                continue
    if not candidates:
        print("找不到测试用的截图，跳过")
        return 1

    size, mtime, remote = candidates[0]
    print(f"       测试文件：{remote}（{size} 字节）")

    # 临时库 + 临时归档目录。CONFIG 是全局单例，改完必须恢复，
    # 否则会把用户真实的 archive_root 覆盖成临时路径（已踩过）。
    original_archive = CONFIG.get("archive_root")
    import atexit

    atexit.register(lambda: CONFIG.set("archive_root", original_archive))

    tmp_root = Path(tempfile.mkdtemp(prefix="s2pc_e2e_"))
    db_path = tmp_root / "data.db"
    archive_root = tmp_root / "归档"
    CONFIG.set("archive_root", str(archive_root))

    store = Store(db_path)
    app = App(qapp, store=store, adb=adb)
    app.puller.start()
    time.sleep(0.2)

    # 直接投喂一个「新文件」事件，模拟 watcher 发现文件
    app._on_new_files([(remote, mtime, size)])
    pulled = pump(qapp, 30.0, until=lambda: app.dock.card_count() > 0)
    check("文件被拉取并生成卡片", pulled, f"卡片数 {app.dock.card_count()}")

    items = store.list_pending((STATUS_WAITING, "pulling", "pulled"))
    check("pending 记录已落库", len(items) == 1, f"{len(items)} 条")
    if not items:
        return 1
    item: PendingItem = items[0]
    check("staging 文件存在", Path(item.staging).is_file(), item.staging)
    check("staging 保持原扩展名", Path(item.staging).suffix.lower() == ".jpg")

    # 归档
    ok = app._save_item(item.id)
    check("归档返回成功", ok)
    saved = list(archive_root.rglob("*"))
    files = [p for p in saved if p.is_file()]
    check("归档目录里有文件", len(files) == 1, str(files[0]) if files else "")
    if files:
        dest = files[0]
        check("按类型分目录", dest.parent.parent.name == "图片", str(dest.parent))
        check("按日期分目录", len(dest.parent.name) == 10 and dest.parent.name[4] == "-", dest.parent.name)
        check("文件名为原名", dest.name == Path(remote).name, dest.name)
        check("大小一致", dest.stat().st_size == size, f"{dest.stat().st_size} vs {size}")

    check("归档后卡片已移除", app.dock.card_count() == 0, f"剩 {app.dock.card_count()}")
    check("archive 表有记录", store.archive_count() == 1, str(store.archive_count()))

    # 忽略逻辑
    app._on_new_files([(remote, mtime + 60, size)])
    pump(qapp, 30.0, until=lambda: app.dock.card_count() > 0)
    items2 = store.list_pending((STATUS_WAITING, "pulling", "pulled"))
    if items2:
        victim = items2[0]
        app._ignore_item(victim.id)
        check("忽略后卡片移除", app.dock.card_count() == 0)
        # 删除是丢给后台线程池的（慢盘上一次 unlink 能到 200ms，不能卡 UI），
        # 所以这里要等它删完，而不是立刻断言。
        pump(qapp, 5.0, until=lambda: not Path(victim.staging).exists())
        check("忽略后 staging 已删", not Path(victim.staging).exists(),
              f"{Path(victim.staging).name}")
        check("忽略表已记录", store.is_ignored(victim.remote_path, victim.mtime))
    else:
        check("忽略用例：拿到待处理项", False)

    app.puller.stop()
    app.puller.wait(3000)
    store.close()

    # 清理临时目录
    import shutil

    shutil.rmtree(tmp_root, ignore_errors=True)

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
