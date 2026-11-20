"""设置窗口渲染探针：截图设置对话框和手机目录浏览器。"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from adb import AdbChannel  # noqa: E402
from settings_dialog import PhoneDirBrowser, SettingsDialog  # noqa: E402

app = QApplication(sys.argv)
adb = AdbChannel()
device = adb.ensure_device()
print("设备:", device.label if device else None)

dlg = SettingsDialog(adb)
dlg.show()
for _ in range(3):
    app.processEvents()
dlg.repaint()
app.processEvents()
print(f"settings {dlg.width()}x{dlg.height()}")
print("save:", dlg.grab().save(str(Path(tempfile.gettempdir()) / "s2pc_settings.png")))

browser = PhoneDirBrowser(adb, "/sdcard")
browser.show()
for _ in range(3):
    app.processEvents()

# 选中一个子目录，看按钮文字和提示是否跟着变
for row in range(browser.list_widget.count()):
    if browser.list_widget.item(row).data(Qt.UserRole) == "/sdcard/Documents":
        browser.list_widget.setCurrentRow(row)
        break
for _ in range(3):
    app.processEvents()
browser.repaint()
app.processEvents()
print(f"browser {browser.width()}x{browser.height()} 行数={browser.list_widget.count()}")
print(f"按钮文字: {browser.choose_btn.text()}")
print(f"提示: {browser.hint.text()}")
print("save:", browser.grab().save(str(Path(tempfile.gettempdir()) / "s2pc_browser.png")))
