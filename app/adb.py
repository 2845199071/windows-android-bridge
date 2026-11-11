"""adb 通道：设备发现、shell、pull、push。

约定：
* 所有调用都用 ``subprocess`` 传 list，绝不拼接 shell 字符串 —— 避免路径里
  的空格/中文/引号被解释。
* Git Bash 下 ``adb push x /sdcard/a`` 会把远程路径改写成
  ``.../PortableGit/sdcard/a``；Python 直调 adb.exe 不受影响，但环境变量仍
  显式设置，防止被别处的 shell 包装层影响。
"""
from __future__ import annotations

import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
import logging
from dataclasses import dataclass
from pathlib import Path

from config import CONFIG

log = logging.getLogger("s2pc.adb")

_CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0

# 父进程是 pythonw（GUI，无控制台）时，光靠 CREATE_NO_WINDOW 并不总能挡住黑框，
# 尤其是 Windows 11 让 Windows Terminal 接管默认终端之后。再补两层：
#   DETACHED_PROCESS  —— 子进程完全不关联任何控制台
#   STARTF_USESHOWWINDOW + SW_HIDE —— 就算建了也强制隐藏
# 三者合用基本可以保证 adb 静默运行。
_DETACHED_PROCESS = 0x00000008
if os.name == "nt":
    _STARTUPINFO = subprocess.STARTUPINFO()
    _STARTUPINFO.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    _STARTUPINFO.wShowWindow = subprocess.SW_HIDE
    _SPAWN_KW: dict = {
        "creationflags": _CREATE_NO_WINDOW | _DETACHED_PROCESS,
        "startupinfo": _STARTUPINFO,
    }
else:
    _SPAWN_KW: dict = {}

# Android 11+ 无线调试的 mDNS 服务名后缀。配对过的手机会以
# "adb-<序列号>-<随机>._adb-tls-connect._tcp" 的形式自动出现在 adb devices 里，
# 不需要手工 adb connect —— 换 WiFi、IP 变了都能自己跟上。
_MDNS_SUFFIXES = ("_adb-tls-connect._tcp", "_adb-tls-pairing._tcp")


def _is_mdns(serial: str) -> bool:
    return any(serial.endswith(suffix) for suffix in _MDNS_SUFFIXES)


def _quote(path: str) -> str:
    """转义成 shell 单引号字符串（手机端 sh 用）。"""
    return path.replace("'", "'\\''")


class AdbError(RuntimeError):
    """adb 调用失败（找不到可执行文件、超时、无设备等）。"""


