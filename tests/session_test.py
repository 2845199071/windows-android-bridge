"""常驻 shell 会话的协议测试。

不依赖手机：用本机 bash 当"远端 shell"，验证 `_ShellSession` 的
请求/响应协议、编码、超时、异常恢复是否可靠。真机行为见 session_live.py。

    ./.venv/Scripts/python.exe tests/session_test.py
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from adb import AdbError, _ShellSession  # noqa: E402

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


def main() -> int:
    bash = shutil.which("bash")
    if not bash:
        print("找不到 bash，跳过")
        return 0
    env = dict(os.environ)

    # --- 基本收发
    s = _ShellSession([bash], env)
    try:
        out = s.run("echo hello", 5)
        check("单条命令输出正确", out.strip() == "hello", repr(out))

        # 命令本身不该被回显（-T 关键作用之一）
        check("命令没有被回显", "echo hello" not in out, repr(out))

        # --- 多次连续调用，每次都拿到自己的结果
        results = [s.run(f"echo N{i}", 5).strip() for i in range(30)]
        check("连续 30 次调用各自正确", results == [f"N{i}" for i in range(30)],
              results[:3] + ["..."] + results[-2:])

        # --- 多行 + 中文 + 特殊字符
        out = s.run("printf '第一行\\n第二行 中文|带竖线\\n'", 5)
        check("多行中文输出完整", out.splitlines() == ["第一行", "第二行 中文|带竖线"], repr(out))

        # --- 大输出（find 扫出上千行的情况）
        t = time.monotonic()
        out = s.run("for i in $(seq 1 5000); do echo \"line-$i\"; done", 30)
        cost = time.monotonic() - t
        lines = out.splitlines()
        check("5000 行输出无丢失", len(lines) == 5000 and lines[0] == "line-1" and lines[-1] == "line-5000",
              f"{len(lines)} 行，{cost * 1000:.0f}ms")

        # --- 命令返回非零不该污染协议
        out = s.run("ls /definitely/not/exist 2>/dev/null; echo after-fail", 5)
        check("失败命令后协议仍正常", out.strip() == "after-fail", repr(out))

        # --- 空输出
        out = s.run("true", 5)
        check("空输出返回空串", out == "", repr(out))

        # --- 哨兵不能和真实输出撞车
        out = s.run("echo __S2PC_END__", 5)
        check("输出里出现相似哨兵不会误判结束", "__S2PC_END__" in out, repr(out))

        # --- 会话自身耗时（常驻的收益）
        t = time.monotonic()
        for _ in range(20):
            s.run("true", 5)
        per_call = (time.monotonic() - t) / 20 * 1000
        check("常驻会话空命令开销 < 5ms", per_call < 5, f"{per_call:.2f} ms/次")
    finally:
        s.close()

    # --- 超时后会话必须被关掉，不能留下挂死进程
    s2 = _ShellSession([bash], env)
    try:
        try:
            s2.run("sleep 5", 1)
            check("超时会抛异常", False, "没有抛异常")
        except AdbError as exc:
            check("超时会抛异常", "超时" in str(exc), str(exc))
        check("超时后会话标记为已关闭", s2._closed, str(s2._closed))
        try:
            s2.run("echo x", 2)
            check("关闭后再用会报错", False, "没报错")
        except AdbError:
            check("关闭后再用会报错", True)
    finally:
        s2.close()

    # --- close 之后再 close 不该炸
    try:
        s2.close()
        check("重复 close 不报错", True)
    except Exception as exc:  # noqa: BLE001
        check("重复 close 不报错", False, str(exc))

    # --- 对端直接退出（模拟手机断开）：应识别为会话结束而不是挂住
    s3 = _ShellSession([bash], env)
    try:
        try:
            s3.run("exit 3", 3)
            check("对端退出会报错", False, "没报错")
        except AdbError as exc:
            check("对端退出会报错", "结束" in str(exc) or "关闭" in str(exc), str(exc))
        check("对端退出后已关闭", s3._closed, str(s3._closed))
    finally:
        s3.close()

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
