"""主题（浅色 / 暗夜）：面板、预览弹窗、设置窗口共用的调色板与样式。

设计令牌（「轻量 iOS 风」+ 蓝白简约，换肤只改主色三档）：
* 主色 #4D7CFE / 浅底 #EAF1FF / 深色 #2F5FE0
* 页底 #F4F6FB，卡片 #FFFFFF，主文字 #1C2333，次级 #8A93A6，弱 #B4BCCB
* 圆角：面板 16 / 卡片 13 / 分组 16 / 输入 10 / 按钮胶囊
* 阴影不进 QSS（半透明窗上叠加会发脏），层次统一靠 1px 浅描边 + 浅底
* 铁律：只有最外层容器半透明，内层全透明或纯白，避免二次叠加发灰

暗色是同一套令牌的深色映射（深海军蓝 + 亮蓝 #7EA0FF），不是简单反色。
"""
from __future__ import annotations

import math
from string import Template

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap, QPolygonF

_THEME_CACHE: dict[tuple[str, bool, float], str] = {}
_ICON_CACHE: dict[tuple[bool, bool], QIcon] = {}


def is_dark() -> bool:
    from config import CONFIG

    return CONFIG.get("theme", "light") == "dark"


# ---------------------------------------------------------------- 面板样式
_DOCK_TEMPLATE = Template("""
QFrame#panel, QFrame#strip {
    background: $panel_bg;
    border: 1px solid $border;
    border-radius: 16px;
}
/* 显式给前景色：QSS 不设 color 时会落到系统调色板，深色系统下就是"白字白底" */
QLabel { background: transparent; color: $title; }
QLabel#title { font: 600 14px 'Microsoft YaHei UI'; color: $title; }
QLabel#status { font: 11px 'Microsoft YaHei UI'; color: $sub; }
QFrame#card {
    background: $card_bg;
    border: 1px solid $card_line;
    border-radius: 13px;
}
QFrame#card:hover {
    border-color: $card_hover_line;
    background: $card_hover_bg;
}
QLabel#fname { font: 500 13px 'Microsoft YaHei UI'; color: $title; }
QLabel#meta { font: 11px 'Microsoft YaHei UI'; color: $sub; }
QLabel#count { font: 11px 'Microsoft YaHei UI'; color: $accent; }

/* 胶囊按钮：主操作实心，次操作描边 */
QPushButton {
    border-radius: 13px; font: 11px 'Microsoft YaHei UI';
    padding: 5px 12px; min-width: 44px;
}
QPushButton#save {
    background: $save_bg; color: #FFFFFF; border: 1px solid $save_bg;
    font: 600 11px 'Microsoft YaHei UI';
}
QPushButton#save:hover { background: $save_hover; border-color: $save_hover; }
QPushButton#save:pressed { background: $save_pressed; border-color: $save_pressed; }
QPushButton#ignore {
    background: transparent; color: $ignore_text; border: 1px solid $card_line;
}
QPushButton#ignore:hover { background: $ignore_hover; border-color: $card_hover_line; }
QPushButton#ignore:pressed { background: $press_bg; }

/* 图标按钮：圆角方形，比纯圆更好点中 */
QPushButton#flat {
    background: transparent; color: $flat; border: none;
    padding: 2px 6px; border-radius: 9px;
}
QPushButton#flat:hover { background: $flat_hover; }
QPushButton#flat:pressed { background: $press_bg; }
QPushButton#flat:checked { background: $save_bg; border-radius: 9px; }
QPushButton#flat[confirm="true"] { color: $danger; font-weight: 600; }

QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget#qt_scrollarea_viewport { background: transparent; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: transparent; width: 8px; margin: 2px 0; }
QScrollBar::handle:vertical { background: $scroll_handle; border-radius: 4px; min-height: 28px; }
QScrollBar::handle:vertical:hover { background: $scroll_handle_hover; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }

QLabel#empty { font: 12px 'Microsoft YaHei UI'; color: $empty; }
QFrame#dropzone {
    background: $drop_bg;
    border: 1px dashed $drop_line;
    border-radius: 13px;
}
QFrame#dropzone:hover { border-color: $accent; background: $drop_hover_bg; }
QFrame#dropzone[dragging="true"] { background: $drop_hover_bg; border-color: $accent; }
QLabel#dropzone { font: 11px 'Microsoft YaHei UI'; color: $sub; }
QLabel#badge { font: 600 15px 'Microsoft YaHei UI'; color: $accent; }

/* 悬停预览窗 */
QFrame#preview {
    background: $preview_bg;
    border: 1px solid $preview_border;
    border-radius: 14px;
}
QLabel#pvName { font: 500 12px 'Microsoft YaHei UI'; color: $title; }
QLabel#pvInfo { font: 11px 'Microsoft YaHei UI'; color: $sub; }
QLabel#pvImage { background: $pv_image; border-radius: 10px; }
""")

