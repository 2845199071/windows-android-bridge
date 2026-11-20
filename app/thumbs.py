"""缩略图：图片走 QImageReader（自带 EXIF 方向），PDF 走 pypdfium2，
视频首帧和 Office 预览图借 Windows Shell（`shellthumb.py`），其余给色块。

图片和 PDF 用自己的解析器：更快，也不依赖系统装了什么。视频/文档则相反 ——
自己解不了，但系统通常有缩略图处理器，拿到的画面和资源管理器里一致。
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QRect, Qt
from PySide6.QtGui import QColor, QFont, QImage, QImageReader, QPainter, QPixmap, QPolygon

from config import classify

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp",
    ".heic", ".heif", ".tif", ".tiff", ".svg",
}
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".wmv", ".flv", ".3gp", ".m4v", ".webm"}
# Office/WPS 文档交给系统：装了 Office 或 WPS 就有预览图，没装就回落到色块
OFFICE_EXTS = {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".wps", ".et", ".dps"}

_KIND_COLORS = {
    "图片": "#4C8DFF",
    "视频": "#FF7A45",
    "音频": "#A855F7",
    "文档": "#12B76A",
    "其他": "#8A94A6",
}


def load_thumb(path: Path | str, size: int = 128) -> QPixmap | None:
    """尽力生成缩略图；失败返回 None，由调用方回落到 placeholder()。

    注意：走 Shell 的那条路依赖 COM，**必须在主线程调用**（Qt 已在主线程初始化
    了 COM 单元）。目前所有调用点都在 dock 里，天然是主线程。
    """
    p = Path(path)
    if not p.is_file():
        return None
    ext = p.suffix.lower()
    if ext in IMAGE_EXTS:
        return _image_thumb(p, size)
    if ext == ".pdf":
        return _pdf_thumb(p, size)
    if ext in VIDEO_EXTS or ext in OFFICE_EXTS:
        return _shell_thumb(p, size)
    return None


def _shell_thumb(path: Path, size: int) -> QPixmap | None:
    """取系统缩略图。按 2 倍尺寸请求再缩回来，边缘比直接小尺寸请求干净。"""
    try:
        import shellthumb

        image = shellthumb.thumbnail(str(path), size * 2)
    except Exception:
        return None
    if image is None or image.isNull():
        return None
    pixmap = QPixmap.fromImage(image)
    if pixmap.width() > size or pixmap.height() > size:
        pixmap = pixmap.scaled(size, size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
    return pixmap


def _image_thumb(path: Path, size: int) -> QPixmap | None:
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)  # 按 EXIF 摆正
    original = reader.size()
    if original.isValid() and original.width() > 0:
        reader.setScaledSize(original.scaled(size, size, Qt.KeepAspectRatio))
    image = reader.read()
    if image.isNull():
        return None
    return QPixmap.fromImage(image)


def _pdf_thumb(path: Path, size: int) -> QPixmap | None:
    try:
        import pypdfium2 as pdfium

        doc = pdfium.PdfDocument(str(path))
        if len(doc) < 1:
            return None
        page = doc[0]
        width_pt, height_pt = page.get_size()
        longest = max(width_pt, height_pt) or 1
        bitmap = page.render(scale=max(size / longest, 0.05), rev_byteorder=True)
        width, height = bitmap.width, bitmap.height
        raw = bytes(bitmap.buffer)
        if len(raw) < width * height * 3:
            return None
        image = QImage(raw, width, height, width * 3, QImage.Format_RGB888).copy()
        return QPixmap.fromImage(image)
    except Exception:
        return None


def placeholder(path: Path | str, size: int = 128) -> QPixmap:
    """按类型着色的方块，中间写扩展名；视频额外画一个播放三角。"""
    p = Path(path)
    ext = p.suffix.lower().lstrip(".").upper() or "FILE"
    kind = classify(p.name)
    color = QColor(_KIND_COLORS.get(kind, _KIND_COLORS["其他"]))

    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(Qt.NoPen)
    painter.setBrush(color)
    painter.drawRoundedRect(QRect(0, 0, size, size), size * 0.16, size * 0.16)

    painter.setPen(QColor(255, 255, 255, 235))
    font = QFont("Microsoft YaHei UI", max(9, int(size * 0.16)))
    font.setBold(True)
    painter.setFont(font)
    painter.drawText(pixmap.rect(), Qt.AlignCenter, ext[:4])

    if p.suffix.lower() in VIDEO_EXTS:
        center = size / 2
        radius = size * 0.14
        triangle = QPolygon(
            [
                _pt(center - radius * 0.6, center - radius),
                _pt(center - radius * 0.6, center + radius),
                _pt(center + radius * 0.9, center),
            ]
        )
        painter.setBrush(QColor(255, 255, 255, 230))
        painter.drawPolygon(triangle)

    painter.end()
    return pixmap


def _pt(x: float, y: float):
    from PySide6.QtCore import QPoint

    return QPoint(int(x), int(y))
