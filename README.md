# 🌸 9KGwash · 容器化洗板网关

把原版单文件 `xiban_server.py`（v9.15.0）完整容器化：**功能 100% 对齐**、分层可扩展、WebUI 重做为深色玻璃拟态控制台。

- 后端：Python 3.12 + Flask 3 + gunicorn（单 worker 多线程，保证洗板互斥与档案一致性）
- 前端：原生 HTML/CSS/JS（零外部 CDN 依赖，内网可用），实时日志 2s 轮询
- 兼容：`/webhook`、`/emby_webhook`、`/api/*` 全部沿用原路径与返回结构，**CD2 / Emby / CMS 的既有配置无需改动**
- 原始单文件版本保留在 [`legacy/`](legacy/) 供对照

```
ghcr.io/peace2323/9kgwash:latest
```

---

## 界面与主题

- **字号**：主体 15px、正文 13～13.5px，最小不再低于 11px（此前大量 10～11.5px 偏小）
- **深色 / 浅色主题**：点击右上角「🌙 深色 / ☀️ 浅色」切换，选择保存在浏览器，刷新后保持
- 首次加载前会先恢复主题，不会出现深色闪屏

---

## 0. docker run（推荐）

```bash
docker run -d \
  --name 9kgwash \
  --restart unless-stopped \
  -p 5000:5000 \
  -e TZ=Asia/Shanghai \
  -v /你的路径/9kgwash/config:/config \
  -v /你的网盘挂载/CloudDrive:/media/CloudDrive \
  -v /你的元数据路径:/metadata \
  ghcr.io/peace2323/9kgwash:latest
```

三个 `-v` 分别是「配置与日志」「网盘挂载」「元数据」，务必换成你机器上的真实路径。
配置文件会自动生成在 `config/config.json`，也可直接把原版配置拷进去无缝迁移。

> 若镜像拉取提示未授权（仓库为私有包），先执行一次：
> `echo <你的GitHub PAT，勾选 read:packages> | docker login ghcr.io -u PeACE2323 --password-stdin`

---

## 1. 快速开始（源码构建）

```bash
cp .env.example .env
# 编辑 .env：CLOUD_MOUNT / METADATA_HOST_DIR 指向宿主机真实路径
docker compose up -d --build
```

打开 `http://<宿主机IP>:5000`。

首次启动会自动生成 `/config/config.json`（对应宿主机 `./data/config/config.json`），
也可以直接把原版的 `config.json` 与 `wash_history.json` 拷进该目录**无缝迁移**。

### 目录映射

| 容器内路径 | 说明 | 建议宿主机路径 |
| --- | --- | --- |
| `/config` | 配置、日志（`logs/xiban-YYYY-MM-DD.log`）、洗板档案 | `./data/config` |
| `/media/CloudDrive` | 网盘挂载根目录（Alist / CD2 / rclone 挂载点） | `/mnt/clouddrive/CloudDrive` |
| `/metadata` | Emby / 刮削元数据根目录（`.strm` `.nfo`） | `/mnt/user/appdata/.../metadata` |

> `/media/CloudDrive` 与 `/metadata` 必须与 `config.json` 中的
> `MOUNT_BASE_PATH` / `METADATA_BASE_PATH` 保持一致（默认值即上表路径）。
> 若网盘目录本身是 FUSE/CIFS 挂载，建议改成 `- /宿主机路径:/media/CloudDrive:rslave`。

---

## 2. 配置项（`/config/config.json`）

| 键 | 含义 | 能否 WebUI 热改 |
| --- | --- | --- |
| `MOUNT_BASE_PATH` | 网盘挂载根，如 `/media/CloudDrive` | ❌（改文件/环境变量后重启） |
| `METADATA_BASE_PATH` | 元数据根，如 `/metadata` | ❌ |
| `WATCHED_DIRS` | 监听目录（相对网盘挂载根），如 `["/115open/9KG/STRM"]` | ❌ |
| `DRY_RUN` | `true` 只记日志不真删 | ✅ |
| `DB_ONLINE_WEBHOOK` | Emby 事件转发目标（POST） | ✅ |
| `STRM_WEBHOOK` | CMS 唤醒（GET，触发 STRM 重建） | ✅ |
| `IGNORE_LEADING_DIGITS` | 去掉番号前置数字并双向互洗 | ✅ |

