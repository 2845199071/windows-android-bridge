"""检测 pythonw 调用 adb 时会不会闪出控制台窗口。

思路：用 pythonw（GUI、无控制台）启动一个密集调 adb 的子进程，同时以 20ms 间隔
枚举系统里可见的控制台窗口，看有没有新的冒出来。比肉眼观察靠谱。
"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PYW = ROOT / ".venv" / "Scripts" / "pythonw.exe"
STRESS = ROOT / "tests" / "_adb_stress.py"

user32 = ctypes.windll.user32
WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
CONSOLE_CLASSES = {"ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS"}


def console_windows() -> set[tuple[str, str]]:
    found: set[tuple[str, str]] = set()

    def callback(hwnd, _lparam):
        if user32.IsWindowVisible(hwnd):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, buf, 256)
            cls = buf.value
            if cls in CONSOLE_CLASSES:
                title = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, title, 256)
                found.add((cls, title.value))
        return True

    user32.EnumWindows(WNDENUMPROC(callback), 0)
    return found


def run_stress(mode: str) -> set[tuple[str, str]]:
    baseline = console_windows()
    proc = subprocess.Popen(
        [str(PYW), str(STRESS), mode],
        creationflags=0x08000000,  # CREATE_NO_WINDOW
    )
    seen: set[tuple[str, str]] = set()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and proc.poll() is None:
        for win in console_windows() - baseline:
            seen.add(win)
        time.sleep(0.02)
    proc.wait(timeout=10)
    return seen


def main() -> int:
    if not PYW.is_file():
        print(f"找不到 {PYW}")
        return 1

    baseline = console_windows()
    print(f"基线：当前可见控制台窗口 {len(baseline)} 个")
    for item in baseline:
        print(f"  {item}")
    print()

    results = {}
    for mode, label in (("legacy", "旧写法（只有 CREATE_NO_WINDOW）"),
                        ("new", "新写法（+DETACHED+STARTUPINFO）")):
        print(f"压测中：{label}")
        seen = run_stress(mode)
        results[mode] = seen
        if seen:
            print(f"  ❌ 冒出 {len(seen)} 个新控制台窗口：{seen}")
        else:
            print("  ✅ 静默，无窗口")
        time.sleep(0.5)

    print()
    if results["new"]:
        print("结论：新写法仍会弹窗，需要换方案（例如长连接 shell）")
        return 1
    if results["legacy"] and not results["new"]:
        print("结论：旧写法会弹、新写法不弹 —— 修复有效")
    else:
        print("结论：两种写法都静默 —— 弹窗另有来源（可能是 adb server 的 fork 进程）")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    raise SystemExit(code)
