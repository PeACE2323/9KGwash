"""Web / API 路由层。

与原脚本接口 1:1 对齐（路径、方法、返回结构、错误码均保持不变）：
    GET  /                          控制台
    GET  /api/logs                  实时日志（最近 100 行）
    POST /api/config                热更新配置
    GET  /api/scan_duplicates       全库查重扫描
    POST /api/resolve_duplicates    一键洗板清理
    GET  /api/scan_empty_folders    空间猎犬扫描
    POST /api/delete_empty_folders  删除空壳目录
    POST /webhook                   CD2 新增文件通知
    GET/POST /emby_webhook          Emby 删除通知中转
新增（不影响原有调用）：
    GET  /api/status                控制台状态（版本/模式/路径/计数）
    GET  /api/fanhao?text=          主番号提取（前端一键复制）
    GET  /api/history               洗板档案
    GET  /healthz                   容器健康检查
"""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, render_template, request

from .. import __version__, config
from ..logging_setup import logger
from ..services import history, library, webhooks
from ..services.fanhao import main_fanhao

bp = Blueprint("main", __name__)

VERSION_LABEL = f"v{__version__} 智能全端清退与一键复制版"


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------
@bp.route("/")
def dashboard():
    return render_template(
        "index.html",
        version=VERSION_LABEL,
        db_webhook=config.get_db_webhook(),
        strm_webhook=config.get_strm_webhook(),
        dry_run=config.get_dry_run(),
        ignore_digits=config.get_ignore_leading_digits(),
        cfg=config.current_config(),
    )


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------
@bp.route("/api/logs")
def get_logs():
    """读取最近日志。

    兼容原接口：无参数时返回最近 100 行原始文本列表。
    新增参数：
        category  按分类过滤（all/wash/hound/dedup/deliver/emby/system）
        limit     返回行数（默认 100，最大 500）
        strip     是否去掉正文里的分类标签（默认 1）
        q         搜索查询关键字（不区分大小写）
    """
    from ..logging_setup import VALID_CATEGORIES, parse_category, strip_category

    category = (request.args.get("category") or "all").strip().lower()
    if category not in VALID_CATEGORIES:
        category = "all"

    q = (request.args.get("q") or "").strip()
    q_lower = q.lower()

    try:
        limit = int(request.args.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 500))

    strip = request.args.get("strip", "1") not in ("0", "false", "no")

    try:
        with open(config.LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        # 过滤时需要看全量再截断，否则某个分类或关键字可能永远为空
        if category != "all" or q_lower:
            kept = []
            for line in lines:
                # 行格式：2025-01-01 00:00:00 [INFO] #cat#正文
                body = line.split("]", 1)[-1] if "]" in line else line
                if category != "all" and parse_category(body.strip()) != category:
                    continue
                if q_lower and q_lower not in line.lower():
                    continue
                kept.append(line)
            lines = kept

        lines = lines[-limit:]

        if strip:
            out = []
            for line in lines:
                out.append(strip_category(line))
        else:
            out = lines

        return jsonify({"logs": out, "category": category, "total": len(out), "q": q})
    except Exception as e:  # noqa: BLE001
        return jsonify({"logs": [f"无法读取日志文件: {str(e)}"], "category": category, "q": q})


@bp.route("/api/logs/categories")
def get_log_categories():
    """返回可用分类及其当前条数，供前端渲染标签页。"""
    from ..logging_setup import CATEGORY_LABELS, parse_category

    counts = {k: 0 for k in CATEGORY_LABELS}
    try:
        with open(config.LOG_FILE, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                body = line.split("]", 1)[-1] if "]" in line else line
                cat = parse_category(body.strip())
                counts[cat] = counts.get(cat, 0) + 1
                counts["all"] += 1
    except Exception:  # noqa: BLE001
        pass

    return jsonify(
        {
            "success": True,
            "categories": [
                {"key": k, "label": v, "count": counts.get(k, 0)}
                for k, v in CATEGORY_LABELS.items()
            ],
        }
    )

# ---------------------------------------------------------------------------
# 洗板权重档位（可拖动排序）
# ---------------------------------------------------------------------------
@bp.route("/api/weight", methods=["GET"])
def get_weight_order():
    """返回当前权重档位顺序与说明，供前端渲染可拖动面板。"""
    order = config.get_weight_order()
    wmap = config.get_weight_map()
    custom = config.get_weight_custom()
    custom_map = {config.CUSTOM_TIER_PREFIX + c["id"]: c for c in custom}

    tiers = []
    for t in order:
        if t in custom_map:
            c = custom_map[t]
            tiers.append(
                {
                    "key": t,
                    "label": c.get("name") or "自定义",
                    "desc": "命中关键字：" + "、".join(c.get("keywords") or []),
                    "weight": wmap.get(t, 0),
                    "is_custom": True,
                    "custom_id": c["id"],
                    "keywords": list(c.get("keywords") or []),
                }
            )
        else:
            tiers.append(
                {
                    "key": t,
                    "label": config.WEIGHT_TIER_LABELS.get(t, t),
                    "desc": config.WEIGHT_TIER_DESC.get(t, ""),
                    "weight": wmap.get(t, 0),
                    "is_custom": False,
                }
            )

    return jsonify(
        {
            "success": True,
            "order": order,
            "weights": wmap,
            "tiers": tiers,
            "custom": custom,
            "default_order": config.DEFAULT_WEIGHT_ORDER,
        }
    )


@bp.route("/api/weight", methods=["POST"])
def update_weight_order():
    """保存权重档位顺序与自定义档位（实时生效，无需重启）。

    请求体：
        order   档位顺序数组（可含内置档与 custom:<id>）
        custom  自定义档位数组（可选；传了则整体替换）
    """
    try:
        data = request.get_json(silent=True) or {}
        raw = data.get("order")
        if not isinstance(raw, list) or not raw:
            return jsonify({"success": False, "error": "order 必须是非空数组"}), 400

        payload: dict = {}

        # 自定义档位先落地，顺序规范化需要知道有哪些自定义档
        if "custom" in data:
            custom = config.normalize_weight_custom(data.get("custom"))
            payload["WEIGHT_CUSTOM"] = custom
        else:
            custom = config.get_weight_custom()

        new_order = config.normalize_weight_order(raw, custom)
        payload["WEIGHT_ORDER"] = new_order

        ok, res = config.save_config(payload)
        if not ok:
            return jsonify({"success": False, "error": str(res)}), 500

        # 立即应用到运行期，无需重启
        config.reload_config()

        wmap = config.get_weight_map()
        logger.info(
            "⚖️ [权重] 档位顺序已更新为 "
            + " > ".join(new_order)
            + " | 权重值 "
            + ", ".join(f"{k}={v}" for k, v in wmap.items())
        )
        if custom:
            logger.info(
                "⚖️ [权重] 自定义档位: "
                + "; ".join(f"{c['name']}({'/'.join(c['keywords'])})" for c in custom)
            )
        return jsonify(
            {
                "success": True,
                "order": new_order,
                "weights": wmap,
                "custom": custom,
            }
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# 配置热更新
# ---------------------------------------------------------------------------
@bp.route("/api/config", methods=["POST"])
def update_config():
    """热更新配置。

    支持字段（均可选，未提交的保持原值）：
        DB_ONLINE_WEBHOOK / STRM_WEBHOOK / DRY_RUN / IGNORE_LEADING_DIGITS
        MOUNT_BASE_PATH / METADATA_BASE_PATH / WATCHED_DIRS
    """
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "error": "提交数据无效"}), 400

        payload = {
            "DB_ONLINE_WEBHOOK": str(data.get("DB_ONLINE_WEBHOOK", "") or "").strip(),
            "STRM_WEBHOOK": str(data.get("STRM_WEBHOOK", "") or "").strip(),
            "DRY_RUN": bool(data.get("DRY_RUN", True)),
            "IGNORE_LEADING_DIGITS": bool(data.get("IGNORE_LEADING_DIGITS", False)),
        }

        # ---- 路径三项（可选，做了规范化与基本校验）----
        path_changes = {}

        if "MOUNT_BASE_PATH" in data:
            v = _norm_cfg_path(data.get("MOUNT_BASE_PATH"))
            if not v:
                return jsonify({"success": False, "error": "网盘根目录不能为空"}), 400
            payload["MOUNT_BASE_PATH"] = v
            path_changes["MOUNT_BASE_PATH"] = v

        if "METADATA_BASE_PATH" in data:
            v = _norm_cfg_path(data.get("METADATA_BASE_PATH"))
            if not v:
                return jsonify({"success": False, "error": "元数据根目录不能为空"}), 400
            payload["METADATA_BASE_PATH"] = v
            path_changes["METADATA_BASE_PATH"] = v

        if "WATCHED_DIRS" in data:
            # 若本次同时改了网盘根，用新网盘根解析；否则用当前生效值
            mb = payload.get("MOUNT_BASE_PATH") or config.mount_base_path()
            dirs = _norm_watched_dirs(data.get("WATCHED_DIRS"), mb)
            if not dirs:
                return jsonify({"success": False, "error": "监听目录至少保留一项"}), 400
            payload["WATCHED_DIRS"] = dirs
            path_changes["WATCHED_DIRS"] = dirs

        # ---- 各任务页的独立扫描路径（空数组 = 回退到默认）----
        for key in ("HOUND_SCAN_PATHS", "DEDUP_SCAN_PATHS"):
            if key in data:
                raw = data.get(key)
                if isinstance(raw, str):
                    raw = raw.replace("\n", ",").split(",")
                if not isinstance(raw, (list, tuple)):
                    raw = []
                vals = []
                for it in raw:
                    v = _norm_cfg_path(it)
                    if v and v not in vals:
                        vals.append(v)
                payload[key] = vals
                path_changes[key] = vals

        success, res = config.save_config(payload)

        if success:
            runtime_updates = {
                "DB_ONLINE_WEBHOOK": payload["DB_ONLINE_WEBHOOK"],
                "STRM_WEBHOOK": payload["STRM_WEBHOOK"],
                "DRY_RUN": payload["DRY_RUN"],
                "IGNORE_LEADING_DIGITS": payload["IGNORE_LEADING_DIGITS"],
            }
            # 路径变更立即生效：写入运行时，让所有推导函数立刻用新值
            for k, v in path_changes.items():
                runtime_updates[k] = v
            config.update_runtime(**runtime_updates)

            if path_changes:
                logger.info(f"⚙️ [热更新] 路径配置已更新并生效: {path_changes}")
            logger.info(
                f"⚙️ [热更新] 网页在线修改配置成功！DRY_RUN={payload['DRY_RUN']}, "
                f"忽略前置数字={payload['IGNORE_LEADING_DIGITS']}, "
                f"STRM触发={payload['STRM_WEBHOOK']}"
            )
            return jsonify({"success": True, "config": config.current_config()})
        return jsonify({"success": False, "error": str(res)}), 500
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "error": str(e)}), 500


