# 手机电脑互通（桌面端）· 设计文档

版本 v0.1 · 2026-10-30

## 1. 需求确认

| 项 | 结论 | 来源 |
|---|---|---|
| 核心痛点 | 手机文件到电脑要手动翻文件夹再另存，路径不固定、操作重复 | 用户原话 |
| 正向（手机→电脑） | 手机新保存的图片/文件自动出现在电脑右侧悬浮窗，点击即归档到固定目录 | 用户 |
| 反向（电脑→手机） | 电脑上选文件，直接推送到手机指定文件夹 | 用户 |
| 连接方式 | **无线 + 有线都要**，自动择优 | 用户确认 |
| 监听范围 | **清单内全部目录**（含微信/QQ 接收） | 用户确认 |
| 归档方式 | **固定根目录 + 按类型/日期自动分子目录** | 用户确认 |
| 手机端 | 零安装（不开发 Android App） | 技术决策 D2 |
| 平台 | Windows 桌面 | 现状 |

## 2. 技术可行性验证（已实测，2026-10-30）

测试设备：REDMI 2602BRT18C / Android 17 (SDK 37) / adb 37.0.1

| 能力 | 结果 | 备注 |
|---|---|---|
| `adb shell ls /sdcard` | ✅ | 全目录可读 |
| `adb push → /sdcard/Download` | ✅ | 实测写入成功，`media_rw` 权限 |
| `adb pull` 中文文件名 | ✅ | 需 `MSYS_NO_PATHCONV=1`（见坑 P1） |
| `find -newermt @<epoch>` | ✅ | toybox 0.8.13 支持，**检测主通道** |
| `stat -c '%Y %s %n'` | ✅ | 可取 mtime/size |
| `content query` MediaStore | ⚠️ 可用但慢 | 6.45s/次，`--projection` 只能单列（见坑 P2） |
| 微信 `MicroMsg/Download` | ✅ 可读（当前为空） | 实际落地在 `/sdcard/Download/WeiXin` |
| 微信 `MicroMsg/<hash>/image` | ❌ 不采用 | 加密 `.dat`，3150 个目录、5.37s，价值为零 |
| QQ `Tencent/QQfile_recv` | ✅ | 含真实文件，0.16s |

### 性能基线

| 策略 | 耗时 | 决策 |
|---|---|---|
| 全白名单 `find maxdepth 4` | 8.73s | ❌ 不可用 |
| MediaStore 单列查询 | 6.45s | ❌ 不可用 |
| 浅层 `find maxdepth 2`（4 主目录） | **1.14s** | ✅ 快路径 |
| QQ Tencent `maxdepth 3` | 0.16s | ✅ 慢路径 |
| 微信 MicroMsg 只列目录 | 5.37s | ❌ 排除 |

结论：**主通道用目录遍历 + mtime**，MediaStore 仅作为可选兜底（配置项，默认关闭）。

## 3. 架构

```
┌──────────────────────── Windows 端 (Python + PySide6) ────────────────────────┐
│                                                                               │
│  ┌──────────────┐      ┌───────────────────┐      ┌────────────────────┐     │
│  │ AdbChannel   │◄────►│ WatchScheduler    │─────►│ EventBus (Queue)   │     │
│  │ USB/WiFi 双模 │      │ 快/慢两条轮询线    │      │ 新文件事件          │     │
│  └──────┬───────┘      └─────────┬─────────┘      └─────────┬──────────┘     │
│         │                        │                          │                │
│         │              ┌─────────▼─────────┐      ┌─────────▼──────────┐     │
│         │              │ SnapshotStore     │      │ Puller (线程池)     │     │
│         │              │ SQLite 快照 diff   │      │ adb pull → staging │     │
│         │              └───────────────────┘      └─────────┬──────────┘     │
│         │                                                   │                │
│  ┌──────▼────────┐      ┌───────────────────┐      ┌────────▼───────────┐    │
│  │ Sender        │      │ SideDock (右侧悬浮)│◄─────│ ThumbProvider      │    │
│  │ push 到手机    │      │ 卡片流 + 一键归档  │      │ 图/视频/文档图标    │    │
│  └───────────────┘      └─────────┬─────────┘      └────────────────────┘    │
│                                   │                                          │
│                         ┌─────────▼─────────┐                                │
│                         │ Archiver          │                                │
│                         │ 类型/日期分目录     │                                │
│                         └───────────────────┘                                │
└───────────────────────────────────────────────────────────────────────────────┘
                                    │ adb (USB 优先 / WiFi 兜底)
                        ┌───────────▼───────────┐
                        │ 安卓手机（零安装）      │
                        │ /sdcard/*             │
                        └───────────────────────┘
```

## 4. 核心流程