结构性路径也可用环境变量覆盖（`MOUNT_BASE_PATH` / `METADATA_BASE_PATH` / `WATCHED_DIRS`，逗号分隔），
入口脚本会在启动时写回 `config.json`。

---

## 3. 功能对照表（原脚本 → 容器版）

| 原功能 | 容器版实现 | 状态 |
| --- | --- | --- |
| `POST /webhook`：CD2 新增/重命名通关，轮询等待挂载同步（60s/5s），分盘缓冲 20s | `services/webhooks.py::handle_cd2` | ✅ 一致 |
| `process_xiban`：雷达同番号目录 → 版本聚类 → 权重/时间决斗 → 三种删除策略 | `services/washer.py` | ✅ 一致 |
| **扩展名隔离**：同名不同扩展名（`.mp4` / `.mkv`）各自保留，互不淘汰 | `services/washer.py` | ➕ 安全增强 |
| 权重规则：`3=中字+无码/破解`、`2=中字`、`1=无码/破解`、`0=普通`；分盘 `CD1/PARTA/-A` | `services/fanhao.py` | ✅ 一致 |
| `clean_exact_metadata`：精确同名关联文件清理，`._` 前缀容错，`only_delete_json` 分支 | `services/fs.py` | ✅ 一致 |
| `safe_delete_dir` 安全锁：越界/受保护根目录强制拦截 | `services/fs.py` | ✅ 一致 |
| `meta_to_mount_dir` 自适应大小写容错映射 | `services/fs.py` | ✅ 一致 |
| `trigger_cms`：Dry Run 模拟、2s 落盘缓冲、5s 超时 | `services/cms.py` | ✅ 一致 |
| `GET /api/scan_empty_folders` 空间猎犬 | `services/library.py` | ✅ 一致 |
| `POST /api/delete_empty_folders` | `services/library.py` | ✅ 一致 |
| `GET /api/scan_duplicates` 全库查重（版本/目录/同源番号三维判重） | `services/library.py` | ✅ 一致 |
| **查重目录判定修正**：改用目录完整路径，父目录同名不再漏判 | `services/library.py` | 🔧 修复 |
| `POST /api/resolve_duplicates` 查重洗板（跨目录粉碎 / 同目录降维） | `services/library.py` | ✅ 一致 |
| `GET/POST /emby_webhook`：幽灵防御、档案拦截、本地扫描、去前缀转发、Header 透传 | `services/webhooks.py::handle_emby` | ✅ 一致 |
| `POST /api/config` 热更新 | `routers/main.py` | ✅ 一致 |
| `GET /api/logs`（最近 100 行） | `routers/main.py` | ✅ 一致 |
| 按天滚动日志 + 保留 3 天（`xiban-YYYY-MM-DD.log`） | `logging_setup.py` | ✅ 一致 |
| 洗板档案 7 天 TTL | `services/history.py` | ✅ 一致 |
| 启动横幅 | `factory.py::_print_banner` | ✅ 一致 |
| 前端控制台（日志/配置/猎犬/查重/一键复制） | `templates/index.html` + `static/` | 🎨 重做 |
| `GET /api/status`、`GET /api/fanhao`、`GET /api/history`、`/healthz` | `routers/main.py` | ➕ 新增（不影响原调用） |

---

## 4. 代码结构

