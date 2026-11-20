"""真机探针：验证补拉确实会把「已保存/已忽略」的文件重新拉出来。

    ./.venv/Scripts/python.exe tests/_rescan_e2e.py

用真实 adb + **数据库副本**（不动真实 data.db），跑两轮扫描对比：
  A. 正常模式  -> 已 seen 的文件不弹（增量语义）
  B. 全量补拉  -> 窗口内全部重来（含已 seen / 已忽略）
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from adb import AdbChannel  # noqa: E402
from config import FAST_DIRS  # noqa: E402
from store import Store  # noqa: E402
from watcher import Watcher  # noqa: E402


def main() -> int:
    qapp = QCoreApplication([])  # noqa: F841
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_e2e_"))
    db = tmp / "data.db"
    # 不能直接 copy 文件：库是 WAL 模式，未 checkpoint 的记录在 data.db-wal 里，
    # 只拷 .db 会拿到落后的快照（实测 seen 972 vs 实际 1030），"已忽略"对照就不准了。
    import sqlite3

    src = sqlite3.connect(os.path.expandvars(r"%LOCALAPPDATA%\s2pc\data.db"))
    dst = sqlite3.connect(db)
    src.backup(dst)
    dst.close()
    src.close()
    store = Store(db)
    adb = AdbChannel()
    try:
        device = adb.ensure_device(allow_connect=True)
        print("设备:", device)
        if device is None:
            print("没有可用设备，跳过")
            return 0

        watcher = Watcher(store, adb)
        watcher._device = device
        events: list[list] = []
        watcher.new_files.connect(lambda items: events.append(items))

        ignored = {r[0] for r in store._conn.execute("SELECT path FROM ignored")}
        seen = {r[0] for r in store._conn.execute("SELECT path FROM seen")}
        print(f"副本库：seen {len(seen)} 条、ignored {len(ignored)} 条")

        # A. 正常模式：只报增量
        n_scan, n_new = watcher._tick(FAST_DIRS)
        print(f"[A] 正常模式   扫到 {n_scan} 个，新增 {n_new} 个")

        # B. 全量补拉 3 小时：窗口内全部重来
        events.clear()
        watcher.rescan_from(3)
        n_scan, n_new = watcher._tick(FAST_DIRS)
        paths = [p for e in events for p, _m, _s in e]
        already_ign = [p for p in paths if p in ignored]
        already_seen = [p for p in paths if p in seen]
        print(f"[B] 全量补拉 3h 扫到 {n_scan} 个，重拉 {n_new} 个（事件 {len(events)} 批）")
        print(f"    其中已忽略 {len(already_ign)} 个、已见过 {len(already_seen)} 个")
        for p in paths[:12]:
            tag = "已忽略" if p in ignored else ("已见过" if p in seen else "全新")
            print(f"    [{tag}] {p}")
        ok = n_new > 0 and (already_seen or already_ign)

        # C. 根目录自建文件夹自动发现（用户在根目录建中文文件夹存文件，如 /sdcard/我的文件）
        watcher._refresh_extra_dirs()
        print(f"[C] 自动发现额外目录 {len(watcher._extra_dirs)} 个")
        for d in watcher._extra_dirs:
            print("    ", d)

        # D. 全量补拉 1 小时，看能不能扫到那个 209MB 的 APK
        events.clear()
        watcher.rescan_from(1)
        n_scan, n_new = watcher._tick(FAST_DIRS + watcher._extra_pairs())
        paths = [p for e in events for p, _m, _s in e]
        hits = [p for p in paths if any(p.startswith(d + "/") for d in watcher._extra_dirs)]
        print(f"[D] 全量补拉 1h 扫到 {n_scan} 个，重拉 {n_new} 个")
        for p in hits:
            print("    ★", p)
        if not hits:
            print("    ⚠️ 没扫到自建目录里的文件")
        ok = ok and bool(hits)

        print("结果:", "✅ 全量重拉 + 自建目录都生效" if ok else "⚠️ 仍有项目未通过")
        return 0 if ok else 1
    finally:
        adb.close_sessions()
        store.close()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.stdout.flush()
    code = main()
    sys.stdout.flush()
    os._exit(code)
