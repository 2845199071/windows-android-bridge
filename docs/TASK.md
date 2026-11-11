# Ping（手机电脑互通）· 任务拆解

对应 `docs/DESIGN.md` v0.1 · 更新 2026-10-31（01:49，卡片 3 天时效 + 忽略链路 E2E 复验）

## 进度（2026-10-30）

**已完成**：M1~M4 全部、T5.1 设置界面（含手机目录浏览器、无线配对、开机自启动、外观调节）、
**T5.2 PyInstaller 打包**（onedir 75 MB，带图标）、**CPU 优化**（常驻 `adb shell -T` + SQLite WAL，
扫描每轮 149ms → 52ms）、**重启恢复未处理卡片**、**视频/Office 缩略图**（Windows Shell）、
**T4.4 资源管理器右键「发送到手机」**（含单实例转交 IPC）、**T3.5 全局热键**
（候选链 Ctrl+Alt+M → J → K，本机 M 被占用实际生效 J）、**托盘图标状态色**
（蓝待连 / 绿 USB / 橙无线 / 红离线）、**连接方式自动/手动可切换**
（设置页开关，手动=面板「连接」按钮点了才连，默认自动）、**暗夜模式**
（`ui_theme.py` 统一调色板：面板/预览/卡片/设置窗口全套深色，标题栏月亮/太阳
切换 + 设置页开关，`theme` 配置持久化）。

**T5.3 八小时挂机**：被中断（应用 17:43 后被用户关闭），仅 1 小时样本（内存平稳
~50MB 私有）；结论不充分，需要时可重跑。

**未做**：T3.3 批量聚合卡（已用「高度封顶 + 滚动」替代，用户确认过）。

测试：`tests/smoke.py` / `tests/e2e.py` / `tests/settings_test.py` / `tests/dock_test.py` /
`tests/session_test.py`(15) / `tests/session_live.py`(12, 真机) / `tests/restore_test.py`(9) /
`tests/shellmenu_test.py`(22) / `tests/hotkey_test.py`(6) / `tests/connect_mode_test.py`(18) /
`tests/theme_test.py`(15) / `tests/ttl_test.py`(9) 全绿。

## M1 · 骨架与 adb 通道

- [ ] **T1.1** 建项目骨架：`app/` + `docs/` + `requirements.txt` + `README.md`
  - 目录：`app/{main.py,adb.py,config.py,store.py,watcher.py,puller.py,archiver.py,thumbs.py,dock.py,tray.py}`
  - 验收：`python app/main.py` 能启动空白窗口
- [ ] **T1.2** `adb.py`：AdbChannel 类
  - `devices()` 解析 `adb devices -l`，返回 `[Device(serial, model, state)]`
  - `shell(cmd)` / `pull(remote, local)` / `push(local, remote)`
  - **全部用 subprocess 传 list，env 中 `MSYS_NO_PATHCONV=1`**（坑 P1）
  - 超时保护（shell 15s / pull 按文件大小动态）
  - 验收：单测能列出真机、能 pull 一个真实文件、中文名不乱码
- [ ] **T1.3** 通道择优：USB 在位用 USB，否则尝试 `adb connect <ip>:5555`
  - IP 获取：USB 连接时 `adb shell ip route | grep wlan0`
  - 断连指数退避重连（2→4→8→16→30s 封顶）
  - 验收：拔 USB 后自动切无线（需手机开无线调试）
- [ ] **T1.4** `store.py`：SQLite schema 初始化 + 迁移（`PRAGMA user_version`）
  - 五张表见 DESIGN §6
  - 验收：首次运行建库成功，二次运行不报错

## M2 · 监听与快照

- [ ] **T2.1** `watcher.py`：分层轮询调度器
  - 快路径 3s / 慢路径 15s，两条独立 QTimer
  - **单轮所有 find 用一次 adb shell 的 `;` 串联**（省往返）
  - 单例保护：上轮未结束跳过本轮
  - 验收：日志显示每轮耗时 ~1.5s，无堆积
- [ ] **T2.2** 目录白名单 + `-newermt` 增量
  - 用上轮最大 mtime 作下界
  - **排除** `MicroMsg/<hash>/image`（加密 dat，坑 P2 之外的性能杀手）
  - 验收：连续跑 10 分钟 CPU <5%
- [ ] **T2.3** 快照 diff → 新文件事件
  - `(path, mtime, size)` 三元组比对，写 `seen` 表
  - 已 ignored 的同 mtime 文件不再入队
  - 验收：手机拍一张照片，3s 内事件被捕获（只看日志不拉取）