def _parse_paths_arg() -> list[str] | None:
    """解析查询参数 paths（逗号分隔）。未传返回 None（表示用配置默认值）。"""
    raw = request.args.get("paths")
    if raw is None:
        return None
    out = []
    for p in str(raw).replace("\n", ",").split(","):
        v = _norm_cfg_path(p)
        if v and v not in out:
            out.append(v)
    return out


def _norm_cfg_path(raw) -> str:
    """规范化配置里的单个路径：统一斜杠、去掉尾部斜杠。

    刻意不做 realpath：网盘挂载点（CD2/rclone FUSE）的 realpath 会解析到
    挂载源，导致保存后的路径与实际不一致。

    也刻意不无条件补前导 "/"：容器内是 Linux 绝对路径，
    但开发/测试在 Windows 上形如 ``E:/media``，补斜杠会让路径失效。
    """
    q = str(raw or "").strip()
    if not q:
        return ""
    q = q.replace("\\", "/")
    while "//" in q:
        q = q.replace("//", "/")
    is_windows = len(q) >= 2 and q[1] == ":"
    if not is_windows and not q.startswith("/"):
        q = "/" + q
    q = q.rstrip("/")
    return q or ("/" if not is_windows else q)


def _norm_watched_dirs(raw, mount_base: str | None = None) -> list[str]:
    """规范化监听目录列表：支持数组或逗号/换行分隔字符串，去重保序。

    允许用户填「完整路径」（含网盘根），会自动剥成相对路径；
    也兼容历史的相对路径写法。这样就不会出现
    网盘根被拼接两遍的畸形路径。
    """
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else str(raw).replace("\n", ",").split(",")

    out, seen = [], set()
    for it in items:
        v = _norm_cfg_path(it)
        if not v:
            continue
        rel, _err = config.resolve_watch_dir(v, mount_base)
        if not rel:
            continue
        if rel in seen:
            continue
        seen.add(rel)
        out.append(rel)
    return out


