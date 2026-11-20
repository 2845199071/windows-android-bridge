"""资源管理器右键菜单「发送到手机」。

只写 ``HKCU\\Software\\Classes\\*\\shell\\...``：当前用户生效、不需要管理员、
不弹 UAC，也不影响别的用户。卸载就是把这一支删掉。

多选用 ``MultiSelectModel=Document``：Windows 会为每个选中文件各起一个进程，
后起的那些通过 ``ipc.py`` 把路径转给正在跑的实例 —— 这是 shell 动词在纯
注册表方案下唯一可靠的多选姿势（要真正"一次调用拿到全部文件"得写 COM
DropTarget 组件，代价和维护成本都不划算）。
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger("s2pc.shellmenu")

VERB_NAME = "Ping"
_LEGACY_VERB_NAME = "手机电脑互通"   # 改名前的注册表键名，migrate_legacy() 负责搬走
VERB_LABEL = "发送到手机"
_KEY = rf"Software\Classes\*\shell\{VERB_NAME}"
_SEND_FLAG = "--send"


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def launch_command() -> str:
    """右键菜单实际执行的命令行（``%1`` 由 Windows 替换成选中文件）。"""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" {_SEND_FLAG} "%1"'
    root = _root()
    pythonw = root / ".venv" / "Scripts" / "pythonw.exe"
    exe = pythonw if pythonw.is_file() else Path(sys.executable)
    script = root / "app" / "main.py"
    return f'"{exe}" "{script}" {_SEND_FLAG} "%1"'


def _icon() -> str:
    if getattr(sys, "frozen", False):
        return sys.executable
    ico = _root() / "packaging" / "icon.ico"
    return str(ico) if ico.is_file() else sys.executable


def current_command() -> str:
    try:
        import winreg
    except ImportError:
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{_KEY}\command") as key:
            return winreg.QueryValueEx(key, "")[0]
    except OSError:
        return ""


def is_installed() -> bool:
    return bool(current_command())


def is_stale() -> bool:
    """注册过了，但命令指向旧路径（项目被挪位置 / 源码版换成 exe 版）。"""
    command = current_command()
    return bool(command) and command != launch_command()


def install() -> tuple[bool, str]:
    try:
        import winreg
    except ImportError:
        return False, "当前系统不支持注册表操作"
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _KEY, 0, winreg.KEY_WRITE) as key:
            winreg.SetValueEx(key, "MUIVerb", 0, winreg.REG_SZ, VERB_LABEL)
            winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, _icon())
            winreg.SetValueEx(key, "MultiSelectModel", 0, winreg.REG_SZ, "Document")
            with winreg.CreateKeyEx(key, "command", 0, winreg.KEY_WRITE) as cmd_key:
                winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, launch_command())
        log.info("已注册资源管理器右键菜单")
        return True, ""
    except OSError as exc:
        return False, str(exc)


def uninstall() -> tuple[bool, str]:
    """删掉整个动词分支。子键要递归删，winreg 没有递归删除。"""
    try:
        import winreg
    except ImportError:
        return False, "当前系统不支持注册表操作"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _KEY, 0,
                            winreg.KEY_ALL_ACCESS) as key:
            while True:                     # 先清子键
                try:
                    sub = winreg.EnumKey(key, 0)
                except OSError:
                    break
                winreg.DeleteKey(key, sub)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, _KEY)
        log.info("已移除资源管理器右键菜单")
        return True, ""
    except FileNotFoundError:
        return True, ""                     # 本来就没有，视作成功
    except OSError as exc:
        return False, str(exc)


def send_paths_from_argv(argv: list[str]) -> list[str]:
    """从命令行里取出 ``--send`` 后面的文件路径（可多个）。"""
    if _SEND_FLAG not in argv:
        return []
    index = argv.index(_SEND_FLAG)
    paths = []
    for raw in argv[index + 1:]:
        if raw.startswith("-"):
            break
        candidate = Path(raw)
        if candidate.exists():
            paths.append(str(candidate))
    return paths


def migrate_legacy() -> None:
    """旧名「手机电脑互通」的右键菜单键搬到新名「Ping」下（装过才搬）。"""
    try:
        import winreg
    except ImportError:
        return
    legacy_key = rf"Software\Classes\*\shell\{_LEGACY_VERB_NAME}"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{legacy_key}\command") as key:
            winreg.QueryValueEx(key, "")
    except OSError:
        return                      # 本来就没装过旧菜单
    was_installed = True
    # 删旧键（uninstall() 只删新名键，管不到旧名）
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, legacy_key, 0,
                            winreg.KEY_ALL_ACCESS) as key:
            while True:
                try:
                    sub = winreg.EnumKey(key, 0)
                except OSError:
                    break
                winreg.DeleteKey(key, sub)
        winreg.DeleteKey(winreg.HKEY_CURRENT_USER, legacy_key)
    except OSError as exc:
        log.warning("迁移旧右键菜单键失败：%s", exc)
        return
    if was_installed:
        install()
        log.info("右键菜单已从旧名迁移到「%s」", VERB_NAME)