class _ShellSession:
    """一条常驻的 ``adb shell -T`` 进程，用来省掉每次调用的进程开销。

    实测每次 ``adb shell`` 光进程创建 + 与本机 adb server 握手就要 **60~70ms**
    （``adb version`` 中位 70.6ms），而监听器每 3 秒就要扫一轮。改成常驻后，
    每轮只剩手机端 ``find`` 的真实耗时。

    协议：把命令写进 stdin，紧跟一条 ``echo <本次会话专属的哨兵>``，
    读到哨兵行就算这条命令结束。哨兵带随机串，不会和文件路径撞上。
    ``-T`` 关掉 PTY：不分配终端就不会回显命令、也不会把长路径按 80 列折断。
    """

    def __init__(self, argv: list[str], env: dict) -> None:
        # bufsize 必须用默认（-1）。设成 0 时 stdout 是裸 FileIO，
        # readline() 会退化成逐字节读 —— 几千行输出就是上百万次系统调用，
        # 实测 CPU 从 22ms 涨到 1500ms/轮。写入本来就有显式 flush，不需要 0。
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=env,
            **_SPAWN_KW,
        )
        self._marker = f"__S2PC_{uuid.uuid4().hex[:12]}__"
        self._queue: queue.Queue[bytes | None] = queue.Queue()
        self._closed = False
        threading.Thread(target=self._pump, daemon=True).start()

    @property
    def marker(self) -> str:
        return self._marker

    def _pump(self) -> None:
        """后台把 stdout 逐行丢进队列，主线程带超时取，避免 readline 卡死。"""
        try:
            for raw in iter(self._proc.stdout.readline, b""):
                self._queue.put(raw)
        except (OSError, ValueError):
            pass
        finally:
            self._queue.put(None)   # EOF 标记

    def run(self, command: str, timeout: float) -> str:
        if self._closed:
            raise AdbError("shell 会话已关闭")
        payload = f"{command}\necho {self._marker}\n".encode()
        try:
            self._proc.stdin.write(payload)
            self._proc.stdin.flush()
        except (OSError, ValueError) as exc:
            self.close()
            raise AdbError(f"向手机写入失败：{exc}") from exc

        lines: list[str] = []
        deadline = time.monotonic() + timeout
        while True:
            remain = deadline - time.monotonic()
            if remain <= 0:
                self.close()   # 挂住的会话不能留着，否则后面每条命令都会跟着卡
                raise AdbError(f"shell 超时 {timeout:.0f}s")
            try:
                raw = self._queue.get(timeout=remain)
            except queue.Empty:
                self.close()
                raise AdbError(f"shell 超时 {timeout:.0f}s")
            if raw is None:
                self.close()
                raise AdbError("shell 会话意外结束（设备可能已断开）")
            text = raw.decode("utf-8", "replace").rstrip("\r\n")
            if text.strip() == self._marker:
                break
            lines.append(text)
        # 保持和一次性进程一致的行尾（那一边 adb 走 PTY 会给 \r\n），
        # 否则同一批命令两条路径输出的字节数不同，比对和解析都得跟着分叉。
        return ("\n".join(lines) + "\n") if lines else ""

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for closer in (
            lambda: self._proc.stdin.close(),
            lambda: self._proc.terminate(),
        ):
            try:
                closer()
            except (OSError, ValueError):
                pass
        try:
            self._proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                self._proc.kill()
            except OSError:
                pass


@dataclass(frozen=True)
class Device:
    serial: str
    state: str
    model: str = ""
    transport: str = "usb"  # usb | wifi

    @property
    def label(self) -> str:
        return self.model or self.serial


def _unique_name(name: str, existing: set[str]) -> str:
    """手机端已有同名文件时加 `` (n)``，避免 mv 覆盖。"""
    if name not in existing:
        return name
    stem = Path(name).stem
    suffix = Path(name).suffix
    for index in range(1, 1000):
        candidate = f"{stem} ({index}){suffix}"
        if candidate not in existing:
            return candidate
    return f"{stem} ({uuid.uuid4().hex[:6]}){suffix}"


