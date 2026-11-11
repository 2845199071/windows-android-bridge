r"""进程间通信：把「第二个实例」手里的文件交给正在跑的实例。

解决两个具体问题：

1. 资源管理器右键「发送到手机」多选几个文件时，Windows 的 shell 动词（纯注册表
   方案）会一个文件起一个进程。有了这条通道，后启动的进程把路径转给正在跑的
   那个，然后立刻退出。
2. 顺手替掉了原来的「第二个实例直接报错退出」—— 双击第二次不再白跑。

**为什么是本机 TCP 而不是 QLocalServer（命名管道）**：实测 QLocalServer 这条路
两个独立灾难 —— ① 客户端 QLocalSocket 的 ``flush()/waitForBytesWritten()`` 在
没有事件循环的子进程里全部失败（错误串只有 Unknown error），数据根本没出进程；
② 换成纯 Python 直写 ``\\.\pipe\<名>``，写是成功了，服务端 QLocalSocket 的
readyRead 却始终只有 0 字节。跨进程命名管道这条路上 Qt 的行为解释不了也不可控，
换成 ``127.0.0.1`` + 端口文件（DATA_DIR/ipc.port），两端都是普通 socket，
行为透明、可调试。端口随机分配、只绑回环地址，不引入任何外部可达面。
"""
from __future__ import annotations

import json
import logging
import socket
import threading

from PySide6.QtCore import QObject, Signal

from config import DATA_DIR

log = logging.getLogger("s2pc.ipc")

_PORT_FILE = DATA_DIR / "ipc.port"


def forward_quietly(payload: dict, timeout: float = 0.8) -> bool:
    """把消息投给正在运行的实例。连不上返回 False（说明没有实例在跑）。"""
    try:
        port = int(_PORT_FILE.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return False
    data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=timeout) as sock:
            sock.settimeout(timeout)
            sock.sendall(data)
            sock.shutdown(socket.SHUT_WR)   # 告诉对方"发完了"
            # 等对端关连接，确认消息真的被收下了
            while sock.recv(64):
                pass
    except (OSError, ValueError) as exc:
        log.debug("投递失败：%s", exc)
        return False
    return True


class IpcServer(QObject):
    """本机 TCP 服务端：收后续实例转来的 JSON，解出来就发信号。

    收发在独立线程里做（socket 阻塞读不能占 UI），JSON 解析完再用 Qt 信号
    抛回主线程 —— 跨线程 emit 会自动走队列连接，槽函数在主线程执行。
    """

    message = Signal(dict)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._sock: socket.socket | None = None
        self._thread: threading.Thread | None = None
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._sock.bind(("127.0.0.1", 0))       # 随机空闲端口
            self._sock.listen(4)
            port = self._sock.getsockname()[1]
            _PORT_FILE.write_text(str(port), encoding="ascii")
            self._thread = threading.Thread(target=self._serve, daemon=True)
            self._thread.start()
            log.info("IPC 监听 127.0.0.1:%d", port)
        except OSError as exc:
            log.warning("IPC 监听失败：%s", exc)

    def _serve(self) -> None:
        sock = self._sock
        if sock is None:
            return
        while True:
            try:
                conn, addr = sock.accept()
            except OSError:
                return          # close() 会关掉监听 socket，正常退出路径
            if addr[0] != "127.0.0.1":
                conn.close()    # 只信本机
                continue
            try:
                payload = self._read_json(conn)
                if payload:
                    self.message.emit(payload)
            except (OSError, ValueError) as exc:
                log.debug("接收失败：%s", exc)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    @staticmethod
    def _read_json(conn: socket.socket) -> dict:
        conn.settimeout(2.0)
        chunks: list[bytes] = []
        total = 0
        while total < 65536:
            chunk = conn.recv(4096)
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if b"\n" in chunk:
                break
        raw = b"".join(chunks).split(b"\n", 1)[0]
        if not raw:
            return {}
        try:
            payload = json.loads(raw.decode("utf-8", "replace"))
        except json.JSONDecodeError:
            log.warning("IPC 收到无法解析的消息")
            return {}
        return payload if isinstance(payload, dict) else {}

    def close(self) -> None:
        if self._sock is not None:
            try:
                self._sock.close()
            except OSError:
                pass
            self._sock = None
        try:
            _PORT_FILE.unlink()
        except OSError:
            pass
