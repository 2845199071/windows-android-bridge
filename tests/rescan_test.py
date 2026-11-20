"""补拉（重新拉取）回归测试。

    ./.venv/Scripts/python.exe tests/rescan_test.py

覆盖三件事：
1. 水位线只能往前退（补拉 1 小时不能把 10:28 的水位线推到 11:00）；
2. 补拉的语义是「窗口内全部重来」，已 saved / ignored 的文件也要重新弹；
3. 一次扫出几千个时不丢文件：超出 max_batch 的进队列，下一轮继续发。
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

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication  # noqa: E402

from config import FAST_DIRS  # noqa: E402
from store import Store  # noqa: E402
from watcher import Watcher  # noqa: E402

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


class FakeAdb:
    """只回放固定 shell 输出，不碰真机。"""

    def __init__(self) -> None:
        self.out = ""

    def shell(self, script: str, timeout: int = 0, reuse: bool = False) -> str:
        return self.out


def main() -> int:
    qapp = QCoreApplication([])  # noqa: F841
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_rescan_"))
    store = Store(tmp / "data.db")
    adb = FakeAdb()
    watcher = Watcher(store, adb)
    now = int(time.time())
    target = FAST_DIRS[0][0]

    try:
        # --- 1. 水位线本来更早：补拉更短的时长不能把它往后推
        store.set_meta(f"wm:{target}", str(now - 2 * 3600))
        watcher.rescan_from(1)
        got = int(store.get_meta(f"wm:{target}"))
        check("补拉 1 小时不覆盖更早的水位线",
              abs(got - (now - 2 * 3600)) <= 3, f"回退到 {now - got} 秒前")

        # --- 2. 水位线很近：补拉 1 天要正常往前退
        store.set_meta(f"wm:{target}", str(now - 60))
        watcher.rescan_from(24)
        got = int(store.get_meta(f"wm:{target}"))
        check("补拉 1 天正常回退",
              abs(got - (now - 24 * 3600)) <= 3, f"回退到 {now - got} 秒前")

        events: list[list] = []
        watcher.new_files.connect(lambda items: events.append(items))
        one = f"@@DIR {target}\n{now - 100}|10|{target}/a.jpg\n"

        # --- 3. 正常模式：首次算新增，第二次不再弹
        watcher._rescan_all = False
        adb.out = one
        n_scan, n_total = watcher._tick([(target, 3)])
        check("正常模式首次：1 个新增", (n_scan, n_total) == (1, 1), f"{n_scan}/{n_total}")
        n_scan, n_total = watcher._tick([(target, 3)])
        check("正常模式重复：扫到 1 但新增 0", (n_scan, n_total) == (1, 0), f"{n_scan}/{n_total}")

        # --- 4. 全量模式：已 seen 的文件必须重新拉出来
        watcher._rescan_all = True
        events.clear()
        n_scan, n_total = watcher._tick([(target, 3)])
        check("全量模式重拉已扫描过的文件", n_total == 1 and len(events) == 1,
              f"总数 {n_total}，事件 {len(events)} 次")

        # --- 5. 全量模式：已 ignored 的文件也要重新拉
        store.mark_ignored(f"{target}/a.jpg", now - 100)
        events.clear()
        n_scan, n_total = watcher._tick([(target, 3)])
        check("全量模式重拉已忽略的文件", n_total == 1 and len(events) == 1,
              f"总数 {n_total}，事件 {len(events)} 次")
        watcher._rescan_all = False

        # --- 6. 正常模式仍要过滤已忽略（不能因为改了补拉就污染日常扫描）
        events.clear()
        n_scan, n_total = watcher._tick([(target, 3)])
        check("正常模式不弹已忽略文件", n_total == 0, f"总数 {n_total}")

        # --- 7. 超过单批上限：剩下的进队列，下轮补发，一个都不能丢
        lines = [f"@@DIR {target}"]
        lines += [f"{now - 200 - i}|10|{target}/bulk{i}.jpg" for i in range(150)]
        adb.out = "\n".join(lines)
        watcher._rescan_all = True
        events.clear()
        n_scan, n_total = watcher._tick([(target, 3)])
        first = len(events[0]) if events else 0
        check("单批最多 100 个", first == 100, f"首批 {first}")
        check("总数如实回报 150", n_total == 150, f"总数 {n_total}")
        check("剩余 50 个已排队", len(watcher._rescan_queue) == 50,
              f"队列 {len(watcher._rescan_queue)}")
        watcher._rescan_all = False

        events.clear()
        adb.out = ""
        watcher._tick([(target, 3)])
        back = len(events[0]) if events else 0
        check("下一轮把队列补发完", back == 50, f"补发 {back}")
        check("队列已清空", not watcher._rescan_queue)

        # --- 9. 根目录自建文件夹自动纳入监听（只收中文名，英文 app 目录不收）
        adb.out = ("/sdcard/我的文件/\n/sdcard/相册备份/\n"
                   "/sdcard/Android/\n/sdcard/Quark/\n/sdcard/DCIM/\n")
        watcher._refresh_extra_dirs()
        check("自动发现中文文件夹",
              watcher._extra_dirs == ["/sdcard/我的文件", "/sdcard/相册备份"],
              str(watcher._extra_dirs))
        check("英文 app 目录不收",
              "/sdcard/Android" not in watcher._extra_dirs
              and "/sdcard/Quark" not in watcher._extra_dirs)

        # --- 10. 补拉要把额外目录的水位线一起回退
        store.set_meta("wm:/sdcard/我的文件", str(now))
        watcher.rescan_from(24)
        got = int(store.get_meta("wm:/sdcard/我的文件"))
        check("补拉覆盖额外目录", abs(got - (now - 24 * 3600)) <= 3, f"回退到 {now - got} 秒前")
        check("扫描组含额外目录", ("/sdcard/我的文件", 2) in watcher._extra_pairs())

        # --- 8. 补拉要记住小时数，供扫完回报给 UI
        watcher.rescan_from(6)
        check("补拉时长已记录", watcher._rescan_hours == 6, str(watcher._rescan_hours))
        check("已开启全量模式", watcher._rescan_all is True)
        check("已请求立刻扫描", watcher._force_scan is True)
    finally:
        store.close()
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
