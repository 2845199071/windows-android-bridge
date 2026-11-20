"""全局配置：默认值 + 持久化到 %LOCALAPPDATA%\\手机电脑互通\\config.json。"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

APP_NAME = "Ping"                    # 显示名（托盘/窗口标题/面板标题）
APP_VERSION = "1.0.4"                # 版本号（设置页展示）
DATA_SLUG = "s2pc"                  # 数据目录名：刻意用 ASCII，避开 adb 的本地路径编码问题


def _local_appdata() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    return Path(base) if base else Path.home() / "AppData" / "Local"


DATA_DIR = _local_appdata() / DATA_SLUG
DATA_DIR.mkdir(parents=True, exist_ok=True)
CONFIG_PATH = DATA_DIR / "config.json"
DB_PATH = DATA_DIR / "data.db"
STAGING_DIR = DATA_DIR / "staging"
LOG_PATH = DATA_DIR / "app.log"
STAGING_DIR.mkdir(parents=True, exist_ok=True)


_staging_cache: tuple[str, Path] | None = None


def staging_dir() -> Path:
    """暂存目录——优先放在归档根同盘。

    实测：保存时 ``shutil.move`` 同盘走 rename 只要 1ms，跨盘（C→D）要 222ms。
    慢的原因不是磁盘（D 盘 440 MB/s），而是 Windows Defender 对**新建文件**的实时扫描，
    同盘 rename 不创建新文件，正好绕开。
    """
    global _staging_cache
    root = CONFIG.archive_root if "CONFIG" in globals() else None
    key = str(root) if root is not None else ""
    if _staging_cache is not None and _staging_cache[0] == key:
        return _staging_cache[1]

    result = STAGING_DIR
    if root is not None and root.drive:
        candidate = root / ".staging"
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            _hide(candidate)
            result = candidate
        except OSError:
            result = STAGING_DIR
    _staging_cache = (key, result)
    return result


def _hide(path: Path) -> None:
    """给目录加 Windows 隐藏属性，免得在归档目录里碍眼。失败就算了。"""
    if os.name != "nt":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)  # FILE_ATTRIBUTE_HIDDEN
    except Exception:
        pass


def find_adb() -> str:
    """按优先级探测 adb.exe；找不到则回落到 PATH 上的 adb。"""
    candidates: list[Path] = []
    # 打包成 exe 后，优先用程序自带/同目录的 adb —— 整个文件夹拷走就能用，
    # 朋友的电脑上不需要装 Android SDK。datas 进的是 _internal，所以两条都要查。
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).parent
        candidates += [
            exe_dir / "platform-tools" / "adb.exe",
            exe_dir / "adb.exe",
            exe_dir / "_internal" / "platform-tools" / "adb.exe",
        ]

    # 源码方式运行（启动.cmd 的兜底路径）：用仓库里自带的 platform-tools，
    # 和打包版是同一份 adb，这样开发机不需要另外装 Android SDK。
    candidates.append(
        Path(__file__).resolve().parent.parent / "packaging" / "platform-tools" / "adb.exe"
    )

    candidates += [
        # 环境变量（装过 Android Studio 一般会设）
        Path(os.environ.get("ANDROID_HOME", "\0")) / "platform-tools" / "adb.exe",
        Path(os.environ.get("ANDROID_SDK_ROOT", "\0")) / "platform-tools" / "adb.exe",
        # 常见安装位置：SDK Manager 默认在 %LOCALAPPDATA%\Android\Sdk；
        # 官方 platform-tools.zip 一般解压到 C:\platform-tools 或用户目录下
        _local_appdata() / "Android" / "Sdk" / "platform-tools" / "adb.exe",
        Path(r"C:\Android\platform-tools\adb.exe"),
        Path(r"C:\platform-tools\adb.exe"),
        Path.home() / "platform-tools" / "adb.exe",
    ]
    for cand in candidates:
        try:
            if cand.is_file():
                return str(cand)
        except OSError:
            continue
    from shutil import which

    return which("adb") or "adb"


# ---------------------------------------------------------------- 监听目录
# fast：每 3s 轮询，覆盖日常产文件的地方。实测 maxdepth 2 四个目录 1.14s。
FAST_DIRS: list[tuple[str, int]] = [
    # maxdepth 必须是 3：实测小米相册会落在 DCIM/Camera/Raw、DCIM/Pindd/save_image、
    # Pictures/WeiXin/<子目录> 这些第三层。depth=2 会漏掉 284 个 DCIM、208 个 Pictures 文件；
    # depth=4 只再多 5 个，纯属浪费（DCIM 有 1.7 万个文件，每深一层都要整树遍历）。
    ("/sdcard/DCIM", 3),
    ("/sdcard/Pictures", 3),
    ("/sdcard/Download", 3),
    ("/sdcard/Documents", 3),
]

# slow：每 15s 轮询，覆盖相册外和 App 接收目录。
# 注意：刻意不扫 /MicroMsg/<hash>/image —— 那是加密 .dat，且子目录上千（实测 5.4s）。
SLOW_DIRS: list[tuple[str, int]] = [
    ("/sdcard/Screenshots", 3),
    ("/sdcard/Movies", 3),
    ("/sdcard/Music", 3),
    ("/sdcard/Android/data/com.tencent.mobileqq/Tencent/QQfile_recv", 3),
    ("/sdcard/Android/data/com.tencent.mm/MicroMsg/Download", 3),
]

# 路径里出现这些片段就跳过（缩略图缓存等垃圾）
EXCLUDE_PATH_PARTS = (
    "/.thumbnails/",
    "/cache/",
    "/.trashed",
    "/Android/data/com.tencent.mm/micromsg/",
)
# 这些扩展名不弹（临时文件 / 微信加密图片）
EXCLUDE_EXTS = {".tmp", ".pending", ".part", ".crdownload", ".lock", ".dat", ".nomedia"}

# ---------------------------------------------------------------- 归档分类
TYPE_DIRS: dict[str, str] = {
    "图片": "图片",
    "视频": "视频",
    "音频": "音频",
    "文档": "文档",
    "其他": "其他",
}

EXT_TO_TYPE: dict[str, str] = {}
for _ext in "jpg jpeg png gif bmp webp heic heif tif tiff dng raw svg".split():
    EXT_TO_TYPE[_ext] = "图片"
for _ext in "mp4 mov avi mkv wmv flv 3gp m4v webm m2ts".split():
    EXT_TO_TYPE[_ext] = "视频"
for _ext in "mp3 wav flac aac ogg m4a amr wma opus".split():
    EXT_TO_TYPE[_ext] = "音频"
for _ext in (
    "pdf doc docx xls xlsx ppt pptx txt md csv tsv json xml html htm rtf epub "
    "zip rar 7z apk exe"
).split():
    EXT_TO_TYPE[_ext] = "文档"


def classify(filename: str) -> str:
    """按扩展名归类，未知归「其他」。"""
    ext = Path(filename).suffix.lower().lstrip(".")
    return EXT_TO_TYPE.get(ext, "其他")


# ---------------------------------------------------------------- 默认配置
DEFAULTS: dict = {
    "adb_path": "",                       # 空 = 自动探测
    "serial": "",                         # 空 = 自动选第一台
    "wifi_endpoint": "",                  # 如 192.168.X.XXX:5555，空 = 不用无线
    # 默认放在当前用户的文档目录下：发布给别人的电脑不一定有 D 盘，
    # 写死盘符会导致 mkdir 直接失败、保存不了任何文件。
    "archive_root": str(Path.home() / "手机归档"),
    "fast_interval": 3,                   # 秒
    "slow_interval": 15,                  # 秒
    "auto_expand": True,                  # 有新文件自动展开面板
    "auto_collapse_seconds": 5,           # 鼠标离开且没待处理文件时，多少秒后自动收起；0=不自动收
    "dock_width": 340,
    "dock_pos": [],                       # [x, y]，空 = 默认贴右上角；拖到哪记到哪
    "panel_alpha": 0.94,                  # 面板背景不透明度 0.35~1.0
    "panel_pinned": False,                # 固定显示（置顶）；默认不置顶，别挡别的 App
    "card_ttl_days": 3,                   # 卡片时效：超过 N 天没保存/忽略就自动忽略并清暂存
    "connect_mode": "auto",               # 无线连接方式：auto=自动重连（现状）/ manual=点「连接」按钮才连
    "remote_send_dir": "/sdcard/Download/电脑发送",
    # 改过发送目录后，旧目录仍要排除，否则里面发过的文件会被当成"新文件"弹回来
    "send_dir_history": [],
    "first_run_hours": 1,                 # 首次启动回溯小时数（避免把历史文件全弹出来）
    "offline_reset_hours": 12,            # 离线超过这么多小时再启动：只回看 first_run_hours，不灌积压
    "max_batch": 100,                     # 单轮最多处理多少个新文件
    # 大到这个程度就**不预下载**：先出卡片（名字/类型/大小），点保存时再下。
    # 手机上 200MB 的安装包很常见，预下载会白占磁盘和流量。0 = 全部按需下载。
    "lazy_pull_mb": 50,
    # 超过这个大小改用手机自带 netcat 直传（绕开 adb 的 TLS 瓶颈，实测快约 2.7 倍）。
    # netcat 有约 0.5s 固定启动开销，小文件不划算；设为 0 则全部走 adb pull（关闭加速）。
    "fast_pull_min_mb": 8,
    # 掉线自愈：曾连过设备、持续无设备超过阈值就自动重启 adb server（救活失活的 mDNS 后端）。
    # 10 分钟窗口内最多 2 次，避免用户主动关 WiFi 时反复重启。设 False 关闭。
    "adb_selfheal": True,
}


class Config:
    """薄封装：dict 语义 + 自动写盘。"""

    def __init__(self, path: Path = CONFIG_PATH) -> None:
        self._path = path
        self._data = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        if not self._path.is_file():
            self.save()
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self._data.update({k: v for k, v in raw.items() if k in DEFAULTS})
        except (OSError, ValueError):
            # 配置损坏就重建成默认值，不阻断启动
            self.save()

    def save(self) -> None:
        try:
            self._path.write_text(
                json.dumps(self._data, ensure_ascii=False, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def get(self, key: str, default=None):
        return self._data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value) -> None:
        self._data[key] = value
        self.save()

    def update(self, mapping: dict) -> None:
        self._data.update(mapping)
        self.save()

    # -- 派生属性
    @property
    def adb_path(self) -> str:
        return self.get("adb_path") or find_adb()

    @property
    def archive_root(self) -> Path:
        return Path(self.get("archive_root"))


CONFIG = Config()
