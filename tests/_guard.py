"""测试配置守卫：备份真实 config.json，跑完还原。

踩过两次：测试把 `panel_alpha` 写成 0.35、`dock_pos` 写成 [1550,111]，
污染了用户真实配置，每次都要手工修。这里统一兜住 —— 测试里调一次 backup()，
finally 里 restore()，无论中间怎么改都能回到原样。

backup() 自带 atexit 兜底：测试中途抛异常没走到 restore 时，进程退出前也会还原
（panel_pinned=True 就是这么泄漏进真实配置的 —— 脚本在 backup 之后崩了）。
"""
from __future__ import annotations

import atexit
import shutil

from config import CONFIG, CONFIG_PATH

_BACKUP = CONFIG_PATH.with_name(CONFIG_PATH.name + ".testbak")
_hooked = False


def backup() -> None:
    global _hooked
    if CONFIG_PATH.is_file():
        shutil.copy2(CONFIG_PATH, _BACKUP)
    if not _hooked:
        atexit.register(restore)   # 异常退出也能还原，幂等
        _hooked = True


def restore() -> None:
    if _BACKUP.is_file():
        shutil.copy2(_BACKUP, CONFIG_PATH)
        _BACKUP.unlink(missing_ok=True)
    CONFIG.load()