_DOCK_LIGHT = dict(
    panel_bg="rgba(255, 255, 255, {alpha:.2f})",
    border="rgba(226, 233, 246, {edge:.2f})",
    card_bg="#FFFFFF",
    card_line="rgba(232, 238, 249, {edge:.2f})",
    card_hover_line="#C7D9FE",
    card_hover_bg="#F8FBFF",
    drop_line="rgba(198, 214, 238, {edge:.2f})",
    drop_bg="#FBFCFF",
    drop_hover_bg="#F4F8FF",
    title="#1C2333",
    sub="#8A93A6",
    ignore_text="#5A6478",
    ignore_hover="#F4F7FD",
    flat="#8A93A6",
    flat_hover="#EFF4FD",
    scroll_handle="#D8E1F0",
    scroll_handle_hover="#BAC8DE",
    empty="#B4BCCB",
    preview_bg="rgba(255, 255, 255, 0.98)",
    preview_border="#E2E9F6",
    pv_image="#F1F5FC",
    accent="#4D7CFE",
    danger="#E5484D",
    badge="#4D7CFE",
    thumb_bg="#F1F5FC",
    save_bg="#4D7CFE",
    save_hover="#3E6AF0",
    save_pressed="#2F5FE0",
    press_bg="rgba(28, 35, 51, 0.10)",
)

_DOCK_DARK = dict(
    panel_bg="rgba(23, 30, 46, {alpha:.2f})",
    border="rgba(52, 63, 86, {edge:.2f})",
    card_bg="rgba(255, 255, 255, 0.03)",
    card_line="rgba(52, 63, 86, {edge:.2f})",
    card_hover_line="#4B6AA8",
    card_hover_bg="rgba(109, 150, 254, 0.10)",
    drop_line="rgba(64, 77, 104, {edge:.2f})",
    drop_bg="rgba(255, 255, 255, 0.02)",
    drop_hover_bg="rgba(109, 150, 254, 0.08)",
    title="#E8ECF5",
    sub="#9099AC",
    ignore_text="#C6CEDE",
    ignore_hover="rgba(255, 255, 255, 0.07)",
    flat="#98A3B8",
    flat_hover="rgba(255, 255, 255, 0.08)",
    scroll_handle="#3C4763",
    scroll_handle_hover="#54638A",
    empty="#6B7488",
    preview_bg="rgba(23, 30, 46, 0.98)",
    preview_border="#34405A",
    pv_image="#101724",
    accent="#7EA0FF",
    danger="#FF8A8E",
    badge="#7EA0FF",
    thumb_bg="#1B2334",
    save_bg="#4D7CFE",
    save_hover="#3E6AF0",
    save_pressed="#3157CC",
    press_bg="rgba(255, 255, 255, 0.16)",
)


def dock_style(alpha: float, dark: bool) -> str:
    """面板/预览/卡片样式。alpha 只作用于最外层容器（半透明层次原则见 README）。"""
    value = max(0.35, min(1.0, float(alpha)))
    edge = min(1.0, value + 0.02)
    palette = dict(_DOCK_DARK if dark else _DOCK_LIGHT)
    for key in ("panel_bg", "border", "card_line", "drop_line", "card_bg", "drop_bg"):
        palette[key] = palette[key].format(alpha=value, edge=edge)
    key = ("dock", dark, round(value, 2))
    if key not in _THEME_CACHE:
        _THEME_CACHE[key] = _DOCK_TEMPLATE.substitute(**palette)
    return _THEME_CACHE[key]