### 4.1 手机 → 电脑

1. `WatchScheduler` 快路径每 **3s**、慢路径每 **15s** 执行一次分层 find
2. 结果 `(path, mtime, size)` 与 `SnapshotStore` 比对，新增项入 `EventBus`
3. `Puller` 线程 `adb pull` 到 `%LOCALAPPDATA%\手机电脑互通\staging\<uuid>.<ext>`
4. `ThumbProvider` 生成缩略图（图片解码 / 视频首帧 / 文档图标）
5. `SideDock` 从右侧滑入一张卡片
6. 用户点「保存」→ `Archiver` 移动到 `D:\手机归档\<类型>\<日期>\<原名>`
7. 用户点「忽略」→ 删除 staging 副本（手机原件保留），记录已忽略避免再弹

### 4.2 电脑 → 手机

1. 悬浮窗底部有投放区，或资源管理器右键「发送到手机」
2. `adb push` 到 `/sdcard/Download/电脑发送/`
3. 手机上用文件管理器 / 微信「从手机相册选择」可见

## 5. 关键设计决策

### D1 · 弹窗形态：右侧贴边悬浮面板（非系统通知）

- 无边框窗口，`Qt.WindowStaysOnTopHint | Qt.Tool`，贴右屏边缘
- 宽度 340px，圆角 + 半透明，从右滑入（180ms 缓动）
- 鼠标离开 3s 自动收成 44px 窄条，窄条上显示未读数量
- 卡片：96×96 缩略图 + 文件名（超长省略）+ 大小/时间 + 「保存」「忽略」
- 支持多选批量保存；单次 >20 个新文件折叠为一条「20 个新文件」聚合卡
- 托盘图标常驻，热键 `Ctrl+Alt+M` 呼出/收起

**备选**：Windows 原生 Toast。放弃理由：不能点击归档、无缩略图、批量管理不可能、堆叠会覆盖。

### D2 · 传输通道：adb，不开发手机 App

- **USB 优先**：`adb devices` 出现即用（快 10 倍）
- **WiFi 兜底**：USB 连接时自动抓 `adb shell ip route` 拿手机 IP，后续靠 `adb connect <ip>:5555`
- 无线调试用 Android 11+ 官方配对（用户手机 Android 17 支持）
- 断连自动重试（指数退避 2s→30s），托盘图标变色提示

**备选**：自研 Android App + WebSocket。放弃理由：开发量 3 倍，且要处理前台服务保活、电池白名单、厂商杀后台；而 adb 已能读写任意目录，功能等价。**若将来需要脱离 USB 调试开关，再迭代该方案，UI 层不用改。**

### D3 · 检测策略：分层轮询 + 快照 diff

```
快路径 3s   : /sdcard/DCIM  /sdcard/Pictures  /sdcard/Download  /sdcard/Documents   (maxdepth 2)
慢路径 15s  : /sdcard/Screenshots /sdcard/Movies /sdcard/Music /sdcard/Download/WeiXin
              /sdcard/Android/data/com.tencent.mobileqq/Tencent/QQfile_recv
              /sdcard/Android/data/com.tencent.mm/MicroMsg/Download
```

- 一轮内所有 find 用**一次** `adb shell` 的 `;` 串联执行，减少进程往返（每次约 150ms）
- 用上轮最大 mtime 作为 `-newermt @<ts>` 下界，避免全量遍历
- 调度器单例：上轮未结束则跳过本轮，杜绝堆积
- 手机离线时静默等待，恢复后一次性补齐（补拉上限 100 个）

### D4 · 归档结构

```
D:\手机归档\                     ← 根目录可配置
├─ 图片\2026-10-30\Screenshot_2026-10-30-01-03-21.jpg
├─ 视频\2026-10-30\MVIMG_20261029_191126.mp4
├─ 文档\2026-10-30\2026年事业部委外安全考试.xlsx
├─ 音频\2026-10-30\xxx.mp3
└─ 其他\2026-10-30\xxx.apk
```

- 类型按扩展名映射，日期取手机文件的 mtime
- 同名文件自动加 ` (1)` 后缀，不覆盖

### D5 · 缩略图

| 类型 | 方案 |
|---|---|
| 图片 | `QImageReader` 解码 + 等比缩放到 96px（EXIF 方向自动处理） |
| 视频 | 复用「文件目录-桌面」的 `shellthumb.py`（ctypes 调 Windows Shell 取首帧） |
| PDF | `pypdfium2` 首页渲染（复用现有栈） |
| 文档/音频/其他 | 按扩展名着色的图标块 + 扩展名文字 |

### D6 · 噪音控制

