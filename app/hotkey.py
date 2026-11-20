"""全局热键（Ctrl+Alt+M 切换面板显示）。

用 Win32 RegisterHotKey：按下组合键时系统向线程消息队列投递 WM_HOTKEY，
Qt 的事件循环会分发这条消息，`nativeEventFilter` 在这里截获并回调。

注意两点：
* hwnd 传 None → 消息发给线程队列而不是某个窗口，面板隐藏/托盘态也能收到；
* 必须在 QApplication 事件循环启动前注册，退出时 UnregisterHotKey 释放。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes
import logging

from PySide6.QtCore import QAbstractNativeEventFilter

log = logging.getLogger("s2pc.hotkey")

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

VK_J = 0x4A  # 'J'
VK_K = 0x4B  # 'K'
VK_M = 0x4D  # 'M'


class GlobalHotKeys(QAbstractNativeEventFilter):
    """注册一组全局热键，触发时回调（在主线程执行）。"""

    def __init__(self) -> None:
        super().__init__()
        self._bindings: dict[int, callable] = {}
        self._next_id = 1
        self._registered: list[tuple[int, int, int]] = []

    def add(self, modifiers: int, vk: int, callback) -> bool:
        """注册一个组合键；成功返回 True（失败多见于被其他程序占用）。"""
        hotkey_id = self._next_id
        self._next_id += 1
        if not self._register(hotkey_id, modifiers, vk):
            return False
        self._bindings[hotkey_id] = callback
        self._registered.append((hotkey_id, modifiers, vk))
        return True

    def add_first_available(self, candidates: list[tuple[int, int, str]], callback) -> str:
        """按顺序尝试注册，返回第一个成功的组合描述；全失败返回空串。

        Ctrl+Alt+M 在不少机器上被别的软件占着（本机实测就是），硬编码一个组合
        等于看运气。自动顺延并在日志里写清楚实际生效的是哪个。
        """
        for modifiers, vk, label in candidates:
            if self.add(modifiers, vk, callback):
                log.info("全局热键：%s", label)
                return label
        return ""

    @staticmethod
    def _register(hotkey_id: int, modifiers: int, vk: int) -> bool:
        # 必须 use_errno 的实例才能拿到真实错误码（ERROR_HOTKEY_ALREADY_REGISTERED=1409）
        return bool(ctypes.windll.user32.RegisterHotKey(None, hotkey_id, modifiers, vk))

    def describe(self) -> str:
        """当前已注册的组合键描述（按注册顺序）。"""
        names = {MOD_CONTROL: "Ctrl", MOD_ALT: "Alt", MOD_SHIFT: "Shift"}
        labels = []
        for _hotkey_id, modifiers, vk in self._registered:
            parts = [name for flag, name in names.items() if modifiers & flag]
            parts.append(chr(vk) if 0x41 <= vk <= 0x5A else hex(vk))
            labels.append("+".join(parts))
        return " / ".join(labels)

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if event_type == b"windows_generic_MSG":
            msg = ctypes.wintypes.MSG.from_address(int(message))
            if msg.message == WM_HOTKEY:
                callback = self._bindings.get(int(msg.wParam))
                if callback is not None:
                    try:
                        callback()
                    except Exception:  # 热键回调出错不能拖垮事件循环
                        log.exception("热键回调异常")
                    return True, 0
        return False, 0

    def release(self) -> None:
        for hotkey_id, modifiers, vk in self._registered:
            if not ctypes.windll.user32.UnregisterHotKey(None, hotkey_id):
                log.debug("热键注销失败 id=%d", hotkey_id)
        self._registered.clear()
        self._bindings.clear()
