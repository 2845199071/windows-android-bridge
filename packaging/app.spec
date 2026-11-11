# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

单目录模式（onedir）：启动比 onefile 快很多，也不会每次解压到临时目录。
不带控制台（对应 pythonw），双击后只有托盘图标。
"""
from PyInstaller.utils.hooks import collect_submodules

hidden = [
    "PySide6.QtCore",
    "PySide6.QtGui",
    "PySide6.QtWidgets",
]
# pypdfium2 是可选依赖（只在预览 PDF 缩略图时用），漏了不影响主流程，
# 但还是显式带上，避免打包版悄悄少了这个能力。
hidden += collect_submodules("pypdfium2")

a = Analysis(
    ["../app/main.py"],
    pathex=["../app"],
    binaries=[],
    # platform-tools（adb.exe + 两个 dll）：朋友的电脑上没装 Android SDK，
    # 打包版必须自带。find_adb() 会按 exe_dir/_internal/platform-tools 找到它们。
    datas=[
        ("platform-tools/adb.exe", "platform-tools"),
        ("platform-tools/AdbWinApi.dll", "platform-tools"),
        ("platform-tools/AdbWinUsbApi.dll", "platform-tools"),
    ],
    hiddenimports=hidden,
    hookspath=[],
    runtime_hooks=[],
    # 这些库一个都用不上，排掉能少几十 MB
    excludes=[
        "tkinter",
        "numpy",
        "matplotlib",
        "PIL",
        "scipy",
        "pandas",
        "PySide6.QtNetwork",
        "PySide6.QtQml",
        "PySide6.QtQuick",
        "PySide6.QtWebEngineCore",
        "PySide6.QtOpenGL",
        "PySide6.QtTest",
        "PySide6.QtSql",
        "PySide6.QtPrintSupport",
    ],
    noarchive=False,
)

pyz = PYZ(a.pure)

# 体积优化：这两样占了近 30 MB，对纯 QWidget 程序完全没用
#   opengl32sw.dll —— 20MB，软件 OpenGL 兜底渲染，QWidget 用不到
#   translations   —— 7MB，Qt 自带的多语言翻译，界面全是自己写的中文
a.binaries = [b for b in a.binaries if "opengl32sw" not in b[0].lower()]
a.datas = [d for d in a.datas if "translations" not in d[0].lower()]

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Ping",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,          # 不显示控制台窗口
    icon="icon.ico",
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="Ping",
)
