"""监听调度器：分层轮询手机目录，把「新出现的文件」抛给 UI。

设计要点：
* **每个目录独立水位线**（存 meta ``wm:<path>``）。fast / slow 两条线的进度
  互不干扰，否则 slow 目录里一个很新的文件会把 fast 的水位线推高，导致
  fast 里稍旧的文件永远扫不到。
* **一轮只发一次 adb shell**：各目录命令用 ``;`` 串联，中间插
  ``echo '@@DIR <path>'`` 当分隔符。7 个目录若各发一次要 1s 纯往返开销。
* 上轮没结束就跳过本轮，杜绝堆积。
"""
from __future__ import annotations

import logging
import time
from pathlib import Path

from PySide6.QtCore import QThread, Signal

from adb import AdbChannel, AdbError, Device
from config import (
    EXCLUDE_EXTS,
    EXCLUDE_PATH_PARTS,
    FAST_DIRS,
    SLOW_DIRS,
    CONFIG,
    DATA_DIR,
)
from store import Store

_DIR_MARK = "@@DIR "
_LAST_SEEN_PATH = DATA_DIR / "last_seen"   # 运行心跳：判断"离线了多久"
log = logging.getLogger("s2pc.watcher")


def _is_excluded(path: str) -> bool:
    lower = path.lower()
    for part in EXCLUDE_PATH_PARTS:
        if part.lower() in lower:
            return True
    name = Path(path).name
    if name.startswith("."):
        return True
    return Path(path).suffix.lower() in EXCLUDE_EXTS


