"""SQLite 持久化：快照 diff、待处理队列、归档记录、忽略表。

快照走「启动时全量载入内存 dict + 增量落库」：手机端文件数量级在几万，
内存占用几 MB 可以接受，换来每轮 diff 零 SQL 查询。
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from config import DB_PATH

_SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    path       TEXT PRIMARY KEY,
    mtime      INTEGER NOT NULL,
    size       INTEGER NOT NULL,
    first_seen INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pending (
    id          TEXT PRIMARY KEY,
    remote_path TEXT NOT NULL,
    filename    TEXT NOT NULL,
    staging     TEXT NOT NULL DEFAULT '',
    mtime       INTEGER NOT NULL,
    size        INTEGER NOT NULL,
    status      TEXT NOT NULL,          -- waiting|pulling|pulled|saved|ignored|failed
    created_at  INTEGER NOT NULL,
    error       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_pending_status ON pending(status);

CREATE TABLE IF NOT EXISTS archive (
    id          TEXT PRIMARY KEY,
    remote_path TEXT NOT NULL,
    local_path  TEXT NOT NULL,
    archived_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS ignored (
    path  TEXT PRIMARY KEY,
    mtime INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

STATUS_WAITING = "waiting"
STATUS_PULLING = "pulling"
STATUS_PULLED = "pulled"
STATUS_SAVED = "saved"
STATUS_IGNORED = "ignored"
STATUS_FAILED = "failed"


@dataclass
class PendingItem:
    id: str
    remote_path: str
    filename: str
    staging: str
    mtime: int
    size: int
    status: str
    created_at: int
    error: str = ""


class Store:
    def __init__(self, db_path: Path = DB_PATH) -> None:
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        # 监听器每 3 秒就要落一批水位线，默认的 rollback journal + FULL 同步会让
        # 每次提交都 fsync 一次。WAL 让读不挡写，synchronous=NORMAL 在桌面场景足够
        # 安全（断电最多丢最后一条水位线，而重启本来就会清掉暂存重扫）。
        with self._lock, self._conn:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.execute("PRAGMA busy_timeout=3000")
            self._conn.executescript(_SCHEMA)
        self._seen: dict[str, tuple[int, int]] = {}
        self._load_seen()

    # ------------------------------------------------------------------ 快照
    def _load_seen(self) -> None:
        with self._lock:
            rows = self._conn.execute("SELECT path, mtime, size FROM seen").fetchall()
        self._seen = {r["path"]: (r["mtime"], r["size"]) for r in rows}

    @property
    def seen_count(self) -> int:
        return len(self._seen)

    def diff(self, items: list[tuple[str, int, int]]) -> list[tuple[str, int, int]]:
        """比对快照，返回新增或内容变化的项，并写回库。

        items: [(remote_path, mtime, size)]
        """
        fresh: list[tuple[str, int, int]] = []
        rows_to_write: list[tuple[str, int, int, int]] = []
        now = int(time.time())
        with self._lock:
            for path, mtime, size in items:
                known = self._seen.get(path)
                if known is None:
                    fresh.append((path, mtime, size))
                    self._seen[path] = (mtime, size)
                    rows_to_write.append((path, mtime, size, now))
                elif known[0] != mtime or known[1] != size:
                    fresh.append((path, mtime, size))
                    self._seen[path] = (mtime, size)
                    rows_to_write.append((path, mtime, size, now))
            if rows_to_write:
                with self._conn:
                    self._conn.executemany(
                        "INSERT INTO seen(path, mtime, size, first_seen) VALUES(?,?,?,?) "
                        "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime, size=excluded.size",
                        rows_to_write,
                    )
        return fresh

    def forget(self, paths: list[str]) -> None:
        """手机端文件被删掉 → 从快照剔除，下次重新出现时能再弹。"""
        if not paths:
            return
        with self._lock:
            for p in paths:
                self._seen.pop(p, None)
            with self._conn:
                self._conn.executemany("DELETE FROM seen WHERE path=?", [(p,) for p in paths])

    # ------------------------------------------------------------------ 忽略
    def is_ignored(self, path: str, mtime: int) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT mtime FROM ignored WHERE path=?", (path,)
            ).fetchone()
        return bool(row) and row["mtime"] == mtime

    def mark_ignored(self, path: str, mtime: int) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO ignored(path, mtime) VALUES(?,?) "
                "ON CONFLICT(path) DO UPDATE SET mtime=excluded.mtime",
                (path, mtime),
            )

    # ------------------------------------------------------------------ pending
    def add_pending(self, item: PendingItem) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO pending"
                "(id, remote_path, filename, staging, mtime, size, status, created_at, error)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (
                    item.id,
                    item.remote_path,
                    item.filename,
                    item.staging,
                    item.mtime,
                    item.size,
                    item.status,
                    item.created_at,
                    item.error,
                ),
            )

    def update_pending(self, item_id: str, **fields) -> None:
        if not fields:
            return
        allowed = {"staging", "status", "error"}
        pairs = [(k, v) for k, v in fields.items() if k in allowed]
        if not pairs:
            return
        assign = ", ".join(f"{k}=?" for k, _ in pairs)
        with self._lock, self._conn:
            self._conn.execute(
                f"UPDATE pending SET {assign} WHERE id=?",
                [v for _, v in pairs] + [item_id],
            )

    def get_pending(self, item_id: str) -> PendingItem | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM pending WHERE id=?", (item_id,)).fetchone()
        return _row_to_pending(row) if row else None

    def list_pending(self, statuses: tuple[str, ...] = (STATUS_WAITING, STATUS_PULLED)) -> list[PendingItem]:
        marks = ",".join("?" for _ in statuses)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM pending WHERE status IN ({marks}) ORDER BY created_at", statuses
            ).fetchall()
        return [_row_to_pending(r) for r in rows]

    def purge_pending(self, keep_statuses: tuple[str, ...] = (STATUS_WAITING, STATUS_PULLING, STATUS_PULLED)) -> int:
        """清掉已终结的记录（saved/ignored/failed），避免表无限增长。

        传空元组 = 全清（启动时用：暂存目录已清空，所有记录都是僵尸）。
        """
        if not keep_statuses:
            with self._lock, self._conn:
                cur = self._conn.execute("DELETE FROM pending")
                return cur.rowcount
        marks = ",".join("?" for _ in keep_statuses)
        with self._lock, self._conn:
            cur = self._conn.execute(
                f"DELETE FROM pending WHERE status NOT IN ({marks})", keep_statuses
            )
            return cur.rowcount

    # ------------------------------------------------------------------ 归档
    def add_archive(self, rec_id: str, remote_path: str, local_path: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO archive(id, remote_path, local_path, archived_at)"
                " VALUES(?,?,?,?)",
                (rec_id, remote_path, local_path, int(time.time())),
            )

    def archive_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) AS n FROM archive").fetchone()
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------------ meta
    def get_meta(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._conn.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def set_meta(self, key: str, value: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )

    def set_meta_many(self, values: dict[str, str]) -> None:
        """一次事务写多条 meta。

        每轮扫描要给 4~7 个目录各推进一次水位线；逐条写就是每次一个事务，
        在机械盘/被杀软盯着的目录上每次提交都要几百微秒。合成一次划算得多。
        """
        if not values:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "INSERT INTO meta(key, value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                list(values.items()),
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _row_to_pending(row: sqlite3.Row) -> PendingItem:
    return PendingItem(
        id=row["id"],
        remote_path=row["remote_path"],
        filename=row["filename"],
        staging=row["staging"],
        mtime=row["mtime"],
        size=row["size"],
        status=row["status"],
        created_at=row["created_at"],
        error=row["error"],
    )
