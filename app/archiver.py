"""归档：staging → <归档根>/<类型>/<日期>/<原文件名>。"""
from __future__ import annotations

import re
import shutil
import time
from pathlib import Path

from config import CONFIG, TYPE_DIRS, classify

_ILLEGAL = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_filename(name: str) -> str:
    """去掉 Windows 不允许的字符；手机文件名理论上没这些，但外部来源不可信。"""
    cleaned = _ILLEGAL.sub("_", name).strip().rstrip(".")
    return cleaned or "unnamed"


def unique_path(path: Path) -> Path:
    """重名自动加 `` (n)``，不覆盖已有文件。"""
    if not path.exists():
        return path
    stem, suffix, parent = path.stem, path.suffix, path.parent
    for index in range(1, 1000):
        candidate = parent / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    return parent / f"{stem} ({int(time.time())}){suffix}"


def archive_path(filename: str, mtime: int) -> Path:
    root = CONFIG.archive_root
    kind = classify(filename)
    stamp = time.localtime(mtime or time.time())
    date_dir = time.strftime("%Y-%m-%d", stamp)
    return unique_path(root / TYPE_DIRS[kind] / date_dir / safe_filename(filename))


def archive_file(staging: str | Path, filename: str, mtime: int) -> Path:
    """把暂存文件移动到归档目录，返回最终路径。"""
    src = Path(staging)
    if not src.is_file():
        raise FileNotFoundError(f"暂存文件不存在：{src}")
    dest = archive_path(filename, mtime)
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    return dest


def send_target_dir() -> str:
    return str(CONFIG.get("remote_send_dir") or "/sdcard/Download/电脑发送")


def human_size(num: int) -> str:
    value = float(num)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} GB"