- [ ] **T2.4** 手机离线/上线处理
  - 离线静默，恢复后一次性补齐（上限 100 个）
  - 验收：关手机 WiFi 再开，恢复后不重复弹窗

## M3 · 右侧悬浮窗

- [ ] **T3.1** `dock.py`：贴边无边框窗口
  - flags：`FramelessWindowHint | WindowStaysOnTopHint | Tool`
  - 贴屏幕右侧，宽度 340，圆角 + 半透明背景
  - 滑入/滑出动画（QPropertyAnimation，180ms）
  - 自动收起：鼠标离开 3s → 收成 44px 窄条（显示未读数）
  - 验收：能贴边、能滑入滑出、不抢焦点、不显示在任务栏
- [ ] **T3.2** 卡片组件
  - 96×96 缩略图 + 文件名（省略号）+ 大小/时间 + 「保存」「忽略」按钮
  - 悬停高亮；点击缩略图 → 大图预览
  - 验收：显示一张真实照片卡片，按钮可点
- [ ] **T3.3** 卡片流与批量
  - 新卡从底部滑入，旧卡上移
  - Ctrl 多选 / 全选按钮 → 批量保存
  - 单次 >20 个 → 聚合卡「20 个新文件」（可展开）
  - 验收：一次推 25 个文件，显示 1 张聚合卡 + 展开后 25 条
- [ ] **T3.4** `thumbs.py`：缩略图提供器
  - 图片：QImageReader + EXIF 方向
  - 视频：ctypes 调 Windows Shell（移植 shellthumb.py）
  - PDF：pypdfium2 首页
  - 其他：扩展名色块图标
  - 验收：jpg/mp4/pdf/xlsx 四种都能出缩略图
- [ ] **T3.5** `tray.py`：托盘 + 热键
  - 托盘菜单：显示/隐藏面板、打开归档目录、设置、退出
  - 托盘图标随通道状态变色（USB 绿 / WiFi 黄 / 断开红）
  - 全局热键 `Ctrl+Alt+M`
  - 验收：关掉窗口不退出进程，托盘能呼出

## M4 · 归档与反向发送

- [ ] **T4.1** `puller.py`：拉取线程池
  - 独立线程，进度回调更新卡片
  - 大文件可取消；失败重试 2 次
  - 验收：拉一个 100MB 视频，UI 不卡，进度可见
- [ ] **T4.2** `archiver.py`：归档
  - 扩展名 → 类型目录；mtime → 日期子目录
  - 同名自动 ` (1)` 后缀
  - 归档后写 `archive` 表、删 staging
  - 验收：中英文名、重名文件都正确落盘
- [ ] **T4.3** 反向发送
  - 悬浮窗底部投放区（拖拽）+ 托盘菜单「发送文件到手机」
  - `adb push` 到 `/sdcard/Download/电脑发送/`
  - 显示发送进度与结果提示
  - 验收：拖一个中文名文件，手机文件管理器可见且名字正常
- [ ] **T4.4** 资源管理器右键集成（可选）
  - 注册表加「发送到手机」动词
  - 验收：右键菜单出现并能发送

## M5 · 设置、打包与加固

- [ ] **T5.1** 设置界面
  - 归档根目录、扫描频率、监听目录白名单、开机自启、面板固定展开
  - 验收：改完重启生效
- [ ] **T5.2** 打包
  - PyInstaller onedir + Inno Setup
  - 装到 `%LOCALAPPDATA%\手机电脑互通\`
  - 验收：干净机器能装能跑
- [ ] **T5.3** 稳定性加固
  - 8h 挂机测试：内存 <200MB、无崩溃
  - 异常兜底：adb 消失、手机重启、目标目录被删
  - 验收：跑满 8h 无异常

## 依赖关系

```
M1 ──► M2 ──► M4
 └──► M3 ──┘
      └────► M5
```

M1 必须先做（一切依赖 adb 通道）。M2 与 M3 可并行（一个管数据一个管展示，通过 EventBus 解耦）。

## 建议节奏

- **第一刀（可演示）**：T1.1 + T1.2 + T2.1~T2.3 + T3.1 + T3.2 + T4.1 + T4.2
  → 效果：手机拍照，右侧弹窗出现，点一下归档到位。**这是能立刻验证价值的闭环。**
- **第二刀**：T1.3（无线）+ T3.3~T3.5（批量/托盘/热键）
- **第三刀**：T4.3 + T5.1 + T5.2 + T5.3