class Watcher(QThread):
    new_files = Signal(list)        # list[tuple[remote_path, mtime, size]]
    device_changed = Signal(object) # Device | None
    scan_finished = Signal(float)   # 本轮耗时（秒）
    failed = Signal(str)
    backlog_skipped = Signal(float, float)   # 长期离线：(离线小时数, 只回看的小时数)
    rescan_done = Signal(int, int, float)    # 补拉结束：(扫到文件数, 新增数, 补拉小时数)
    self_healed = Signal(str)                # 掉线自愈：触发了 adb server 重启（附说明，供 UI 提示）

    def __init__(self, store: Store, adb: AdbChannel, parent=None) -> None:
        super().__init__(parent)
        self._store = store
        self._adb = adb
        self._running = True
        self._device: Device | None = None
        self._last_device_check = 0.0
        # 掉线自愈：曾连过设备 + 持续无设备超过阈值 → 重启 adb server 救活失活的 mDNS 后端
        self._ever_connected = False          # 本次运行是否成功连过（没连过就不自愈，避免瞎重启）
        self._lost_since = 0.0                # 开始持续掉线的 monotonic 时刻，0=当前在线
        self._heal_times: list[float] = []    # 最近几次自愈时刻（滑动窗口限频）
        self._auto_connect = (CONFIG.get("connect_mode", "auto") == "auto")
        self._fast_interval = max(1, int(CONFIG.get("fast_interval", 3)))
        self._slow_interval = max(self._fast_interval, int(CONFIG.get("slow_interval", 15)))
        self._first_run_hours = int(CONFIG.get("first_run_hours", 1))
        self._last_seen_flush = 0.0
        self._force_scan = False
        self._rescan_hours: float | None = None   # 本次补拉回退的小时数（扫完要回报给 UI）
        self._rescan_all = False                  # 补拉进行中：窗口内全部重来，不看 seen/ignored
        self._rescan_queue: list[tuple[str, int, int]] = []   # 超出单批上限的，排队慢发
        self._extra_dirs: list[str] = []          # /sdcard 根目录下用户自建的文件夹（自动发现）
        self._last_dir_refresh = 0.0

    # ------------------------------------------------------------------ 生命周期
    def stop(self) -> None:
        self._running = False
        self._touch_last_seen(force=True)

    def reload_config(self) -> None:
        """设置改完即时生效，不用重启监听线程（int 赋值是原子的）。"""
        self._fast_interval = max(1, int(CONFIG.get("fast_interval", 3)))
        self._slow_interval = max(self._fast_interval, int(CONFIG.get("slow_interval", 15)))
        self._first_run_hours = int(CONFIG.get("first_run_hours", 1))
        self._auto_connect = (CONFIG.get("connect_mode", "auto") == "auto")

    def run(self) -> None:  # noqa: D102
        last_fast = 0.0
        last_slow = 0.0
        while self._running:
            try:
                self._touch_last_seen()
                now = time.monotonic()
                # 掉线时 2 秒重试一次（手机息屏 WiFi 休眠断开后，点亮能很快连回来）；
                # 正常时 10 秒查一次就够，省点 adb 调用。
                # 手动连接模式下没有"自动重连"可言，掉线后 5 秒被动查一次就够
                # （比如 adb server 自己把配对设备连回来时能及时看到）。
                if self._device is not None:
                    interval = 10.0
                else:
                    interval = 2.0 if self._auto_connect else 5.0
                if now - self._last_device_check >= interval:
                    # 手动模式只做被动检查：绝不主动 adb connect
                    self._refresh_device()
                    self._last_device_check = now

                # 掉线自愈：维护计时并在必要时重启 adb server（后台线程，不卡 UI）
                self._update_heal_state(now)
                self._maybe_self_heal(now)

                if self._device is not None:
                    # 每 60 秒重新发现一次根目录自建文件夹（用户随时可能新建一个）
                    if now - self._last_dir_refresh >= 60:
                        self._refresh_extra_dirs()
                        self._last_dir_refresh = now
                    extra = self._extra_pairs()
                    # 补拉：设置里点了「补拉过去 N 小时」，水位线已回退，立刻扫一轮
                    if self._force_scan:
                        self._force_scan = False
                        scanned = fresh = 0
                        for group in (FAST_DIRS + extra, SLOW_DIRS):
                            n_scan, n_new = self._tick(group)
                            scanned += n_scan
                            fresh += n_new
                        self._rescan_all = False     # 只对这一轮扫描生效
                        last_fast = last_slow = time.monotonic()
                        hours, self._rescan_hours = self._rescan_hours, None
                        if hours is not None:
                            log.info("补拉完成：扫到 %d 个、新增 %d 个（回看 %.1f 小时）",
                                     scanned, fresh, hours)
                            self.rescan_done.emit(scanned, fresh, hours)
                        continue
                    if now - last_fast >= self._fast_interval:
                        self._tick(FAST_DIRS + extra)
                        last_fast = time.monotonic()
                    if now - last_slow >= self._slow_interval:
                        self._tick(SLOW_DIRS)
                        last_slow = time.monotonic()
            except AdbError as exc:
                self._drop_device(str(exc))
            except Exception as exc:  # 保活：任何异常都不该让监听线程死掉
                self.failed.emit(f"监听异常：{exc}")
            # 细粒度睡眠，stop() 能及时生效
            for _ in range(5):
                if not self._running:
                    break
                time.sleep(0.1)

    # ------------------------------------------------------------------ 离线时长
    def _touch_last_seen(self, force: bool = False) -> None:
        """心跳：每 60 秒落一次时间戳，用来判断下次启动"离线了多久"。"""
        now = time.time()
        if not force and now - self._last_seen_flush < 60:
            return
        self._last_seen_flush = now
        try:
            _LAST_SEEN_PATH.parent.mkdir(parents=True, exist_ok=True)
            _LAST_SEEN_PATH.write_text(str(int(now)), encoding="utf-8")
        except OSError:
            pass

    def _read_last_seen(self) -> float:
        try:
            return float(_LAST_SEEN_PATH.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            return 0.0

    def prepare_startup(self) -> float | None:
        """启动基线调整：离线太久就只回看最近 N 小时，别把积压全灌上来。

        场景：十天没开程序 → 水位线还停在十天前 → 一启动把这十天所有文件都当
        "新文件"拉上来。这里按心跳判断离线时长，超过 ``offline_reset_hours``
        （默认 12 小时）就把所有目录水位线重置为「现在 - first_run_hours」。

        返回离线小时数（触发了降级时），否则 None。
        必须在 start() 之前由主线程调用（此时监听线程还没跑，无并发问题）。
        """
        last = self._read_last_seen()
        now = time.time()
        self._touch_last_seen(force=True)
        if last <= 0:
            return None                      # 首次安装：各目录按首次基线走
        gap_hours = (now - last) / 3600
        threshold = float(CONFIG.get("offline_reset_hours", 12))
        if gap_hours < threshold:
            return None                      # 短暂关闭：正常从水位线续，不丢文件
        keep = max(1, int(self._first_run_hours))
        self._reset_watermarks(int(now) - keep * 3600)
        log.info("离线 %.1f 小时（超过阈值 %.0f），只回看最近 %d 小时",
                 gap_hours, threshold, keep)
        self.backlog_skipped.emit(gap_hours, float(keep))
        return gap_hours

    def _reset_watermarks(self, base: int) -> None:
        """把所有扫描目录的水位线设为指定时间戳（补拉/长期离线降级共用）。"""
        for path in self._all_dirs():
            self._store.set_meta(f"wm:{path}", str(int(base)))

    def _all_dirs(self) -> list[str]:
        """固定监听目录 + 自动发现的根目录自建文件夹。"""
        return [p for p, _ in (*FAST_DIRS, *SLOW_DIRS)] + list(self._extra_dirs)

    def _extra_pairs(self) -> list[tuple[str, int]]:
        return [(p, 2) for p in self._extra_dirs]

    def _refresh_extra_dirs(self) -> None:
        """把 /sdcard 根目录下的中文文件夹自动纳入监听。

        用户习惯在根目录建「我的图片」「项目资料」这类文件夹存东西，
        只盯 DCIM / Download 这几个固定目录永远扫不到（实测 209MB 的 APK
        躺在自建文件夹里，补拉也找不到）。实测这些文件夹只有
        0~118 个文件，加进来扫描代价很小。

        只自动收**含中文**的目录：Android / MIUI / tencent / Quark 这些英文
        app 目录动辄上万文件，扫了纯属浪费；需要时用 watch_extra_dirs 手动加。
        """
        try:
            out = self._adb.shell("ls -d /sdcard/*/ 2>/dev/null", timeout=15, reuse=True)
        except AdbError:
            return
        auto: list[str] = []
        for line in out.splitlines():
            path = line.strip().rstrip("/")
            if not path.startswith("/sdcard/"):
                continue
            name = path.rsplit("/", 1)[-1]
            if not name or not any(ord(ch) > 127 for ch in name):
                continue
            if path not in auto:
                auto.append(path)
        manual = (str(d).strip().rstrip("/") for d in (CONFIG.get("watch_extra_dirs") or []))
        merged: list[str] = []
        for p in (*auto, *manual):
            if p.startswith("/sdcard/") and p not in merged:
                merged.append(p)
        if merged != self._extra_dirs:
            preview = "、".join(x.rsplit("/", 1)[-1] for x in merged[:8])
            log.info("额外监听 %d 个文件夹：%s%s",
                     len(merged), preview, "…" if len(merged) > 8 else "")
        self._extra_dirs = merged

    def rescan_from(self, hours: float) -> int:
        """补拉：水位线回退到 N 小时前并立刻扫一轮（找回落下的旧文件）。

        水位线**只能往前退**。如果现有水位线本来就更早（比如停在 10:28），
        补拉 1 小时算出的 11:00 反而更晚，直接覆盖会把 10:28~11:00 这段跳过，
        越补越漏。所以每个目录取 min(现值, now-N)。
        """
        hours = max(0.0, float(hours))
        base = int(time.time() - hours * 3600)
        marks: dict[str, str] = {}
        for path in self._all_dirs():
            try:
                cur = int(self._store.get_meta(f"wm:{path}", "0"))
            except ValueError:
                cur = 0
            target = min(cur, base) if cur > 0 else base
            marks[f"wm:{path}"] = str(target)
            base = min(base, target)
        self._store.set_meta_many(marks)
        self._rescan_hours = hours
        self._rescan_all = True        # 语义：把这个窗口内的文件**全部**重新拉一遍
        self._rescan_queue.clear()     # 上一次没发完的旧队列作废，避免串味
        self._force_scan = True
        log.info("补拉：水位线回退到 %.1f 小时前（含已保存/已忽略，全量重拉）", hours)
        return base

    # ------------------------------------------------------------------ 设备
    def poke_device_check(self) -> None:
        """让监听线程下一轮立刻检查设备（切模式/重启 server 后用）。"""
        self._last_device_check = 0.0

    def _refresh_device(self) -> None:
        try:
            # 手动连接模式：allow_connect=False，只看已连上的设备，不主动发起连接
            device = self._adb.ensure_device(allow_connect=self._auto_connect)
        except AdbError:
            device = None
        if device != self._device:
            self._device = device
            self.device_changed.emit(device)

    # 掉线自愈：adb server 的 mDNS 后端偶尔失活，设备全变 offline，
    # 普通重连怎么都连不上，只有 kill-server/start-server 能救活（实测两次）。
    # 只在「本次运行曾连上过 + 持续掉线一段时间」时触发，且滑动窗口限频，
    # 避免用户主动关手机 WiFi / 拔线时反复重启 server。
    _HEAL_AFTER_S = 30.0       # 持续掉线多久才自愈
    _HEAL_WINDOW_S = 600.0     # 限频窗口
    _HEAL_MAX = 2              # 窗口内最多自愈次数

    def _update_heal_state(self, now: float) -> None:
        """跟随设备状态维护自愈计时：在线清零，掉线开始计时。"""
        if self._device is not None:
            self._ever_connected = True
            self._lost_since = 0.0
        elif self._ever_connected and self._lost_since <= 0:
            self._lost_since = now

    def _maybe_self_heal(self, now: float) -> None:
        if not self._ever_connected or self._device is not None:
            return
        if self._lost_since <= 0 or now - self._lost_since < self._HEAL_AFTER_S:
            return
        if not CONFIG.get("adb_selfheal", True):
            return
        # 滑动窗口限频
        self._heal_times[:] = [t for t in self._heal_times if now - t < self._HEAL_WINDOW_S]
        if len(self._heal_times) >= self._HEAL_MAX:
            return
        self._heal_times.append(now)
        self._lost_since = now        # 重置计时：给 server 恢复和重连留时间
        log.info("持续掉线，自动重启 adb server（第 %d 次/窗口）", len(self._heal_times))
        ok = self._adb.restart_server()
        self.poke_device_check()
        self.self_healed.emit(
            "已自动重启手机连接服务，正在恢复…" if ok else "自动恢复连接失败，请手动重启程序"
        )

    def _drop_device(self, reason: str) -> None:
        if self._device is not None:
            self._device = None
            self.device_changed.emit(None)
        self.failed.emit(f"设备断开：{reason}")

    # ------------------------------------------------------------------ 扫描
    def _watermark(self, path: str) -> int:
        key = f"wm:{path}"
        raw = self._store.get_meta(key, "0")
        try:
            wm = int(raw)
        except ValueError:
            wm = 0
        if wm > 0:
            return wm
        # 首次：只看最近 N 小时，避免把手机里历史文件全弹出来
        wm = int(time.time()) - self._first_run_hours * 3600
        self._store.set_meta(key, str(wm))
        return wm

    def _tick(self, dirs: list[tuple[str, int]]) -> tuple[int, int]:
        started = time.monotonic()
        buckets = self._scan(dirs)
        scanned = sum(len(v) for v in buckets.values())   # 本轮扫到的文件总数（含已处理过的）
        collected: list[tuple[str, int, int]] = []
        # 自己刚推到手机的文件不该再弹回来给用户确认一遍（含改过目录之前的旧目录）
        send_dirs = [str(CONFIG.get("remote_send_dir") or "").rstrip("/")]
        send_dirs += [str(d).rstrip("/") for d in (CONFIG.get("send_dir_history") or [])]
        send_dirs = [d for d in send_dirs if d]
        full = self._rescan_all      # 补拉：窗口内全部重来，不再看 seen / ignored
        watermarks: dict[str, str] = {}
        for path, items in buckets.items():
            if not items:
                continue
            fresh = self._store.diff(items)   # 快照照常更新，下次正常扫描不会重复弹
            # 水位线推进到本轮该目录最大 mtime；diff 已去重，往回留 2s 余量安全
            newest = max(mtime for _, mtime, _ in items)
            watermarks[f"wm:{path}"] = str(max(0, newest - 2))
            for remote, mtime, size in (items if full else fresh):
                if any(remote.startswith(d + "/") for d in send_dirs):
                    continue
                if _is_excluded(remote):
                    continue
                if not full and self._store.is_ignored(remote, mtime):
                    continue
                collected.append((remote, mtime, size))
        # 多个目录的水位线合成一次事务，减少 fsync
        self._store.set_meta_many(watermarks)

        total = len(collected)     # 截断前的总数，用来告诉用户"一共要找多少"
        if collected:
            limit = int(CONFIG.get("max_batch", 100))
            collected.sort(key=lambda x: x[1])
            if len(collected) > limit:
                # 补拉一次可能扫出几千个：先给最新的 limit 个，剩下的排队慢发。
                # 原来直接 emit(collected[-limit:]) 会把超出的直接丢掉 —— 那正是"补拉不全"。
                self._rescan_queue = collected[:-limit] + self._rescan_queue
                collected = collected[-limit:]
            self.new_files.emit(collected)
        # 队列每轮只补发一批（3 秒一轮 × max_batch 个），几千个也能在两三分钟内发完。
        # 本轮已经有新文件就不发，避免一次往面板里塞 200 张卡。
        if not collected and self._rescan_queue:
            limit = int(CONFIG.get("max_batch", 100))
            extra = self._rescan_queue[:limit]
            del self._rescan_queue[:limit]
            self.new_files.emit(extra)
        self.scan_finished.emit(time.monotonic() - started)
        return scanned, total

    def _scan(self, dirs: list[tuple[str, int]]) -> dict[str, list[tuple[str, int, int]]]:
        """一次 adb shell 扫完一组目录，返回 {目录: [(path, mtime, size)]}。"""
        if not dirs:
            return {}
        script: list[str] = []
        for path, depth in dirs:
            wm = self._watermark(path)
            script.append(f"echo '{_DIR_MARK}{path}'")
            script.append(
                f"find '{path}' -maxdepth {depth} -type f -newermt @{wm} "
                f"-printf '%T@|%s|%p\\n' 2>/dev/null"
            )
        # 走常驻会话：每轮省掉一次 adb 进程创建（实测 60~70ms），3 秒一轮差别明显
        out = self._adb.shell("; ".join(script), timeout=90, reuse=True)

        buckets: dict[str, list[tuple[str, int, int]]] = {}
        current: str | None = None
        for raw_line in out.splitlines():
            line = raw_line.strip()
            if line.startswith(_DIR_MARK):
                current = line[len(_DIR_MARK):].strip()
                buckets.setdefault(current, [])
                continue
            if current is None or not line:
                continue
            parts = line.split("|", 2)
            if len(parts) != 3:
                continue
            ts, size_text, remote = parts
            try:
                mtime = int(float(ts))
                size = int(size_text)
            except ValueError:
                continue
            buckets[current].append((remote, mtime, size))
        return buckets