# ---------------------------------------------------------------------------
# 路径校验（供界面实时提示，不落盘）
# ---------------------------------------------------------------------------
@bp.route("/api/path_check", methods=["POST"])
def path_check():
    """校验一组路径配置是否合理，返回解析结果与人类可读的提示。

    请求体（任意子集）：
        MOUNT_BASE_PATH / METADATA_BASE_PATH / WATCHED_DIRS

    返回：
        success  bool
        resolved 每个监听目录解析后的网盘侧/元数据侧路径与存在性
        errors   阻塞性错误（会导致保存失败）
        warnings 非阻塞提醒
    """
    data = request.get_json(silent=True) or {}

    # 显式传了空值 -> 视为错误；完全没传 -> 用当前生效值
    if "MOUNT_BASE_PATH" in data:
        mount = _norm_cfg_path(data.get("MOUNT_BASE_PATH"))
    else:
        mount = _norm_cfg_path(config.mount_base_path())
    if "METADATA_BASE_PATH" in data:
        meta = _norm_cfg_path(data.get("METADATA_BASE_PATH"))
    else:
        meta = _norm_cfg_path(config.metadata_base_path())

    raw_dirs = data.get("WATCHED_DIRS")
    if raw_dirs is None:
        raw_dirs = config.watched_dirs()
    if isinstance(raw_dirs, str):
        raw_dirs = [x for x in raw_dirs.replace("\n", ",").split(",")]
    if not isinstance(raw_dirs, (list, tuple)):
        raw_dirs = []

    errors: list[str] = []
    warnings: list[str] = []
    resolved = []

    if not mount:
        errors.append("网盘根目录不能为空")
    if not meta:
        errors.append("元数据根目录不能为空")

    mount_norm = mount.rstrip("/")
    for raw in raw_dirs:
        v = _norm_cfg_path(raw)
        if not v:
            continue
        rel, err = config.resolve_watch_dir(v, mount_norm)
        if err:
            errors.append(f"监听目录 {v}：{err}")
            continue

        mount_path = config._join(mount_norm, rel)
        meta_path = config._join(meta, config.meta_subdir_for(rel, meta))

        resolved.append(
            {
                "input": v,
                "watch_dir": rel,
                "mount_path": mount_path,
                "meta_path": meta_path,
                "mount_ok": os.path.isdir(mount_path),
                "meta_ok": os.path.isdir(meta_path),
            }
        )
        if not os.path.isdir(mount_path):
            warnings.append(f"容器内找不到网盘目录：{mount_path}")
        if not os.path.isdir(meta_path):
            warnings.append(f"容器内找不到元数据目录：{meta_path}")

    if not resolved and not errors:
        errors.append("监听目录至少保留一项")

    if not os.path.isdir(mount_norm) and mount_norm:
        warnings.append(f"网盘根目录在容器内不存在：{mount_norm}")
    if meta and not os.path.isdir(meta):
        warnings.append(f"元数据根目录在容器内不存在：{meta}")

    return jsonify(
        {
            "success": not errors,
            "resolved": resolved,
            "errors": errors,
            "warnings": warnings,
            "mount_base_path": mount_norm,
            "metadata_base_path": meta,
        }
    )


