"""右侧贴边悬浮窗：卡片流 + 投放区 + 收起态窄条。

窗口用 ``Qt.Tool`` + 无边框 + 置顶，确保不占任务栏、不被主窗口遮挡。
收起时窗口宽度缩到 48px，变成一条显示未读数的窄条，点一下展开。
"""
from __future__ import annotations

import time
from pathlib import Path
from string import Template

from PySide6.QtCore import QPoint, QRect, QSize, Qt, QTimer, Signal
from PySide6.QtCore import QMimeData, QPoint, QPointF, QRect, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QColor,
    QDrag,
    QIcon,
    QCursor,
    QFontMetrics,
    QGuiApplication,
    QPainter,
    QPen,
    QPixmap,
    QPolygonF,
)

_PIN_ICON_CACHE: dict[tuple[str, bool], QIcon] = {}


def _pin_icon(color: str, filled: bool) -> QIcon:
    """画一个图钉图标：未固定用灰色轮廓，固定后用白色实心（配蓝底）。

    不用 📌 emoji —— 它是彩色字形（红色），颜色改不了，用户明确不要红色。
    形状：扁圆头 + 颈 + 收尖的针，勾线/填充由 filled 决定。
    """
    key = (color, filled)
    if key in _PIN_ICON_CACHE:
        return _PIN_ICON_CACHE[key]

    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    c = QColor(color)
    p.setPen(QPen(c, 2.0))
    p.setBrush(c if filled else Qt.NoBrush)
    # 头（扁圆角矩形）
    p.drawRoundedRect(QRectF(9.5, 4.5, 13, 5.5), 2.5, 2.5)
    # 颈 + 针：一个收尖的五边形，勾线和填充都不断开
    body = QPolygonF([
        QPointF(13.2, 10.0),
        QPointF(18.8, 10.0),
        QPointF(18.8, 16.5),
        QPointF(16.0, 27.0),
        QPointF(13.2, 16.5),
    ])
    p.drawPolygon(body)
    p.end()
    icon = QIcon(pm)
    _PIN_ICON_CACHE[key] = icon
    return icon
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from archiver import human_size
from config import APP_NAME, CONFIG
from store import PendingItem
from thumbs import load_thumb, placeholder
from ui_theme import dock_palette, dock_style, is_dark, theme_icon