```
.
├── Dockerfile / docker-compose.yml / .env.example
├── requirements.txt
└── backend/
    ├── docker-entrypoint.sh        # 配置初始化 + gunicorn 启动
    ├── config.default.json         # 首次启动写入的默认配置
    ├── tools/env_override.py       # 环境变量覆盖结构性路径
    ├── tests/
    │   ├── test_e2e.py             # 洗板核心 116 项端到端回归
    │   ├── test_deliver.py         # 文件投递 88 项测试
    │   └── compare_original.py     # 与原脚本逐场景对照
    └── app/
        ├── factory.py              # 应用工厂 / 启动横幅 / 拉起投递监控
        ├── config.py               # 配置加载、保存、运行期热更新
        ├── logging_setup.py        # 按天滚动日志
        ├── routers/
        │   ├── main.py             # 洗板相关路由
        │   └── deliver_api.py      # 文件投递路由
        ├── services/
        │   ├── fanhao.py           # 番号解析 / 权重 / 版本聚类（纯函数）
        │   ├── washer.py           # 核心洗板对决
        │   ├── library.py          # 查重扫描/洗板 + 空间猎犬
        │   ├── webhooks.py         # CD2 通关 + Emby 中转
        │   ├── fs.py               # 安全删除 / 路径推导 / 关联清理
        │   ├── history.py          # 洗板档案
        │   ├── cms.py              # CMS 唤醒
        │   ├── naming.py           # 番号智能定位 + 标记插入（纯函数）
        │   ├── deliver.py          # 投递引擎（扫描/删除/改名/投递/清空目录）
        │   └── deliver_store.py    # 投递配置持久化 + 后台监控线程
        ├── templates/index.html
        └── static/{css,js}/
```

**扩展一个功能**的推荐姿势：
- 新的目录/文件规则 → 加在 `services/fanhao.py` 或 `services/naming.py`（纯函数，易测）
- 新的清理流程 → 组合 `services/fs.py` 的安全原语，别自己写 `rmtree`
- 新的投递规则类型 → 在 `services/deliver.py::process_one_file` 增加分支
- 新的 HTTP 接口 → 在 `routers/` 下加 `@bp.route`，前端在 `static/js/app.js` 调 `api()`
- 新的页面 → 在 `index.html` 加一个 `<section class="view" id="view-xxx">`，并在 `VIEW_META` 与导航里登记

---

## 5. 测试

```bash
# 需要 Python 3.12 + flask + requests
cd backend
python -m tests.test_e2e        # 洗板核心 116 项断言
python -m tests.test_deliver    # 文件投递 88 项断言
python tests/compare_original.py  # 需要原 xiban_server.py（XIBAN_ORIG_SRC 可指定路径）
```

覆盖：番号解析、权重、洗板三策略、Dry Run、猎犬、安全锁、查重扫描与洗板、
CD2 通关、Emby 五种判定分支、去前缀转发、配置热更新、日志接口、静态资源；
以及番号智能定位、标记插入、关键字匹配、体积阈值、移动/复制/覆盖、空目录清理、投递 API；
欧美番号识别、厂牌匹配、兜底规则与规则顺序优先级。

---

## 6. 文件投递（关键字改名 + 定向分发）

用于处理 115 离线下载到「重命名文件夹」的原始文件：**自动清理小文件、按关键字给番号打标、分发到指定目录**。

### 6.1 工作流程

```
监控目录 ──扫描──> ① 小于「最小体积」的文件 ──> 直接删除（含非媒体文件）
                        │
                        └─ ② 媒体文件命中关键字 ──> 番号后插入标记 ──> 投递到目标目录
                                                    │
                                                    └─ 文件名无番号则跳过（不误投）
               ③ 处理完毕 ──> 清理空文件夹
```

### 6.2 命名示例

| 原始文件名 | 关键字 | 标记 | 结果 |
| --- | --- | --- | --- |
| `www.98T.la@DASS-787無防備に…_restored_wm.mp4` | `restored` | `-U` | `www.98T.la@DASS-787-U無防備に…_restored_wm.mp4` |

- 自动跳过 `www.98T.la@`、`[ThZu.Cc]`、`hhd800.com@` 等发布站前缀，不会把 `98T` 误判成番号
- 支持前置数字厂牌：`259LUXU-1900` → `259LUXU-1900-U`
- **支持无连字符写法**：`katu00118`、`ABP00123`、`katu00118hhb`
  （数字后的字母视为后缀，标记加在数字与后缀之间：`katu00118-Uhhb`）
- 自动排除 `1080p` / `x264` / `4k` / `hhd800` 等伪番号
- 关键字**不分大小写**

#### 关键字与画质标识

关键字匹配前会先**抹掉画质/编码标识**（`FHD` `HD` `1080p` `720p` `x264` `HEVC` `4K` …），
避免短关键字被这类片段误触发：