# ---------------------------------------------------------------------------
# 查重
# ---------------------------------------------------------------------------
@bp.route("/api/scan_duplicates", methods=["GET"])
def api_scan_duplicates():
    """全库查重。

    可选查询参数 paths（逗号分隔）：仅扫描指定目录，
    不传则使用「影片查重」页设置的扫描路径。
    """
    paths = _parse_paths_arg()
    result = library.scan_duplicates(paths)
    if result.get("success"):
        return jsonify(result)
    return jsonify(result), 500


@bp.route("/api/resolve_duplicates", methods=["POST"])
def api_resolve_duplicates():
    data = request.get_json(silent=True) or {}
    duplicates = data.get("duplicates", [])
    return jsonify(library.resolve_duplicates(duplicates))


# ---------------------------------------------------------------------------
# 空间猎犬
# ---------------------------------------------------------------------------
@bp.route("/api/scan_empty_folders", methods=["GET"])
def api_scan_empty():
    """空间猎犬扫描。

    可选查询参数 paths（逗号分隔）：仅扫描指定目录，
    不传则使用「空间猎犬」页设置的扫描路径。
    """
    paths = _parse_paths_arg()
    result = library.scan_empty_folders(paths)
    if result.get("success"):
        return jsonify(result)
    return jsonify(result), 500


