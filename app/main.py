"""入口：把 adb 通道、监听、拉取、悬浮窗串起来。

运行：``.venv\\Scripts\\python.exe app\\main.py``
"""
from __future__ import annotations

import logging
import os
import sys
import time
import uuid
from pathlib import Path

from PySide6.QtCore import QLockFile, QRunnable, Qt, QThread, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QAction, QColor, QDesktopServices, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from adb import AdbChannel, AdbError
from archiver import archive_file, send_target_dir
from config import APP_NAME, CONFIG, DATA_DIR, DEFAULTS, LOG_PATH, STAGING_DIR, staging_dir
import autostart
import ipc
import shellmenu
from dock import SideDock
from hotkey import (
    GlobalHotKeys,
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    MOD_SHIFT,
    VK_J,
    VK_K,
    VK_M,
)
from puller import Puller
from settings_dialog import SettingsDialog
from store import (
    STATUS_FAILED,
    STATUS_IGNORED,
    STATUS_PULLED,
    STATUS_SAVED,
    STATUS_WAITING,
    PendingItem,
    Store,
)
from watcher import Watcher

log = logging.getLogger("s2pc")

_green = "#34C77B"
_amber = "#FF9F43"
_red = "#E5484D"


class _FileDeleter(QRunnable):
    """后台删暂存文件。实测在慢盘上单次 unlink 要 200ms，不能放 UI 线程。"""

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path

    def run(self) -> None:  # noqa: D102
        try:
            Path(self._path).unlink(missing_ok=True)
        except OSError:
            pass


class Sender(QThread):
    """电脑 → 手机，串行 push，避免多路 adb 抢通道。"""

    finished_with = Signal(bool, str)

    def __init__(self, adb: AdbChannel, paths: list[str], parent=None) -> None:
        super().__init__(parent)
        self._adb = adb
        self._paths = paths

    def run(self) -> None:  # noqa: D102
        remote = send_target_dir()
        ok = 0
        last_error = ""
        for raw in self._paths:
            path = Path(raw)
            if not path.is_file():
                continue
            try:
                if self._adb.push(path, remote):
                    ok += 1
                else:
                    last_error = f"{path.name} 推送失败"
            except Exception as exc:
                last_error = str(exc)
        total = len(self._paths)
        if ok > 0:
            # 手机端提示（系统通知，无需装 app）：写清楚存到了哪里、收到了什么
            names = "、".join(Path(p_).name for p_ in self._paths[:3])
            suffix = f" 等 {ok} 个" if ok > 3 else ""
            self._adb.notify_phone(
                "Ping 电脑发送",
                f"已收到 {ok} 个文件，已存到 {remote}\n{names}{suffix}",
            )
        if ok == total and ok > 0:
            self.finished_with.emit(True, f"{ok} 个文件已发送到手机 {remote}")
        elif ok > 0:
            self.finished_with.emit(True, f"{ok}/{total} 发送成功（{last_error}）")
        else:
            self.finished_with.emit(False, last_error or "发送失败")


class _ModeSwitch(QThread):
    """切换自动/手动要重启 adb server（环境变量只在 server 启动时生效）。

    kill-server + start-server 实测约 2.3 秒——放界面线程就是硬卡 2 秒，必须后台跑。
    """

    done = Signal(bool, str)

    def __init__(self, adb, auto: bool, parent=None) -> None:
        super().__init__(parent)
        self._adb = adb
        self._auto = auto

    def run(self) -> None:  # noqa: D102
        try:
            self._adb.set_mdns_auto_connect(self._auto, restart=True)
            self.done.emit(True, "")
        except Exception as exc:      # noqa: BLE001
            self.done.emit(False, str(exc))


class _ManualConnect(QThread):
    """手动连接模式的一次性连接尝试（点「连接」按钮触发）。"""

    done = Signal(object)   # Device | None

    def __init__(self, adb: AdbChannel) -> None:
        super().__init__()
        self._adb = adb

    def run(self) -> None:  # noqa: D102
        # 带连接权限的 ensure_device：存过地址/mDNS 都会试一遍
        try:
            device = self._adb.ensure_device()
        except AdbError:
            device = None
        self.done.emit(device)


