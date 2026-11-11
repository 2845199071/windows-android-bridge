"""手机端系统通知（notify_phone）测试：无需装 app，cmd notification post 最佳努力。

    ./.venv/Scripts/python.exe tests/phone_notify_test.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _guard import backup, restore  # noqa: E402

backup()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from adb import AdbChannel  # noqa: E402

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


class SpyAdb(AdbChannel):
    """捕获发给手机的 shell 命令，不真跑 adb。"""

    def __init__(self, reply: str = "Posted") -> None:
        super().__init__()
        self._serial = "fake-serial"
        self.commands: list[str] = []
        self.reply = reply

    def _run(self, args, timeout: float = 25):  # noqa: D102
        self.commands.append(" ".join(args))
        from subprocess import CompletedProcess
        return CompletedProcess(args, 0, stdout=self.reply.encode("utf-8"), stderr=b"")


def main() -> int:
    try:
        # 无设备时直接放弃
        a = AdbChannel()
        a._serial = ""
        check("无设备时返回 False", a.notify_phone("t", "x") is False)

        # 有设备：命令结构正确
        spy = SpyAdb()
        ok = spy.notify_phone("Ping 电脑发送", "已收到 2 个文件，已存到 /sdcard/Download/电脑发送\na.jpg、b.png")
        check("有设备时返回 True", ok is True)
        check("共一条 shell 命令", len(spy.commands) == 1)
        cmd = spy.commands[-1]
        check("走 cmd notification post", "cmd notification post" in cmd)
        check("tag 固定 s2pc（同 tag 通知互相覆盖不堆积）", "s2pc" in cmd)
        check("带 bigtext 样式", "-S bigtext" in cmd)
        check("标题已包含", "Ping 电脑发送" in cmd)
        check("内容已包含去向与文件名", "已存到 /sdcard/Download/电脑发送" in cmd and "a.jpg、b.png" in cmd)
        check("文案内引号被剥掉（只剩 4 个包裹引号）", cmd.count("'") == 4, cmd)

        # ROM 拒绝（返回 Error）→ 返回 False 不抛异常
        spy2 = SpyAdb(reply="Error: something")
        check("ROM 拒绝时返回 False", spy2.notify_phone("t", "x") is False)
    finally:
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
