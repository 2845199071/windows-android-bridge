"""Windows 系统缩略图（IShellItemImageFactory）。

为什么需要：视频首帧、Office 文档预览图这些，Qt 自己解析不了，但系统装了对应的
缩略图处理器 —— 直接借 Shell 的服务，拿到的就和资源管理器里看到的是同一张图。
（移植自「文件目录-桌面」项目里的同名模块，那边已稳定运行。）

纯 ctypes，无第三方依赖。**只能在主线程调用**：COM 单元已由 Qt 在主线程初始化，
在 Puller 之类的子线程里调会失败。
"""
from __future__ import annotations

import ctypes
import os
import uuid
from ctypes import POINTER, byref, c_int, c_long, c_void_p, c_wchar_p, wintypes

from PySide6.QtGui import QImage

# IShellItemImageFactory {BCC18B79-BA16-442F-80C4-8A59C30C463B}
_IID_ISHELLITEMIMAGEFACTORY = uuid.UUID("bcc18b79-ba16-442f-80c4-8a59c30c463b")

SIIGBF_BIGGERSIZEOK = 0x01
SIIGBF_THUMBNAILONLY = 0x08
SIIGBF_SCALEUP = 0x100


class _GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", ctypes.c_ulong),
        ("Data2", ctypes.c_ushort),
        ("Data3", ctypes.c_ushort),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class _SIZE(ctypes.Structure):
    _fields_ = [("cx", c_long), ("cy", c_long)]


def _make_guid(u: uuid.UUID) -> _GUID:
    g = _GUID()
    g.Data1 = u.time_low
    g.Data2 = u.time_mid
    g.Data3 = u.time_hi_version
    for i in range(8):
        g.Data4[i] = u.bytes[8 + i]
    return g


_IID = _make_guid(_IID_ISHELLITEMIMAGEFACTORY)

_shell32 = ctypes.windll.shell32 if os.name == "nt" else None
if _shell32 is not None:
    _shell32.SHCreateItemFromParsingName.restype = ctypes.c_long
    _shell32.SHCreateItemFromParsingName.argtypes = [
        c_wchar_p, c_void_p, POINTER(_GUID), POINTER(c_void_p)]
_gdi32 = ctypes.windll.gdi32 if os.name == "nt" else None

_com_ready = False


def _ensure_com() -> None:
    global _com_ready
    if not _com_ready:
        try:
            ctypes.windll.ole32.CoInitializeEx(None, 0x2)  # APARTMENTTHREADED
        except Exception:
            pass
        _com_ready = True


def thumbnail(path: str, size: int = 256) -> QImage | None:
    """取系统缩略图；没有对应处理器时返回 None（不会退化成图标）。

    只请求 THUMBNAILONLY：宁可没有，也不要拿个类型图标冒充画面 —— 卡片上真要
    没画面，我们自己的色块 + 扩展名反而更清楚。
    """
    if _shell32 is None:
        return None
    try:
        if not os.path.isfile(path):
            return None
        _ensure_com()
        item = c_void_p()
        hr = _shell32.SHCreateItemFromParsingName(
            os.path.normpath(path), None, byref(_IID), byref(item))
        if hr != 0 or not item.value:
            return None
        try:
            vtable = ctypes.cast(item, POINTER(POINTER(c_void_p))).contents
            get_image = ctypes.WINFUNCTYPE(
                c_long, c_void_p, _SIZE, c_int, POINTER(wintypes.HBITMAP))(vtable[3])
            hbm = wintypes.HBITMAP()
            hr2 = get_image(item, _SIZE(size, size),
                            SIIGBF_THUMBNAILONLY | SIIGBF_BIGGERSIZEOK, byref(hbm))
            if hr2 != 0 or not hbm.value:
                return None
            try:
                img = QImage.fromHBITMAP(hbm.value)
            finally:
                _gdi32.DeleteObject(hbm)
            return None if img.isNull() else img
        finally:
            try:
                release = ctypes.WINFUNCTYPE(ctypes.c_ulong, c_void_p)(vtable[2])
                release(item)
            except Exception:
                pass
    except Exception:
        return None
