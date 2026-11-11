"""开机自启动：写 HKCU 的 Run 键。

用注册表而不是「启动」文件夹的快捷方式 —— 不用生成 .lnk，
路径里带中文和空格也不怕（可执行文件和脚本各自用引号包起来）。
只写当前用户，不需要管理员权限。
"""
from __future__ import annotations

import sys
import winreg
from pathlib import Path

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "Ping"
LEGACY_VALUE_NAME = "手机电脑互通"   # 改名前的注册表值名，migrate_legacy() 负责搬走


def _launch_command() -> str:
    """开机启动命令。

    打包成 exe 后直接指向 exe 自己；源码运行时用 pythonw + main.py
    （pythonw 才不会开机时弹一个黑框）。
    """
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    root = Path(__file__).resolve().parent.parent
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    script = root / "app" / "main.py"
    exe = pythonw if pythonw.is_file() else Path(sys.executable)
    return f'"{exe}" "{script}"'


def is_enabled() -> bool:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return bool(str(value).strip())
    except (FileNotFoundError, OSError):
        return False


def current_command() -> str:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, VALUE_NAME)
            return str(value)
    except (FileNotFoundError, OSError):
        return ""


def set_enabled(enabled: bool) -> tuple[bool, str]:
    """开关自启动。返回 (是否成功, 错误信息)。"""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            if enabled:
                winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, _launch_command())
            else:
                try:
                    winreg.DeleteValue(key, VALUE_NAME)
                except FileNotFoundError:
                    pass
        return True, ""
    except OSError as exc:
        return False, str(exc)


def is_registered_but_stale() -> bool:
    """注册表里有值，但指向的路径已经不是当前这份程序（比如项目挪了位置、或源码版换成了 exe 版）。"""
    command = current_command()
    if not command:
        return False
    return _launch_command().lower() not in command.lower()


def migrate_legacy() -> None:
    """旧名「手机电脑互通」的 Run 值搬到新名「Ping」下（只搬一次，没有就跳过）。

    改名后旧值指向的 exe 路径会失效；搬过来之后 stale 检测会把路径修正成当前程序。
    """
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            old, _ = winreg.QueryValueEx(key, LEGACY_VALUE_NAME)
    except (FileNotFoundError, OSError):
        return
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, str(old))
            winreg.DeleteValue(key, LEGACY_VALUE_NAME)
    except OSError:
        return
