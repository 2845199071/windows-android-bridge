"""v1.0.4 回归：进度条重试重建 + 常亮提醒 + 掉线自愈状态机。

    ./.venv/Scripts/python.exe tests/selfheal_progress_test.py

背景：
* 用户反馈"下载时进度条一直是 0"——根因是 _tick_progress 只在 card._pulling
  已为 True 时刷新，而失败重试的卡片被移除后没重建、定时器也停了。
* adb server 的 mDNS 后端反复失活（设备全 offline），需要自愈重启。
"""
from __future__ import annotations

import os
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

from config import CONFIG  # noqa: E402
from main import App  # noqa: E402
from store import STATUS_PULLING, PendingItem, Store  # noqa: E402

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


def test_progress_rebuild(qapp):
    """pulling 的 item 即使卡片不存在，_tick_progress 也要补建并刷新进度。"""
    tmp = Path(tempfile.mkdtemp(prefix="s2pc_prog4_"))
    original = CONFIG.get("archive_root")
    CONFIG.set("archive_root", str(tmp / "归档"))
    try:
        store = Store(tmp / "data.db")
        app = App(qapp, store=store, adb=None)
        staging = tmp / "big.apk"
        staging.write_bytes(b"x" * 5000)
        item = PendingItem(
            id="pg1", remote_path="/sdcard/Download/big.apk", filename="big.apk",
            staging=str(staging), mtime=int(time.time()), size=10000,
            status=STATUS_PULLING, created_at=int(time.time()),
        )
        store.add_pending(item)
        # 关键前置：故意不 add_item，模拟"失败移除卡片后重试"的状态
        check("前置：卡片确实不存在", "pg1" not in app.dock.item_ids())

        app._tick_progress()
        qapp.processEvents()
        card = app.dock._cards.get("pg1")
        check("tick 补建了下载中的卡片", card is not None)
        if card:
            check("卡片处于 pulling 态", card._pulling)
            check("进度按暂存大小算出 50%", "下载中 50%" in card.meta_label.text(),
                  card.meta_label.text())
            check("按钮已锁", not card.save_btn.isEnabled())

        # 常亮提醒：首次下载弹过（_last_awake_hint 被设置）
        check("下载时触发了常亮提醒计时", app._last_awake_hint > 0)
        first_hint = app._last_awake_hint
        app._maybe_awake_hint()
        check("冷却期内不重复提醒", app._last_awake_hint == first_hint)

        app.store.close()
    finally:
        CONFIG.set("archive_root", original)


class _FakeAdb:
    def __init__(self):
        self.restart_calls = 0

    def restart_server(self):
        self.restart_calls += 1
        return True


def test_selfheal_state_machine():
    """掉线自愈：必须曾连过 + 持续掉线超阈值 + 限频 + 开关可关。"""
    from watcher import Watcher
    from store import Store

    tmp = Path(tempfile.mkdtemp(prefix="s2pc_heal_"))
    original = CONFIG.get("adb_selfheal")
    try:
        store = Store(tmp / "data.db")
        fake = _FakeAdb()
        w = Watcher(store, fake)          # 不 start()，直接调状态机方法
        now = 1000.0

        # 没连过就一定不自愈
        w._update_heal_state(now)         # device=None
        w._maybe_self_heal(now)
        check("从未连过不自愈", fake.restart_calls == 0)

        # 曾连过
        from adb import Device
        w._device = Device("fakemdns._adb-tls-connect._tcp", "device", transport="wifi")
        w._update_heal_state(now)
        check("在线时记录 ever_connected", w._ever_connected and w._lost_since == 0)
        check("在线不自愈", (w._maybe_self_heal(now) or fake.restart_calls == 0))

        # 掉线，但没到阈值
        w._device = None
        w._update_heal_state(now + 5)
        w._maybe_self_heal(now + 5)
        check("掉线未达阈值不自愈", fake.restart_calls == 0)

        # 掉线超阈值 → 自愈
        w._maybe_self_heal(now + 40)
        check("持续掉线触发自愈", fake.restart_calls == 1)

        # 限频：窗口内第 2 次仍可，第 3 次被拦
        w._lost_since = now + 40
        w._maybe_self_heal(now + 80)
        check("窗口内第 2 次允许", fake.restart_calls == 2)
        w._lost_since = now + 80
        w._maybe_self_heal(now + 120)
        check("窗口内第 3 次被限频", fake.restart_calls == 2)

        # 开关关闭
        CONFIG.set("adb_selfheal", False)
        w2 = Watcher(store, fake)
        w2._ever_connected = True
        w2._lost_since = 1.0
        w2._maybe_self_heal(100.0)
        check("开关关闭时不自愈", fake.restart_calls == 2)

        store.close()
    finally:
        CONFIG.set("adb_selfheal", original)


def main() -> int:
    qapp = QApplication(sys.argv)
    test_progress_rebuild(qapp)
    test_selfheal_state_machine()
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
