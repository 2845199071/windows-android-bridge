"""常驻会话的真机对照测试：同一批命令分别走「常驻」和「一次性进程」，逐条比对输出。

需要一个在线的设备。手机不在线时会跳过（不报失败），方便随时跑。

    ./.venv/Scripts/python.exe tests/session_live.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from adb import AdbChannel, AdbError  # noqa: E402

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


# 覆盖：空输出、多行、中文路径、带引号、失败命令、大输出
CASES = [
    ("空命令", "true"),
    ("单行", "echo hello"),
    ("多行", "printf 'a\\nb\\nc\\n'"),
    ("中文", "echo 中文测试"),
    ("列目录", "ls /sdcard 2>/dev/null | head -5"),
    ("带管道", "ls /sdcard 2>/dev/null | wc -l"),
    ("引号路径", "ls '/sdcard/Download' 2>/dev/null | head -3"),
    ("失败命令", "ls /definitely/not/exist 2>/dev/null; echo after"),
    ("find 全量", "find /sdcard/Download -maxdepth 2 -type f 2>/dev/null | head -40"),
]


def main() -> int:
    adb = AdbChannel()
    device = adb.ensure_device()
    if device is None:
        print("没有在线设备，跳过真机对照测试")
        return 0
    print(f"设备：{device.serial} ({device.transport})")
    print()

    try:
        for label, cmd in CASES:
            try:
                one = adb.shell(cmd, timeout=30, reuse=False)
            except AdbError as exc:
                check(label, False, f"一次性进程报错 {exc}")
                continue
            try:
                reused = adb.shell(cmd, timeout=30, reuse=True)
            except AdbError as exc:
                check(label, False, f"常驻会话报错 {exc}")
                continue
            same = one == reused
            check(f"{label} 输出一致", same,
                  f"{len(one)} vs {len(reused)} 字节" if not same else f"{len(one)} 字节")
            if not same:
                a, b = one.splitlines(), reused.splitlines()
                for i, (x, y) in enumerate(zip(a, b)):
                    if x != y:
                        print(f"       第一处不同 行{i}: 一次性={x!r} 常驻={y!r}")
                        break

        # --- 连续 50 轮扫描，每轮都要正确（模拟 3 秒一轮跑 2.5 分钟）
        script = (
            "echo '@@DIR /sdcard/DCIM'; "
            "find /sdcard/DCIM -maxdepth 2 -type f -printf '%T@|%s|%p\\n' 2>/dev/null | head -200"
        )
        t0 = time.monotonic()
        rounds, bad = 50, 0
        for _ in range(rounds):
            try:
                out = adb.shell(script, timeout=60, reuse=True)
            except AdbError:
                bad += 1
                continue
            if "@@DIR /sdcard/DCIM" not in out:
                bad += 1
        cost = (time.monotonic() - t0) / rounds * 1000
        check(f"连续 {rounds} 轮扫描全部成功", bad == 0, f"{bad} 轮失败，平均 {cost:.0f} ms/轮")

        # --- 与一次性进程的单轮耗时对比（这才是省下来的那部分）
        t0 = time.monotonic()
        for _ in range(5):
            adb.shell(script, timeout=60, reuse=False)
        spawn_cost = (time.monotonic() - t0) / 5 * 1000
        check("常驻比一次性快", cost < spawn_cost,
              f"常驻 {cost:.0f} ms vs 一次性 {spawn_cost:.0f} ms（省 {spawn_cost - cost:.0f} ms/轮）")

        # --- 会话还活着，且能在失败命令之后继续用
        check("失败命令后会话仍可用", adb.shell("echo ok", 10, reuse=True).strip() == "ok")
    finally:
        adb.close_sessions()

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