def _decode(raw: bytes) -> str:
    """adb 输出可能是 UTF-8，也可能是中文 Windows 的 GBK，逐级回落。"""
    for enc in ("utf-8", "gbk"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", "replace")


class AdbChannel:
    def __init__(self, adb_path: str | None = None) -> None:
        self.adb_path = adb_path or CONFIG.adb_path
        self._env = dict(os.environ)
        self._env["MSYS_NO_PATHCONV"] = "1"   # 防 MSYS 改写 /sdcard/...
        self._serial: str = CONFIG.get("serial") or ""
        self._recorded_serial: str = ""
        self._session: _ShellSession | None = None
        self._session_serial: str = ""
        self._session_lock = threading.Lock()
        # adb server 会对「已配对」的 mDNS 设备自动回连（实测断开+重启 server 后
        # 3 秒内自己连回来）。手动连接模式必须关掉它，否则"点了才连"形同虚设。
        # 这个环境变量只在 server 启动时生效，所以切换时必须重启 server。
        self._server_auto_connect: bool | None = None

    def set_mdns_auto_connect(self, auto: bool, restart: bool | None = None) -> None:
        """控制 adb server 是否自动回连已配对的无线设备。

        - 自动模式（auto=True）：保持 adb 默认行为（自动回连最快，我们的显式
          connect 照常工作）；
        - 手动模式（auto=False）：给 server 传 ``ADB_MDNS_AUTO_CONNECT=0``，
          手机息屏再亮屏 server 也不会自己连回来，只有点「连接」按钮才连。

        环境变量只在 **server 启动时**生效——server 已经在跑就必须 kill 后带
        环境重启（会清掉所有现存连接；自动模式几秒内自愈，手动模式正合意图）。
        ``restart=None`` 时按需判断：进手动、或从手动退出时才重启；
        传 True/False 可强制（测试用 False 只改环境不发命令）。
        """
        auto = bool(auto)
        if restart is None:
            restart = (not auto) or (self._server_auto_connect is False)
        self._env["ADB_MDNS_AUTO_CONNECT"] = "1" if auto else "0"
        self._server_auto_connect = auto
        if not restart:
            return
        self.close_sessions()
        self._serial = ""
        try:
            self._run(["kill-server"], timeout=10)
            self._run(["start-server"], timeout=15)
            log.info("adb server 已重启（mDNS 自动回连：%s）", "开" if auto else "关")
        except AdbError as exc:
            log.warning("重启 adb server 失败（手动模式下 server 自动回连可能仍然生效）：%s", exc)

    # ------------------------------------------------------------------ 底层
    def _run(self, args: list[str], timeout: float = 25) -> subprocess.CompletedProcess:
        # DETACHED_PROCESS 是关键：父进程是 pythonw 时，光有 CREATE_NO_WINDOW
        # 仍会创建 conhost（实测压测 25 次冒出 56 个控制台宿主），加它之后为 0。
        log.debug("adb %s", " ".join(args)[:150])
        try:
            return subprocess.run(
                [self.adb_path, *args],
                capture_output=True,
                timeout=timeout,
                env=self._env,
                **_SPAWN_KW,
            )
        except FileNotFoundError as exc:
            raise AdbError(f"找不到 adb：{self.adb_path}") from exc
        except subprocess.TimeoutExpired as exc:
            raise AdbError(f"adb 超时 {timeout:.0f}s：{' '.join(args)}") from exc
        except OSError as exc:
            raise AdbError(f"启动 adb 失败：{exc}") from exc

    @property
    def serial(self) -> str:
        return self._serial

    # 这些是 adb 自己报的"设备不可用"，不是命令本身失败。识别出来就能立刻
    # 判定掉线（监听器 3 秒内就知道），而不是当成"这条命令没有输出"。
    # 注意 adb 的实际措辞带序列号：``device '192.168.1.113:42663' not found``，
    # 所以不能写死成 "device not found"。
    _GONE_RE = re.compile(
        r"(device .*not found|device offline|device unauthorized|no devices|"
        r"error: closed|connection reset|protocol fault)",
        re.IGNORECASE,
    )

    def _raise_if_gone(self, proc: subprocess.CompletedProcess) -> None:
        if proc.returncode == 0:
            return
        err = _decode(proc.stderr).strip()
        if err and self._GONE_RE.search(err):
            self._serial = ""
            self.close_sessions()
            raise AdbError(f"设备已断开：{err.splitlines()[0][:120]}")

    # ------------------------------------------------------------------ 设备
    def devices(self) -> list[Device]:
        proc = self._run(["devices", "-l"], timeout=10)
        found: list[Device] = []
        for line in _decode(proc.stdout).splitlines():
            line = line.strip()
            if not line or line.startswith(("List of devices", "*", "adb ")):
                continue
            parts = line.split()
            if len(parts) < 2:
                continue
            serial, state = parts[0], parts[1]
            model = ""
            for token in parts[2:]:
                if token.startswith("model:"):
                    model = token.split(":", 1)[1]
            if _is_mdns(serial) or re.search(r":\d+$", serial):
                transport = "wifi"
            else:
                transport = "usb"
            found.append(Device(serial, state, model, transport))
        return found

    def connect(self, endpoint: str, timeout: float = 12) -> bool:
        try:
            proc = self._run(["connect", endpoint], timeout=timeout)
        except AdbError:
            return False
        out = (_decode(proc.stdout) + _decode(proc.stderr)).lower()
        return "connected to" in out and "cannot" not in out

    def disconnect(self, endpoint: str) -> None:
        try:
            self._run(["disconnect", endpoint], timeout=8)
        except AdbError:
            pass

    def pair(self, endpoint: str, code: str, timeout: float = 30) -> tuple[bool, str]:
        """无线调试配对。

        endpoint 是手机上「使用配对码配对设备」弹框里显示的地址，
        **注意它和无线调试主页显示的连接端口不是同一个**。code 是 6 位配对码。
        配对是一次性的：成功后手机会被记住，以后靠 mDNS 自动连，不用再配对。
        """
        endpoint = endpoint.strip()
        code = code.strip()
        if not re.match(r"^[\d.]+:\d+$", endpoint):
            return False, "地址要填成 IP:端口，例如 192.168.1.103:40061"
        if not re.match(r"^\d{6}$", code):
            return False, "配对码是 6 位数字"
        try:
            proc = self._run(["pair", endpoint, code], timeout=timeout)
        except AdbError as exc:
            return False, str(exc)
        out = (_decode(proc.stdout) + _decode(proc.stderr)).strip()
        if "Successfully paired" in out:
            return True, out.splitlines()[0]
        return False, out or "配对失败（配对码可能已过期，重新点一次配对）"

    def ensure_device(self, allow_connect: bool = True) -> Device | None:
        """挑一台可用设备。优先级：USB > mDNS 自动发现 > 手工填的 IP:端口。

        ``allow_connect=False`` 是**被动模式**（手动连接模式用）：只看当前已经
        连上的设备，绝不主动发起 ``adb connect``——手机息屏掉线后就保持未连接，
        等用户点面板上的「连接」按钮再调 ``ensure_device()``（默认参数）重连。
        """
        alive = [d for d in self.devices() if d.state == "device"]

        # 没有设备时先试手工指定的地址（用户在设置里填过 IP:端口的情况）
        if not alive and allow_connect:
            endpoint = (CONFIG.get("wifi_endpoint") or "").strip()
            if endpoint and self.connect(endpoint):
                alive = [d for d in self.devices() if d.state == "device"]

        # 再试 mDNS：配对过的手机会自动广播，但 adb 刚启动时可能还没发现完
        if not alive and allow_connect:
            for endpoint in self._mdns_endpoints():
                if self.connect(endpoint):
                    alive = [d for d in self.devices() if d.state == "device"]
                    if alive:
                        break

        if not alive:
            self._serial = ""
            self.close_sessions()      # 设备没了，常驻会话也成了空壳
            return None

        preferred = (CONFIG.get("serial") or "").strip()
        chosen = next((d for d in alive if d.serial == preferred), None)
        if chosen is None:
            usb = [d for d in alive if d.transport == "usb"]
            if usb:
                chosen = usb[0]
            else:
                # 无线有多条时优先 mDNS：手机换 IP 后它能自己跟上，写死的 IP:端口会失效
                mdns = [d for d in alive if _is_mdns(d.serial)]
                chosen = mdns[0] if mdns else alive[0]
        if chosen.serial != self._serial:
            self.close_sessions()      # 换了设备，旧会话指向另一台手机
        self._serial = chosen.serial
        # 无线连上时把 mDNS 播出来的地址记进 wifi_endpoint，作为断线后的兜底：
        # 手机息屏后 mDNS 广播可能停，但 adb 端口还开着，直连这个地址能救回来。
        if chosen.transport != "usb" and chosen.serial != self._recorded_serial:
            self._recorded_serial = chosen.serial
            endpoints = self._mdns_endpoints()
            if endpoints and endpoints[0] != (CONFIG.get("wifi_endpoint") or ""):
                CONFIG.set("wifi_endpoint", endpoints[0])
        return chosen

    def _mdns_endpoints(self) -> list[str]:
        """列出 mDNS 广播出来的无线调试地址（ip:port）。"""
        try:
            proc = self._run(["mdns", "services"], timeout=10)
        except AdbError:
            return []
        found: list[str] = []
        for line in _decode(proc.stdout).splitlines():
            if "_adb-tls-connect._tcp" not in line:
                continue
            parts = line.split()
            if len(parts) >= 3 and re.match(r"^[\d.]+:\d+$", parts[-1]):
                found.append(parts[-1])
        return found

    # ------------------------------------------------------------------ 文件
    def notify_phone(self, title: str, text: str) -> bool:
        """在手机上发一条系统通知（cmd notification post，以 shell 账号，无需装 app）。

        Android 8+ 自带该 shell 命令；不同 ROM 可能没有或被裁剪——失败静默返回
        False，绝不影响发送本身。通知展示为系统账号 "Shell"，这是预期行为。
        """
        if not self._serial:
            return False
        esc = lambda s: s.replace("'", "")   # 通知文案不带引号最稳
        title, text = esc(title), esc(text)
        try:
            proc = self._run(
                ["-s", self._serial, "shell",
                 f"cmd notification post -S bigtext -t '{title}' s2pc '{text}'"],
                timeout=12,
            )
            ok = "Error" not in _decode(proc.stdout)
            if ok:
                log.info("手机通知已发送：%s / %s", title, text)
            else:
                log.debug("手机通知被 ROM 拒绝：%s", _decode(proc.stdout).strip())
            return ok
        except AdbError as exc:
            log.debug("手机通知失败：%s", exc)
            return False

    def shell(self, command: str, timeout: float = 30, reuse: bool = False) -> str:
        """在手机上执行一条命令。

        ``reuse=True`` 走常驻会话（每轮省 60~70ms 进程开销，监听器用这个）；
        默认仍是一次性进程 —— 用户手动触发的操作（浏览目录、发送文件）不占用
        会话锁，也不会因为会话异常影响功能。
        """
        if not self._serial:
            raise AdbError("没有可用设备")
        if reuse:
            return self._shell_reused(command, timeout)
        proc = self._run(["-s", self._serial, "shell", command], timeout=timeout)
        self._raise_if_gone(proc)
        return _decode(proc.stdout).replace("\r\n", "\n")

    # ------------------------------------------------------------------ 常驻会话
    def _shell_reused(self, command: str, timeout: float) -> str:
        with self._session_lock:
            session = self._session
            if session is not None and self._session_serial != self._serial:
                session.close()          # 换设备了，旧会话指向另一台手机
                session = None
            if session is None:
                session = self._open_session()
            try:
                return session.run(command, timeout)
            except AdbError as exc:
                log.debug("常驻会话失败，改用一次性进程：%s", exc)
                self._session = None
        # 会话坏了不要连累功能：退回一次性的 adb shell，行为与优化前完全一致
        proc = self._run(["-s", self._serial, "shell", command], timeout=timeout)
        self._raise_if_gone(proc)
        return _decode(proc.stdout).replace("\r\n", "\n")

    def _open_session(self) -> _ShellSession:
        argv = [self.adb_path, "-s", self._serial, "shell", "-T"]
        try:
            session = _ShellSession(argv, self._env)
        except FileNotFoundError as exc:
            raise AdbError(f"找不到 adb：{self.adb_path}") from exc
        except OSError as exc:
            raise AdbError(f"启动 shell 会话失败：{exc}") from exc
        self._session = session
        self._session_serial = self._serial
        log.debug("已建立常驻 shell 会话 %s", session.marker)
        return session

    def close_sessions(self) -> None:
        with self._session_lock:
            if self._session is not None:
                self._session.close()
                self._session = None

    def pull(self, remote: str, local: Path, timeout: float = 900) -> bool:
        if not self._serial:
            raise AdbError("没有可用设备")
        local.parent.mkdir(parents=True, exist_ok=True)
        proc = self._run(["-s", self._serial, "pull", remote, str(local)], timeout=timeout)
        return proc.returncode == 0 and local.is_file()

    def push(self, local: Path, remote_dir: str, timeout: float = 900) -> bool:
        """把本地文件推到手机目录，保留原文件名（含中文/空格）。

        坑：Windows 版 adb 用 ANSI 处理**本地**路径，中文名会被截断甚至直接失败
        （实测「混合abc中文123.dat」推上去变成「混合abc中文1」）。所以这里先把
        文件复制成 ASCII 临时名再推，推完在手机端 mv 回原名 —— mv 走 adb shell，
        全程 UTF-8，不会失真。
        """
        if not self._serial:
            raise AdbError("没有可用设备")
        local = Path(local)
        if not local.is_file():
            raise AdbError(f"本地文件不存在：{local}")

        target_dir = remote_dir.rstrip("/") + "/"
        self.shell(f"mkdir -p '{_quote(target_dir)}'", timeout=15)

        # 目标目录已有同名文件时自动加序号，不覆盖
        try:
            existing = set(self.shell(f"ls -1 '{_quote(target_dir)}' 2>/dev/null").splitlines())
        except AdbError:
            existing = set()
        final_name = _unique_name(local.name, existing)

        suffix = local.suffix.lower()
        ascii_name = f"s2pc_{uuid.uuid4().hex}{suffix}"
        tmp_dir = Path(tempfile.mkdtemp(prefix="s2pc_push_"))
        staged = tmp_dir / ascii_name
        try:
            shutil.copy2(local, staged)
            remote_tmp = target_dir + ascii_name
            proc = self._run(["-s", self._serial, "push", str(staged), remote_tmp], timeout=timeout)
            if proc.returncode != 0:
                return False
            if final_name == ascii_name:
                return True
            final_path = target_dir + final_name
            out = self.shell(
                f"mv '{_quote(remote_tmp)}' '{_quote(final_path)}' && echo __S2PC_OK__",
                timeout=60,
            )
            return "__S2PC_OK__" in out
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

    def delete(self, remote: str) -> bool:
        try:
            self.shell(f"rm -f '{remote}'", timeout=15)
            return True
        except AdbError:
            return False

    # ------------------------------------------------------------------ 信息
    def wifi_ip(self) -> str:
        try:
            out = self.shell("ip route 2>/dev/null | grep wlan0", timeout=10)
        except AdbError:
            return ""
        match = re.search(r"src (\d{1,3}(?:\.\d{1,3}){3})", out)
        return match.group(1) if match else ""

    def android_info(self) -> tuple[str, str]:
        """返回 (版本号, SDK)。取不到就返回空串。"""
        try:
            rel = self.shell("getprop ro.build.version.release", timeout=10).strip()
            sdk = self.shell("getprop ro.build.version.sdk", timeout=10).strip()
            return rel, sdk
        except AdbError:
            return "", ""

    def latest_mtime(self, dirs: list[tuple[str, int]]) -> int:
        """用于首次启动时把水位线设到「最近的已有文件」，避免把历史文件全弹出来。"""
        if not dirs:
            return 0
        cmds = []
        for path, depth in dirs:
            cmds.append(
                f"find '{path}' -maxdepth {depth} -type f -printf '%T@\\n' 2>/dev/null"
            )
        try:
            out = self.shell("; ".join(cmds), timeout=40)
        except AdbError:
            return 0
        stamps = []
        for line in out.splitlines():
            line = line.strip()
            if line:
                try:
                    stamps.append(int(float(line)))
                except ValueError:
                    continue
        return max(stamps) if stamps else 0