_KIND_RULES = [
    ("视", "#FF9F43", {".mp4", ".mov", ".mkv", ".avi", ".3gp", ".webm", ".m4v", ".ts"}),
    ("音", "#7B61FF", {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg", ".amr"}),
    ("文", "#34C77B", {".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
                      ".txt", ".md", ".csv", ".json", ".xml"}),
    ("包", "#94A3B8", {".zip", ".rar", ".7z", ".tar", ".gz", ".apk"}),
]


def _kind_of(filename: str) -> tuple[str, str]:
    """(角标文字, 颜色)：图片不标（缩略图自己会说话），其余给个类型角标。"""
    suffix = Path(filename).suffix.lower()
    for label, color, exts in _KIND_RULES:
        if suffix in exts:
            return label, color
    return "", ""


def _elide_name(name: str, limit: int = 42) -> str:
    """长文件名截断，尽量保住扩展名 —— .jpg / .xlsx 这种信息比中间那几个字重要。"""
    if len(name) <= limit:
        return name
    dot = name.rfind(".", 0, limit)
    return name[: dot if dot > 8 else limit] + "…"


def _place_preview(size: QSize, anchor: QPoint) -> QPoint:
    """预览窗定位：优先放光标左侧（面板贴在屏幕右边），贴边自动翻转，保证不越界。"""
    screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
    geo: QRect = screen.availableGeometry() if screen else QRect(0, 0, 1920, 1080)
    x = anchor.x() - size.width() - 18
    if x < geo.left() + 8:
        x = anchor.x() + 18
    if x + size.width() > geo.right() - 8:
        x = geo.right() - size.width() - 8
    y = anchor.y() - size.height() // 2
    y = max(geo.top() + 8, min(y, geo.bottom() - size.height() - 8))
    return QPoint(x, y)


class PreviewPopup(QWidget):
    """鼠标悬停在卡片上弹出的大图预览。

    用 ``WA_TransparentForMouseEvents``：预览窗不吃鼠标事件，否则鼠标一移到它上面，
    卡片就会收到 leaveEvent，预览立刻消失。
    """

    MAX_W = 360
    MAX_H = 300

    def __init__(self) -> None:
        super().__init__(None, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setAttribute(Qt.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WA_TransparentForMouseEvents)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        panel = QFrame(self)
        panel.setObjectName("preview")
        outer.addWidget(panel)

        inner = QVBoxLayout(panel)
        inner.setContentsMargins(10, 10, 10, 10)
        inner.setSpacing(6)

        self.image_label = QLabel(panel)
        self.image_label.setObjectName("pvImage")
        self.image_label.setAlignment(Qt.AlignCenter)
        inner.addWidget(self.image_label)

        self.name_label = QLabel(panel)
        self.name_label.setObjectName("pvName")
        self.name_label.setWordWrap(False)
        inner.addWidget(self.name_label)

        self.info_label = QLabel(panel)
        self.info_label.setObjectName("pvInfo")
        inner.addWidget(self.info_label)

        self.setStyleSheet(dock_style(0.98, is_dark()))

    def show_for(self, item: PendingItem, anchor: QPoint) -> None:
        pixmap = load_thumb(item.staging, 384)
        if pixmap is None:
            pixmap = placeholder(item.filename, 150)
        scaled = pixmap.scaled(self.MAX_W, self.MAX_H, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self.image_label.setPixmap(scaled)
        self.image_label.setFixedSize(max(scaled.width(), 240), max(scaled.height(), 160))

        self.name_label.setText(_elide_name(item.filename))
        self.name_label.setMaximumWidth(max(240, scaled.width()))
        stamp = time.strftime("%m-%d %H:%M", time.localtime(item.mtime or time.time()))
        self.info_label.setText(f"{human_size(item.size)} · {stamp}")

        self.adjustSize()
        self.move(_place_preview(self.size(), anchor))
        self.show()
        self.raise_()


class CardWidget(QFrame):
    save_requested = Signal(str)
    ignore_requested = Signal(str)
    preview_requested = Signal(str)
    hover_entered = Signal(str)   # 悬停够久，请求弹预览
    hover_left = Signal()
    drag_out = Signal(str)        # 拖拽已结束（宿主决定移除卡片）
    drag_started = Signal()       # 正在拖（宿主暂停自动收起，别把面板收了）

    THUMB = 88

    def __init__(self, item: PendingItem, thumb: QPixmap | None, parent=None,
                 drag_source=None) -> None:
        super().__init__(parent)
        self.item = item
        # drag_source(item_id) -> 归档后的本地路径（拖出去给别的程序用）
        self._drag_source = drag_source
        self._press_pos: QPoint | None = None
        self.setObjectName("card")
        self.setFixedHeight(self.THUMB + 20)
        self.setToolTip("拖到微信 / 文件夹 / 其他程序直接使用（会自动归档保存）")

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 10, 10, 10)
        row.setSpacing(10)

        self.thumb_label = QLabel(self)
        self.thumb_label.setFixedSize(self.THUMB, self.THUMB)
        self.thumb_label.setAlignment(Qt.AlignCenter)
        self.set_dark_style(is_dark())
        pixmap = thumb or placeholder(item.filename, self.THUMB * 2)
        self.thumb_label.setPixmap(
            pixmap.scaled(self.THUMB, self.THUMB, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
        kind_text, kind_color = _kind_of(item.filename)
        if kind_text:
            self.kind_badge = QLabel(kind_text, self.thumb_label)
            self.kind_badge.setObjectName("kindBadge")
            self.kind_badge.setAlignment(Qt.AlignCenter)
            self.kind_badge.setFixedSize(20, 18)
            self.kind_badge.move(self.THUMB - 24, self.THUMB - 22)
            self.kind_badge.setStyleSheet(
                f"background:{kind_color}; color:#FFFFFF; border-radius:5px;"
                "font: 10px 'Microsoft YaHei UI';"
            )
        row.addWidget(self.thumb_label)

        right = QVBoxLayout()
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(4)

        self.name_label = QLabel(self)
        self.name_label.setObjectName("fname")
        self.update_name_width(int(CONFIG.get("dock_width", 340)))
        self.name_label.setToolTip(item.remote_path)
        right.addWidget(self.name_label)

        stamp = time.strftime("%m-%d %H:%M", time.localtime(item.mtime or time.time()))
        self.meta_label = QLabel(f"{human_size(item.size)} · {stamp}", self)
        self.meta_label.setObjectName("meta")
        right.addWidget(self.meta_label)

        right.addStretch(1)

        buttons = QHBoxLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setSpacing(6)
        self.save_btn = QPushButton("保存", self)
        self.save_btn.setObjectName("save")
        self.save_btn.setCursor(Qt.PointingHandCursor)
        self.save_btn.clicked.connect(lambda: self.save_requested.emit(self.item.id))
        self.ignore_btn = QPushButton("忽略", self)
        self.ignore_btn.setObjectName("ignore")
        self.ignore_btn.setCursor(Qt.PointingHandCursor)
        self.ignore_btn.clicked.connect(lambda: self.ignore_requested.emit(self.item.id))
        buttons.addWidget(self.save_btn)
        buttons.addWidget(self.ignore_btn)
        buttons.addStretch(1)
        right.addLayout(buttons)

        row.addLayout(right, 1)

        # 悬停 0.42 秒才弹预览：鼠标划过列表时不会一路乱闪
        self._hover_timer = QTimer(self)
        self._hover_timer.setSingleShot(True)
        self._hover_timer.setInterval(420)
        self._hover_timer.timeout.connect(lambda: self.hover_entered.emit(self.item.id))


    def set_dark_style(self, dark: bool) -> None:
        """缩略图占位底色：浅色 #F1F3F7 / 暗夜 #1B2330（主题切换时逐卡刷新）。"""
        palette = dock_palette(dark)
        self.thumb_label.setStyleSheet(
            f"background:{palette['thumb_bg']}; border-radius:8px;"
        )

    def update_name_width(self, panel_width: int) -> None:
        """文件名宽度跟着面板宽度走（面板有窄/宽两档，写死宽度会浪费或多截）。"""
        # 面板内边距 12*2 + 卡片内边距 10*2 + 缩略图 88 + 间距 10 + 余量 8
        width = max(120, int(panel_width) - 24 - 20 - self.THUMB - 10 - 8)
        self.name_label.setFixedWidth(width)
        metrics = QFontMetrics(self.name_label.font())
        self.name_label.setText(metrics.elidedText(self.item.filename, Qt.ElideRight, width))

    # ---- 拖出：拖到微信/PS/资源管理器，交付的是归档后的正式路径
    def build_drag_mime(self):
        """构造拖拽内容：优先 uri-list（跨程序最通用），附带纯文本路径。"""
        if self._drag_source is None:
            return None, None
        path = self._drag_source(self.item.id)
        if not path:
            return None, None
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path)])
        mime.setText(path)          # 有些程序只认文本，比如聊天输入框
        pixmap = self.thumb_label.pixmap()
        if pixmap is not None and not pixmap.isNull():
            pixmap = pixmap.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            pixmap = None
        return mime, pixmap

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_pos is None or not (event.buttons() & Qt.LeftButton):
            super().mouseMoveEvent(event)
            return
        moved = (event.position().toPoint() - self._press_pos).manhattanLength()
        if moved < QApplication.startDragDistance():
            return
        self._press_pos = None
        mime, pixmap = self.build_drag_mime()
        if mime is None:
            return
        self.hover_left.emit()      # 拖拽时把预览窗收掉
        drag = QDrag(self)
        drag.setMimeData(mime)
        if pixmap is not None:
            drag.setPixmap(pixmap)
            drag.setHotSpot(pixmap.rect().center())
        self.drag_started.emit()
        drag.exec(Qt.CopyAction)
        self.drag_out.emit(self.item.id)

    def enterEvent(self, event) -> None:  # noqa: N802
        if self.item.staging:
            self._hover_timer.start()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._hover_timer.stop()
        self.hover_left.emit()
        super().leaveEvent(event)

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        self.preview_requested.emit(self.item.id)
        super().mouseDoubleClickEvent(event)


class DropZone(QFrame):
    files_dropped = Signal(list)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("dropzone")
        self.setAcceptDrops(True)
        self.setFixedHeight(56)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        tip = QLabel("拖文件到这里 → 发到手机", self)
        tip.setObjectName("dropzone")
        tip.setAlignment(Qt.AlignCenter)
        layout.addWidget(tip)

    def _set_dragging(self, on: bool) -> None:
        self.setProperty("dragging", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    def dragEnterEvent(self, event) -> None:  # noqa: N802
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            self._set_dragging(True)

    def dragLeaveEvent(self, event) -> None:  # noqa: N802
        self._set_dragging(False)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # noqa: N802
        self._set_dragging(False)
        paths = [u.toLocalFile() for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.files_dropped.emit(paths)
            event.acceptProposedAction()


class DragBar(QFrame):
    """标题栏：按住拖整个面板。

    只让标题栏可拖，卡片和按钮区域不受影响 —— 否则点按钮会变成拖窗口。
    """

    dropped = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._offset = None

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            window = self.window()
            self._offset = event.globalPosition().toPoint() - window.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._offset is not None and (event.buttons() & Qt.LeftButton):
            self.window().move(event.globalPosition().toPoint() - self._offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if self._offset is not None:
            self._offset = None
            self.dropped.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class SideDock(QWidget):
    save_requested = Signal(str)
    ignore_requested = Signal(str)
    send_requested = Signal(list)
    preview_requested = Signal(str)
    save_all_requested = Signal()
    ignore_all_requested = Signal()   # 一键忽略：清空所有待处理卡片
    drag_out_requested = Signal(str)  # 卡片拖出结束（宿主移除卡片并收尾）
    settings_requested = Signal()
    connect_requested = Signal()   # 手动连接模式：点「连接」按钮

    STRIP_SIZE = 44        # 收起态是个小方点，不占地方
    CHROME = 32 + 8 + 56 + 24 + 34   # 标题栏 + 间距 + 投放区 + 内边距 + 批量操作条
    MAX_CARD_AREA = 330         # 卡片区硬上限（约 3 张卡的高度）
    MAX_SCREEN_RATIO = 0.45     # 窗口总高不超过屏幕高度的这个比例（小屏上再收）
    PANEL_WIDTH = 340
    PANEL_HEIGHT = 600
    MARGIN = 10

    def __init__(self) -> None:
        # 默认**不置顶**——盖在所有 App 上会挡事；标题栏有 📌 固定按钮，
        # 按下才加 WindowStaysOnTopHint（状态记进配置，重启保持）。
        self._pinned = bool(CONFIG.get("panel_pinned", False))
        self._dark = is_dark()
        flags = Qt.FramelessWindowHint | Qt.Tool
        if self._pinned:
            flags |= Qt.WindowStaysOnTopHint
        super().__init__(None, flags)
        self.setAttribute(Qt.WA_TranslucentBackground)
        self.setStyleSheet(dock_style(float(CONFIG.get("panel_alpha", 0.94)), self._dark))
        self._collapsed = False
        self._expanded_width = int(CONFIG.get("dock_width", self.PANEL_WIDTH))
        self._cards: dict[str, CardWidget] = {}
        self._unread = 0
        self._manual_mode = False
        self._connected = False
        self._drag_source = None
        self._dragging = False
        self._ignore_confirm = False
        self._confirm_timer = QTimer(self)
        self._confirm_timer.setSingleShot(True)
        self._confirm_timer.timeout.connect(self._reset_ignore_confirm)
        # 鼠标离开后延迟收起；有待处理文件时不收（一次来一堆文件时尤其重要）
        self._idle_timer = QTimer(self)
        self._idle_timer.setSingleShot(True)
        self._idle_timer.timeout.connect(self._maybe_collapse)
        self._preview = PreviewPopup()
        self._build_ui()
        self._place()

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        # 不设这个的话，layout 的 minimumSize 会把窗口撑回 240px 宽，
        # 收起态想要的 48px 窄条就压不下去。
        outer.setSizeConstraint(QLayout.SetNoConstraint)

        self._stack = QStackedWidget(self)
        outer.addWidget(self._stack)

        # --- 展开态
        panel = QFrame(self)
        panel.setObjectName("panel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(12, 12, 12, 12)
        panel_layout.setSpacing(8)

        header_bar = DragBar(panel)
        header_bar.setObjectName("headerbar")
        header_bar.setCursor(Qt.SizeAllCursor)
        header_bar.setToolTip("按住这里拖动面板")
        header_bar.dropped.connect(self._save_pos)
        header = QHBoxLayout(header_bar)
        header.setContentsMargins(2, 2, 2, 2)
        header.setSpacing(6)
        title = QLabel(APP_NAME, header_bar)
        title.setObjectName("title")
        header.addWidget(title)
        header.addStretch(1)

        self.status_label = QLabel("● 离线", header_bar)
        self.status_label.setObjectName("status")
        self.status_label.setToolTip("未连接")
        header.addWidget(self.status_label)

        # 「连接」按钮：仅手动连接模式且未连接时可见（点了才发起连接）
        self.connect_btn = QPushButton("连接", header_bar)
        self.connect_btn.setObjectName("flat")
        self.connect_btn.setCursor(Qt.PointingHandCursor)
        self.connect_btn.setToolTip("手动连接无线设备（手机需开无线调试且在同一 WiFi）")
        self.connect_btn.setVisible(False)
        self.connect_btn.clicked.connect(self.connect_requested)
        header.addWidget(self.connect_btn)


        # 📌 固定：按下 = 始终置顶；默认不置顶，别挡别的 App
        # 图标自绘（灰轮廓/白实心），emoji 是红色的改不了色
        self.pin_btn = QPushButton(header_bar)
        self.pin_btn.setObjectName("flat")
        self.pin_btn.setIcon(_pin_icon("#8A94A6", False))
        self.pin_btn.setIconSize(QSize(17, 17))
        self.pin_btn.setFixedSize(QSize(30, 26))
        self.pin_btn.setCursor(Qt.PointingHandCursor)
        self.pin_btn.setCheckable(True)
        self.pin_btn.setToolTip("固定显示（始终在最上层）" if not self._pinned else "取消固定（允许被其他窗口盖住）")
        self.pin_btn.setChecked(self._pinned)
        self.pin_btn.setProperty("pinned", self._pinned)
        self.pin_btn.toggled.connect(self.set_pinned)
        header.addWidget(self.pin_btn)

        # 🌙/☀ 主题切换：浅色显示月亮（点击进暗夜），暗色显示太阳
        self.theme_btn = QPushButton(header_bar)
        self.theme_btn.setObjectName("flat")
        self.theme_btn.setIcon(theme_icon(self._dark))
        self.theme_btn.setIconSize(QSize(17, 17))
        self.theme_btn.setFixedSize(QSize(30, 26))
        self.theme_btn.setCursor(Qt.PointingHandCursor)
        self.theme_btn.setToolTip("切换到暗夜模式" if not self._dark else "切换回浅色模式")
        # 坑：clicked 会把 checked(False) 当参数传给 set_dark，
        # 等于每次都"切到浅色"然后被短路——必须用无参 lambda
        self.theme_btn.clicked.connect(lambda: self.set_dark())
        header.addWidget(self.theme_btn)

        settings_btn = QPushButton("⚙", header_bar)
        settings_btn.setObjectName("flat")
        settings_btn.setCursor(Qt.PointingHandCursor)
        settings_btn.setToolTip("设置")
        settings_btn.clicked.connect(self.settings_requested)
        header.addWidget(settings_btn)

        collapse_btn = QPushButton("⟩", header_bar)
        collapse_btn.setObjectName("flat")
        collapse_btn.setCursor(Qt.PointingHandCursor)
        collapse_btn.setToolTip("收起")
        collapse_btn.clicked.connect(lambda: self.set_collapsed(True))
        header.addWidget(collapse_btn)
        panel_layout.addWidget(header_bar)

        # 批量操作条：标题栏放不下（340px 宽），挪到列表上方反而更近、语义更清楚
        self.card_bar = QWidget(panel)
        bar_row = QHBoxLayout(self.card_bar)
        bar_row.setContentsMargins(2, 0, 2, 0)
        bar_row.setSpacing(6)
        self.count_label = QLabel("", self.card_bar)
        self.count_label.setObjectName("count")
        bar_row.addWidget(self.count_label)
        bar_row.addStretch(1)
        self.save_all_btn = QPushButton("全部保存", self.card_bar)
        self.save_all_btn.setObjectName("flat")
        self.save_all_btn.setCursor(Qt.PointingHandCursor)
        self.save_all_btn.setToolTip("保存所有待处理文件到归档目录")
        self.save_all_btn.clicked.connect(self.save_all_requested)
        bar_row.addWidget(self.save_all_btn)
        self.ignore_all_btn = QPushButton("全部忽略", self.card_bar)
        self.ignore_all_btn.setObjectName("flat")
        self.ignore_all_btn.setCursor(Qt.PointingHandCursor)
        self.ignore_all_btn.setToolTip("忽略所有待处理卡片（暂存文件一并删除）")
        self.ignore_all_btn.clicked.connect(self._on_ignore_all_clicked)
        bar_row.addWidget(self.ignore_all_btn)
        self.card_bar.setVisible(False)
        panel_layout.addWidget(self.card_bar)

        self.scroll = QScrollArea(panel)
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._list_host = QWidget()
        self._list_layout = QVBoxLayout(self._list_host)
        self._list_layout.setContentsMargins(0, 0, 0, 0)
        self._list_layout.setSpacing(8)
        self._list_layout.setAlignment(Qt.AlignTop)
        self.scroll.setWidget(self._list_host)
        # 滚动时卡片位置变了，预览窗会错位，直接收起来
        self.scroll.verticalScrollBar().valueChanged.connect(lambda _: self._hide_preview())
        panel_layout.addWidget(self.scroll, 1)

        self.empty_label = QLabel("等待手机新文件…\n拍张照或存个文件试试", panel)
        self.empty_label.setObjectName("empty")
        self.empty_label.setAlignment(Qt.AlignCenter)
        panel_layout.addWidget(self.empty_label)

        self.drop_zone = DropZone(panel)
        self.drop_zone.files_dropped.connect(self.send_requested)
        panel_layout.addWidget(self.drop_zone)

        self._stack.addWidget(panel)

        # --- 收起态：44×44 小方点，只显示未读数；鼠标移上来或点一下就展开
        strip = QFrame(self)
        strip.setObjectName("strip")
        strip.setCursor(Qt.PointingHandCursor)
        strip.setToolTip("点击或把鼠标移上来展开")
        strip_layout = QVBoxLayout(strip)
        strip_layout.setContentsMargins(0, 0, 0, 0)
        self.strip_dot = QLabel("●", strip)
        self.strip_dot.setObjectName("stripDot")
        self.strip_dot.setAlignment(Qt.AlignCenter)
        self.strip_dot.setToolTip("离线")
        strip_layout.addWidget(self.strip_dot)
        strip_layout.addStretch(1)
        self.badge_label = QLabel("·", strip)
        self.badge_label.setObjectName("badge")
        self.badge_label.setAlignment(Qt.AlignCenter)
        strip_layout.addWidget(self.badge_label)
        strip_layout.addStretch(1)
        self._stack.addWidget(strip)

    # ------------------------------------------------------------------ 定位
    def _desired_height(self) -> int:
        """展开态高度跟着卡片数走，但**有硬上限**，超出的靠滚轮翻。

        上限取「330px 卡片区」和「屏幕高度 45%」的较小值 —— 1080 屏上窗口总高 466px
        （3 张卡），只占四成三。文件一多就撑到快占满屏幕是很难受的，多出来的滚动看。
        """
        chrome = self.CHROME
        if not self._cards:
            return 300
        per_card = CardWidget.THUMB + 20 + 6
        screen = QGuiApplication.primaryScreen()
        screen_h = screen.availableGeometry().height() if screen else 900
        content = min(len(self._cards) * per_card, self.MAX_CARD_AREA)
        wanted = chrome + content + 16
        # 小屏上卡片区还够宽，但外框（标题栏+投放区）是固定 120px，所以按总高再夹一道
        cap = min(chrome + self.MAX_CARD_AREA + 16, int(screen_h * self.MAX_SCREEN_RATIO))
        return min(wanted, cap)

    def _place(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is None:
            return
        geo: QRect = screen.availableGeometry()
        if self._collapsed:
            width = height = self.STRIP_SIZE
        else:
            width = int(CONFIG.get("dock_width", self.PANEL_WIDTH))
            width = max(280, min(width, geo.width() // 2))
            height = min(self._desired_height(), geo.height() - 80)
            self._expanded_width = width

        x, y = self._saved_position(geo, width, height)
        if x is None:
            x = geo.right() - width - self.MARGIN
            y = geo.top() + 60
        elif self._collapsed:
            # 收起时保持右边缘不动，视觉上是从左边缩进去，不会突然跳位置
            x += max(0, self._expanded_width - width)
        self.setGeometry(x, y, width, height)

    def _saved_position(self, geo: QRect, width: int, height: int) -> tuple[int | None, int | None]:
        """用上次拖到的位置；位置已不在屏幕内（换显示器/分辨率变了）就返回 None。"""
        pos = CONFIG.get("dock_pos") or []
        if not isinstance(pos, (list, tuple)) or len(pos) != 2:
            return None, None
        try:
            x, y = int(pos[0]), int(pos[1])
        except (TypeError, ValueError):
            return None, None
        # 收起态保存的是展开时的左边缘，校验要用展开宽度，
        # 否则 48px 的窄条会判成"位置超出屏幕"而跳回默认位置
        span_w = max(width, self._expanded_width)
        span_h = max(height, self._desired_height())
        overlap_w = min(x + span_w, geo.right()) - max(x, geo.left())
        overlap_h = min(y + span_h, geo.bottom()) - max(y, geo.top())
        if overlap_w < 80 or overlap_h < 40:
            return None, None
        return x, y

    def _save_pos(self) -> None:
        """拖动结束后记录左上角坐标。收起态要还原成展开态的左边缘。"""
        x = self.x()
        if self._collapsed:
            x -= max(0, self._expanded_width - self.STRIP_SIZE)
        CONFIG.set("dock_pos", [x, self.y()])

    def set_alpha(self, alpha: float) -> None:
        """调整背景不透明度，立即生效并落盘。"""
        value = max(0.35, min(1.0, float(alpha)))
        CONFIG.set("panel_alpha", value)
        self.setStyleSheet(dock_style(value, self._dark))

    def set_drag_source(self, fn) -> None:
        """注入"拖出时归档并返回路径"的回调（由 main 提供，dock 不该自己管归档）。"""
        self._drag_source = fn
        for card in self._cards.values():
            card._drag_source = fn

    def set_panel_width(self, width: int) -> None:
        """面板宽度两档切换（窄 340 / 宽 420）：卡片文件名宽度与定位一起跟着改。"""
        width = max(300, min(560, int(width)))
        CONFIG.set("dock_width", width)
        self._expanded_width = width
        for card in self._cards.values():
            card.update_name_width(width)
        self._place()

    def reset_position(self) -> None:
        CONFIG.set("dock_pos", [])
        self._place()

    # ------------------------------------------------------------------ 状态
    def set_collapsed(self, collapsed: bool) -> None:
        if collapsed == self._collapsed:
            return
        self._collapsed = collapsed
        self._stack.setCurrentIndex(1 if collapsed else 0)
        if not collapsed:
            self._unread = 0
        self._refresh_badge()
        self._place()
        if collapsed:
            self._idle_timer.stop()
        elif not self._mouse_inside():
            self._schedule_idle_collapse()

    def set_pinned(self, pinned: bool) -> None:
        """📌 固定显示：置顶开关。

        坑：setWindowFlag 改 WindowStaysOnTopHint 会让 Qt **立即隐藏**窗口，
        必须先把可见性记下来、改完再 show 回去——顺序反了窗口就没了。
        状态写进配置，重启保持；按钮勾选态用 QSS 高亮，一眼能看出来。
        """
        pinned = bool(pinned)
        changed = pinned != self._pinned
        self._pinned = pinned
        if changed:
            CONFIG.set("panel_pinned", pinned)
        # 按钮状态同步（用户点击和程序调用都会走到这里；setChecked 会再触发
        # toggled→本函数一次，第二次 changed=False 直接短路）
        if self.pin_btn.isChecked() != pinned:
            self.pin_btn.setChecked(pinned)
        self.pin_btn.setIcon(_pin_icon("#FFFFFF" if pinned else "#8A94A6", pinned))
        self.pin_btn.setProperty("pinned", pinned)
        self.pin_btn.setToolTip(
            "取消固定（允许被其他窗口盖住）" if pinned else "固定显示（始终在最上层）"
        )
        self.pin_btn.style().unpolish(self.pin_btn)
        self.pin_btn.style().polish(self.pin_btn)
        if not changed:
            return
        was_visible = self.isVisible()   # 必须在 setWindowFlag 之前取
        self.setWindowFlag(Qt.WindowStaysOnTopHint, pinned)
        if was_visible:
            self.show()
            self._place()

    # ------------------------------------------------------------------ 自动收起
    # ---- 拖出：拖到微信/PS/资源管理器，交付的是归档后的正式路径
    def build_drag_mime(self):
        """构造拖拽内容：优先 uri-list（跨程序最通用），附带纯文本路径。"""
        if self._drag_source is None:
            return None, None
        path = self._drag_source(self.item.id)
        if not path:
            return None, None
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(path)])
        mime.setText(path)          # 有些程序只认文本，比如聊天输入框
        pixmap = self.thumb_label.pixmap()
        if pixmap is not None and not pixmap.isNull():
            pixmap = pixmap.scaled(96, 96, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        else:
            pixmap = None
        return mime, pixmap

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.LeftButton:
            self._press_pos = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        self._press_pos = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if self._press_pos is None or not (event.buttons() & Qt.LeftButton):
            super().mouseMoveEvent(event)
            return
        moved = (event.position().toPoint() - self._press_pos).manhattanLength()
        if moved < QApplication.startDragDistance():
            return
        self._press_pos = None
        mime, pixmap = self.build_drag_mime()
        if mime is None:
            return
        self.hover_left.emit()      # 拖拽时把预览窗收掉
        drag = QDrag(self)
        drag.setMimeData(mime)
        if pixmap is not None:
            drag.setPixmap(pixmap)
            drag.setHotSpot(pixmap.rect().center())
        self.drag_started.emit()
        drag.exec(Qt.CopyAction)
        self.drag_out.emit(self.item.id)

    def enterEvent(self, event) -> None:  # noqa: N802
        self._idle_timer.stop()
        if self._collapsed:
            self.set_collapsed(False)
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # noqa: N802
        self._schedule_idle_collapse()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        # 收起态点一下也展开：比只靠 hover 稳，远程桌面/触摸板上也好用
        if self._collapsed and event.button() == Qt.LeftButton:
            self.set_collapsed(False)
            event.accept()
            return
        super().mousePressEvent(event)

    def _mouse_inside(self) -> bool:
        """鼠标是否停在面板上。

        只用 ``underMouse()`` 不够稳：窗口刚缩放的那一瞬间它可能返回 False，
        会导致鼠标明明在上面却被自动收起。再用光标坐标兜一层。
        """
        if self.underMouse():
            return True
        return self.frameGeometry().contains(QCursor.pos())

    def _schedule_idle_collapse(self) -> None:
        delay = int(CONFIG.get("auto_collapse_seconds", 5))
        if delay <= 0 or self._collapsed:
            return
        self._idle_timer.start(delay * 1000)

    def _maybe_collapse(self) -> None:
        if self._collapsed or self._dragging or self._mouse_inside():
            return
        if self._cards:
            # 还有没处理的文件就不收。一次来 20 个文件时，
            # 处理掉一个就缩回去是最烦人的。
            return
        self.set_collapsed(True)

    def is_collapsed(self) -> bool:
        return self._collapsed

    _SHORT_STATUS = {"无线已连接": "无线", "USB 已连接": "USB", "未连接": "离线"}

    def set_device_status(self, text: str, color: str) -> None:
        # 标题栏只放得下短词，完整状态放悬停提示（8 个元素挤 316px 会把文字压断行）
        self.status_label.setText(f"● {self._SHORT_STATUS.get(text, text)}")
        self.status_label.setToolTip(text)
        self.status_label.setStyleSheet(
            f"font: 11px 'Microsoft YaHei UI'; color: {color};"
        )
        self._connected = text != "未连接"
        if hasattr(self, "strip_dot"):
            self.strip_dot.setStyleSheet(f"color: {color}; font: 9px 'Microsoft YaHei UI';")
            self.strip_dot.setToolTip(text)
        # 手动连接模式：未连接时亮出「连接」按钮，连上就藏起来
        self.connect_btn.setVisible(self._manual_mode and not self._connected)

    def set_manual_mode(self, manual: bool) -> None:
        """手动/自动连接模式切换（设置里改完即时生效）。

        切到手动时如果当前未连接，立刻亮出按钮；切回自动时按钮消失
        （监听线程自己会去重连）。
        """
        self._manual_mode = bool(manual)
        self.connect_btn.setVisible(self._manual_mode and not self._connected)


    def set_dark(self, dark: bool | None = None) -> None:
        """暗夜模式开关：点标题栏月亮/太阳按钮触发（None = 取反）。

        刷新顺序：配置 → 面板样式 → 预览弹窗 → 已有卡片的缩略图底色 → 按钮图标。
        """
        dark = (not self._dark) if dark is None else bool(dark)
        if dark == self._dark:
            return
        self._dark = dark
        CONFIG.set("theme", "dark" if dark else "light")
        alpha = float(CONFIG.get("panel_alpha", 0.94))
        self.setStyleSheet(dock_style(alpha, dark))
        self._preview.setStyleSheet(dock_style(0.98, dark))
        for card in self._cards.values():
            card.set_dark_style(dark)
        self.theme_btn.setIcon(theme_icon(dark))
        self.theme_btn.setToolTip("切换回浅色模式" if dark else "切换到暗夜模式")

    def set_connecting(self, loading: bool) -> None:
        """连接动作进行中：按钮禁用换文案，防止连点。"""
        self.connect_btn.setText("连接中…" if loading else "连接")
        self.connect_btn.setEnabled(not loading)
        if loading:
            self.connect_btn.setVisible(True)

    def _refresh_badge(self) -> None:
        """收起条数字：显示待处理总数（含收起期间的未读），没有就显示点。"""
        count = max(len(self._cards), self._unread)
        self.badge_label.setText(str(count) if count else "·")

    def _on_card_drag_started(self) -> None:
        self._dragging = True
        self._idle_timer.stop()      # 拖拽中别收起面板（鼠标必然在面板外）

    def _on_card_drag_out(self, item_id: str) -> None:
        """拖拽结束（成功或取消都算）：归档已完成，通知宿主移除卡片。"""
        self._dragging = False
        self.drag_out_requested.emit(item_id)

    def _on_ignore_all_clicked(self) -> None:
        """全部忽略是破坏性操作：第一次点变红确认，3 秒内再点才真忽略。

        不用弹窗——弹窗打断更烦；就地变色二次确认足够防误触，还能顺手显示数量。
        """
        if self._ignore_confirm:
            self._reset_ignore_confirm()
            self.ignore_all_requested.emit()
            return
        self._ignore_confirm = True
        self.ignore_all_btn.setText(f"确认忽略 {len(self._cards)} 个？")
        self.ignore_all_btn.setProperty("confirm", True)
        self._repolish(self.ignore_all_btn)
        self._confirm_timer.start(3000)

    def _reset_ignore_confirm(self) -> None:
        self._drag_source = None
        self._dragging = False
        self._ignore_confirm = False
        self._confirm_timer.stop()
        self.ignore_all_btn.setText("全部忽略")
        self.ignore_all_btn.setProperty("confirm", False)
        self._repolish(self.ignore_all_btn)

    @staticmethod
    def _repolish(widget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _refresh_card_bar(self) -> None:
        """批量操作条：有卡片才出现，标题显示待处理数量。"""
        count = len(self._cards)
        self.count_label.setText(f"{count} 个待处理" if count else "")
        self.card_bar.setVisible(count > 0)
        self._refresh_badge()
        if not count and self._ignore_confirm:
            self._reset_ignore_confirm()   # 卡片没了，确认态也别留着

    # ------------------------------------------------------------------ 卡片
    def add_item(self, item: PendingItem, thumb: QPixmap | None = None) -> None:
        if item.id in self._cards:
            return
        pixmap = thumb if thumb is not None else load_thumb(item.staging)
        card = CardWidget(item, pixmap, self._list_host, drag_source=self._drag_source)
        card.drag_out.connect(self._on_card_drag_out)
        card.drag_started.connect(self._on_card_drag_started)
        card.save_requested.connect(self.save_requested)
        card.ignore_requested.connect(self.ignore_requested)
        card.preview_requested.connect(self.preview_requested)
        card.hover_entered.connect(self._on_card_hover)
        card.hover_left.connect(self._hide_preview)
        self._list_layout.insertWidget(0, card)
        self._cards[item.id] = card
        self._refresh_card_bar()

        self.empty_label.setVisible(False)
        self._idle_timer.stop()  # 有东西要处理，先别收
        if self._collapsed:
            self._unread += 1
            self._refresh_badge()
        if CONFIG.get("auto_expand", True) and self._collapsed:
            self.set_collapsed(False)
        else:
            self._place()

    def remove_item(self, item_id: str) -> None:
        card = self._cards.pop(item_id, None)
        self._hide_preview()
        if card is not None:
            # 不要 setParent(None) —— 那会把卡片变成独立顶层窗口，创建/销毁原生窗口很贵
            self._list_layout.removeWidget(card)
            card.deleteLater()
        self._refresh_card_bar()
        if not self._cards:
            self.empty_label.setVisible(True)
            self._schedule_idle_collapse()  # 最后一个处理完，开始倒计时收起
        self._place()

    def _on_card_hover(self, item_id: str) -> None:
        """鼠标在某张卡片上停够时间 —— 在面板左侧弹出大图预览。

        anchor 的 x 用面板左边缘而不是光标位置：否则预览窗右边缘会压到面板上。
        """
        card = self._cards.get(item_id)
        if card is None or not card.item.staging:
            return
        self._preview.show_for(card.item, QPoint(self.x(), QCursor.pos().y()))

    def _hide_preview(self) -> None:
        self._preview.hide()

    def item_ids(self) -> list[str]:
        return list(self._cards.keys())
    def card_count(self) -> int:
        return len(self._cards)
