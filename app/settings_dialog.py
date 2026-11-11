"""设置窗口 + 手机目录浏览器。

手机路径不是 Windows 路径，没法用系统文件对话框选，所以自己做一个基于
``adb shell find`` 的目录浏览器：从 /sdcard 起逐级点进去，选目录。
"""
from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QThread, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

import autostart
import shellmenu
from adb import AdbChannel, AdbError, _is_mdns, _quote
from config import APP_NAME, APP_VERSION, CONFIG
from ui_theme import is_dark, settings_style

class PairWorker(QThread):
    """配对要 2~5 秒，放后台线程，避免把设置窗口卡死。"""

    done = Signal(bool, str)

    def __init__(self, adb: AdbChannel, endpoint: str, code: str, parent=None) -> None:
        super().__init__(parent)
        self._adb = adb
        self._endpoint = endpoint
        self._code = code

    def run(self) -> None:  # noqa: D102
        try:
            ok, message = self._adb.pair(self._endpoint, self._code)
        except Exception as exc:
            ok, message = False, str(exc)
        self.done.emit(ok, message)


class PhoneDirBrowser(QDialog):
    """手机目录选择器：逐级浏览，返回选中目录。"""

    def __init__(self, adb: AdbChannel, start: str = "/sdcard", parent=None) -> None:
        super().__init__(parent)
        self._adb = adb
        self._current = start.rstrip("/") or "/sdcard"
        self._picked: str | None = None

        self.setWindowTitle("选择手机目录")
        self.setModal(True)
        self.resize(420, 440)
        self.setStyleSheet(settings_style(is_dark()))

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        self.path_label = QLabel(self._current, self)
        self.path_label.setObjectName("path")
        self.path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        layout.addWidget(self.path_label)

        self.list_widget = QListWidget(self)
        self.list_widget.itemDoubleClicked.connect(self._on_double_click)
        self.list_widget.currentItemChanged.connect(self._on_current_changed)
        layout.addWidget(self.list_widget, 1)

        self.hint = QLabel("双击进入文件夹，或单击选中后点右下角按钮", self)
        self.hint.setObjectName("sub")
        layout.addWidget(self.hint)

        buttons = QHBoxLayout()
        self.up_btn = QPushButton("上一级", self)
        self.up_btn.setObjectName("ghost")
        self.up_btn.clicked.connect(self._go_up)
        buttons.addWidget(self.up_btn)
        buttons.addStretch(1)
        cancel = QPushButton("取消", self)
        cancel.setObjectName("ghost")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        self.choose_btn = QPushButton("选这个目录", self)
        self.choose_btn.setObjectName("primary")
        self.choose_btn.setMinimumWidth(130)
        self.choose_btn.clicked.connect(self._choose)
        buttons.addWidget(self.choose_btn)
        layout.addLayout(buttons)

        self._reload()

    # ------------------------------------------------------------------ 逻辑
    @property
    def picked(self) -> str | None:
        return self._picked

    def _reload(self) -> None:
        self.path_label.setText(self._current)
        self.up_btn.setEnabled(self._current.rstrip("/") not in ("", "/sdcard"))
        self.list_widget.clear()

        try:
            # -L 必须加：/sdcard 本身是符号链接，不加的话 find 只看到链接本身，
            # -type d 不匹配，直接返回空。
            out = self._adb.shell(
                f"find -L '{_quote(self._current)}' -maxdepth 1 -type d 2>/dev/null", timeout=20
            )
        except AdbError as exc:
            self.hint.setText(f"读取失败：{exc}")
            return

        base = self._current.rstrip("/")
        paths = {
            line.strip().rstrip("/") for line in out.splitlines() if line.strip()
        } - {base, "/sdcard", ""}
        dirs = sorted(p for p in paths if not p.rsplit("/", 1)[-1].startswith("."))

        for path in dirs:
            name = path.rstrip("/").rsplit("/", 1)[-1]
            item = QListWidgetItem(f"▸  {name}")
            item.setData(Qt.UserRole, path)
            item.setSizeHint(QSize(0, 32))  # QSS 的 item 样式会把 sizeHint 算成 0，必须显式给
            self.list_widget.addItem(item)

        if not dirs:
            self.hint.setText("这个目录下没有子文件夹，可以直接选它")
        else:
            self.hint.setText(f"{len(dirs)} 个子文件夹 · 双击进入")

    def _on_double_click(self, item: QListWidgetItem) -> None:
        """双击进入文件夹。直接用被双击的 item，不依赖 currentItem（可能还没同步）。"""
        if item is not None:
            self._current = item.data(Qt.UserRole)
            self._reload()

    def _on_current_changed(self, current: QListWidgetItem | None, _previous) -> None:
        if current is not None:
            path = current.data(Qt.UserRole)
            name = path.rstrip("/").rsplit("/", 1)[-1]
            self.choose_btn.setText(f"选「{name}」")
            self.hint.setText(f"将选：{path}")
        else:
            self.choose_btn.setText("选这个目录")
            self.hint.setText("双击进入文件夹，或单击选中后点右下角按钮")

    def _go_up(self) -> None:
        parent = self._current.rstrip("/").rsplit("/", 1)[0] or "/"
        if parent in ("", "/"):
            return
        self._current = parent
        self._reload()

    def _choose(self) -> None:
        """优先用列表里选中的那一项；没选中任何项才用当前所在目录。"""
        item = self.list_widget.currentItem()
        if item is not None:
            self._picked = item.data(Qt.UserRole)
        else:
            self._picked = self._current
        self.accept()


