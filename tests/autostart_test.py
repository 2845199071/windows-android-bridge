"""开机自启动测试：写 / 读 / 删 HKCU Run 键，测完恢复原状态。

运行：``.venv\\Scripts\\python.exe tests\\autostart_test.py``
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

import autostart  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main() -> int:
    original = autostart.is_enabled()
    print(f"测试前的状态：{'已开启' if original else '未开启'}")
    if original:
        print(f"  现有命令：{autostart.current_command()}")

    try:
        ok, err = autostart.set_enabled(True)
        check("写入注册表成功", ok, err)
        check("读取状态为已开启", autostart.is_enabled())

        command = autostart.current_command()
        check("命令里有 pythonw.exe", "pythonw.exe" in command, command)
        check("命令里有 main.py", "main.py" in command)
        check("两个路径都带引号", command.count('"') == 4, command)
        check("指向当前项目目录", str(Path(__file__).resolve().parent.parent) in command,
              command)

        ok, err = autostart.set_enabled(False)
        check("删除注册表成功", ok, err)
        check("读取状态为未开启", not autostart.is_enabled())

        # 重复删除不该报错
        ok, _ = autostart.set_enabled(False)
        check("重复关闭不报错", ok)
    finally:
        autostart.set_enabled(original)
        print(f"已恢复测试前状态：{'开启' if original else '关闭'}")

    print()
    if failures:
        print(f"失败 {len(failures)} 项：{', '.join(failures)}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    _code = main()
    sys.stdout.flush()
    os._exit(_code)
