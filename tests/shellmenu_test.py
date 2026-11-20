"""右键菜单「发送到手机」+ 单实例转交 + 发送队列 的测试。

只碰 HKCU，不写系统级注册表；测完会把动词分支删掉恢复原状。

    ./.venv/Scripts/python.exe tests/shellmenu_test.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from _guard import backup, restore  # noqa: E402

backup()
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

import shellmenu  # noqa: E402

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


class _FakeSignal:
    def connect(self, *_a, **_k):
        pass


class _FakeSender:
    """替掉真正的发送线程：只记状态，不碰手机。"""

    instances: list["_FakeSender"] = []

    def __init__(self, adb, paths, qapp) -> None:
        self.paths = list(paths)
        self.running = False
        self.finished_with = _FakeSignal()
        _FakeSender.instances.append(self)

    def isRunning(self) -> bool:      # noqa: N802 - 跟 QThread 保持一致
        return self.running

    def start(self) -> None:
        self.running = True


def test_shellmenu() -> None:
    was = shellmenu.is_installed()
    try:
        ok, err = shellmenu.install()
        check("注册右键菜单", ok, err)
        check("能读到已注册", shellmenu.is_installed())
        cmd = shellmenu.current_command()
        check("命令里带 --send 和 %1", "--send" in cmd and "%1" in cmd, cmd)
        check("命令指向本程序", "python" in cmd.lower() or "手机电脑互通" in cmd, cmd)
        check("图标路径已写", bool(shellmenu._icon()))

        check("重复安装不报错", shellmenu.install()[0])

        ok, err = shellmenu.uninstall()
        check("移除右键菜单", ok, err)
        check("移除后读不到", not shellmenu.is_installed())
        check("重复移除不报错", shellmenu.uninstall()[0])
    finally:
        if was:
            shellmenu.install()


def test_argv_parse(tmp: Path) -> None:
    a = tmp / "甲.txt"
    b = tmp / "乙 带空格.txt"
    a.write_text("x", encoding="utf-8")
    b.write_text("y", encoding="utf-8")
    got = shellmenu.send_paths_from_argv(["app.py", "--send", str(a), str(b)])
    check("解析多个文件", got == [str(a), str(b)], str(got))
    check("不存在的路径被丢掉",
          shellmenu.send_paths_from_argv(["app.py", "--send", str(tmp / "没有.txt")]) == [])
    check("没有 --send 时返回空", shellmenu.send_paths_from_argv(["app.py"]) == [])


def test_send_queue(tmp: Path) -> None:
    """多选 5 个文件时 Windows 会连发 5 条，不能丢。"""
    import main as app_main

    files = []
    for i in range(5):
        f = tmp / f"队{i}.txt"
        f.write_text(str(i), encoding="utf-8")
        files.append(str(f))

    qapp = QApplication.instance() or QApplication(sys.argv)
    real_sender = app_main.Sender
    app_main.Sender = _FakeSender
    try:
        from store import Store

        app = app_main.App(qapp, store=Store(tmp / "q.db"), adb=None)
        _FakeSender.instances.clear()

        app._send_files(files[:1])
        check("第一批立即发送", len(_FakeSender.instances) == 1, f"{len(_FakeSender.instances)} 个")

        for f in files[1:]:
            app._send_files([f])
        check("发送中不会重复起线程", len(_FakeSender.instances) == 1)
        check("其余的文件都排队了", len(app._send_queue) == 4, f"{len(app._send_queue)} 个")

        # 模拟上一批发完
        app._sender.running = False
        app._on_send_done(True, "ok")
        check("上一批发完自动接下一批", len(_FakeSender.instances) == 2)
        check("第二批拿到 4 个", len(_FakeSender.instances[1].paths) == 4,
              str(len(_FakeSender.instances[1].paths)))
        check("队列已清空", not app._send_queue)

        # 不存在的一次都不排
        app._sender.running = False
        app._on_send_done(True, "ok")
        app._send_files([str(tmp / "不存在.txt")])
        check("不存在的文件被忽略", not app._send_queue and len(_FakeSender.instances) == 2)
        app.store.close()
    finally:
        app_main.Sender = real_sender


def test_ipc_forward(tmp: Path) -> None:
    """另起一个进程当"第二个实例"，把消息投给本进程的服务端。"""
    import ipc

    qapp = QApplication.instance() or QApplication(sys.argv)
    received: list[dict] = []
    server = ipc.IpcServer()
    server.message.connect(received.append)

    payload = {"cmd": "send", "paths": [str(tmp / "甲.txt")]}
    code = (
        "import sys, json, os;"
        "sys.path.insert(0, r'%s');"
        "import ipc;"
        "ok = ipc.forward_quietly(json.loads(r'''%s'''));"
        "print('FORWARDED' if ok else 'NOBODY');"
    ) % (Path(__file__).resolve().parent.parent / "app", __import__("json").dumps(payload, ensure_ascii=False))

    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    out = (proc.stdout + proc.stderr).strip()
    deadline = time.monotonic() + 5
    while not received and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(0.05)

    check("第二个实例能连上服务端", "FORWARDED" in out, out[-120:])
    check("消息内容完整到达",
          bool(received) and received[0].get("paths") == payload["paths"],
          str(received[:1]))

    # 服务端关掉后，转发应当安静地失败（说明没有实例在跑）
    server.close()
    proc2 = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60)
    check("没有实例时转发返回失败", "NOBODY" in (proc2.stdout + proc2.stderr),
          (proc2.stdout + proc2.stderr).strip()[-80:])


def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_shell_"))
    try:
        test_shellmenu()
        test_argv_parse(tmp)
        test_send_queue(tmp)
        test_ipc_forward(tmp)
    finally:
        restore()
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

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
