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


def _norm_path(p: str) -> str:
    """规范化路径用于前缀比较：统一斜杠、去掉重复与尾部斜杠。

    刻意不使用 realpath / resolve：
    网盘挂载点（CD2、rclone 等 FUSE 挂载）的 realpath 可能指向挂载源，
    会让挂载点下的合法子目录被误判越界。

    也刻意不强行补前导 "/"：容器内是 Linux 绝对路径，
    但 Windows 开发环境下路径形如 ``E:/media``，补斜杠会让路径失效。
    """
    q = str(p or "").strip()
    if not q:
        return "/"
    q = q.replace("\\", "/")
    while "//" in q:
        q = q.replace("//", "/")
    is_windows = len(q) >= 2 and q[1] == ":"
    if not is_windows and not q.startswith("/"):
        q = "/" + q
    q = q.rstrip("/")
    return q or ("/" if not is_windows else q)

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
    """列出某个目录下的子目录，供前端做目录选择器。

    注意：不能用 os.path.realpath 做越权校验。
    CD2 / rclone 等网盘挂载多为 FUSE 挂载点，realpath 可能解析到
    挂载源或其它路径，导致挂载点下的合法子目录被误判为「越界」而过滤掉，
    表现为「打开目录看不到里面的文件夹」。
    这里统一用 normpath + 显式前缀比较（含大小写容错）。
    """
    path = (request.args.get("path") or "/").strip()

    allowed_roots = [
        app_config.mount_base_path(),
        app_config.metadata_base_path(),
        "/media",
        "/metadata",
        "/data",
        "/deliver",
    ]
    extra = os.environ.get("XIBAN_BROWSE_ROOTS", "")
    if extra:
        allowed_roots.extend([p.strip() for p in extra.split(",") if p.strip()])

    allowed_roots = [_norm_path(r) for r in allowed_roots if r]
    # 去重，避免重复项
    seen = set()
    uniq_roots = []
    for r in allowed_roots:
        if r and r not in seen:
            seen.add(r)
            uniq_roots.append(r)
    allowed_roots = uniq_roots

    def is_allowed(p: str) -> bool:
        q = _norm_path(p)
        if q == "/":
            return True
        for root in allowed_roots:
            if q == root or q.startswith(root.rstrip("/") + "/"):
                return True
        return False

    if not is_allowed(path):
        return jsonify({"success": False, "error": "该路径不在允许浏览的范围内"}), 403

    if not os.path.isdir(path):
        return jsonify({"success": False, "error": f"目录不存在: {path}"}), 404

    dirs = []
    try:
        with os.scandir(path) as it:
            for entry in it:
                try:
                    if entry.is_dir():
                        dirs.append({"name": entry.name, "path": entry.path})
                except OSError:
                    # 网盘挂载下个别条目可能无权限/已失效，跳过即可
                    continue
    except PermissionError:
        return jsonify(
            {"success": False, "error": f"没有权限访问该目录: {path}"}
        ), 403
    except Exception as e:  # noqa: BLE001
        return jsonify({"success": False, "error": str(e)}), 500

    dirs.sort(key=lambda d: d["name"].lower())
    parent = os.path.dirname(_norm_path(path)) or "/"
    return jsonify(
        {
            "success": True,
            "path": _norm_path(path),
            "parent": parent if is_allowed(parent) else None,
            "roots": allowed_roots,
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
        # 跨组链式改名：取全部命中组，最终结果来自最后一组
        hits = deliver.resolve_matches_by_group(name, cfg)
        fallback = False

        if hits:
            last = hits[-1]
            group, child = last["group"], last["child"]
            output = last["name_after"]
            kw = last["keyword"]
            reason = ""
        else:
            group, child = deliver.find_fallback_group(cfg)
            child = child or {}
            fallback = bool(group)
            output = name
            kw = None
            reason = "" if group else "没有可用规则"

        # 若命中的组缺失格式（理论不会发生，因匹配阶段已校验），补一条提示
        kind = (group or {}).get("kind", "jav")
        mark = child.get("mark", "") or ""
        position = child.get("mark_position", "after")
        jav_info = naming.find_fanhao(name)
        west_info = naming.find_western(name)

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
                # 链式改名轨迹：跨组时会有多条
                "chain": [
                    {
                        "group": h["group"].get("name"),
                        "keyword": h["keyword"],
                        "before": h["name_before"],
                        "after": h["name_after"],
                    }
                    for h in hits
                ],
                "hit_count": len(hits),
            }
        )
    return jsonify({"success": True, "results": results})
