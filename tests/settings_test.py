"""设置窗口校验逻辑测试：非法路径拦截 + 合法保存 + 目录浏览器导航。

运行：``.venv\\Scripts\\python.exe tests\\settings_test.py``
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QDialog  # noqa: E402

from _guard import backup as guard_backup, restore as guard_restore  # noqa: E402
from adb import AdbChannel  # noqa: E402
from archiver import send_target_dir  # noqa: E402
from config import CONFIG  # noqa: E402
from settings_dialog import PhoneDirBrowser, SettingsDialog  # noqa: E402

failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        failures.append(name)


def main() -> int:
    qapp = QApplication(sys.argv)
    guard_backup()
    adb = AdbChannel()

    # --- 配对参数校验（不依赖设备，先跑，也不会真的去连）
    ok, msg = adb.pair("", "123456")
    check("拒绝空配对地址", not ok and "地址" in msg, msg)
    ok, msg = adb.pair("192.168.1.5", "123456")
    check("拒绝没有端口的地址", not ok, msg)
    ok, msg = adb.pair("192.168.1.5:40061", "12345")
    check("拒绝 5 位配对码", not ok and "6 位" in msg, msg)
    ok, msg = adb.pair("192.168.1.5:40061", "abc123")
    check("拒绝非数字配对码", not ok, msg)
    ok, msg = adb.pair("192.168.1.5:40061", "1234567")
    check("拒绝 7 位配对码", not ok, msg)

    if adb.ensure_device() is None:
        print()
        if failures:
            print(f"失败 {len(failures)} 项：{', '.join(failures)}")
            return 1
        print("设备没连，跟设备相关的用例已跳过；配对参数校验全部通过")
        return 0

    original_send = CONFIG.get("remote_send_dir")
    original_fast = CONFIG.get("fast_interval")
    original_slow = CONFIG.get("slow_interval")

    dlg = SettingsDialog(adb)
    check("打开时带出当前配置",
          dlg.send_edit.text() == original_send,
          f"{dlg.send_edit.text()} vs {original_send}")

    # --- 实时应用后非法输入要被拒绝并回退（路径失焦触发 _apply_send_dir）
    def apply_send():
        dlg._apply_send_dir()

    dlg.send_edit.setText("")
    dlg._apply_send_dir()
    check("拒绝空目录", CONFIG.get("remote_send_dir") == original_send)
    check("空目录有提示", bool(dlg._error.text()), dlg._error.text())

    dlg.send_edit.setText("Download/foo")
    dlg._apply_send_dir()
    check("拒绝非绝对路径", CONFIG.get("remote_send_dir") == original_send, dlg._error.text())

    dlg.send_edit.setText("/sdcard")
    dlg._apply_send_dir()
    check("拒绝手机根目录", CONFIG.get("remote_send_dir") == original_send, dlg._error.text())

    # --- 间隔：fast > slow 被自动纠正（不再报错阻断）
    dlg._select_value(dlg.fast_combo, 60)
    dlg._select_value(dlg.slow_combo, 10)
    check("间隔倒挂自动纠正",
          int(dlg.fast_combo.currentData()) <= int(dlg.slow_combo.currentData()),
          f"{dlg.fast_combo.currentData()}/{dlg.slow_combo.currentData()}")
    check("纠正后的间隔已实时生效",
          CONFIG.get("fast_interval") == int(dlg.fast_combo.currentData())
          and CONFIG.get("slow_interval") == int(dlg.slow_combo.currentData()))

    # --- 归档根目录：盘符根拒绝
    dlg.archive_edit.setText("D:////")
    dlg._apply_archive_root()
    check("拒绝盘符根目录", bool(dlg._error.text()), dlg._error.text())

    # --- 合法配置：实时落库
    target = "/sdcard/Download/电脑发送改测试"
    dlg.send_edit.setText(target)
    dlg._apply_send_dir()
    dlg._select_value(dlg.fast_combo, 4)
    dlg._select_value(dlg.slow_combo, 20)
    check("合法配置实时生效",
          CONFIG.get("remote_send_dir") == target and CONFIG.get("fast_interval") == 4,
          f"{CONFIG.get('remote_send_dir')} / {CONFIG.get('fast_interval')}")
    values = dlg.values()
    check("values() 一致", values["remote_send_dir"] == target and values["fast_interval"] == 4,
          str(values))

    # 真发一个文件到新目录
    import tempfile

    sample = Path(tempfile.gettempdir()) / "设置测试.txt"
    sample.write_text("ok", encoding="utf-8")
    ok = adb.push(sample, target)
    check("能发到新目录", ok)
    listing = adb.shell(f"ls -1 '{target}' 2>/dev/null").strip()
    check("新目录里有文件", "设置测试.txt" in listing, listing)
    adb.shell(f"rm -rf '{target}'")

    # --- 目录浏览器导航
    browser = PhoneDirBrowser(adb, "/sdcard")
    check("浏览器列出子目录", browser.list_widget.count() > 5, f"{browser.list_widget.count()} 项")
    if browser.list_widget.count():
        first = browser.list_widget.item(0)
        name = first.text()
        browser.list_widget.setCurrentRow(0)
        browser._on_double_click(first)
        check("双击能进入子目录", browser._current != "/sdcard", f"{name} → {browser._current}")
        browser._go_up()
        check("能返回上一级", browser._current == "/sdcard", browser._current)

    # 关键回归：单击选中子目录 → 点选择，必须拿到那一个，而不是当前所在目录
    target_name = None
    for row in range(browser.list_widget.count()):
        item = browser.list_widget.item(row)
        if item.data(Qt.UserRole) == "/sdcard/A常用":
            target_name = item.text()
            browser.list_widget.setCurrentRow(row)
            check("选中后按钮文字跟着变", "A常用" in browser.choose_btn.text(), browser.choose_btn.text())
            browser._choose()
            break
    if target_name:
        check("选中的是 A常用 而不是 /sdcard",
              browser.picked == "/sdcard/A常用", str(browser.picked))
    else:
        check("手机上存在 /sdcard/A常用", False, "没找到，无法验证")

    # 没选中任何项时，选的是当前所在目录
    browser2 = PhoneDirBrowser(adb, "/sdcard/Download")
    browser2.list_widget.setCurrentItem(None)
    browser2.list_widget.clearSelection()
    browser2._choose()
    check("无选中时用当前目录", browser2.picked == "/sdcard/Download", str(browser2.picked))

    # 恢复（走备份还原，连测试中途漏改的项也一起还原）
    guard_restore()
    check("配置已还原（走备份）", send_target_dir() == original_send, send_target_dir())

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
