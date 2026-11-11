"""长期离线降级测试：天没开机 → 只回看最近 1 小时；补拉能找回更早的。

背景：水位线停在十天前 → 一启动把这十天文件全当"新文件"灌上来。
本文件验证：心跳判定、水位线重置、短离线不误伤、补拉回退。

    ./.venv/Scripts/python.exe tests/offline_gap_test.py
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

from config import CONFIG, FAST_DIRS, SLOW_DIRS  # noqa: E402
from store import Store  # noqa: E402
from watcher import _LAST_SEEN_PATH, Watcher  # noqa: E402

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


def wm_of(store: Store, path: str) -> int:
    try:
        return int(store.get_meta(f"wm:{path}", "0"))
    except ValueError:
        return 0


def main() -> int:
    qapp = QApplication(sys.argv)   # noqa: F841  (Qt 上下文，Watcher 需要)
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_gap_"))
    original_seen = _LAST_SEEN_PATH.read_text() if _LAST_SEEN_PATH.is_file() else None

    try:
        # ---------- 场景 1：离线 10 天 → 只回看最近 1 小时
        store = Store(tmp / "a.db")
        w = Watcher(store, adb=None)
        for path, _ in (*FAST_DIRS, *SLOW_DIRS):
            store.set_meta(f"wm:{path}", str(int(time.time()) - 10 * 86400))
        _LAST_SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        _LAST_SEEN_PATH.write_text(str(int(time.time() - 10 * 86400)), encoding="utf-8")

        got = []
        w.backlog_skipped.connect(lambda gap, keep: got.append((gap, keep)))
        gap = w.prepare_startup()
        qapp.processEvents()

        now = time.time()
        one_hour_ago = now - 3600
        check("识别出长期离线", gap is not None and 239 < gap < 241, f"{gap:.1f} 小时")
        check("发出降级信号", len(got) == 1 and got[0][1] == 1.0, str(got))
        wms = [wm_of(store, p) for p, _ in (*FAST_DIRS, *SLOW_DIRS)]
        check("所有目录水位线重置为 1 小时前",
              all(abs(v - one_hour_ago) < 10 for v in wms), f"{min(wms)}..{max(wms)}")
        check("心跳已更新为当前时间", abs(float(_LAST_SEEN_PATH.read_text()) - now) < 5)
        store.close()

        # ---------- 场景 2：只离线 1 小时 → 不重置，正常续（不丢文件）
        store2 = Store(tmp / "b.db")
        w2 = Watcher(store2, adb=None)
        keep_at = int(time.time() - 3600)
        store2.set_meta(f"wm:{FAST_DIRS[0][0]}", str(keep_at))
        _LAST_SEEN_PATH.write_text(str(int(time.time() - 3600)), encoding="utf-8")
        got2 = []
        w2.backlog_skipped.connect(lambda gap, keep: got2.append(gap))
        gap2 = w2.prepare_startup()
        check("短离线不触发降级", gap2 is None and not got2)
        check("短离线水位线保持原样", wm_of(store2, FAST_DIRS[0][0]) == keep_at)
        store2.close()

        # ---------- 场景 3：首次安装（无心跳）→ 不重置
        try:
            _LAST_SEEN_PATH.unlink()
        except OSError:
            pass
        store3 = Store(tmp / "c.db")
        w3 = Watcher(store3, adb=None)
        check("首次运行不降级", w3.prepare_startup() is None)
        check("首次运行写入心跳", _LAST_SEEN_PATH.is_file())
        store3.close()

        # ---------- 场景 4：补拉 48 小时 → 水位线回退
        store4 = Store(tmp / "d.db")
        w4 = Watcher(store4, adb=None)
        for path, _ in (*FAST_DIRS, *SLOW_DIRS):
            store4.set_meta(f"wm:{path}", str(int(time.time())))
        base = w4.rescan_from(48)
        qapp.processEvents()
        check("补拉水位线 = 48 小时前",
              abs(base - (time.time() - 48 * 3600)) < 10, str(base))
        check("补拉覆盖所有目录",
              all(abs(wm_of(store4, p) - base) < 2 for p, _ in (*FAST_DIRS, *SLOW_DIRS)))
        check("补拉已置强制扫描标记", w4._force_scan is True)
        store4.close()
    finally:
        if original_seen is None:
            try:
                _LAST_SEEN_PATH.unlink()
            except OSError:
                pass
        else:
            _LAST_SEEN_PATH.write_text(original_seen, encoding="utf-8")
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
