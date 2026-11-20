"""进程级追踪：确认调用 adb 时有没有 conhost.exe（控制台宿主）被创建。

比抓窗口可靠 —— 窗口一闪而过会漏采样，但 conhost 进程会存活几百毫秒。
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

kernel32 = ctypes.windll.kernel32
TH32CS_SNAPPROCESS = 0x00000002


class PROCESSENTRY32(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def snapshot() -> dict[int, tuple[str, int]]:
    handle = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if handle == -1:
        return {}
    entry = PROCESSENTRY32()
    entry.dwSize = ctypes.sizeof(PROCESSENTRY32)
    result: dict[int, tuple[str, int]] = {}
    ok = kernel32.Process32First(handle, ctypes.byref(entry))
    while ok:
        name = entry.szExeFile.decode("mbcs", "replace")
        result[entry.th32ProcessID] = (name, entry.th32ParentProcessID)
        ok = kernel32.Process32Next(handle, ctypes.byref(entry))
    kernel32.CloseHandle(handle)
    return result


def watch(mode: str, seconds: float = 12.0) -> dict[str, int]:
    before = snapshot()
    proc = subprocess.Popen(
        [str(PYW), str(STRESS), mode], creationflags=0x08000000
    )
    seen: dict[str, int] = {}
    samples = 0
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline and proc.poll() is None:
        samples += 1
        current = snapshot()
        for pid, (name, _parent) in current.items():
            if pid not in before:
                seen[name] = seen.get(name, 0) + 1
        time.sleep(0.008)
    proc.wait(timeout=10)
    print(f"  采样 {samples} 次")
    return seen


def main() -> int:
    print("基线进程数:", len(snapshot()))
    for mode, label in (("legacy", "旧写法 CREATE_NO_WINDOW"),
                        ("new", "新写法 +DETACHED+STARTUPINFO")):
        print(f"\n压测：{label}")
        seen = watch(mode)
        interesting = {k: v for k, v in seen.items() if k.lower() in
                       ("conhost.exe", "openconsole.exe", "windowsterminal.exe")}
        print(f"  期间新建进程：{seen}")
        if interesting:
            print(f"  ❌ 出现控制台宿主：{interesting} —— 会有黑框")
        else:
            print("  ✅ 没有 conhost/终端进程，说明确实没有分配控制台")
    return 0


if __name__ == "__main__":
    code = main()
    sys.stdout.flush()
    raise SystemExit(code)
