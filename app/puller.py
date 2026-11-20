"""拉取线程：把手机文件 adb pull 到本地 staging 目录。

staging 文件名用 uuid + 原扩展名（纯 ASCII），手机原文件名单独存在
``pending.filename`` 里 —— 归档时再用它命名，这样中途改名/中文都不会出问题。
"""
from __future__ import annotations

import queue
import uuid
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from adb import AdbChannel, AdbError
from config import CONFIG, staging_dir
from store import STATUS_FAILED, STATUS_PULLED, STATUS_PULLING, Store


class Puller(QThread):
    item_pulled = Signal(str)          # item_id
    item_failed = Signal(str, str)     # item_id, 错误信息

    def __init__(self, store: Store, adb: AdbChannel, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._adb = adb
        self._queue: queue.Queue[str] = queue.Queue()
        self._running = True
        self._cancelled: set[str] = set()

    def enqueue(self, item_id: str) -> None:
        self._queue.put(item_id)

    def cancel(self, item_id: str) -> None:
        self._cancelled.add(item_id)

    def stop(self) -> None:
        self._running = False

    def run(self) -> None:  # noqa: D102
        while self._running:
            try:
                item_id = self._queue.get(timeout=0.3)
            except queue.Empty:
                continue
            if item_id in self._cancelled:
                self._cancelled.discard(item_id)
                continue
            try:
                self._handle(item_id)
            except Exception as exc:  # 单个失败不能拖死整条队列
                self._store.update_pending(item_id, status=STATUS_FAILED, error=str(exc))
                self.item_failed.emit(item_id, str(exc))

    def _handle(self, item_id: str) -> None:
        item = self._store.get_pending(item_id)
        if item is None or item.status in (STATUS_PULLED,):
            return
        suffix = Path(item.filename).suffix.lower() or ".bin"
        staging = staging_dir() / f"{uuid.uuid4().hex}{suffix}"
        # staging 路径要在 pull **之前**落库：UI 靠轮询这个文件的大小算下载进度，
        # 否则 200MB 的 APK 下载期间面板上什么都看不到（用户以为功能坏了）。
        self._store.update_pending(item_id, status=STATUS_PULLING, staging=str(staging))

        error = ""
        try:
            # 大文件走手机自带 netcat 直传（绕开 adb TLS 瓶颈），失败自动回退 adb pull。
            # 阈值可在设置里调；0 = 全部走 adb pull（关闭加速）。
            min_mb = CONFIG.get("fast_pull_min_mb", 8)
            min_size = int(min_mb * 1024 * 1024) if min_mb > 0 else 0
            if min_mb > 0:
                ok = self._adb.pull_fast(
                    item.remote_path, staging,
                    expected_size=int(item.size or 0), min_size=min_size,
                )
                # pull_fast 内部已回退过一次 adb；这里再兜一层，确保任一意外路径
                # 都不会因为加速功能而让文件拉不下来（代价仅是极端情况多试一次）。
                if not ok:
                    ok = self._adb.pull(item.remote_path, staging)
            else:
                ok = self._adb.pull(item.remote_path, staging)
            if not ok:
                error = "拉取失败"
        except AdbError as exc:
            ok = False
            error = str(exc)

        if ok and staging.is_file():
            self._store.update_pending(item_id, status=STATUS_PULLED, staging=str(staging))
            self.item_pulled.emit(item_id)
        else:
            staging.unlink(missing_ok=True)
            self._store.update_pending(item_id, status=STATUS_FAILED, error=error or "未知错误")
            self.item_failed.emit(item_id, error or "未知错误")