def _tray_icon(color: str = "#2563EB") -> QIcon:
    """托盘图标：底色随连接状态变（蓝=待连接 / 绿=USB / 橙=无线 / 红=离线）。"""
    pixmap = QPixmap(32, 32)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor(color))
    painter.drawRoundedRect(1, 1, 30, 30, 9, 9)
    painter.setPen(QColor("#FFFFFF"))
    font = QFont("Segoe UI", 13)
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, "⇄")
    painter.end()
    return QIcon(pixmap)


class App:
    def __init__(self, qapp: QApplication, store: Store | None = None, adb: AdbChannel | None = None) -> None:
        self.qapp = qapp
        self.store = store if store is not None else Store()
        self.adb = adb if adb is not None else AdbChannel()
        self.dock = SideDock()
        self.puller = Puller(self.store, self.adb)
        self.watcher = Watcher(self.store, self.adb)
        self._inflight: set[str] = set()
        self._sender: Sender | None = None
        self._send_queue: list[str] = []
        self._pull_retries: dict[str, int] = {}   # item_id → 已重试次数
        self._backlog_gap_hours: float | None = None   # 长期离线降级时的离线时长
        self._was_connected = False
        self._tray: QSystemTrayIcon | None = None
        self._saved_count = 0
        self._saved_last = ""
        self._saved_timer: QTimer | None = None
        # 专用线程池：删文件很吃 IO，并发太高反而互相拖慢
        self._deleter_pool = QThreadPool()
        self._deleter_pool.setMaxThreadCount(2)
        self._connect_thread: _ManualConnect | None = None
        self.dock.set_manual_mode(CONFIG.get("connect_mode", "auto") == "manual")
        self._build_tray()
        self._wire()

    # ------------------------------------------------------------------ 装配
    def _wire(self) -> None:
        self.watcher.new_files.connect(self._on_new_files)
        self.watcher.device_changed.connect(self._on_device_changed)
        self.watcher.failed.connect(self._on_watcher_error)
        self.watcher.backlog_skipped.connect(self._on_backlog_skipped)

        self.puller.item_pulled.connect(self._on_pulled)
        self.puller.item_failed.connect(self._on_pull_failed)

        self.dock.save_requested.connect(self._save_item)
        self.dock.ignore_requested.connect(self._ignore_item)
        self.dock.preview_requested.connect(self._preview_item)
        self.dock.set_drag_source(self._save_for_drag)
        self.dock.drag_out_requested.connect(self._on_card_dragged)
        self.dock.save_all_requested.connect(self._save_all)
        self.dock.ignore_all_requested.connect(self._ignore_all)
        self.dock.send_requested.connect(self._send_files)
        self.dock.connect_requested.connect(self._connect_manual)
        self.dock.settings_requested.connect(self.open_settings)

    def _build_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.warning("系统托盘不可用")
            return
        self._tray = QSystemTrayIcon(_tray_icon(), self.qapp)
        self._tray.setToolTip(f"{APP_NAME} · 等待连接")
        menu = QMenu()

        show_action = QAction("显示面板", menu)
        show_action.triggered.connect(self._toggle_dock)
        menu.addAction(show_action)

        settings_action = QAction("设置…", menu)
        settings_action.triggered.connect(self.open_settings)
        menu.addAction(settings_action)

        archive_action = QAction("打开归档目录", menu)
        archive_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(CONFIG.archive_root)))
        )
        menu.addAction(archive_action)

        staging_action = QAction("打开暂存目录", menu)
        staging_action.triggered.connect(
            lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(staging_dir())))
        )
        menu.addAction(staging_action)

        menu.addSeparator()
        quit_action = QAction("退出", menu)
        quit_action.triggered.connect(self.shutdown)
        menu.addAction(quit_action)

        self._tray.setContextMenu(menu)
        self._tray.activated.connect(self._on_tray_activated)
        self._tray.show()

    # ------------------------------------------------------------------ 事件
    def _toggle_dock(self) -> None:
        if self.dock.isVisible() and not self.dock.is_collapsed():
            self.dock.set_collapsed(True)
        else:
            self.dock.set_collapsed(False)
            self.dock.show()
            self.dock.raise_()

    def _on_tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self._toggle_dock()

    def _on_device_changed(self, device) -> None:
        online = device is not None
        was_online = self._was_connected
        self._was_connected = online
        if device is None:
            self.dock.set_device_status("未连接", _red)
            if self._tray:
                self._tray.setIcon(_tray_icon("#EF4444"))
                self._tray.setToolTip(f"{APP_NAME} · 未连接")
            return
        wired = device.transport == "usb"
        label = "USB 已连接" if wired else "无线已连接"
        self.dock.set_device_status(label, _green if wired else _amber)
        if self._tray:
            self._tray.setIcon(_tray_icon("#22C55E" if wired else "#F59E0B"))
            self._tray.setToolTip(f"{APP_NAME} · {device.label} · {label}")
        log.info("设备就绪：%s (%s)", device.serial, device.transport)
        # 断线期间拉取失败的文件：设备一回来就自动重试（大文件拉一半断连的补救）
        if online and not was_online:
            self._retry_failed_pulls()

    def _on_watcher_error(self, message: str) -> None:
        log.warning("监听：%s", message)

    # ------------------------------------------------------------------ 连接方式切换
    def _switch_connect_mode(self, auto: bool) -> None:
        """后台重启 adb server 应用新环境；界面立刻给出「切换中」反馈。

        实测 kill-server + start-server ≈ 2.3 秒，放界面线程就是硬卡两秒。
        """
        if getattr(self, "_mode_thread", None) is not None and self._mode_thread.isRunning():
            return                        # 上一次还在切，别叠着来
        self._mode_auto = auto
        self.dock.set_device_status("切换中", _amber)
        # 坑：App 不是 QObject，不能当 QThread 的 parent（QTimer(self) 踩过同一个坑）
        self._mode_thread = _ModeSwitch(self.adb, auto)
        self._mode_thread.done.connect(self._on_mode_switched)
        self._mode_thread.start()

    def _on_mode_switched(self, ok: bool, error: str) -> None:
        if not ok:
            self._notify("切换连接方式失败", error, critical=True)
            return
        # server 刚重启，让监听线程立刻复查一次设备（否则最多等 10 秒才刷新状态）
        self.watcher.poke_device_check()
        log.info("adb server 已按新连接方式重启")
        # 切换期间用户又改过模式的话补一次，避免环境与实际选择不一致
        current_auto = CONFIG.get("connect_mode", "auto") == "auto"
        if current_auto != getattr(self, "_mode_auto", current_auto) \
                and getattr(self, "_mode_reconciles", 0) < 1:
            self._mode_reconciles = getattr(self, "_mode_reconciles", 0) + 1
            log.info("切换期间模式又变了，补一次切换")
            self._switch_connect_mode(current_auto)

    # ------------------------------------------------------------------ 手动连接
    def _connect_manual(self) -> None:
        """手动连接模式：点「连接」才发起一次连接尝试（后台线程，不卡 UI）。"""
        if self._connect_thread is not None and self._connect_thread.isRunning():
            return
        self.dock.set_connecting(True)
        self._connect_thread = _ManualConnect(self.adb)
        self._connect_thread.done.connect(self._on_manual_connect_done)
        self._connect_thread.start()
        log.info("手动连接：开始尝试")

    def _on_manual_connect_done(self, device) -> None:
        self.dock.set_connecting(False)
        if device is not None:
            # 立刻刷新状态与托盘，不等监听线程的下一轮被动检查
            self._on_device_changed(device)
            log.info("手动连接成功：%s", device.serial)
        else:
            # 保持「未连接」与按钮可见，等用户再点或换网络后重试
            self._on_device_changed(None)
            log.info("手动连接失败：未发现可用设备")

    def _on_new_files(self, items: list) -> None:
        for remote_path, mtime, size in items:
            if remote_path in self._inflight:
                continue
            self._inflight.add(remote_path)
            item = PendingItem(
                id=uuid.uuid4().hex,
                remote_path=remote_path,
                filename=Path(remote_path).name,
                staging="",
                mtime=mtime,
                size=size,
                status=STATUS_WAITING,
                created_at=int(time.time()),
            )
            self.store.add_pending(item)
            self.puller.enqueue(item.id)
        log.info("发现 %d 个新文件", len(items))

    def _on_pulled(self, item_id: str) -> None:
        item = self.store.get_pending(item_id)
        if item is None:
            return
        self._pull_retries.pop(item_id, None)   # 成功了，重试计数清零
        self.dock.add_item(item)
        if not self.dock.isVisible():
            self.dock.show()
        if self._tray:
            self._tray.setToolTip(f"{APP_NAME} · 有 {self.dock.card_count()} 个待处理")

    _PULL_RETRY_LIMIT = 3   # 同一个文件最多自动重试次数，超过就放弃并通知

    def _on_pull_failed(self, item_id: str, error: str) -> None:
        """拉取失败：半截文件已被拉取线程删掉，这里给反馈 + 等设备恢复自动重试。

        不立刻 discard inflight——重试期间避免同一文件被重复入队；
        重试次数用尽才放弃（那时才从 inflight 摘除并明确告知用户）。
        """
        self._pull_retries[item_id] = self._pull_retries.get(item_id, 0) + 1
        item = self.store.get_pending(item_id)
        name = item.filename if item else item_id
        log.warning("拉取失败 %s（第 %d 次）：%s", item_id,
                    self._pull_retries[item_id], error)
        self._notify("拉取失败",
                     f"{name} · {error}（设备恢复后自动重试，最多 {self._PULL_RETRY_LIMIT} 次）")

    def _retry_failed_pulls(self) -> None:
        """设备重新上线：把拉取失败的文件重新入队（限次）。"""
        for item in self.store.list_pending(("failed",)):
            tries = self._pull_retries.get(item.id, 0)
            if tries >= self._PULL_RETRY_LIMIT:
                self._notify("拉取多次失败，已放弃",
                             f"{item.filename}（文件仍在手机上，可手动处理）")
                self._inflight.discard(item.remote_path)
                self._pull_retries.pop(item.id, None)
                continue
            self.store.update_pending(item.id, status=STATUS_WAITING)
            self._inflight.add(item.remote_path)
            self.puller.enqueue(item.id)
            log.info("设备恢复，重试拉取：%s（第 %d 次）", item.filename, tries + 1)

    # ------------------------------------------------------------------ 动作
    def _archive_item(self, item_id: str) -> str | None:
        """把暂存文件移进归档目录并写库，返回最终路径；失败返回 None。

        保存按钮和「拖出卡片」共用这一段——拖出去必须给目标程序一个长期有效的路径，
        不能给 .staging 里的临时文件（忽略/超时会把它删掉，对方就打不开了）。
        """
        item = self.store.get_pending(item_id)
        if item is None:
            return None
        if not item.staging or not Path(item.staging).is_file():
            # 普通气泡而不是 critical：这不是致命错误，弹模态框会打断用户
            self._notify("文件已不在暂存里", f"{item.filename}（可能已被清理）")
            return None
        try:
            dest = archive_file(item.staging, item.filename, item.mtime)
        except Exception as exc:
            log.exception("归档失败")
            self._notify("保存失败", str(exc), critical=True)
            return None
        self.store.add_archive(item_id, item.remote_path, str(dest))
        self.store.update_pending(item_id, status=STATUS_SAVED)
        self._inflight.discard(item.remote_path)
        return str(dest)

    def _save_item(self, item_id: str) -> bool:
        log.debug("点击保存 %s", item_id)
        dest = self._archive_item(item_id)
        if dest is None:
            return False
        self.dock.remove_item(item_id)
        self._notify_saved(dest)
        return True

    def _save_for_drag(self, item_id: str) -> str | None:
        """卡片被拖出：先归档（默认行为），再把归档路径交给拖拽对象。"""
        dest = self._archive_item(item_id)
        if dest is None:
            return None
        log.info("拖出卡片，已自动归档：%s", dest)
        self._notify("已自动保存", f"{Path(dest).name} 已归档，可直接拖给其他程序使用")
        return dest

    def _on_card_dragged(self, item_id: str) -> None:
        """拖拽结束：归档那一步已经做完，这里只把卡片撤掉。"""
        self.dock.remove_item(item_id)

    def _notify_saved(self, dest: str) -> None:
        """连续保存时合并成一条通知，避免批量操作刷出十几个托盘气泡。"""
        self._saved_count += 1
        self._saved_last = dest
        if self._saved_timer is None:
            self._saved_timer = QTimer()
            self._saved_timer.setSingleShot(True)
            self._saved_timer.setInterval(1200)
            self._saved_timer.timeout.connect(self._flush_saved)
        self._saved_timer.start()
        if self._saved_count == 1:
            self._notify("已保存", dest)

    def _flush_saved(self) -> None:
        if self._saved_count > 1:
            self._notify("已保存", f"共 {self._saved_count} 个文件")
        self._saved_count = 0

    def _ignore_item(self, item_id: str) -> None:
        log.debug("点击忽略 %s", item_id)
        item = self.store.get_pending(item_id)
        if item is None:
            return
        # 删除放后台：慢盘上单次 unlink 可达 200ms，卡在 UI 线程用户能感觉到
        if item.staging:
            self._deleter_pool.start(_FileDeleter(item.staging))
        self.store.mark_ignored(item.remote_path, item.mtime)
        self.store.update_pending(item_id, status=STATUS_IGNORED)
        self._inflight.discard(item.remote_path)
        self.dock.remove_item(item_id)

    def _preview_item(self, item_id: str) -> None:
        item = self.store.get_pending(item_id)
        if item and item.staging and Path(item.staging).is_file():
            QDesktopServices.openUrl(QUrl.fromLocalFile(item.staging))

    def _save_all(self) -> None:
        for item_id in list(self.dock.item_ids()):
            self._save_item(item_id)

    def _ignore_all(self) -> None:
        """一键忽略：清空所有待处理卡片（走单条忽略链路：记忽略表+后台删暂存）。

        列表先拷贝再遍历——_ignore_item 会边遍历边移除卡片，改原列表会跳项。
        """
        for item_id in list(self.dock.item_ids()):
            self._ignore_item(item_id)

    def open_settings(self) -> None:
        """设置窗口：所有项改完即时生效（回调 _on_setting_live 逐项落地）。"""
        dialog = SettingsDialog(self.adb, self.dock, on_change=self._on_setting_live,
                                on_rescan=self.watcher.rescan_from)
        dialog.exec()
        self.watcher.reload_config()   # 关窗口时兜底同步一次

    def _on_setting_live(self, key: str, value, old) -> None:
        """设置窗口的实时应用回调（在 UI 线程执行）。"""
        if key in ("fast_interval", "slow_interval"):
            self.watcher.reload_config()
        elif key == "connect_mode":
            self.watcher.reload_config()
            self.dock.set_manual_mode(value == "manual")
            log.info("连接方式：%s", "自动" if value == "auto" else "手动")
            self._mode_reconciles = 0        # 新的用户操作，重置补偿次数
            self._switch_connect_mode(value == "auto")
        elif key == "theme":
            self.dock.set_dark(value == "dark")
        elif key == "dock_width":
            self.dock.set_panel_width(int(value))
        elif key == "remote_send_dir":
            if old:
                history = [str(d) for d in (CONFIG.get("send_dir_history") or [])]
                if old not in history:
                    history.append(old)
                CONFIG.set("send_dir_history", history)
                log.info("发送目录 %s → %s（旧目录加入排除表）", old, value)
            else:
                log.info("发送目录设置为 %s", value)

    def _send_files(self, paths: list) -> None:
        """接收一批待发文件，排进队列。

        不能「发着就拒绝」：资源管理器多选 5 个文件时，Windows 会起 5 个进程，
        每个都通过 IPC 投一条消息进来，间隔几十毫秒。直接拒绝会丢掉 4 个，
        还弹出四条「正在发送」。所以统一入队，发完一批自动接下批。
        """
        fresh = [str(Path(p)) for p in paths if Path(p).is_file()]
        if not fresh:
            return
        self._send_queue.extend(fresh)
        self._pump_send_queue()

    def _pump_send_queue(self) -> None:
        if self._sender is not None and self._sender.isRunning():
            return
        if not self._send_queue:
            return
        batch = list(self._send_queue)
        self._send_queue.clear()
        self._sender = Sender(self.adb, batch, self.qapp)
        self._sender.finished_with.connect(self._on_send_done)
        self._sender.start()

    def _on_send_done(self, ok: bool, message: str) -> None:
        log.info("发送结果：%s / %s", ok, message)
        if ok:
            self._notify("发送完成", message)
        else:
            self._notify("发送失败", message, critical=True)
        self._pump_send_queue()   # 后面还排着的话接着发

    def _notify(self, title: str, message: str, critical: bool = False) -> None:
        if self._tray and not critical:
            self._tray.showMessage(title, message, QSystemTrayIcon.Information, 2500)
            return
        if critical:
            QMessageBox.warning(None, title, message)

    # ------------------------------------------------------------------ 生命周期
    def start(self) -> None:
        self._guard_archive_root()
        self._cleanup_staging()
        # 改名迁移：旧名「手机电脑互通」的自启动值 / 右键菜单键搬到「Ping」下
        autostart.migrate_legacy()
        shellmenu.migrate_legacy()
        # 手动连接模式必须禁掉 adb server 对已配对设备的自动回连（否则"点了才连"失效）
        self.adb.set_mdns_auto_connect(CONFIG.get("connect_mode", "auto") == "auto")
        # 项目被挪过位置的话注册表里还是老路径，开机自启会静默失效 —— 这里自动修正
        if autostart.is_registered_but_stale():
            ok, _ = autostart.set_enabled(True)
            if ok:
                log.info("开机自启动的路径已更新为当前目录")
        self.dock.show()
        # 长期离线降级（必须在启动扫描之前、由主线程执行）：天没开机时只回看最近 1 小时，
        # 不把积压全灌上来；同时把卡片时效临时收窄到同一个窗口（见 _expire_stale_cards）
        self.watcher.prepare_startup()
        self.watcher.start()
        self.puller.start()
        # 卡片时效：启动时清一次 + 每小时巡检（超过 N 天没处理 → 自动忽略并清暂存）
        self._expire_stale_cards()
        # 坑：App 不是 QObject，QTimer(self) 会直接 TypeError 崩掉启动（实测）。
        # 不带 parent，靠 self._ttl_timer 持引用防回收。
        self._ttl_timer = QTimer()
        self._ttl_timer.setInterval(3600 * 1000)
        self._ttl_timer.timeout.connect(self._expire_stale_cards)
        self._ttl_timer.start()
        log.info("启动完成，归档根目录：%s", CONFIG.archive_root)

    def _on_backlog_skipped(self, gap_hours: float, keep_hours: float) -> None:
        """长期离线启动：只回看最近 N 小时，收窄遗留卡片的时效窗口，并告知用户。"""
        self._backlog_gap_hours = float(gap_hours)
        span = f"{gap_hours / 24:.1f} 天" if gap_hours >= 24 else f"{gap_hours:.1f} 小时"
        log.info("离线 %s，本次只同步最近 %.0f 小时", span, keep_hours)
        self._notify(
            "只同步最近的文件",
            f"距上次使用已 {span}，本次只拉取最近 {keep_hours:.0f} 小时的文件。"
            f"更早的文件仍在手机上——需要时可到设置里「补拉」。",
        )

    @staticmethod
    def _guard_archive_root() -> None:
        """归档根目录落在 %TEMP% 里就回退默认值。

        测试脚本会临时改 archive_root；极端情况下（进程被强杀）值会残留到真实配置，
        之后所有文件都归档进临时目录、被系统清理时全丢——这里兜一道。
        """
        root = str(CONFIG.get("archive_root") or "")
        temp = str(os.environ.get("TEMP") or "").rstrip("\\/").lower()
        if temp and root.lower().startswith(temp):
            fallback = DEFAULTS.get("archive_root") or "D:\\手机归档"
            log.warning("归档根目录指向临时目录（%s），已回退到 %s", root, fallback)
            CONFIG.set("archive_root", fallback)

    def _expire_stale_cards(self) -> int:
        """卡片时效：超过 N 天没保存/忽略的，按「忽略」处理（记忽略表 + 删暂存）。

        长期离线后（见 _on_backlog_skipped）把窗口收窄到"只保留最近 N 小时"，
        免得一开机满屏都是攒了十天的旧卡片。忽略表按「路径+修改时间」记录，
        之后同路径出新版本会正常重新弹出——不会误伤新文件。
        """
        ttl_days = max(1, int(CONFIG.get("card_ttl_days", 3)))
        now = int(time.time())
        cutoff = now - ttl_days * 86400
        note = f"超过 {ttl_days} 天未处理"
        if self._backlog_gap_hours is not None:
            keep_hours = max(1, int(CONFIG.get("first_run_hours", 1)))
            backlog_cutoff = now - keep_hours * 3600
            if backlog_cutoff > cutoff:
                cutoff = backlog_cutoff
                note = f"长期离线后仅保留最近 {keep_hours} 小时"
        expired = [it for it in self.store.list_pending() if it.created_at < cutoff]
        for item in expired:
            if item.staging:
                self._deleter_pool.start(_FileDeleter(item.staging))
            self.store.mark_ignored(item.remote_path, item.mtime)
            self.store.update_pending(item.id, status=STATUS_IGNORED)
            self._inflight.discard(item.remote_path)
            self._pull_retries.pop(item.id, None)
            self.dock.remove_item(item.id)
            log.info("卡片超时（%s），自动忽略并清理：%s", note, item.filename)
        if expired:
            self._notify("超时自动忽略",
                         f"{len(expired)} 个{note}的文件已自动忽略并清理暂存")
        return len(expired)

    def _cleanup_staging(self) -> None:
        """启动时恢复上次没处理完的卡片，顺手清掉没人引用的暂存文件。

        以前这里是一律清空（暂存删光 + pending 全清）。后果是：关程序时有几张卡
        没点，重启后那些文件就**永远不会再出现**了 —— 水位线早就推进过，监听器
        不会再通知一遍，用户只能自己去手机里翻，正是这个工具想消灭的麻烦。
        """
        alive = {
            str(Path(item.staging).resolve())
            for item in self.store.list_pending()
            if item.staging and Path(item.staging).is_file()
        }
        removed = 0
        # 两个位置都清：staging_dir() 跟着归档盘走，改过设置后旧位置会留孤儿
        for folder in {staging_dir(), STAGING_DIR}:
            for leftover in folder.glob("*"):
                try:
                    if leftover.is_file() and str(leftover.resolve()) not in alive:
                        leftover.unlink()
                        removed += 1
                except OSError:
                    continue

        restored = zombie = 0
        for item in self.store.list_pending():
            if item.staging and Path(item.staging).is_file():
                if item.status != STATUS_PULLED:
                    self.store.update_pending(item.id, status=STATUS_PULLED)
                    item = self.store.get_pending(item.id) or item
                self._inflight.add(item.remote_path)
                self.dock.add_item(item)
                restored += 1
            else:
                self.store.update_pending(item.id, status=STATUS_FAILED, error="暂存文件已丢失")
                zombie += 1
        purged = self.store.purge_pending()   # 只清 saved/ignored/failed
        if removed or restored or zombie or purged:
            log.info("启动恢复：%d 张卡片、清理 %d 个孤儿暂存、%d 条失效记录、%d 条历史",
                     restored, removed, zombie, purged)

    def on_ipc_message(self, payload: dict) -> None:
        """收到后续实例转来的消息（目前只有「发送这些文件」一种）。"""
        if payload.get("cmd") != "send":
            return
        paths = [p for p in (payload.get("paths") or []) if Path(p).is_file()]
        if paths:
            log.info("从其他实例收到 %d 个待发送文件", len(paths))
            if not self.dock.isVisible():
                self.dock.show()
            self._send_files(paths)

    def send_from_cli(self, paths: list[str]) -> None:
        """命令行 ``--send`` 带进来的文件（首次启动、还没有别的实例时）。"""
        fresh = [p for p in paths if Path(p).is_file()]
        if not fresh:
            return
        log.info("命令行收到 %d 个待发送文件", len(fresh))
        if not self.dock.isVisible():
            self.dock.show()
        self._send_files(fresh)

    def shutdown(self) -> None:
        # 幂等保护：曾出现过同一实例记两条「退出中」的情况。双重退出时
        # 第二次会去 stop 已停的线程、关已关的库，虽然多半无害，但不该发生。
        if getattr(self, "_shutdown_done", False):
            log.info("退出中（重复调用，忽略）")
            return
        self._shutdown_done = True
        log.info("退出中")
        self.watcher.stop()
        self.puller.stop()
        self.watcher.wait(3000)
        self.puller.wait(3000)
        if self._sender is not None:
            self._sender.wait(3000)
        self.adb.close_sessions()   # 常驻 shell 要显式收掉，否则会在后台留一个 adb 子进程
        self.store.close()
        self.qapp.quit()