| 文件名 | 关键字 | 结果 |
| --- | --- | --- |
| `RKI413.FHD_restored.mp4` | `FH` | ❌ 不再命中（`.FHD` 是画质） |
| `RKI413.FHD_restored.mp4` | `restored` | ✅ 正常命中 |
| `abc-FH-123.mp4` | `FH` | ✅ 正常命中（独立出现） |
| `a.1080p.x264.mp4` | `1080p` | ✅ 命中（显式配置完整画质词仍可筛选） |

- 幂等：已带 `-U` 的文件不会重复添加

### 6.3 规则分组（组 + 子规则）

规则采用 **「规则组 → 子规则」** 两级结构，解决规则一多就难管理的问题：

```
规则组1  有码破解   [JAV]   目标: /交付/破解     ← 组头：类型/目录/动作/启用（组内共享）
   ├─ 子规则 1:  restored        → -U（番号后）
   └─ 子规则 2:  LTX, 破解       → U（番号前）
规则组2  欧美番号   [欧美]  目标: /交付/欧美     ← 欧美组不提供标记
   └─ 子规则 1:  Blacked, Vixen
规则组3  兜底投递   [兜底]  目标: /待整理
```

**改名与投递规则：**

- **同一组内**：只按「第一个命中」的子规则改名一次，组内不叠加
- **不同组都命中**：逐组**链式改名**（后一组在前一组的结果上继续改），
  **最终只投递「最后一个命中组」那一份**
- 组头拖动 ⠿ 调整组优先级；组内子规则用 ▲▼ 调整
- 组内共享：规则类型、目标目录、投递方式（移动/复制）、启用开关
- 子规则独立：关键字、标记、标记位置、启用开关

#### 关键字输入方式

关键字以**标签（chip）**形式编辑，不再需要手打逗号：

| 操作 | 效果 |
| --- | --- |
| 输入后按 **回车** | 确认成一个标签 |
| 输入后按 **逗号 / 中文逗号 / 顿号** | 立即确认，标签不受标点影响 |
| **粘贴** `a, b，c` | 自动拆成多个标签 |
| 点标签上的 **×** | 删除该标签 |
| 输入框为空时按 **退格** | 删除最后一个标签 |
| 输入框失焦时仍有残留文字 | 自动转为标签，不会丢失 |

- 标签不分大小写**自动去重**（`SMD` 与 `smd` 视为同一个）
- 首尾空格自动清理
- **旧的逗号分隔配置会自动转换**为标签，无需手动改
- 规则组折叠时，关键字以小标签预览，超过 3 个显示 `+N`
- 欧美组的关键字（实为厂牌）用青色标签区分

链式改名示例（同一文件同时命中 3 个组）：

```
原始  AAA-111_both.mp4
组A   AAA-111_both.mp4    → AAA-111-A_both.mp4      （-A，番号后）
组B   AAA-111-A_both.mp4  → B-AAA-111-A_both.mp4    （B-，番号前）
组C   B-AAA-111-A_both.mp4→ B-AAA-111-A_both_c.mp4  （_c，文件名后）
最终  只投递到 组C 的目标目录
```

| 类型 | 行为 |
| --- | --- |
| **JAV 番号规则** | 命中关键字 → 按「标记位置」在番号前/后加标记 → 投递；**文件名无日本番号则继续下一条** |
| **欧美番号规则** | 关键字填厂牌 → 命中且含「年.月.日」→ **原样投递，不提供标记** |
| **普通关键字规则** | 命中关键字 → 按「标记位置」在文件名前/后加标记 → 投递 |
| **兜底规则** | 以上全部未命中时执行；仅处理**通过体积阈值的媒体文件**，原样投递 |

> 旧版扁平 `RULES` 配置在读取时会**自动迁移**为分组结构，无需手动改动。

#### 标记位置（前 / 后）

| 类型 | 标记位置 | 原始文件名 | 结果 |
| --- | --- | --- | --- |
| JAV | 番号后 | `www.98T.la@DASS-787無防備.mp4` | `www.98T.la@DASS-787-U無防備.mp4` |
| JAV | 番号前 | `www.98T.la@DASS-787無防備.mp4` | `www.98T.la@U-DASS-787無防備.mp4` |
| 普通 | 文件名后 | `Some.Video.2024.mp4` | `Some.Video.2024_restored.mp4` |
| 普通 | 文件名前 | `Some.Video.2024.mp4` | `U-Some.Video.2024.mp4` |