def dock_palette(dark: bool) -> dict:
    """代码里需要的颜色（卡片缩略图底色等）。"""
    return dict(_DOCK_DARK if dark else _DOCK_LIGHT)


# ---------------------------------------------------------------- 设置窗口样式
_SETTINGS_TEMPLATE = Template("""
QDialog { background: $dlg_bg; }
/* 滚动区 viewport 是独立 widget，不铺色会露出系统深色调色板底色 */
QScrollArea { background: $dlg_bg; border: none; }
QScrollArea > QWidget#qt_scrollarea_viewport { background: $dlg_bg; }
QWidget#settingsBody { background: $dlg_bg; }

QLabel { background: transparent; font: 12px 'Microsoft YaHei UI'; color: $text; }
QLabel#dlgTitle { font: 600 17px 'Microsoft YaHei UI'; color: $title; }
QLabel#groupTitle { font: 600 12px 'Microsoft YaHei UI'; color: $group_title; }
QLabel#fieldLabel { font: 12px 'Microsoft YaHei UI'; color: $field_label; }
QLabel#sub { font: 11px 'Microsoft YaHei UI'; color: $sub; }
QLabel#path { font: 11px 'Consolas', 'Microsoft YaHei UI'; color: $path; }

/* 分组白卡 + 浅描边，不用阴影保持干净 */
QFrame#group {
    background: $group_bg;
    border: 1px solid $group_border;
    border-radius: 16px;
}

/* 胶囊按钮 */
QPushButton {
    border-radius: 14px;
    padding: 6px 16px;
    min-height: 18px;
    font: 12px 'Microsoft YaHei UI';
    background: transparent;
    color: $group_title;
    border: 1px solid transparent;
    outline: none;
}
QPushButton:hover { background: $btn_hover; }
QPushButton:pressed { background: $press_bg; }
QPushButton:focus { outline: none; border: 1px solid $accent; }
QPushButton#primary {
    background: $accent; color: #FFFFFF; border: 1px solid $accent;
    font: 600 12px 'Microsoft YaHei UI';
}
QPushButton#primary:hover { background: $primary_hover; border-color: $primary_hover; }
QPushButton#primary:pressed { background: $primary_pressed; border-color: $primary_pressed; }
QPushButton#primary:focus { outline: none; border: 2px solid $primary_focus; }
QPushButton#ghost {
    background: $ghost_bg; color: $ghost_text; border: 1px solid $ghost_border;
}
QPushButton#ghost:hover { background: $ghost_hover; }
QPushButton#ghost:pressed { background: $ghost_pressed; }
QPushButton#ghost:focus { outline: none; border: 1px solid $accent; }
QPushButton#chip {
    background: $chip_bg; color: $chip_text; border: 1px solid $chip_border;
    border-radius: 12px; padding: 4px 12px; min-height: 14px;
    font: 11px 'Microsoft YaHei UI';
}
QPushButton#chip:hover { background: $chip_hover_bg; border-color: $chip_hover_border; }
QPushButton#chip:pressed { background: $press_bg; }
QPushButton:disabled { background: $disabled_bg; color: $disabled_text; border-color: $disabled_border; }

QLineEdit {
    border: 1px solid $input_border; border-radius: 10px;
    padding: 6px 11px; min-height: 18px;
    font: 12px 'Microsoft YaHei UI'; color: $input_text; background: $input_bg;
    outline: none;
}
QLineEdit:focus { border: 1px solid $accent; }
QSpinBox {
    border: 1px solid $input_border; border-radius: 10px;
    padding: 6px 11px; min-width: 68px; min-height: 18px;
    font: 12px 'Microsoft YaHei UI'; color: $input_text; background: $input_bg;
}
QSpinBox:focus { border: 1px solid $accent; }
QSpinBox::up-button, QSpinBox::down-button { width: 0; height: 0; border: none; }

QComboBox {
    border: 1px solid $input_border; border-radius: 10px;
    padding: 5px 10px; min-height: 18px; min-width: 78px;
    font: 12px 'Microsoft YaHei UI'; color: $input_text; background: $input_bg;
    padding-right: 24px;
}
QComboBox:hover { border-color: $accent; }
QComboBox:focus { border: 1px solid $accent; }
QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: center right;
    width: 24px;
    border: none;
}
QComboBox::down-arrow {
    image: url($combo_arrow_img);
    width: 14px;
    height: 14px;
}
QComboBox QAbstractItemView {
    background: $input_bg; color: $input_text;
    border: 1px solid $input_border; border-radius: 10px;
    selection-background-color: $list_selected_bg;
    selection-color: $list_selected_text;
    outline: none; padding: 4px;
}

QCheckBox { font: 12px 'Microsoft YaHei UI'; color: $cb_text; spacing: 8px; }
QCheckBox::indicator { width: 17px; height: 17px; }
QCheckBox::indicator:unchecked {
    border: 1px solid $cb_border; border-radius: 6px; background: $cb_bg;
}
QCheckBox::indicator:checked {
    border: 1px solid $accent; border-radius: 6px; background: $accent;
    image: url($check_img);
}
QCheckBox::indicator:hover { border-color: $accent; }

QSlider::groove:horizontal { height: 6px; background: $slider_groove; border-radius: 3px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 3px; }
QSlider::handle:horizontal {
    width: 18px; height: 18px; margin: -6px 0;
    border-radius: 9px; background: #FFFFFF; border: 3px solid $accent;
}
QSlider:focus { outline: none; }

QListWidget {
    border: 1px solid $group_border; border-radius: 12px; background: $list_bg;
    font: 12px 'Microsoft YaHei UI'; color: $list_text; outline: none;
}
QListWidget::item { border-radius: 8px; padding: 3px 4px; }
QListWidget::item:hover { background: $list_hover; }
QListWidget::item:selected { background: $list_selected_bg; color: $list_selected_text; }
QScrollBar:vertical { background: transparent; width: 10px; margin: 2px; }
QScrollBar::handle:vertical { background: $scroll_handle; border-radius: 5px; min-height: 36px; }
QScrollBar::handle:vertical:hover { background: $combo_arrow; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0; }
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: transparent; }
""")

