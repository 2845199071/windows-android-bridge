"""生成「手机电脑互通」宣传图。

用法：
    ./.venv/Scripts/python.exe scripts/make_promo.py [输出路径]

输出：1600x900（实际 3200x1800 @2x），风格与软件本体一致（蓝 #2563EB、白卡、圆角）。
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "app"))

from PySide6.QtCore import QBuffer, QPointF, QRectF, Qt  # noqa: E402
from PySide6.QtGui import (  # noqa: E402
    QColor,
    QFont,
    QGuiApplication,
    QImage,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

W, H = 1600, 900
SCALE = 2.0


def font(size: int, bold: bool = False) -> QFont:
    f = QFont("Microsoft YaHei UI")
    f.setPixelSize(size)
    f.setBold(bold)
    return f


def text_w(p: QPainter, s: str, f: QFont) -> int:
    p.setFont(f)
    return p.fontMetrics().horizontalAdvance(s)


def chip(p: QPainter, cx: int, y: int, s: str, size: int = 19) -> int:
    """画一枚胶囊标签，返回宽度（布局用）。"""
    f = font(size)
    tw = text_w(p, s, f)
    w, h = tw + 44, 46
    r = QRectF(cx - w / 2, y, w, h)
    p.setPen(QPen(QColor("#C9DBF8"), 1.5))
    p.setBrush(QColor("#FFFFFF"))
    p.drawRoundedRect(r, h / 2, h / 2)
    p.setPen(QColor("#185FA5"))
    p.setFont(f)
    p.drawText(r, Qt.AlignCenter, s)
    return w


def card(p: QPainter, x: float, y: float, thumb_color: str, kind: str) -> None:
    """面板里的一张小卡片（缩略图 + 文字条 + 按钮）。"""
    p.setPen(QPen(QColor("#E8ECF3"), 1))
    p.setBrush(QColor("#F8FAFC"))
    p.drawRoundedRect(QRectF(x, y, 226, 74), 10, 10)
    # 缩略图
    t = QRectF(x + 8, y + 9, 56, 56)
    path = QPainterPath()
    path.addRoundedRect(t, 8, 8)
    p.save()
    p.setClipPath(path)
    if kind == "photo":
        p.fillRect(t, QColor("#B5D4F4"))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#FAC775"))
        p.drawEllipse(QRectF(t.right() - 20, t.top() + 5, 14, 14))
        p.setBrush(QColor("#64748B"))
        poly = [
            QPointF(t.left(), t.bottom()),
            QPointF(t.left() + 26, t.top() + 26),
            QPointF(t.left() + 40, t.bottom() - 10),
            QPointF(t.left() + 52, t.top() + 32),
            QPointF(t.right(), t.bottom()),
        ]
        p.drawPolygon(poly)
    else:
        p.fillRect(t, QColor("#12B76A"))
        p.setPen(QPen(QColor("#FFFFFF"), 2))
        for i in range(3):
            line_y = t.top() + 14 + i * 12
            p.drawLine(QPointF(t.left() + 10, line_y), QPointF(t.right() - 10, line_y))
    p.restore()
    # 文字条
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#D7DCE4"))
    p.drawRoundedRect(QRectF(x + 74, y + 14, 96, 9), 4, 4)
    p.drawRoundedRect(QRectF(x + 74, y + 30, 66, 9), 4, 4)
    # 按钮
    b1 = QRectF(x + 74, y + 46, 46, 20)
    p.setBrush(QColor("#2563EB"))
    p.drawRoundedRect(b1, 6, 6)
    p.setPen(QColor("#FFFFFF"))
    p.setFont(font(12))
    p.drawText(b1, Qt.AlignCenter, "保存")
    b2 = QRectF(x + 126, y + 46, 46, 20)
    p.setBrush(QColor("#EEF2F7"))
    p.drawRoundedRect(b2, 6, 6)
    p.setPen(QColor("#6B7280"))
    p.drawText(b2, Qt.AlignCenter, "忽略")


def render() -> QImage:
    img = QImage(int(W * SCALE), int(H * SCALE), QImage.Format_ARGB32_Premultiplied)
    img.setDevicePixelRatio(SCALE)
    img.fill(QColor("#F6F9FE"))
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    p.setRenderHint(QPainter.TextAntialiasing)

    # ---- 背景装饰：两团淡色
    p.setPen(Qt.NoPen)
    p.setBrush(QColor(37, 99, 235, 16))
    p.drawEllipse(QRectF(1150, -160, 520, 520))
    p.setBrush(QColor(13, 148, 136, 18))
    p.drawEllipse(QRectF(-140, 700, 420, 420))

    # ---- 顶部：图标 + 标题（居中）
    title_s = "手机电脑互通"
    tf = font(54, bold=True)
    icon_s = 66
    total = icon_s + 18 + text_w(p, title_s, tf)
    x0 = (W - total) / 2
    # 图标：蓝底圆角方块 + 白色 ⇄
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#2563EB"))
    p.drawRoundedRect(QRectF(x0, 66, icon_s, icon_s), 16, 16)
    p.setPen(QPen(QColor("#FFFFFF"), 5, Qt.SolidLine, Qt.RoundCap))
    p.drawLine(QPointF(x0 + 16, 88), QPointF(x0 + icon_s - 16, 88))
    p.drawLine(QPointF(x0 + icon_s - 16, 110), QPointF(x0 + 16, 110))
    p.setBrush(QColor("#FFFFFF"))
    for tip_x, tip_y in ((x0 + icon_s - 16, 88), (x0 + 16, 110)):
        p.drawPolygon([QPointF(tip_x, tip_y - 6), QPointF(tip_x + (7 if tip_y == 88 else -7), tip_y), QPointF(tip_x, tip_y + 6)])
    p.setPen(QColor("#0F172A"))
    p.setFont(tf)
    p.drawText(QRectF(x0 + icon_s + 18, 66, total, icon_s), Qt.AlignVCenter | Qt.AlignLeft, title_s)

    sub = "手机一拍 · 电脑就到 —— 新文件 3 秒自动弹到屏幕右侧"
    p.setPen(QColor("#4B5563"))
    p.setFont(font(23))
    p.drawText(QRectF(0, 152, W, 40), Qt.AlignHCenter | Qt.AlignVCenter, sub)

    # ---- 主视觉：手机 → 无线 → 电脑屏幕（右侧浮着我们的面板）
    # 手机
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#111827"))
    p.drawRoundedRect(QRectF(292, 236, 180, 376), 30, 30)
    p.setBrush(QColor("#FFFFFF"))
    p.drawRoundedRect(QRectF(300, 248, 164, 352), 22, 22)
    # 屏幕里的照片
    photo = QRectF(316, 300, 132, 96)
    p.fillRect(photo, QColor("#B5D4F4"))
    p.setBrush(QColor("#FAC775"))
    p.drawEllipse(QRectF(photo.right() - 30, photo.top() + 8, 22, 22))
    p.setBrush(QColor("#64748B"))
    p.drawPolygon([
        QPointF(photo.left(), photo.bottom()),
        QPointF(photo.left() + 44, photo.top() + 46),
        QPointF(photo.left() + 78, photo.bottom() - 18),
        QPointF(photo.left() + 100, photo.top() + 56),
        QPointF(photo.right(), photo.bottom()),
    ])
    p.setPen(QColor("#374151"))
    p.setFont(font(15))
    p.drawText(QRectF(300, 404, 164, 26), Qt.AlignHCenter | Qt.AlignVCenter, "刚拍的照片")
    # “新”角标
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#E24B4A"))
    p.drawEllipse(QRectF(424, 286, 44, 44))
    p.setPen(QColor("#FFFFFF"))
    p.setFont(font(18, bold=True))
    p.drawText(QRectF(424, 286, 44, 44), Qt.AlignCenter, "新")
    # 底部 home 条
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#D1D5DB"))
    p.drawRoundedRect(QRectF(352, 576, 60, 5), 3, 3)

    # ---- 中间：无线波纹 + 「3 秒」
    pen = QPen(QColor("#93C5FD"), 3, Qt.DashLine, Qt.RoundCap)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    for i, r in enumerate((96, 128, 160)):
        p.drawArc(QRectF(672 - r, 430 - r, r * 2, r * 2), -40 * 16, 80 * 16)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#2563EB"))
    p.drawEllipse(QRectF(664, 396, 72, 72))
    p.setPen(QColor("#FFFFFF"))
    p.setFont(font(24, bold=True))
    p.drawText(QRectF(664, 396, 72, 72), Qt.AlignCenter, "3 秒")

    # ---- 电脑屏幕
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#111827"))
    p.drawRoundedRect(QRectF(782, 250, 570, 384), 18, 18)
    p.setBrush(QColor("#DCE9FA"))
    p.drawRoundedRect(QRectF(794, 262, 546, 348), 10, 10)
    # 任务栏
    p.setBrush(QColor("#FFFFFF"))
    p.drawRect(QRectF(794, 578, 546, 32))
    p.setBrush(QColor("#C9DBF8"))
    for i in range(4):
        p.drawRoundedRect(QRectF(812 + i * 34, 586, 18, 16), 4, 4)
    # 支架
    p.setBrush(QColor("#111827"))
    p.drawRoundedRect(QRectF(1022, 634, 90, 16), 4, 4)
    p.drawRoundedRect(QRectF(982, 650, 170, 10), 5, 5)

    # ---- 屏幕右侧：面板本体（缩小复刻）
    px, py, pw = 1096, 288, 236
    p.setPen(QPen(QColor("#E2E8F0"), 1))
    p.setBrush(QColor("#FFFFFF"))
    p.drawRoundedRect(QRectF(px, py, pw, 306), 14, 14)
    p.setPen(QColor("#111827"))
    p.setFont(font(13, bold=True))
    p.drawText(QRectF(px + 14, py + 8, 130, 24), Qt.AlignVCenter | Qt.AlignLeft, "手机电脑互通")
    p.setPen(QColor("#16A34A"))
    p.setFont(font(11))
    p.drawText(QRectF(px + 120, py + 8, 102, 24), Qt.AlignVCenter | Qt.AlignRight, "● 无线已连接")
    card(p, px + 12, py + 40, "#378ADD", "photo")
    card(p, px + 12, py + 122, "#12B76A", "doc")
    # 投放区
    dz = QRectF(px + 12, py + 206, pw - 24, 84)
    p.setBrush(QColor("#F4F8FF"))
    p.setPen(QPen(QColor("#93C5FD"), 1.6, Qt.DashLine))
    p.drawRoundedRect(dz, 10, 10)
    p.setPen(QColor("#378ADD"))
    p.setFont(font(13))
    p.drawText(dz, Qt.AlignHCenter | Qt.AlignBottom, "拖文件发到手机")
    p.drawLine(QPointF(dz.center().x(), dz.top() + 18), QPointF(dz.center().x(), dz.top() + 44))

    # ---- 卖点 chips
    chips = ["零安装", "无线 / USB", "3 秒自动弹窗", "一键归档", "右键发送到手机"]
    widths = []
    p.setFont(font(19))
    for s in chips:
        widths.append(text_w(p, s, font(19)) + 44)
    total = sum(widths) + 16 * (len(chips) - 1)
    cx = (W - total) / 2
    for s, w in zip(chips, widths):
        chip(p, int(cx + w / 2), 712, s)
        cx += w + 16

    # ---- 底部说明
    p.setPen(QColor("#6B7280"))
    p.setFont(font(17))
    p.drawText(QRectF(0, 812, W, 30), Qt.AlignHCenter | Qt.AlignVCenter,
               "Windows 电脑 · 安卓手机免装 App · 局域网点对点，数据不过服务器")

    p.end()
    return img


def main() -> None:
    # 坑：没有 QGuiApplication 时 drawText 直接访问冲突（字体库未初始化）
    app = QGuiApplication(sys.argv)
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "docs/宣传图.png")
    out.parent.mkdir(parents=True, exist_ok=True)
    img = render()
    ok = img.save(str(out))
    print(f"save ok={ok} -> {out} {img.width()}x{img.height()} dpr={img.devicePixelRatio()}")


if __name__ == "__main__":
    main()