- 前置标记会自动归一：`U` / `U-` / `-U` / `_U` 都会得到 `U-`；`[U]` 这类自带收尾的写法原样使用
- 标记**留空则不改名**；已带同一标记时不会重复添加（幂等）
- **欧美规则与兜底规则不使用标记**，配置层会自动清空其 mark 字段

#### 欧美番号说明

格式为 `厂牌.年.月.日`，年份支持 2 位或 4 位：

| 原始文件名 | 厂牌 | 结果 |
| --- | --- | --- |
| `BlackedRaw.26.09.23.Delilah...XXX.1080p.MP4-P2P[XC].mp4` | `blackedraw` | 原样投递（不改名） |
| `Tushy.2024.01.05.Some.Title.mp4` | `tushy` | 原样投递（不改名） |

- 必须同时满足「命中厂牌」+「含合法日期」，避免把 `Some.Movie.2024.1080p` 误判为欧美片
- **欧美规则不提供标记功能**，文件保持原名投递

#### 规则顺序

**规则从上到下依次匹配，先命中者生效**，可在界面拖动 ⠿ 或点 ▲▼ 调整。

顺序有实际意义：例如一个文件同时含 `restored` 和 `blackedraw`，
把 JAV 规则排在前面 → 走 JAV 分支；把欧美规则排在前面 → 走欧美分支。

> 特殊规则：若命中了某条 JAV 规则但文件名**没有日本番号**，会自动继续尝试下一条规则，
> 而不是直接丢弃，避免文件漏投。

### 6.4 配置项

| 键 | 说明 |
| --- | --- |
| `SCAN_DIR` | 监控目录（容器内路径） |
| `MIN_SIZE_MB` | 最小体积，低于此值直接删除 |
| `WATCH_INTERVAL` | 后台轮询间隔（秒） |
| `SETTLE_SECONDS` | 落盘稳定期，文件静置够久才处理（防拷到一半） |
| `RECURSIVE` | 是否递归子目录 |
| `REMOVE_EMPTY_DIRS` | 处理后清理空文件夹 |
| `OVERWRITE` | 目标同名文件是否覆盖（关闭则自动加 `_1`） |
| `RULES` | 规则数组 |

配置独立存于 `/config/deliver.json`，与洗板的 `config.json` 互不影响。

> ⚠️ 投递功能同样受 **DRY_RUN** 保护：Dry Run 下只记日志、不动文件。
> 建议先点「预览（Dry Run）」确认改名与目标路径无误，再实弹执行。

---

### 6.5 日志与执行结果

投递的执行结果不再单独占用一个面板，而是**直接写入「活动日志 → 文件投递」**标签页：

```
📦 [投递] 任务完成：扫描 3 个文件 | 移动 2 · 复制 0 · 删除小文件 1 · 跳过 0 · 失败 0
```

日志只保留**逐文件的处理结果**和**一条汇总**，不再输出「开始扫描 + 目录路径」这类噪音，
避免大目录下日志被路径刷屏。执行完点击投递页下方的「📡 活动日志 → 文件投递」即可查看详情。

### 6.6 活动日志分类

「活动日志」页顶部按业务分类，可切换查看，标签上带实时条数：

| 标签 | 涵盖内容 |
| --- | --- |
| 全部 | 所有日志 |
| 🧽 洗板 | CD2 通关、版本对决、删除/保留决策 |
| 🐕 空间猎犬 | 空壳目录扫描与清理 |
| 🔍 影片查重 | 全库查重与查重洗板 |
| 📦 文件投递 | 投递任务的逐文件结果与汇总 |
| 📺 Emby 中转 | Emby 删除通知的拦截/放行/转发 |
| ⚙️ 系统 | 启动横幅、配置变更等 |

实现方式是给日志正文加分类前缀（如 `#deliver#`），原有日志格式不变，可用 `grep` 正常检索。
接口：`GET /api/logs?category=deliver`、`GET /api/logs/categories`。

#### 日志搜索

日志页顶部有搜索框，可查询任意内容（番号、目录名、关键字），不区分大小写：

| 操作 | 说明 |
| --- | --- |
| 输入 | 300ms 防抖自动搜索 |
| 回车 | 立即搜索 |
| Esc 或点 ✕ | 清空搜索，恢复全部日志 |