@bp.route("/api/delete_empty_folders", methods=["POST"])
def api_delete_empty():
    data = request.get_json(silent=True) or {}
    folders = data.get("folders", [])
    return jsonify(library.delete_empty_folders(folders))


# ---------------------------------------------------------------------------
# 新增辅助接口
# ---------------------------------------------------------------------------
@bp.route("/api/status")
def api_status():
    cfg = config.current_config()
    watched = []
    for w in config.watched_dirs():
        mount_path = config.mount_path_for(w)
        meta_path = config.meta_path_for(w)
        watched.append(
            {
                "watch_path": w,
                "mount_path": mount_path,
                "meta_path": meta_path,
                "mount_ok": os.path.exists(mount_path),
                "meta_ok": os.path.exists(meta_path),
            }
        )
    hist = history.load_wash_history()
    return jsonify(
        {
            "success": True,
            "version": __version__,
            "dry_run": config.get_dry_run(),            "ignore_leading_digits": config.get_ignore_leading_digits(),
            "db_webhook": config.get_db_webhook(),
            "strm_webhook": config.get_strm_webhook(),
            "mount_base_path": cfg.get("MOUNT_BASE_PATH"),
            "metadata_base_path": cfg.get("METADATA_BASE_PATH"),
            "watched": watched,
            "history_count": len(hist),
            # 各任务页的扫描路径（空数组表示使用默认值）
            "hound_scan_paths": config.hound_scan_paths(),
            "dedup_scan_paths": config.dedup_scan_paths(),
            "default_scan_paths": config.default_meta_scan_paths(),
        }
    )


@bp.route("/api/fanhao")
def api_fanhao():
    text = request.args.get("text", "")
    return jsonify({"success": True, "text": text, "main_fanhao": main_fanhao(text)})


@bp.route("/api/history")
def api_history():
    hist = history.load_wash_history()
    items = sorted(hist.items(), key=lambda kv: kv[1], reverse=True)
    return jsonify(
        {
            "success": True,
            "count": len(items),
            "items": [{"key": k, "ts": v} for k, v in items],
        }
    )


@bp.route("/healthz")
def healthz():
    return jsonify({"status": "ok", "version": __version__})


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------
@bp.route("/webhook", methods=["POST"])
def handle_cd2_webhook():
    try:
        payload = request.get_json(silent=True)
        webhooks.handle_cd2(payload)
        return "OK", 200
    except Exception as e:  # noqa: BLE001
        logger.error(f"处理 Webhook 报错: {str(e)}")
        return "Error", 500


@bp.route("/emby_webhook", methods=["GET", "POST"])
def handle_emby_webhook():
    if request.method == "GET":
        return f"✅ 9KGwash Webhook 中转网关 ({VERSION_LABEL}) 运行正常！", 200

    try:
        payload = request.get_json(silent=True)
        if not payload:
            return "OK", 200
        body, _reason = webhooks.handle_emby(payload, request.headers)
        return body, 200
    except Exception as e:  # noqa: BLE001
        logger.error(f"处理 Emby Webhook 报错: {str(e)}")
        return "Error", 500
