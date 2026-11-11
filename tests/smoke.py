"""非 GUI 冒烟测试：adb 通道 + 扫描解析 + 快照 diff。

运行：``.venv\\Scripts\\python.exe tests\\smoke.py``
退出码 0 = 全过。
"""
from __future__ import annotations

import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from PySide6.QtCore import QCoreApplication  # noqa: E402

_qapp = QCoreApplication(sys.argv)

from adb import AdbChannel  # noqa: E402
from config import FAST_DIRS, SLOW_DIRS, classify  # noqa: E402
from store import Store  # noqa: E402
from watcher import Watcher, _is_excluded  # noqa: E402

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    mark = "PASS" if condition else "FAIL"
    print(f"[{mark}] {name}" + (f" — {detail}" if detail else ""))
    if not condition:
        failures.append(name)


def main() -> int:
    # ---------------------------------------------------------------- adb
    adb = AdbChannel()
    device = adb.ensure_device()
    check("能发现设备", device is not None, str(device))
    if device is None:
        print("没有设备，后续用例跳过")
        return 1

    version, sdk = adb.android_info()
    print(f"       手机 Android {version} (SDK {sdk})  通道={device.transport}")

    # ---------------------------------------------------------------- 扫描
    db_path = Path(tempfile.gettempdir()) / "s2pc_smoke.db"
    db_path.unlink(missing_ok=True)
    store = Store(db_path)
    watcher = Watcher(store, adb)

    started = time.monotonic()
    buckets = watcher._scan(FAST_DIRS)
    elapsed = time.monotonic() - started
    check("快路径扫描返回结果", bool(buckets), f"{len(buckets)} 个目录，{elapsed:.2f}s")
    check("扫描耗时 < 5s", elapsed < 5.0, f"{elapsed:.2f}s")

    total = sum(len(v) for v in buckets.values())
    print(f"       最近 1 小时内文件：{total} 个")

    sample_ok = True
    for path, items in buckets.items():
        for remote, mtime, size in items:
            if not remote.startswith("/sdcard/"):
                sample_ok = False
                print(f"       路径异常：{remote}")
            if mtime < 1_600_000_000 or size < 0:
                sample_ok = False
                print(f"       字段异常：{remote} mtime={mtime} size={size}")
    check("解析出的字段合理", sample_ok)

    # 水位线应已写入
    wm = store.get_meta(f"wm:{FAST_DIRS[0][0]}", "0")
    check("水位线已记录", int(wm) > 0, wm)

    # ---------------------------------------------------------------- diff
    flat = [item for items in buckets.values() for item in items]
    if flat:
        first = store.diff(flat)
        check("首次 diff 全部视为新", len(first) == len(flat), f"{len(first)}/{len(flat)}")
        second = store.diff(flat)
        check("二次 diff 无新增", len(second) == 0, f"多出 {len(second)}")
        path, mtime, size = flat[0]
        changed = store.diff([(path, mtime + 5, size)])
        check("mtime 变化视为新", len(changed) == 1)
        store.mark_ignored(path, mtime + 5)
        check("忽略表生效", store.is_ignored(path, mtime + 5))
        check("忽略表按 mtime 区分", not store.is_ignored(path, mtime))
    else:
        # 没有近 1 小时的文件，用构造数据验证同一套逻辑
        fake = [("/sdcard/DCIM/Camera/a.jpg", 1793300000, 1024)]
        check("构造 diff 首次为新", len(store.diff(fake)) == 1)
        check("构造 diff 二次为空", len(store.diff(fake)) == 0)
        check("构造 diff 变化为新", len(store.diff([("/sdcard/DCIM/Camera/a.jpg", 1793300009, 1024)])) == 1)

    # ---------------------------------------------------------------- 规则
    check("排除 .dat（微信加密图）", _is_excluded("/sdcard/x/a.dat"))
    check("排除隐藏文件", _is_excluded("/sdcard/x/.nomedia"))
    check("排除缩略图缓存", _is_excluded("/sdcard/DCIM/.thumbnails/t.jpg"))
    check("保留正常照片", not _is_excluded("/sdcard/DCIM/Camera/a.jpg"))
    check("保留微信落地文件", not _is_excluded("/sdcard/Download/WeiXin/报表.xlsx"))

    check("分类：图片", classify("a.JPG") == "图片")
    check("分类：视频", classify("a.mp4") == "视频")
    check("分类：文档", classify("a.xlsx") == "文档")
    check("分类：未知归其他", classify("a.xyz") == "其他")

    # ---------------------------------------------------------------- 慢路径
    started = time.monotonic()
    slow = watcher._scan(SLOW_DIRS)
    slow_elapsed = time.monotonic() - started
    check("慢路径扫描完成", True, f"{slow_elapsed:.2f}s，{sum(len(v) for v in slow.values())} 个文件")
    check("慢路径耗时 < 20s", slow_elapsed < 20.0, f"{slow_elapsed:.2f}s")

    store.close()
    db_path.unlink(missing_ok=True)

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