class SettingsDialog(QDialog):
    """设置：手机连接 / 文件去向 / 扫描与外观。"""

    QUICK_DIRS = [
        ("下载 Download", "/sdcard/Download"),
        ("相机 DCIM", "/sdcard/DCIM/Camera"),
        ("图片 Pictures", "/sdcard/Pictures"),
        ("文档 Documents", "/sdcard/Documents"),
    ]

    def __init__(self, adb: AdbChannel, dock=None, parent=None, on_change=None,
                 on_rescan=None) -> None:
        super().__init__(parent)
        self._adb = adb
        self._dock = dock
        self._on_change = on_change      # 实时应用回调：_on_change(key, value, old)
        self._on_rescan = on_rescan      # 补拉回调：on_rescan(hours)
        self._applied: dict = {}         # 每个键最后一次应用的值（去重 + 校验失败回退）

        self.setWindowTitle(f"设置 · {APP_NAME}")
        self.setModal(True)
        self.setFixedWidth(500)
        self.setStyleSheet(settings_style(is_dark()))

        # 内容放进滚动区：1366x768 笔记本上整窗 900px 会被屏幕切掉
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._scroll = QScrollArea(self)
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.NoFrame)
        self._scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        body = QWidget(self._scroll)
        body.setObjectName("settingsBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(18, 14, 18, 6)
        layout.setSpacing(10)
        self._scroll.setWidget(body)
        outer.addWidget(self._scroll, 1)

        heading = QLabel("设置", self)
        heading.setObjectName("dlgTitle")
        layout.addWidget(heading)

        # --- 手机连接（连不上时后面那些设置都没意义，所以放最前）
        conn_group, conn_layout = self._make_group("手机连接")
        self._build_connection_section(conn_layout)
        layout.addWidget(conn_group)

        # --- 文件去向
        path_group, path_layout = self._make_group("文件去向")
        self._build_paths(path_layout)
        layout.addWidget(path_group)

        # --- 扫描与外观
        misc_group, misc_layout = self._make_group("扫描与外观")
        self._build_misc(misc_layout)
        layout.addWidget(misc_group)

        layout.addSpacing(2)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        ver = QLabel(f"{APP_NAME} v{APP_VERSION}", self)
        ver.setObjectName("sub")
        footer.addWidget(ver)
        footer.addStretch(1)
        # 所有设置项都是改完即时生效（滑杆/开关当场应用，路径在输入框失焦时应用），
        # 所以只有「完成」一个按钮 —— 它只负责关窗口，不承担保存职责。
        done = QPushButton("完成", self)
        done.setObjectName("primary")
        done.setMinimumWidth(88)
        done.setDefault(True)
        done.clicked.connect(self.accept)
        footer.addWidget(done)

        foot = QWidget(self)
        foot_layout = QVBoxLayout(foot)
        foot_layout.setContentsMargins(20, 4, 20, 14)
        foot_layout.setSpacing(6)
        self._error = QLabel("", foot)
        self._error.setStyleSheet("color: #DC2626; font: 11px 'Microsoft YaHei UI';")
        self._error.setWordWrap(True)
        foot_layout.addWidget(self._error)
        foot_layout.addLayout(footer)
        outer.addWidget(foot)

        self._load()

        self._poll_timer = QTimer(self)
        self._poll_timer.setInterval(2500)
        self._poll_timer.timeout.connect(self._refresh_connection)
        self._pair_worker: PairWorker | None = None

        # 尺寸：屏幕可用高度的 90% 与 860 取小，内容超出靠滚动区（1366x768 也放得下）
        avail = QGuiApplication.primaryScreen().availableGeometry()
        self.resize(500, int(min(860, avail.height() * 0.9)))

    # ------------------------------------------------------------------ 分组
    def _make_group(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        """一个浅底圆角卡片 + 蓝色小标题，用来把设置项分层。"""
        frame = QFrame(self)
        frame.setObjectName("group")
        inner = QVBoxLayout(frame)
        inner.setContentsMargins(14, 10, 14, 12)
        inner.setSpacing(6)
        label = QLabel(title, frame)
        label.setObjectName("groupTitle")
        inner.addWidget(label)
        return frame, inner

    def _field(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("fieldLabel")
        return label

    # ------------------------------------------------------------------ 文件去向
    def _build_paths(self, layout: QVBoxLayout) -> None:
        layout.addWidget(self._field("电脑 → 手机  发送目录"))
        row = QHBoxLayout()
        row.setSpacing(6)
        self.send_edit = QLineEdit(self)
        self.send_edit.setPlaceholderText("/sdcard/Download/电脑发送")
        self.send_edit.editingFinished.connect(self._apply_send_dir)
        row.addWidget(self.send_edit, 1)
        browse_phone = QPushButton("浏览手机…", self)
        browse_phone.setObjectName("ghost")
        browse_phone.clicked.connect(self._browse_phone)
        row.addWidget(browse_phone)
        layout.addLayout(row)

        chips = QHBoxLayout()
        chips.setSpacing(6)
        for text, path in self.QUICK_DIRS:
            chip = QPushButton(text, self)
            chip.setObjectName("chip")
            chip.setCursor(Qt.PointingHandCursor)
            chip.clicked.connect(lambda _=False, p=path: self._apply_quick(p))
            chips.addWidget(chip)
        chips.addStretch(1)
        layout.addLayout(chips)

        hint = QLabel(
            "拖进悬浮窗的文件会存到这里；不存在的子文件夹会自动创建。\n"
            "面板上的卡片可以直接拖到微信 / 文件夹 / 其他程序，拖出时会自动归档到这里。",
            self,
        )
        hint.setObjectName("sub")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        layout.addSpacing(6)

        layout.addWidget(self._field("手机 → 电脑  归档根目录"))
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        self.archive_edit = QLineEdit(self)
        self.archive_edit.setPlaceholderText(r"D:\手机归档")
        row2.addWidget(self.archive_edit, 1)
        browse_pc = QPushButton("浏览…", self)
        browse_pc.setObjectName("ghost")
        browse_pc.clicked.connect(self._browse_pc)
        row2.addWidget(browse_pc)
        layout.addLayout(row2)

        hint2 = QLabel(r"保存时按「类型\日期\原文件名」分层，例如 图片\2026-10-30\xxx.jpg", self)
        hint2.setObjectName("sub")
        hint2.setWordWrap(True)
        layout.addWidget(hint2)

        layout.addSpacing(6)
        shell_row = QHBoxLayout()
        shell_row.setSpacing(8)
        self.shell_check = QCheckBox("在资源管理器右键菜单里加「发送到手机」", self)
        self.shell_check.toggled.connect(self._apply_shell_menu)
        self.shell_check.setToolTip(
            "装好后在任意文件上右键就能发到手机，支持多选；只写当前用户的注册表，不需要管理员"
        )
        shell_row.addWidget(self.shell_check)
        shell_row.addStretch(1)
        layout.addLayout(shell_row)

    # ------------------------------------------------------------------ 扫描与外观
    def _build_misc(self, layout: QVBoxLayout) -> None:
        interval_row = QHBoxLayout()
        interval_row.setSpacing(8)
        interval_row.addWidget(self._field("常用目录"))
        self.fast_combo = self._combo(
            [("2 秒", 2), ("3 秒", 3), ("5 秒", 5), ("10 秒", 10), ("20 秒", 20)],
            int(CONFIG.get("fast_interval", 3)),
        )
        self.fast_combo.setToolTip("拍照、截图、下载这些目录多久查一次")
        self.fast_combo.currentIndexChanged.connect(self._apply_intervals)
        interval_row.addWidget(self.fast_combo)
        interval_row.addSpacing(12)
        interval_row.addWidget(self._field("其他目录"))
        self.slow_combo = self._combo(
            [("15 秒", 15), ("30 秒", 30), ("1 分钟", 60), ("2 分钟", 120), ("5 分钟", 300)],
            int(CONFIG.get("slow_interval", 15)),
        )
        self.slow_combo.currentIndexChanged.connect(self._apply_intervals)
        interval_row.addWidget(self.slow_combo)
        interval_row.addStretch(1)
        # 补拉：天没开机时程序只同步最近的，想找回更早的文件点这里
        rescan_row = QHBoxLayout()
        rescan_row.setSpacing(8)
        rescan_row.addWidget(self._field("补拉旧文件"))
        self.rescan_combo = self._combo(
            [("1 小时", 1), ("6 小时", 6), ("12 小时", 12), ("1 天", 24),
             ("3 天", 72), ("7 天", 168), ("10 天", 240)],
            24,
        )
        rescan_row.addWidget(self.rescan_combo)
        self.rescan_btn = QPushButton("开始补拉", self)
        self.rescan_btn.setObjectName("ghost")
        self.rescan_btn.setCursor(Qt.PointingHandCursor)
        self.rescan_btn.clicked.connect(self._on_rescan_clicked)
        rescan_row.addWidget(self.rescan_btn)
        self.rescan_hint = QLabel("找回漏掉的旧文件", self)
        self.rescan_hint.setObjectName("sub")
        self.rescan_hint.setWordWrap(True)
        rescan_row.addWidget(self.rescan_hint, 1)
        layout.addLayout(rescan_row)
        layout.addLayout(interval_row)

        alpha_row = QHBoxLayout()
        alpha_row.setSpacing(10)
        alpha_row.addWidget(self._field("不透明度"))
        self.alpha_slider = QSlider(Qt.Horizontal, self)
        self.alpha_slider.setRange(35, 100)
        self._original_alpha = float(CONFIG.get("panel_alpha", 0.94))
        self.alpha_slider.setValue(int(self._original_alpha * 100))
        self.alpha_slider.valueChanged.connect(self._on_alpha_changed)
        alpha_row.addWidget(self.alpha_slider, 1)
        self.alpha_label = QLabel(f"{int(self._original_alpha * 100)}%", self)
        self.alpha_label.setMinimumWidth(38)
        self.alpha_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        alpha_row.addWidget(self.alpha_label)
        layout.addLayout(alpha_row)

        width_row = QHBoxLayout()
        width_row.setSpacing(8)
        width_row.addWidget(self._field("面板宽度"))
        self.width_combo = self._combo(
            [("窄（340）", 340), ("中（380）", 380), ("宽（420）", 420)],
            int(CONFIG.get("dock_width", 340)),
        )
        self.width_combo.currentIndexChanged.connect(self._apply_panel_width)
        width_row.addWidget(self.width_combo)
        width_hint = QLabel("文件名字长选宽一点", self)
        width_hint.setObjectName("sub")
        width_row.addWidget(width_hint, 1)
        layout.addLayout(width_row)

        pos_row = QHBoxLayout()
        pos_row.setSpacing(8)
        pos_row.addWidget(self._field("面板位置"))
        reset_btn = QPushButton("复位到右上角", self)
        reset_btn.setObjectName("ghost")
        reset_btn.setCursor(Qt.PointingHandCursor)
        reset_btn.setToolTip("把面板挪回屏幕右上角")
        reset_btn.clicked.connect(self._reset_position)
        pos_row.addWidget(reset_btn)
        pos_hint = QLabel("平时直接拖标题栏移动", self)
        pos_hint.setObjectName("sub")
        pos_row.addWidget(pos_hint, 1)
        layout.addLayout(pos_row)

        collapse_row = QHBoxLayout()
        collapse_row.setSpacing(8)
        collapse_row.addWidget(self._field("自动收起"))
        self.collapse_combo = self._combo(
            [("不自动收起", 0), ("3 秒", 3), ("5 秒", 5), ("8 秒", 8),
             ("10 秒", 10), ("30 秒", 30)],
            int(CONFIG.get("auto_collapse_seconds", 5)),
        )
        self.collapse_combo.currentIndexChanged.connect(self._apply_collapse)
        self.collapse_combo.setToolTip(
            "鼠标离开后多少秒收起成小圆点；有未处理文件时不会自动收，处理完才收"
        )
        collapse_row.addWidget(self.collapse_combo)
        collapse_row.addStretch(1)
        layout.addLayout(collapse_row)

        autostart_row = QHBoxLayout()
        autostart_row.setSpacing(8)
        self.autostart_check = QCheckBox("开机时自动启动", self)
        self.autostart_check.toggled.connect(self._apply_autostart)
        self.autostart_check.setToolTip("开机后在后台静默运行，只在托盘显示图标，不弹任何窗口")
        autostart_row.addWidget(self.autostart_check)
        autostart_row.addStretch(1)
        layout.addLayout(autostart_row)

        theme_row = QHBoxLayout()
        theme_row.setSpacing(8)
        self.dark_check = QCheckBox("深色模式（面板与设置窗口）", self)
        self.dark_check.toggled.connect(self._apply_theme)
        self.dark_check.setToolTip(
            "暗夜配色，晚上看舒服；也可以随时点面板标题栏的月亮/太阳图标切换"
        )
        theme_row.addWidget(self.dark_check)
        theme_row.addStretch(1)
        layout.addLayout(theme_row)

        hint = QLabel(
            "调低不透明度能透出后面的内容，文字仍然清晰；按住面板标题栏可以拖到任意位置。", self
        )
        hint.setObjectName("sub")
        hint.setWordWrap(True)
        layout.addWidget(hint)

    # ------------------------------------------------------------------ 连接
    def _build_connection_section(self, layout: QVBoxLayout) -> None:
        auto_row = QHBoxLayout()
        auto_row.setSpacing(8)
        self.auto_check = QCheckBox("自动连接（断线自动重连，推荐）", self)
        self.auto_check.toggled.connect(self._apply_connect_mode)
        self.auto_check.setToolTip(
            "取消勾选改为手动连接：未连接时面板标题栏出现「连接」按钮，"
            "点了才连；手机息屏掉线后不会自动重连"
        )
        auto_row.addWidget(self.auto_check)
        auto_row.addStretch(1)
        layout.addLayout(auto_row)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.conn_label = QLabel("检查中…", self)
        self.conn_label.setStyleSheet("font: 12px 'Microsoft YaHei UI'; color: #8A94A6;")
        row.addWidget(self.conn_label, 1)

        self.refresh_btn = QPushButton("刷新", self)
        self.refresh_btn.setObjectName("ghost")
        self.refresh_btn.clicked.connect(self._refresh_connection)
        row.addWidget(self.refresh_btn)

        self.disconnect_btn = QPushButton("断开无线", self)
        self.disconnect_btn.setObjectName("ghost")
        self.disconnect_btn.clicked.connect(self._disconnect_wireless)
        row.addWidget(self.disconnect_btn)
        layout.addLayout(row)

        pair_row = QHBoxLayout()
        pair_row.setSpacing(6)
        self.pair_addr = QLineEdit(self)
        self.pair_addr.setPlaceholderText("配对地址  192.168.1.103:40061")
        pair_row.addWidget(self.pair_addr, 1)
        self.pair_code = QLineEdit(self)
        self.pair_code.setPlaceholderText("配对码")
        self.pair_code.setMaxLength(6)
        self.pair_code.setFixedWidth(84)
        self.pair_code.returnPressed.connect(self._start_pair)
        pair_row.addWidget(self.pair_code)
        self.pair_btn = QPushButton("配对", self)
        self.pair_btn.setObjectName("primary")
        self.pair_btn.setMinimumWidth(72)
        self.pair_btn.clicked.connect(self._start_pair)
        pair_row.addWidget(self.pair_btn)
        layout.addLayout(pair_row)

        self.pair_hint = QLabel(
            "首次使用或换手机：手机「开发者选项 → 无线调试 → 使用配对码配对设备」，"
            "把弹框里的地址和配对码填到上面（那个地址和无线调试主页显示的端口不是同一个）",
            self,
        )
        self.pair_hint.setStyleSheet("font: 11px 'Microsoft YaHei UI'; color: #8A94A6;")
        self.pair_hint.setWordWrap(True)
        layout.addWidget(self.pair_hint)

    def _set_pair_hint(self, text: str, error: bool = False) -> None:
        color = "#DC2626" if error else "#8A94A6"
        self.pair_hint.setText(text)
        self.pair_hint.setStyleSheet(f"font: 11px 'Microsoft YaHei UI'; color: {color};")

    def _refresh_connection(self) -> None:
        try:
            devices = [d for d in self._adb.devices() if d.state == "device"]
        except AdbError:
            devices = []

        if not devices:
            self.conn_label.setText("● 未连接")
            self.conn_label.setStyleSheet("font: 12px 'Microsoft YaHei UI'; color: #DC2626;")
            self.disconnect_btn.setEnabled(False)
            return

        usb = [d for d in devices if d.transport == "usb"]
        chosen = usb[0] if usb else next((d for d in devices if _is_mdns(d.serial)), devices[0])
        if chosen.transport == "usb":
            self.conn_label.setText(f"● USB 已连接 · {chosen.label}")
            self.conn_label.setStyleSheet("font: 12px 'Microsoft YaHei UI'; color: #12B76A;")
        else:
            self.conn_label.setText(f"● 无线已连接 · {chosen.label}")
            self.conn_label.setStyleSheet("font: 12px 'Microsoft YaHei UI'; color: #F59E0B;")
        self.disconnect_btn.setEnabled(True)

    def _disconnect_wireless(self) -> None:
        try:
            devices = [d for d in self._adb.devices() if d.state == "device"]
        except AdbError:
            devices = []
        for device in [d for d in devices if d.transport != "usb"]:
            self._adb.disconnect(device.serial)
        self._refresh_connection()
        if "未连接" in self.conn_label.text():
            self._set_pair_hint("已断开。想重连：手机重新打开「无线调试」开关即可，不用再配对。")
        else:
            self._set_pair_hint(
                "无线还在连着（mDNS 会自动重连）。要彻底断开，请在手机上关掉「无线调试」开关。"
            )

    def _start_pair(self) -> None:
        endpoint = self.pair_addr.text().strip()
        code = self.pair_code.text().strip()
        if not endpoint or not code:
            self._set_pair_hint("先填配对地址和 6 位配对码（在手机那个配对弹框里）", error=True)
            return
        self.pair_btn.setEnabled(False)
        self.pair_btn.setText("配对中…")
        self._set_pair_hint("正在配对，请保持手机上的配对弹框开着…")
        self._pair_worker = PairWorker(self._adb, endpoint, code, self)
        self._pair_worker.done.connect(self._on_paired)
        self._pair_worker.start()

    def _on_paired(self, ok: bool, message: str) -> None:
        self.pair_btn.setEnabled(True)
        self.pair_btn.setText("配对")
        if not ok:
            self._set_pair_hint(message, error=True)
            return
        self._set_pair_hint("配对成功，正在连接…")
        self.pair_code.clear()
        QTimer.singleShot(900, self._after_paired)

    def _after_paired(self) -> None:
        if not self.isVisible():
            return
        try:
            self._adb.ensure_device()
        except AdbError:
            pass
        self._refresh_connection()
        if "已连接" in self.conn_label.text():
            self._set_pair_hint("已连接。以后只要手机开着「无线调试」，程序会自动连，不用再配对。")
        else:
            self._set_pair_hint("已配对但还没连上——把手机「无线调试」页面开着，几秒后点「刷新」。")

    def showEvent(self, event) -> None:  # noqa: N802
        super().showEvent(event)
        self._refresh_connection()
        self._poll_timer.start()

    def hideEvent(self, event) -> None:  # noqa: N802
        self._poll_timer.stop()
        super().hideEvent(event)

    # ------------------------------------------------------------------ 组件
    def _header(self, text: str) -> QLabel:
        label = QLabel(text, self)
        label.setObjectName("h")
        return label

    def _load(self) -> None:
        self.send_edit.setText(str(CONFIG.get("remote_send_dir") or "/sdcard/Download/电脑发送"))
        self.archive_edit.setText(str(CONFIG.get("archive_root") or ""))
        self._select_value(self.fast_combo, int(CONFIG.get("fast_interval", 3)))
        self._select_value(self.slow_combo, int(CONFIG.get("slow_interval", 15)))
        self._select_value(self.collapse_combo, int(CONFIG.get("auto_collapse_seconds", 5)))
        self.autostart_check.setChecked(autostart.is_enabled())
        self.shell_check.setChecked(shellmenu.is_installed())
        self.auto_check.setChecked(CONFIG.get("connect_mode", "auto") == "auto")
        self.dark_check.setChecked(CONFIG.get("theme", "light") == "dark")

    def _apply_quick(self, path: str) -> None:
        """快捷按钮：替换父目录，保留当前子目录名（如果是自定义的）。"""
        current = self.send_edit.text().strip().rstrip("/")
        tail = ""
        if current and current.startswith("/sdcard/") and current.count("/") > 2:
            candidate = current.rsplit("/", 1)[-1]
            if candidate and candidate not in ("Download", "Camera", "Pictures", "Documents"):
                tail = "/" + candidate
        self.send_edit.setText(path + tail)

    def _browse_phone(self) -> None:
        # 从 /sdcard 顶层开始：手机上的常用文件夹（A常用、Download 等）都在这一层。
        # 若从当前发送目录的上级开始，用户还得先点一次「上一级」才看得到。
        dialog = PhoneDirBrowser(self._adb, "/sdcard", self)
        if dialog.exec() == QDialog.Accepted and dialog.picked:
            self.send_edit.setText(dialog.picked)

    def _browse_pc(self) -> None:
        start = self.archive_edit.text().strip() or str(CONFIG.archive_root)
        chosen = QFileDialog.getExistingDirectory(self, "选择归档根目录", start)
        if chosen:
            self.archive_edit.setText(chosen)

    # ------------------------------------------------------------------ 外观
    def _on_alpha_changed(self, value: int) -> None:
        self.alpha_label.setText(f"{value}%")
        if self._dock is not None:
            self._dock.set_alpha(value / 100)

    def _reset_position(self) -> None:
        if self._dock is not None:
            self._dock.reset_position()

    def reject(self) -> None:
        # 所有改动都已实时落地，关闭窗口不需要任何回滚
        super().reject()

    # ------------------------------------------------------------------ 实时应用
    def _apply_config(self, key: str, value) -> None:
        """写配置并通知宿主；值没变就跳过（滑杆/开关的重复触发都在这里被滤掉）。"""
        old = self._applied.get(key, CONFIG.get(key))
        if old == value:
            return
        CONFIG.set(key, value)
        self._applied[key] = value
        if self._on_change is not None:
            self._on_change(key, value, old)

    def _apply_send_dir(self) -> None:
        """发送目录：输入框失焦/回车时校验并应用；非法就报错并回退文本。"""
        text = self.send_edit.text().strip().rstrip("/")
        if text == self._applied.get("remote_send_dir", CONFIG.get("remote_send_dir")):
            self._error.setText("")
            return
        if not text.startswith("/"):
            self._error.setText("发送目录要填手机上的绝对路径，以 / 开头，例如 /sdcard/Download/电脑发送")
            self.send_edit.setText(str(self._applied.get("remote_send_dir", "")))
            return
        if text in ("/sdcard", "/storage/emulated/0", "/"):
            self._error.setText("不要直接选手机根目录，会跟系统文件混在一起，请再往里选一层")
            self.send_edit.setText(str(self._applied.get("remote_send_dir", "")))
            return
        self._error.setText("")
        self.send_edit.setText(text)
        self._apply_config("remote_send_dir", text)

    def _apply_archive_root(self) -> None:
        """归档根目录：失焦/回车时校验并应用。"""
        text = self.archive_edit.text().strip()
        if text == self._applied.get("archive_root", CONFIG.get("archive_root")):
            self._error.setText("")
            return
        if not text:
            self._error.setText("归档根目录不能为空")
            self.archive_edit.setText(str(self._applied.get("archive_root", "")))
            return
        if len(text.rstrip("\\/")) <= 2:
            self._error.setText("归档目录不要选盘符根目录，建议新建一个专用文件夹")
            self.archive_edit.setText(str(self._applied.get("archive_root", "")))
            return
        self._error.setText("")
        self.archive_edit.setText(text)
        self._apply_config("archive_root", text)

    def _apply_intervals(self) -> None:
        """扫描间隔：值一变就应用；fast > slow 时自动抬高 slow 保持不变式。"""
        fast_raw, slow_raw = self.fast_combo.currentData(), self.slow_combo.currentData()
        if fast_raw is None or slow_raw is None:
            return
        fast, slow = int(fast_raw), int(slow_raw)
        if fast > slow:
            if self.sender() is self.slow_combo:
                self._select_value(self.fast_combo, slow)
                fast = slow
            else:
                self._select_value(self.slow_combo, fast)
                slow = fast
        self._apply_config("fast_interval", fast)
        self._apply_config("slow_interval", slow)

    def _apply_panel_width(self) -> None:
        data = self.width_combo.currentData()
        if data is not None:
            self._apply_config("dock_width", int(data))

    def _apply_collapse(self) -> None:
        data = self.collapse_combo.currentData()
        if data is not None:
            self._apply_config("auto_collapse_seconds", int(data))

    def _apply_connect_mode(self, checked: bool) -> None:
        self._apply_config("connect_mode", "auto" if checked else "manual")

    def _apply_theme(self, checked: bool) -> None:
        dark = bool(checked)
        self.setStyleSheet(settings_style(dark))   # 设置窗口自己也当场换装
        self._apply_config("theme", "dark" if dark else "light")

    def _apply_autostart(self, checked: bool) -> None:
        ok, err = autostart.set_enabled(bool(checked))
        if not ok:
            self.autostart_check.setChecked(not checked)   # 失败回退
            QMessageBox.warning(self, "自启动设置失败", err)

    def _apply_shell_menu(self, checked: bool) -> None:
        ok, err = shellmenu.install() if checked else shellmenu.uninstall()
        if not ok:
            self.shell_check.setChecked(not checked)
            QMessageBox.warning(self, "右键菜单设置失败", err)

    def _on_rescan_clicked(self) -> None:
        """补拉：把水位线回退到 N 小时前，立刻扫一轮找回旧文件。"""
        hours = int(self.rescan_combo.currentData() or 24)
        if self._on_rescan is None:
            return
        try:
            self._on_rescan(hours)
        except Exception as exc:      # noqa: BLE001 补拉失败不该炸设置窗口
            self._error.setText(f"补拉失败：{exc}")
            return
        self._error.setText("")
        self.rescan_hint.setText(f"已开始补拉过去 {hours} 小时的文件，稍等几秒看面板")

    # ------------------------------------------------------------------ 下拉辅助
    def _combo(self, presets: list[tuple[str, int]], current: int) -> QComboBox:
        """常用值下拉：预设之外的现值自动补一条「自定义」，不改用户配置。"""
        cb = QComboBox(self)
        cb.setCursor(Qt.PointingHandCursor)
        for label, value in presets:
            cb.addItem(label, value)
        idx = cb.findData(int(current))
        if idx < 0:
            cb.addItem(f"自定义（{int(current)}）", int(current))
            idx = cb.count() - 1
        cb.setCurrentIndex(max(0, idx))
        return cb

    @staticmethod
    def _select_value(cb: QComboBox, value: int) -> None:
        """按值选中下拉项（不在预设里就补一条自定义）。"""
        value = int(value)
        idx = cb.findData(value)
        if idx < 0:
            cb.addItem(f"自定义（{value}）", value)
            idx = cb.count() - 1
        cb.setCurrentIndex(idx)

    def values(self) -> dict:
        return {
            "remote_send_dir": self.send_edit.text().strip().rstrip("/"),
            "archive_root": self.archive_edit.text().strip(),
            "fast_interval": int(self.fast_combo.currentData() or 3),
            "slow_interval": int(self.slow_combo.currentData() or 15),
            "auto_collapse_seconds": int(self.collapse_combo.currentData() or 0),
            "dock_width": int(self.width_combo.currentData() or 340),
            "connect_mode": "auto" if self.auto_check.isChecked() else "manual",
            "dark_theme": self.dark_check.isChecked(),
            # 不进 CONFIG，由调用方直接写注册表
            "autostart": self.autostart_check.isChecked(),
            "shell_menu": self.shell_check.isChecked(),
        }
