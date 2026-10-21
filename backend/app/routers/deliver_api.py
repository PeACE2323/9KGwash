"""文件投递相关 API。

    GET  /api/deliver/config        读取投递配置（含规则）
    POST /api/deliver/config        保存投递配置（热生效）
    POST /api/deliver/run           立即执行一次投递（支持 dry_run 预览）
    GET  /api/deliver/status        后台监控线程状态
    GET  /api/deliver/browse?path=  浏览目录（用于界面选择监控/目标目录）
    POST /api/deliver/preview       预览单条命名规则效果（不落盘）
"""

from __future__ import annotations

import os

from flask import Blueprint, jsonify, request

from .. import config as app_config
from ..logging_setup import logger
from ..services import deliver, deliver_store, naming

bp = Blueprint("deliver_api", __name__, url_prefix="/api/deliver")


@bp.route("/config", methods=["GET"])
def get_config():
    cfg = deliver_store.current_deliver_config()
    return jsonify(
        {
            "success": True,
            "config": cfg,
            "watcher": deliver_store.watcher_status(),
            "dry_run": app_config.get_dry_run(),
        }
    )


@bp.route("/config", methods=["POST"])
def save_config():
    data = request.get_json(silent=True) or {}
    ok, res = deliver_store.save_deliver_config(data)
    if not ok:
        return jsonify({"success": False, "error": str(res)}), 500

    rules = res.get("RULES", [])
    logger.info(
        f"📦 [投递] 配置已更新：启用监控={'是' if res.get('ENABLED') else '否'}，"
        f"监控目录={res.get('SCAN_DIR') or '未设置'}，最小体积={res.get('MIN_SIZE_MB')}MB，规则数={len(rules)}"
    )
    return jsonify({"success": True, "config": res})


@bp.route("/run", methods=["POST"])
def run_now():
    data = request.get_json(silent=True) or {}
    cfg = deliver_store.current_deliver_config()

    # 允许前端临时覆盖部分参数（不影响已保存配置）
    merged = dict(cfg)
    for key in ("SCAN_DIR", "MIN_SIZE_MB", "RECURSIVE", "REMOVE_EMPTY_DIRS", "OVERWRITE", "SETTLE_SECONDS"):
        if key in data:
            merged[key] = data[key]
    if "RULES" in data:
        merged["RULES"] = data["RULES"]

    dry_run = data.get("dry_run")
    if dry_run is None:
        dry_run = app_config.get_dry_run()
    dry_run = bool(dry_run)

    stats = deliver.run_deliver(merged, dry_run=dry_run, only_recent=False)
    return jsonify(stats)


@bp.route("/status", methods=["GET"])
def status():
    return jsonify({"success": True, "watcher": deliver_store.watcher_status()})


@bp.route("/browse", methods=["GET"])
def browse():
    """列出某个目录下的子目录，供前端做目录选择器。"""
    path = (request.args.get("path") or "/").strip()

    # 只允许浏览常见挂载根，防止越权访问系统目录
    allowed_roots = [
        app_config.mount_base_path(),
        app_config.metadata_base_path(),
        "/media",
        "/metadata",
        "/data",
        "/deliver",
    ]
    # 额外允许根（测试/特殊部署用，逗号分隔环境变量）
    extra = os.environ.get("XIBAN_BROWSE_ROOTS", "")
    if extra:
        allowed_roots.extend([p.strip() for p in extra.split(",") if p.strip()])

    allowed_roots = [r for r in allowed_roots if r]

    def is_allowed(p: str) -> bool:
        if p == "/":
            return True
        for root in allowed_roots:
            try:
                if os.path.commonpath([os.path.realpath(root), os.path.realpath(p)]) == os.path.realpath(root):
                    return True
            except (ValueError, OSError):
                continue
        return False

    if not is_allowed(path):
        return jsonify({"success": False, "error": "该路径不在允许浏览的范围内"}), 403

    if not os.path.isdir(path):
        return jsonify({"success": False, "error": f"目录不存在: {path}"}), 404

    dirs, files = [], []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir():
                        if is_allowed(entry.path):
                            dirs.append({"name": entry.name, "path": entry.path})
                except OSError:
                    continue
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "error": str(e)}), 500

    dirs.sort(key=lambda d: d["name"].lower())
    parent = os.path.dirname(path.rstrip("/")) or "/"
    return jsonify(
        {
            "success": True,
            "path": path,
            "parent": parent if is_allowed(parent) else None,
            "dirs": dirs,
        }
    )


@bp.route("/preview", methods=["POST"])
def preview():
    """预览命名效果：给一批文件名 + 规则（groups 或旧版 rules），返回命中与改名结果。"""
    data = request.get_json(silent=True) or {}
    files = data.get("files") or []

    # 支持 groups（推荐）、旧版 rules，以及单规则简写
    if data.get("groups"):
        cfg = deliver.normalize_deliver_config({"GROUPS": data["groups"]})
    elif data.get("rules"):
        cfg = deliver.normalize_deliver_config({"RULES": data["rules"]})
    else:
        keywords = data.get("keywords") or []
        if isinstance(keywords, str):
            keywords = [k.strip() for k in keywords.replace("，", ",").split(",") if k.strip()]
        cfg = deliver.normalize_deliver_config(
            {
                "GROUPS": [
                    {
                        "kind": data.get("kind", "jav"),
                        "target_dir": "/x",
                        "children": [
                            {
                                "keywords": keywords,
                                "mark": data.get("mark") or "",
                                "mark_position": data.get("mark_position", "after"),
                            }
                        ],
                    }
                ]
            }
        )

    results = []
    for name in files:
        name = str(name)
        group, child, kw = deliver.classify_file(name, cfg)
        fallback = False
        if not group:
            group, child = deliver.find_fallback_group(cfg)
            fallback = bool(group)
        child = child or {}

        kind = (group or {}).get("kind", "jav")
        mark = child.get("mark", "") or ""
        position = child.get("mark_position", "after")

        jav_info = naming.find_fanhao(name)
        west_info = naming.find_western(name)

        output = name
        reason = ""
        if not group:
            reason = "没有可用规则"
        elif kind == "jav":
            if jav_info and mark:
                output = naming.apply_mark_to_fanhao(name, mark, position)
            elif not jav_info:
                reason = "不含日本番号，实际执行时会被跳过"
        elif kind == "western":
            # 欧美规则不提供标记功能，预览时原样返回
            if not west_info:
                reason = "不含「年.月.日」，实际执行时会被跳过"
        elif kind == "other":
            if mark:
                output = naming.apply_mark(name, mark, position, prefer_fanhao=False)

        results.append(
            {
                "input": name,
                "matched_group": (group or {}).get("name"),
                "rule_kind": kind,
                "fallback": fallback,
                "matched_keyword": kw,
                "mark": mark,
                "mark_position": position,
                "is_video": deliver.is_video(name),
                "fanhao": jav_info["fanhao"] if jav_info else None,
                "western_studio": west_info["studio"] if west_info else None,
                "western_date": west_info["date"] if west_info else None,
                "output": output,
                "reason": reason,
            }
        )
    return jsonify({"success": True, "results": results})
