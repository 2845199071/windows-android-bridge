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
from config import staging_dir
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
        self._store.update_pending(item_id, status=STATUS_PULLING)
        suffix = Path(item.filename).suffix.lower() or ".bin"
        staging = staging_dir() / f"{uuid.uuid4().hex}{suffix}"

        error = ""
        try:
            ok = self._adb.pull(item.remote_path, staging)
            if not ok:
                error = "adb pull 返回失败"
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
