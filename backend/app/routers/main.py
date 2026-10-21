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
    try:
        with open(config.LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
            return jsonify({"logs": lines[-100:]})
    except Exception as e:  # noqa: BLE001
        return jsonify({"logs": [f"无法读取日志文件: {str(e)}"]})


# ---------------------------------------------------------------------------
# 配置热更新
# ---------------------------------------------------------------------------
@bp.route("/api/config", methods=["POST"])
def update_config():
    try:
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"success": False, "error": "提交数据无效"}), 400

        new_webhook = str(data.get("DB_ONLINE_WEBHOOK", "") or "").strip()
        new_strm_webhook = str(data.get("STRM_WEBHOOK", "") or "").strip()
        new_dry_run = bool(data.get("DRY_RUN", True))
        new_ignore_digits = bool(data.get("IGNORE_LEADING_DIGITS", False))

        success, res = config.save_config(
            {
                "DB_ONLINE_WEBHOOK": new_webhook,
                "STRM_WEBHOOK": new_strm_webhook,
                "DRY_RUN": new_dry_run,
                "IGNORE_LEADING_DIGITS": new_ignore_digits,
            }
        )

        if success:
            config.update_runtime(
                DB_ONLINE_WEBHOOK=new_webhook,
                STRM_WEBHOOK=new_strm_webhook,
                DRY_RUN=new_dry_run,
                IGNORE_LEADING_DIGITS=new_ignore_digits,
            )
            logger.info(
                f"⚙️ [热更新] 网页在线修改配置成功！DRY_RUN={new_dry_run}, "
                f"忽略前置数字={new_ignore_digits}, STRM触发={new_strm_webhook}"
            )
            return jsonify({"success": True})
        return jsonify({"success": False, "error": str(res)}), 500
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "error": str(e)}), 500


# ---------------------------------------------------------------------------
# 查重
# ---------------------------------------------------------------------------
@bp.route("/api/scan_duplicates", methods=["GET"])
def api_scan_duplicates():
    result = library.scan_duplicates()
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
    result = library.scan_empty_folders()
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
    mount_base = config.mount_base_path().rstrip("/")
    meta_base = config.metadata_base_path().rstrip("/")
    watched = []
    for w in config.watched_dirs():
        meta_path = (meta_base + w.replace("/115open/", "/")).rstrip("/")
        watched.append(
            {
                "watch_path": w,
                "mount_path": mount_base + w,
                "meta_path": meta_path,
                "mount_ok": os.path.exists(mount_base + w),
                "meta_ok": os.path.exists(meta_path),
            }
        )
    hist = history.load_wash_history()
    return jsonify(
        {
            "success": True,
            "version": __version__,
            "dry_run": config.get_dry_run(),
            "ignore_leading_digits": config.get_ignore_leading_digits(),
            "db_webhook": config.get_db_webhook(),
            "strm_webhook": config.get_strm_webhook(),
            "mount_base_path": cfg.get("MOUNT_BASE_PATH"),
            "metadata_base_path": cfg.get("METADATA_BASE_PATH"),
            "watched": watched,
            "history_count": len(hist),
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