def _setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        # %(process)d 是为了分辨日志出自哪个实例 —— 曾出现过同一时刻两条
        # 「退出中」却只有一个「启动完成」，没有 pid 根本说不清是谁退的。
        format="%(asctime)s %(levelname)s [%(process)d] %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(LOG_PATH, encoding="utf-8"),
            logging.StreamHandler(sys.stdout),
        ],
    )


def main() -> int:
    _setup_logging()
    # 把工作目录挪到数据目录：程序以「发布目录」为 CWD 运行时，它拉起的 adb
    # server 会继承这个 CWD，导致发布文件夹在 server 存活期间永远删不掉
    # （换版本/卸载时表现为"目录正在使用"）。程序内部路径全是绝对路径，挪走无害。
    try:
        os.chdir(DATA_DIR)
    except OSError:
        pass
    qapp = QApplication(sys.argv)
    qapp.setApplicationName(APP_NAME)
    qapp.setQuitOnLastWindowClosed(False)

    incoming = shellmenu.send_paths_from_argv(sys.argv)

    # 单实例：双击两次「启动.cmd」不该出现两个托盘图标、两条监听线
    lock = QLockFile(str(DATA_DIR / "app.lock"))
    lock.setStaleLockTime(30_000)
    if not lock.tryLock(300):
        # 已有实例在跑：把这次要发的文件转过去，别让用户以为点了没反应
        if incoming and ipc.forward_quietly({"cmd": "send", "paths": incoming}):
            log.info("已转交 %d 个文件给正在运行的实例", len(incoming))
        else:
            print("程序已在运行，请看屏幕右侧面板或系统托盘。")
        return 0

    app = App(qapp)
    # 全局热键：任何界面下都能收起/唤出面板。Ctrl+Alt+M 常被别的软件占用，
    # 所以给了候选链，实际生效哪个会写进日志。
    hotkeys = GlobalHotKeys()
    chosen = hotkeys.add_first_available(
        [
            (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_M, "Ctrl+Alt+M"),
            (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_J, "Ctrl+Alt+J"),
            (MOD_CONTROL | MOD_ALT | MOD_NOREPEAT, VK_K, "Ctrl+Alt+K"),
            (MOD_CONTROL | MOD_SHIFT | MOD_NOREPEAT, VK_M, "Ctrl+Shift+M"),
        ],
        app._toggle_dock,
    )
    if not chosen:
        log.warning("全局热键候选全部被占用，只能用托盘切换面板")
    qapp.installNativeEventFilter(hotkeys)

    server = ipc.IpcServer()          # 监听后续实例（右键多选时会连续来好几条）
    server.message.connect(app.on_ipc_message)
    app.start()
    if incoming:
        app.send_from_cli(incoming)
    code = qapp.exec()
    app.shutdown()
    server.close()
    hotkeys.release()
    lock.unlock()
    return code


if __name__ == "__main__":
    raise SystemExit(main())