_SETTINGS_LIGHT = dict(
    dlg_bg="#F4F6FB",
    text="#1C2333",
    title="#0F1729",
    group_title="#4D7CFE",
    field_label="#3A4459",
    sub="#8A93A6",
    path="#4A5568",
    group_bg="#FFFFFF",
    group_border="#E6EDF9",
    btn_hover="#F2F7FF",
    accent="#4D7CFE",
    primary_hover="#3E6AF0",
    primary_pressed="#2F5FE0",
    primary_focus="#C7D9FE",
    ghost_bg="#EAF1FF",
    ghost_text="#2F5FE0",
    ghost_border="#D8E5FF",
    ghost_hover="#E0EAFF",
    ghost_pressed="#D5E2FF",
    chip_bg="#FFFFFF",
    chip_text="#4D7CFE",
    chip_border="#DCE7FB",
    chip_hover_bg="#F2F7FF",
    chip_hover_border="#BFD4FE",
    disabled_bg="#F2F5FA",
    disabled_text="#C2C9D6",
    disabled_border="#EDF1F8",
    input_border="#DEE7F6",
    input_text="#1C2333",
    input_bg="#FFFFFF",
    cb_text="#3A4459",
    cb_border="#CBD5E1",
    cb_bg="#FFFFFF",
    slider_groove="#E9EDF5",
    list_bg="#FFFFFF",
    list_text="#1C2333",
    list_hover="#F5F9FF",
    list_selected_bg="#EAF1FF",
    list_selected_text="#1C2333",
    scroll_handle="#D8E1F0",
    combo_arrow="#8A93A6",
    combo_arrow_img="",
    press_bg="rgba(77, 124, 254, 0.14)",
)