- 相同 `path + size + mtime` 已归档 → 跳过
- 微信加密 `.dat` 目录 → **不监听**（拉出来无法查看）
- 单轮新增 >20 → 聚合卡片
- 忽过的文件写入 `ignored` 表，同 mtime 不再弹
- 手机端删除的文件 → 卡片自动移除

## 6. 数据模型

SQLite：`%LOCALAPPDATA%\手机电脑互通\data.db`

```sql
CREATE TABLE seen (
  path        TEXT PRIMARY KEY,   -- 手机端绝对路径
  mtime       INTEGER NOT NULL,
  size        INTEGER NOT NULL,
  first_seen  INTEGER NOT NULL
);

CREATE TABLE pending (
  id          TEXT PRIMARY KEY,   -- uuid
  remote_path TEXT NOT NULL,
  staging     TEXT NOT NULL,
  mtime       INTEGER NOT NULL,
  size        INTEGER NOT NULL,
  status      TEXT NOT NULL,      -- waiting|pulled|saved|ignored|failed
  created_at  INTEGER NOT NULL
);

CREATE TABLE archive (
  id          TEXT PRIMARY KEY,
  remote_path TEXT NOT NULL,
  local_path  TEXT NOT NULL,
  archived_at INTEGER NOT NULL
);

CREATE TABLE ignored (
  path     TEXT PRIMARY KEY,
  mtime    INTEGER NOT NULL
);

CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
```

## 7. 风险预演

| 风险 | 概率 | 应对 |
|---|---|---|
| 无线调试断连 / IP 变化 | 高 | 指数退避重连；USB 在位时优先 USB；托盘显示当前通道 |
| FUSE 遍历慢导致轮询堆积 | 中 | 单例调度 + 跳过重叠轮次 + 分层频率 |
| adb server 被其他工具（AS/刷机工具）抢占 | 中 | `adb start-server` 幂等调用；检测 transport 消失自动重建 |
| 大视频 pull 卡 UI | 高 | 独立线程 + 卡片显示进度 + 可取消 |
| 中文/空格文件名处理 | 中 | 全程 Unicode；subprocess 不做 shell 拼接，传 list |
| Git Bash 路径改写 | 高 | 见坑 P1 |
| 手机锁屏后 adb 不可用 | 低 | 实测锁屏仍可用（USB 调试状态） |
| 用户误以为「忽略」= 删除手机文件 | 中 | 忽略只删电脑 staging 副本，UI 文案写明「手机端保留」 |

### 已踩坑记录

- **P1 · MSYS 路径改写**：Git Bash 下 `adb push x /sdcard/a.txt` 会被改写成 `.../PortableGit/sdcard/a.txt`。必须 `MSYS_NO_PATHCONV=1`，且此时本地路径不能用 `/tmp`（Windows adb 不认），要传 `C:/Users/.../x`。**代码里用 subprocess 传 list + 显式设置 env，不用 shell 字符串。**
- **P2 · `content query --projection`**：Android 17 上逗号分隔会报 `Invalid column "_id,_data"`；重复传参只有最后一个生效。故弃用 MediaStore 为主通道。
- **P3 · `ls --time-style`** 在 toybox 不支持；用 `stat -c '%Y %s %n'` 或 `find -newermt`。

## 8. 技术栈

| 层 | 选型 | 理由 |
|---|---|---|
| GUI | PySide6-Essentials ≥6.6 | 与「文件目录-桌面」同栈，PyInstaller 打包链已验证 |
| 存储 | sqlite3（标准库） | 零依赖 |
| 传输 | adb（现有 platform-tools 37.0.1） | 已验证可读写任意目录 |
| 视频缩略图 | ctypes + Windows Shell（复用 shellthumb.py） | 免 ffmpeg |
| PDF 缩略图 | pypdfium2 | 复用现有栈 |
| 打包 | PyInstaller onedir + Inno Setup 6.7.3 | 复用现有打包链 |

## 9. 验收标准

1. 手机拍一张照片 → **5s 内**右侧弹窗出现缩略图
2. 点击「保存」→ 文件出现在 `D:\手机归档\图片\<今天>\`，文件名不变
3. 手机微信保存文件到 Download → **15s 内**弹窗出现
4. 电脑拖文件到悬浮窗 → 手机 `/sdcard/Download/电脑发送/` 可见，中文名正常
5. 拔 USB 走 WiFi → 功能不中断（3s 内切换到无线通道）
6. 连续 8h 运行：内存 <200MB、无崩溃、无弹窗刷屏
7. 手机端删除已弹窗的文件 → 卡片自动消失

## 10. 非目标（本期不做）

- 双向自动同步（Syncthing 式，无确认）
- 手机端 App
- 相册远程浏览
- macOS / Linux
- 微信聊天记录解析