- 可与分类标签组合（如只在「文件投递」里搜）
- 命中内容**高亮**显示，并提示「命中 N 条」；无结果时提示「没有匹配的日志」
- 接口：`GET /api/logs?q=DASS-787&category=deliver&limit=500`

#### 自动跟随最新日志

- **默认开启**，新日志自动滚动到底部
- 手动往上翻时自动切换为「⏸️ 已锁定」，不会把你拽回底部
- 翻回底部或点「⬇️ 自动跟随」即可恢复
- 进入日志页、切换分类、搜索时都会自动定位到最新内容

---

## 7. 洗板权重顺序（可拖动调整）

洗板对决时，「权重高者保留、低者清理」。权重来自文件名标记，分 4 个档位：

| 档位 | 含义 | 默认权重 |
| --- | --- | --- |
| **UC** | 中字 + 无码/破解（同时含 C 与 U 标记） | 3（最高） |
| **C** | 仅中字（中字 / 字幕 / `-C`） | 2 |
| **U** | 仅无码/破解（无码 / 破解 / 流出 / `-U`） | 1 |
| **普通** | 不含上述标记 | 0（最低） |

**默认顺序 `UC > C > U > 普通`，与原始脚本行为完全一致。**

在「⚙️ 运行配置 → ⚖️ 洗板权重顺序」中可拖动 ⠿ 或点 ▲▼ 调整先后，
保存后**实时生效，无需重启**，也可一键「恢复默认」。

- 4 个档位严格排序，不设并列；调整后权重值自动重算为 3/2/1/0
- 同权重时仍按「目录修改时间 → 文件修改时间」决定胜负
- 不含番号的文件权重固定为 -1，不参与洗板对决，也不受档位顺序影响
- 配置项：`WEIGHT_ORDER`（存于 `/config/config.json`）

### 扩展名同权豁免（重要）

洗板时，**只有「权重相同」且「扩展名不同」的版本才两个都保留**；
权重不同则照常按权重洗板，扩展名不构成豁免。

| 场景 | 处理 |
| --- | --- |
| `IPZZ-937-C.mp4` + `IPZZ-937-C.mkv`（权重都是 1） | ✅ 两个都保留 |
| `ABC-200.mp4`(权0) + `ABC-200-C.mkv`(权1) | ✅ 删低权 `.mp4`，留 `.mkv` |
| `ABC-100.mp4` + `ABC-100-C.mp4`（同扩展名） | ✅ 按权重淘汰低者 |

**为什么要这样**：CMS 按 115 目录逐文件生成 STRM，Emby 又把同主干的 `.mp4` / `.mkv`
视为同一个影片项。删掉权重相同的其中一个，会连锁导致另一个的 STRM 也被清空
（表现为「影片消失，只剩元数据」）。
但删除低权重版本是洗板的核心价值，因此豁免只限「同权重 + 不同扩展名」。

--- | --- |
| `IPZZ-937-C.mp4` + `IPZZ-937-C.mkv` | ✅ 两个都保留 |
| `ABC-100.mp4` + `ABC-100-C.mp4` | ✅ 同扩展名内按权重淘汰低者 |
| `ABC-100.mp4` + `ABC-100-C.mp4` + `ABC-100.mkv` | ✅ mp4 桶内留 `-C`，mkv 照常保留 |

> 代价：同名不同扩展名会各占一份空间。若你更在意节省空间，可改为手动清理。

---

## 8. 常见问题

**Q：容器里 `/media/CloudDrive` 是空的？**
检查宿主机路径是否挂对，且 `config.json` 的 `MOUNT_BASE_PATH` 与之匹配；`/api/status` 会返回 `mount_ok` / `meta_ok`。

**Q：日志时间不对？**
设置 `.env` 的 `TZ`（默认 `Asia/Shanghai`）。

**Q：想看是否真删了？**
保持 `DRY_RUN=true` 先跑一轮：日志里会出现 `[Dry Run] 准备删除 ...`，不会动文件。

**Q：文件投递看不到效果？**
① 确认监控目录已正确挂载进容器；② 后台监控需勾选「启用后台自动监控」并保存；③ 也可以直接点「立即执行投递」；④ 检查最小体积阈值是否设得过高。