_SETTINGS_DARK = dict(
    dlg_bg="#121A29",
    text="#E5EAF3",
    title="#F2F5FA",
    group_title="#7EA0FF",
    field_label="#C3CBD9",
    sub="#9099AC",
    path="#B7C0CF",
    group_bg="#1A2233",
    group_border="#27334A",
    btn_hover="rgba(126, 160, 255, 0.12)",
    accent="#7EA0FF",
    primary_hover="#6B90FF",
    primary_pressed="#5A7FF0",
    primary_focus="#2E3F66",
    ghost_bg="#1F2A3E",
    ghost_text="#C9D6FF",
    ghost_border="#2F3D57",
    ghost_hover="#26334A",
    ghost_pressed="#223049",
    chip_bg="#1A2233",
    chip_text="#7EA0FF",
    chip_border="#2E3D58",
    chip_hover_bg="#223049",
    chip_hover_border="#3B5280",
    disabled_bg="#1A2233",
    disabled_text="#566278",
    disabled_border="#232C3A",
    input_border="#2E3C55",
    input_text="#E5EAF3",
    input_bg="#1A2233",
    cb_text="#C3CBD9",
    cb_border="#46536B",
    cb_bg="#1A2233",
    slider_groove="#26334A",
    list_bg="#1A2233",
    list_text="#E5EAF3",
    list_hover="#223049",
    list_selected_bg="#20304F",
    list_selected_text="#F2F5FA",
    scroll_handle="#3C4763",
    combo_arrow="#98A3B8",
    combo_arrow_img="",
    press_bg="rgba(126, 160, 255, 0.18)",
)


def _check_icon_path() -> str:
    """勾选态的对勾：自绘白勾（QSS 一旦自定义 background，原生勾就没了）。"""
    from config import DATA_DIR

    path = DATA_DIR / "assets" / "check_white.png"
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        pm = QPixmap(18, 18)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(QPen(QColor("#FFFFFF"), 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.drawPolyline(QPolygonF([QPointF(4.5, 9.3), QPointF(7.6, 12.4), QPointF(13.4, 5.6)]))
        p.end()
        pm.save(str(path))
    return path.as_posix()


def _combo_arrow_path(color: str) -> str:
    """下拉箭头小图：QSS 的 ::down-arrow 用 border 画三角不可靠，改为预生成 PNG。"""
    from config import DATA_DIR

    assets = DATA_DIR / "assets"
    name = f"combo_arrow_{color.lstrip('#').lower()}.png"
    path = assets / name
    if not path.is_file():
        assets.mkdir(parents=True, exist_ok=True)
        pm = QPixmap(14, 14)
        pm.fill(Qt.transparent)
        p = QPainter(pm)
        p.setRenderHint(QPainter.Antialiasing)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawPolygon(QPolygonF([QPointF(3, 5), QPointF(11, 5), QPointF(7, 10)]))
        p.end()
        pm.save(str(path))
    return path.as_posix()


def settings_style(dark: bool) -> str:
    palette = dict(_SETTINGS_DARK if dark else _SETTINGS_LIGHT)
    palette["combo_arrow_img"] = _combo_arrow_path(palette["combo_arrow"])
    palette["check_img"] = _check_icon_path()
    key = ("settings", dark)
    if key not in _THEME_CACHE:
        _THEME_CACHE[key] = _SETTINGS_TEMPLATE.substitute(**palette)
    return _THEME_CACHE[key]


# ---------------------------------------------------------------- 主题切换图标
def theme_icon(dark: bool) -> QIcon:
    """主题切换按钮图标：浅色模式显示月亮（点击进暗夜），暗色显示太阳。"""
    key = ("icon", dark)
    if key in _ICON_CACHE:
        return _ICON_CACHE[key]

    pm = QPixmap(32, 32)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    color = QColor("#98A3B8" if dark else "#8A93A6")
    if dark:
        p.setPen(QPen(color, 2.2, Qt.SolidLine, Qt.RoundCap))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QRectF(11, 11, 10, 10))
        cx, cy = 16.0, 16.0
        for i in range(8):
            angle = math.pi / 4 * i
            p.drawLine(
                QPointF(cx + math.cos(angle) * 8.5, cy + math.sin(angle) * 8.5),
                QPointF(cx + math.cos(angle) * 12, cy + math.sin(angle) * 12),
            )
    else:
        from PySide6.QtGui import QPainterPath

        path = QPainterPath()
        path.setFillRule(Qt.OddEvenFill)
        path.addEllipse(QRectF(8, 6, 18, 18))
        path.addEllipse(QRectF(13, 4, 16, 16))
        p.setPen(Qt.NoPen)
        p.setBrush(color)
        p.drawPath(path)
    p.end()
    icon = QIcon(pm)
    _ICON_CACHE[key] = icon
    return icon
