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
| 权重规则：`3=中字+无码/破解`、`2=中字`、`1=无码/破解`、`0=普通`；分盘 `CD1/PARTA/-A` | `services/fanhao.py` | ✅ 一致 |
| `clean_exact_metadata`：精确同名关联文件清理，`._` 前缀容错，`only_delete_json` 分支 | `services/fs.py` | ✅ 一致 |
| `safe_delete_dir` 安全锁：越界/受保护根目录强制拦截 | `services/fs.py` | ✅ 一致 |
| `meta_to_mount_dir` 自适应大小写容错映射 | `services/fs.py` | ✅ 一致 |
| `trigger_cms`：Dry Run 模拟、2s 落盘缓冲、5s 超时 | `services/cms.py` | ✅ 一致 |
| `GET /api/scan_empty_folders` 空间猎犬 | `services/library.py` | ✅ 一致 |
| `POST /api/delete_empty_folders` | `services/library.py` | ✅ 一致 |
| `GET /api/scan_duplicates` 全库查重（版本/目录/同源番号三维判重） | `services/library.py` | ✅ 一致 |
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
    │   ├── test_e2e.py             # 113 项端到端回归
    │   └── compare_original.py     # 与原脚本逐场景对照
    └── app/
        ├── factory.py              # 应用工厂 / 启动横幅
        ├── config.py               # 配置加载、保存、运行期热更新
        ├── logging_setup.py        # 按天滚动日志
        ├── routers/main.py         # 全部路由
        ├── services/
        │   ├── fanhao.py           # 番号解析 / 权重 / 版本聚类（纯函数）
        │   ├── washer.py           # 核心洗板对决
        │   ├── library.py          # 查重扫描/洗板 + 空间猎犬
        │   ├── webhooks.py         # CD2 通关 + Emby 中转
        │   ├── fs.py               # 安全删除 / 路径推导 / 关联清理
        │   ├── history.py          # 洗板档案
        │   └── cms.py              # CMS 唤醒
        ├── templates/index.html
        └── static/{css,js}/
```

**扩展一个功能**的推荐姿势：
- 新的目录/文件规则 → 加在 `services/fanhao.py`（纯函数，易测）
- 新的清理流程 → 组合 `services/fs.py` 的安全原语，别自己写 `rmtree`
- 新的 HTTP 接口 → 在 `routers/main.py` 加 `@bp.route`，前端在 `static/js/app.js` 调 `api()`

---

## 5. 测试

```bash
# 需要 Python 3.12 + flask + requests
cd backend
python -m tests.test_e2e        # 113 项断言
python tests/compare_original.py  # 需要原 xiban_server.py（XIBAN_ORIG_SRC 可指定路径）
```

覆盖：番号解析、权重、洗板三策略、Dry Run、猎犬、安全锁、查重扫描与洗板、
CD2 通关、Emby 五种判定分支、去前缀转发、配置热更新、日志接口、静态资源。

---

## 6. 常见问题

**Q：容器里 `/media/CloudDrive` 是空的？**
检查宿主机路径是否挂对，且 `config.json` 的 `MOUNT_BASE_PATH` 与之匹配；`/api/status` 会返回 `mount_ok` / `meta_ok`。

**Q：日志时间不对？**
设置 `.env` 的 `TZ`（默认 `Asia/Shanghai`）。

**Q：想看是否真删了？**
保持 `DRY_RUN=true` 先跑一轮：日志里会出现 `[Dry Run] 准备删除 ...`，不会动文件。
