"""快速拉取回归：大文件走 netcat 直传、阈值可关、失败回退、进度回调。

    ./.venv/Scripts/python.exe tests/fast_pull_test.py

背景：adb pull 走 TLS 通道，无线实测只有 1.3MB/s（本地文件 833MB/s），
改用手机自带 toybox netcat 直传后实测 3.7~15MB/s。任何失败都要回退 adb，
功能只增不减；小文件 nc 启动开销不划算，仍走 adb。
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

from config import CONFIG  # noqa: E402
from store import PendingItem, Store, STATUS_PULLING, STATUS_PULLED, STATUS_FAILED  # noqa: E402
from puller import Puller  # noqa: E402

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
    """记录被调了哪个方法，pull_fast 按预期写一个假文件。"""
    def __init__(self, fast_ok=True, raise_err=False):
        self.calls = []
        self.fast_ok = fast_ok
        self.raise_err = raise_err

    def pull_fast(self, remote, local, expected_size=0, min_size=0, **kw):
        self.calls.append(("pull_fast", remote, expected_size, min_size))
        if self.raise_err:
            raise RuntimeError("模拟异常")
        if self.fast_ok:
            Path(local).write_bytes(b"x" * (expected_size or 1024))
        return self.fast_ok

    def pull(self, remote, local, timeout=900):
        self.calls.append(("pull", remote))
        Path(local).write_bytes(b"y" * 1024)
        return True


def run_puller(fake, size, min_mb, item_id):
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_fast_"))
    db = Store(tmp / "data.db")
    CONFIG.set("fast_pull_min_mb", min_mb)
    item = PendingItem(
        id=item_id, remote_path="/sdcard/Documents/测试.bin",
        filename="测试.bin", staging="", mtime=int(time.time()), size=size,
        status=STATUS_PULLING, created_at=int(time.time()),
    )
    db.add_pending(item)
    puller = Puller(db, fake)
    puller._handle(item_id)
    state = db.get_pending(item_id).status
    db.close()
    shutil.rmtree(tmp, ignore_errors=True)
    return state, fake.calls


def main() -> int:
    original = CONFIG.get("fast_pull_min_mb")
    try:
        big = 20 * 1024 * 1024
        state, calls = run_puller(FakeAdb(), big, 8, "big")
        check("大文件走 pull_fast", calls and calls[0][0] == "pull_fast", str(calls))
        check("大文件传到阈值", calls and calls[0][3] == 8 * 1024 * 1024, str(calls))
        check("大文件完成后状态正确", state == STATUS_PULLED, state)

        state, calls = run_puller(FakeAdb(), 2 * 1024 * 1024, 8, "small")
        check("小文件在阈值内仍走 fast(由 adb 内部回退)",
              calls and calls[0][0] == "pull_fast", str(calls))

        original_min = CONFIG.get("fast_pull_min_mb")
        state, calls = run_puller(FakeAdb(), big, 0, "off")
        check("阈值设0全部走 adb pull", calls and calls[0][0] == "pull", str(calls))

        state, calls = run_puller(FakeAdb(fast_ok=False), big, 8, "fb")
        check("pull_fast 返回 False 时回退 adb",
              len(calls) >= 2 and calls[0][0] == "pull_fast" and calls[1][0] == "pull", str(calls))
        check("回退后仍能完成", state == STATUS_PULLED, state)

        # pull_fast 内部：无直连 IP 时直接回退，不连 socket
        from adb import AdbChannel
        ch = AdbChannel()
        ch._serial = "emulator-5554"   # USB 形态，无 IP
        check("USB 连接不走快速通道", ch._fast_host() == "")

        ch._serial = "192.168.1.100:5555"  # 示例保留地址
        CONFIG.set("wifi_endpoint", "192.168.1.100:37123")
        check("无线连接能取到直连 IP", ch._fast_host() == "192.168.1.100", ch._fast_host())

    finally:
        CONFIG.set("fast_pull_min_mb", original)
        restore()

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
