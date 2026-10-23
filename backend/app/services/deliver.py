"""文件重命名与指定目录投递引擎。

业务流程（一次「投递」= 扫描监控目录 -> 逐文件处理）：
    1. 扫描监控目录下所有文件（可限制递归深度）；
    2. 小于「最小体积」的文件：直接删除（含非媒体文件）；
    3. 「媒体文件」且命中某条 JAV 规则（关键字不分大小写）：
         a. 在番号后插入该规则的标记（如 DASS-787 -> DASS-787-U）；
         b. 若文件名含可识别番号 -> 移动到/复制到该规则的「JAV 目标目录」；
           不含番号则跳过并告警（不会误投递）；
    4. 「媒体文件」且不含番号、命中某条普通规则：
         -> 移动到/复制到该规则的「目标目录」（不做改名）；
    5. 处理后清理监控目录下的空文件夹。

安全约束：
    - 只删除监控目录内部的文件，绝不触碰外部路径；
    - 目标目录不存在时自动创建；
    - 同名文件按配置覆盖（默认覆盖，符合用户选择）；
    - 所有动作先写日志，便于追溯与回滚判断。
"""

from __future__ import annotations

import os
import shutil
import threading
import time

from ..logging_setup import categorized
from . import naming

# 本模块日志统一打上分类标签，供前端「活动日志」按标签页筛选
logger = categorized("deliver")

VIDEO_EXTS = (
    ".mp4", ".mkv", ".avi", ".ts", ".wmv", ".iso", ".rmvb", ".flv", ".m2ts",
    ".mov", ".mpg", ".mpeg", ".m4v", ".webm", ".vob", ".rm",
)

# 媒体之外、但通常需要一并处理的附属文件
SIDECAR_EXTS = (".nfo", ".srt", ".ass", ".vtt", ".jpg", ".jpeg", ".png", ".webp", ".json")

MB = 1024 * 1024

_lock = threading.RLock()


# ---------------------------------------------------------------------------
# 规则与调度配置的默认值
# ---------------------------------------------------------------------------
def default_deliver_config() -> dict:
    return {
        "ENABLED": False,              # 是否启用后台监控
        "SCAN_DIR": "",                # 监控目录（宿主路径映射进来的，如 /deliver/inbox）
        "MIN_SIZE_MB": 100,            # 最小体积（MB），低于此值直接删除
        "RECURSIVE": True,             # 是否递归扫描子目录
        "REMOVE_EMPTY_DIRS": True,     # 处理后清理空目录
        "OVERWRITE": True,             # 目标同名文件覆盖
        "WATCH_INTERVAL": 60,          # 监控轮询间隔（秒）
        "SETTLE_SECONDS": 10,          # 文件最后修改距今需超过此时长才处理（防止拷到一半）
        # 分组规则：优先级 = 先看组的顺序，再看组内子规则的顺序，命中即停
        #   GROUPS[*].kind        组内共享：jav / western / other / fallback
        #   GROUPS[*].target_dir  组内共享目标目录
        #   GROUPS[*].children    组内子规则：只包含 keywords / mark / mark_position
        "GROUPS": [],
        # 兼容字段：旧版扁平规则列表（读取时自动迁移为分组）
        "RULES": [],
    }


VALID_KINDS = ("jav", "western", "other", "fallback")


def _split_keywords(raw) -> list[str]:
    """把关键字字段统一为去重后的字符串列表。

    兼容：字符串（英文逗号 / 中文逗号 / 顿号 / 换行）、数组（元素内也可能含分隔符）。
    不分大小写去重，保留首次出现的大小写写法，并清理首尾空白。
    这样旧版「逗号分隔字符串」配置升级后会自动拆成规范的关键字列表。
    """
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else [raw]

    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "")
        for sep in ("，", "、", "\n", "\r", ";", "；"):
            text = text.replace(sep, ",")
        for piece in text.split(","):
            kw = piece.strip()
            if not kw:
                continue
            key = kw.lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(kw)
    return out


def _normalize_child(raw: dict, idx: int = 0, group_kind: str = "jav") -> dict:
    """组内子规则：只保留关键字与标记相关字段。"""
    position = str(raw.get("mark_position", "after")).lower()
    if position not in ("before", "after"):
        position = "after"

    mark = str(raw.get("mark") or "").strip()
    # 欧美规则与兜底规则不提供标记
    if group_kind in ("western", "fallback"):
        mark = ""

    return {
        "id": str(raw.get("id") or f"c{idx + 1}"),
        "keywords": _split_keywords(raw.get("keywords")),
        "mark": mark,
        "mark_position": position,
        "enabled": bool(raw.get("enabled", True)),
    }


def _normalize_group(raw: dict, idx: int = 0) -> dict:
    """规范化一个规则组（组共享类型/目录/动作，子规则只含关键字与标记）。"""
    kind = str(raw.get("kind", "jav")).lower()
    if kind not in VALID_KINDS:
        kind = "jav"

    children_raw = raw.get("children")
    if not isinstance(children_raw, list):
        # 兼容：组上直接挂 keywords（单子规则简写）
        children_raw = [{"keywords": raw.get("keywords"), "mark": raw.get("mark"),
                         "mark_position": raw.get("mark_position")}]

    children = [
        _normalize_child(c, i, kind) for i, c in enumerate(children_raw) if isinstance(c, dict)
    ]

    # 兜底组如果没有任何子规则，自动补一条空关键字子规则（兜底不依赖关键字）
    if kind == "fallback" and not children:
        children = [_normalize_child({}, 0, kind)]

    return {
        "id": str(raw.get("id") or f"g{idx + 1}"),
        "name": str(raw.get("name") or f"规则组{idx + 1}").strip(),
        "kind": kind,
        "enabled": bool(raw.get("enabled", True)),
        "target_dir": str(raw.get("target_dir") or "").strip(),
        "action": "copy" if str(raw.get("action", "move")).lower() == "copy" else "move",
        "children": children,
    }


def _migrate_flat_rules(rules: list) -> list:
    """把旧版扁平 RULES 迁移为分组结构。

    每个旧规则 -> 一个组（保留其类型/目录/动作），
    旧规则的关键字与标记 -> 组内唯一一条子规则。
    """
    groups = []
    for i, r in enumerate(rules):
        if not isinstance(r, dict):
            continue
        kind = str(r.get("kind", "jav")).lower()
        if kind not in VALID_KINDS:
            kind = "jav"
        groups.append(
            _normalize_group(
                {
                    "id": f"g{i + 1}",
                    "name": r.get("name") or f"规则{i + 1}",
                    "kind": kind,
                    "enabled": r.get("enabled", True),
                    "target_dir": r.get("target_dir"),
                    "action": r.get("action", "move"),
                    "children": [
                        {
                            "keywords": r.get("keywords"),
                            "mark": r.get("mark"),
                            "mark_position": r.get("mark_position", "after"),
                        }
                    ],
                },
                i,
            )
        )
    return groups


def normalize_deliver_config(raw: dict) -> dict:
    cfg = default_deliver_config()
    if not raw:
        return cfg

    for key in ("SCAN_DIR",):
        if key in raw:
            cfg[key] = str(raw.get(key) or "").strip()

    for key in ("ENABLED", "RECURSIVE", "REMOVE_EMPTY_DIRS", "OVERWRITE"):
        if key in raw:
            cfg[key] = bool(raw.get(key))

    for key in ("MIN_SIZE_MB", "WATCH_INTERVAL", "SETTLE_SECONDS"):
        if key in raw:
            try:
                val = int(float(raw.get(key)))
            except (TypeError, ValueError):
                continue
            cfg[key] = max(0, val)

    # 优先读 GROUPS；若只有旧版 RULES，则自动迁移为分组
    groups_raw = raw.get("GROUPS")
    if isinstance(groups_raw, list) and groups_raw:
        cfg["GROUPS"] = [
            _normalize_group(g, i) for i, g in enumerate(groups_raw) if isinstance(g, dict)
        ]
    else:
        rules_raw = raw.get("RULES")
        if isinstance(rules_raw, list) and rules_raw:
            cfg["GROUPS"] = _migrate_flat_rules(rules_raw)

    return cfg


def iter_group_children(cfg: dict):
    """按「组序 -> 组内序」产出 (组, 子规则)，跳过停用的组与子规则。

    这是优先级判定的唯一入口：先遍历组，再遍历组内子规则，命中即停。
    """
    for group in cfg.get("GROUPS", []):
        if not group.get("enabled", True):
            continue
        for child in group.get("children", []):
            if not child.get("enabled", True):
                continue
            yield group, child


# ---------------------------------------------------------------------------
# 基础工具
# ---------------------------------------------------------------------------
def is_video(filename: str) -> bool:
    return os.path.splitext(filename)[1].lower() in VIDEO_EXTS


def _unique_target_path(path: str) -> str:
    """生成不冲突的路径（覆盖模式关闭时使用）：xxx.mp4 -> xxx_1.mp4。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 1
    while True:
        candidate = f"{base}_{n}{ext}"
        if not os.path.exists(candidate):
            return candidate
        n += 1


def _safe_inside(base: str, target: str) -> bool:
    """target 是否位于 base 之内（防止路径穿越）。"""
    try:
        b = os.path.realpath(base)
        t = os.path.realpath(target)
        return os.path.commonpath([b, t]) == b
    except (ValueError, OSError):
        return False


def _ensure_dir(path: str) -> bool:
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(f"📦 [投递] 无法创建目标目录 [{path}]: {e}")
        return False


def _transfer(src: str, dst_dir: str, action: str, overwrite: bool) -> tuple[bool, str]:
    """把 src 投递到 dst_dir，返回 (是否成功, 最终路径或错误信息)。"""
    if not _ensure_dir(dst_dir):
        return False, "目标目录创建失败"

    dst = os.path.join(dst_dir, os.path.basename(src))
    if os.path.exists(dst) and not overwrite:
        dst = _unique_target_path(dst)

    try:
        if action == "copy":
            shutil.copy2(src, dst)
            logger.info(f"   [-] ✅ [投递] 已复制: {os.path.basename(src)} -> {dst_dir}")
        else:
            shutil.move(src, dst)
            logger.info(f"   [-] ✅ [投递] 已移动: {os.path.basename(src)} -> {dst_dir}")
        return True, dst
    except Exception as e:  # noqa: BLE001
        logger.error(f"   [-] ❌ [投递] 失败 [{src}]: {e}")
        return False, str(e)


def remove_empty_dirs(root: str) -> int:
    """自底向上删除 root 下的空目录（保留 root 本身），返回删除数量。"""
    if not root or not os.path.isdir(root):
        return 0
    removed = 0
    for cur, dirs, files in os.walk(root, topdown=False):
        if os.path.realpath(cur) == os.path.realpath(root):
            continue
        try:
            if not os.listdir(cur):
                os.rmdir(cur)
                removed += 1
                logger.info(f"   [-] 🧹 [投递] 已清理空目录: {cur}")
        except Exception:  # noqa: BLE001
            pass
    return removed


def iter_files(root: str, recursive: bool):
    """遍历 root 下的文件，产出 (绝对路径, 相对目录)。"""
    if recursive:
        for cur, _dirs, files in os.walk(root):
            for f in files:
                yield os.path.join(cur, f), cur
    else:
        try:
            with os.scandir(root) as it:
                for entry in it:
                    if entry.is_file():
                        yield entry.path, root
        except Exception:  # noqa: BLE001
            return


# ---------------------------------------------------------------------------
# 核心：处理单个文件
# ---------------------------------------------------------------------------
def resolve_matches_by_group(filename: str, cfg: dict) -> list[dict]:
    """解析一个文件名命中的所有组，并按组序做**链式改名**。

    语义（与用户约定一致）：
      - 同一组内：只取「第一条命中」的子规则，改一次名，组内不叠加；
      - 跨组：后一组基于**前一组改名后的结果**继续改名（链式累加）；
      - 最终只投递「最后一个命中的组」那一份，前面各组的改名只影响最终文件名。

    返回按组序排列的命中列表，每项:
        {group, child, keyword, name_before, name_after}
    未命中任何组返回空列表。
    """
    hits: list[dict] = []
    current = filename

    for group in cfg.get("GROUPS", []):
        if not group.get("enabled", True):
            continue
        kind = group.get("kind")
        if kind == "fallback":
            continue

        picked = None
        for child in group.get("children", []):
            if not child.get("enabled", True):
                continue
            kw = naming.match_any_keyword(current, child.get("keywords", []))
            if not kw:
                continue

            # 类型适用性校验
            if kind == "jav" and not naming.find_fanhao(current):
                logger.info(
                    f"   [-] ↩️ [投递] 组[{group.get('name')}]命中关键词[{kw}]但无日本番号，跳过该子规则"
                )
                continue
            if kind == "western" and not naming.find_western(current):
                logger.info(
                    f"   [-] ↩️ [投递] 组[{group.get('name')}]命中厂牌[{kw}]但无欧美日期，跳过该子规则"
                )
                continue

            picked = (child, kw)
            break  # 组内只取第一条命中的子规则

        if not picked:
            continue

        child, kw = picked
        before = current
        mark = child.get("mark") or ""
        position = child.get("mark_position", "after")
        after = before

        if mark:
            if kind == "jav":
                after = naming.apply_mark_to_fanhao(before, mark, position)
            elif kind == "other":
                after = naming.apply_mark(before, mark, position, prefer_fanhao=False)

        hits.append(
            {
                "group": group,
                "child": child,
                "keyword": kw,
                "name_before": before,
                "name_after": after,
            }
        )
        current = after

    return hits


def classify_file(filename: str, cfg: dict) -> tuple[dict | None, dict | None, str | None]:
    """返回最后命中的组（用于投递）。保留旧调用签名。"""
    hits = resolve_matches_by_group(filename, cfg)
    if not hits:
        return None, None, None
    last = hits[-1]
    return last["group"], last["child"], last["keyword"]


def find_fallback_group(cfg: dict) -> tuple[dict | None, dict | None]:
    """返回第一条启用中的兜底组及其首个子规则。"""
    for group, child in iter_group_children(cfg):
        if group.get("kind") == "fallback":
            return group, child
    return None, None


def process_one_file(path: str, cfg: dict, dry_run: bool = False) -> dict:
    """处理单个文件，返回处理结果描述 dict。"""
    filename = os.path.basename(path)
    result = {
        "file": filename,
        "action": "skipped",
        "reason": "",
        "new_name": None,
        "target": None,
    }

    try:
        size = os.path.getsize(path)
    except OSError as e:
        result["reason"] = f"读取体积失败: {e}"
        return result

    min_bytes = int(cfg.get("MIN_SIZE_MB", 0)) * MB

    # ---- 1. 体积不足：直接删除（用户选择：小文件和非媒体文件都删）----
    if min_bytes > 0 and size < min_bytes:
        if dry_run:
            logger.info(f"   [-] [Dry Run] 准备删除小文件({size / MB:.1f}MB < {cfg['MIN_SIZE_MB']}MB): {filename}")
        else:
            try:
                os.remove(path)
                logger.info(f"   [-] 🗑️ [投递] 已删除小文件({size / MB:.1f}MB): {filename}")
            except Exception as e:  # noqa: BLE001
                logger.error(f"   [-] ❌ [投递] 删除失败 [{filename}]: {e}")
                result["reason"] = str(e)
                return result
        result["action"] = "deleted"
        result["reason"] = f"体积 {size / MB:.1f}MB 低于阈值 {cfg['MIN_SIZE_MB']}MB"
        return result

    # ---- 2. 非媒体文件：体积够大也不投递（通常是字幕/图片，跟着视频走）----
    if not is_video(filename):
        result["reason"] = "非媒体文件"
        return result

    # ---- 3. 解析所有命中组，并按组序链式改名 ----
    hits = resolve_matches_by_group(filename, cfg)
    fallback = False

    # 兜底：所有关键字组都没命中时，交给兜底组（兜底不改名）
    if not hits:
        fb_group, fb_child = find_fallback_group(cfg)
        if fb_group:
            fallback = True
            result["reason"] = "未命中关键字，走兜底规则"
            group, child = fb_group, fb_child or {}
            new_name = filename
        else:
            result["reason"] = "未命中任何关键字规则"
            return result
    else:
        last = hits[-1]
        group = last["group"]
        child = last["child"]
        # 链式改名的最终结果
        new_name = last["name_after"]

        # 各组命中与改名过程写入日志，便于追溯
        for h in hits:
            hg, hc = h["group"], h["child"]
            hkind = hg.get("kind")
            hmark = hc.get("mark") or ""
            hpos = hc.get("mark_position", "after")
            changed = h["name_before"] != h["name_after"]
            if hmark and changed:
                pos_label = {
                    "jav": ("番号前" if hpos == "before" else "番号后"),
                    "other": ("文件名前" if hpos == "before" else "文件名后"),
                }.get(hkind, "")
                logger.info(
                    f"   [-] 🏷️ [投递] 组[{hg.get('name')}]命中关键词[{h['keyword']}] "
                    f"标记={hmark}({pos_label})"
                )
            else:
                logger.info(f"   [-] 🏷️ [投递] 组[{hg.get('name')}]命中关键词[{h['keyword']}]（不改名）")

        if len(hits) > 1:
            logger.info(
                f"   [-] 🔗 [投递] 跨组命中 {len(hits)} 个组，已链式改名，最终投递组[{group.get('name')}]"
            )

    child = child or {}
    group_name = group.get("name", "")
    kind = group.get("kind")

    target_root = group.get("target_dir", "").strip()
    if not target_root:
        result["reason"] = f"规则组[{group_name}]未配置目标目录"
        logger.warning(f"   [-] ⚠️ [投递] {filename} 命中规则组[{group_name}]但未配置目标目录，跳过")
        return result

    if kind == "fallback":
        logger.info(f"   [-] 🏷️ [投递] 未命中任何关键字，执行兜底组[{group_name}]")

    final_dir = target_root

    result["new_name"] = new_name
    result["rule"] = group_name
    result["fallback"] = fallback
    # 记录链式改名轨迹（供前端/日志展示）
    result["chain"] = [
        {
            "group": h["group"].get("name"),
            "keyword": h["keyword"],
            "before": h["name_before"],
            "after": h["name_after"],
        }
        for h in hits
    ]

    # ---- 4. 执行投递 ----
    action = group.get("action", "move")
    overwrite = bool(cfg.get("OVERWRITE", True))

    if dry_run:
        logger.info(f"   [-] [Dry Run] 准备{('复制' if action == 'copy' else '移动')}: {new_name} -> {final_dir}")
        result["action"] = "dry-run"
        result["target"] = os.path.join(final_dir, new_name)
        return result

    if not _ensure_dir(final_dir):
        result["reason"] = "目标目录创建失败"
        return result

    dst = os.path.join(final_dir, new_name)
    if os.path.exists(dst) and not overwrite:
        dst = _unique_target_path(dst)

    try:
        if action == "copy":
            shutil.copy2(path, dst)
            logger.info(f"   [-] ✅ [投递] 已复制: {new_name} -> {final_dir}")
        else:
            # 移动时源文件名可能被改写：先在同一目录下改名，再移动
            src_for_move = path
            if new_name != filename:
                renamed = os.path.join(os.path.dirname(path), new_name)
                if renamed != path:
                    if os.path.exists(renamed) and overwrite:
                        os.remove(renamed)
                    os.rename(path, renamed)
                    logger.info(f"   [-] ✏️ [投递] 已重命名: {filename} -> {new_name}")
                    src_for_move = renamed
            shutil.move(src_for_move, dst)
            logger.info(f"   [-] ✅ [投递] 已移动: {new_name} -> {final_dir}")
        result["action"] = "copied" if action == "copy" else "moved"
        result["target"] = dst
        return result
    except Exception as e:  # noqa: BLE001
        logger.error(f"   [-] ❌ [投递] 执行失败 [{filename}]: {e}")
        result["action"] = "failed"
        result["reason"] = str(e)
        return result


# ---------------------------------------------------------------------------
# 批量执行
# ---------------------------------------------------------------------------
def run_deliver(raw_cfg: dict, dry_run: bool = False, only_recent: bool = False) -> dict:
    """执行一次投递任务。返回统计信息。

    only_recent=True 时只处理「最后修改时间距今超过 SETTLE_SECONDS」的文件，
    用于后台监控，避免处理正在拷贝中的文件。
    """
    with _lock:
        cfg = normalize_deliver_config(raw_cfg)
        scan_dir = cfg.get("SCAN_DIR", "")

        stats = {
            "success": True,
            "scanned": 0,
            "deleted": 0,
            "moved": 0,
            "copied": 0,
            "skipped": 0,
            "failed": 0,
            "removed_dirs": 0,
            "dry_run": dry_run,
            "details": [],
            "error": "",
        }

        if not scan_dir:
            stats["success"] = False
            stats["error"] = "未配置监控目录"
            logger.warning("📦 [投递] 未配置监控目录，任务跳过。")
            return stats

        if not os.path.isdir(scan_dir):
            stats["success"] = False
            stats["error"] = f"监控目录不存在: {scan_dir}"
            logger.warning(f"📦 [投递] 监控目录不存在: {scan_dir}")
            return stats

        # 注意：不再输出「开始扫描 + 目录路径」这类噪音日志，
        # 日志里只保留逐文件的处理结果与最终汇总，避免活动日志被路径刷屏。
        now = time.time()
        settle = cfg.get("SETTLE_SECONDS", 0)

        for path, _parent in list(iter_files(scan_dir, cfg["RECURSIVE"])):
            if not os.path.isfile(path):
                continue
            if only_recent and settle > 0:
                try:
                    if now - os.path.getmtime(path) < settle:
                        continue
                except OSError:
                    continue

            stats["scanned"] += 1
            try:
                r = process_one_file(path, cfg, dry_run=dry_run)
            except Exception as e:  # noqa: BLE001
                logger.error(f"📦 [投递] 处理异常 [{os.path.basename(path)}]: {e}")
                stats["failed"] += 1
                continue

            act = r["action"]
            if act == "deleted":
                stats["deleted"] += 1
            elif act == "moved":
                stats["moved"] += 1
            elif act == "copied":
                stats["copied"] += 1
            elif act == "failed":
                stats["failed"] += 1
            else:
                stats["skipped"] += 1

            if r["action"] not in ("skipped",):
                stats["details"].append(r)

        if cfg.get("REMOVE_EMPTY_DIRS") and not dry_run:
            stats["removed_dirs"] = remove_empty_dirs(scan_dir)

        # 没有任何文件被处理时不刷日志，保持活动日志干净
        if stats["scanned"] == 0:
            return stats

        # 汇总行：作为一次投递任务的「执行结果」，替代旧的结果卡片
        dry_label = " [Dry Run 预览]" if dry_run else ""
        logger.info(
            f"📦 [投递] 任务完成{dry_label}：扫描 {stats['scanned']} 个文件 | "
            f"移动 {stats['moved']} · 复制 {stats['copied']} · 删除小文件 {stats['deleted']} · "
            f"跳过 {stats['skipped']} · 失败 {stats['failed']}"
            + (f" · 清理空目录 {stats['removed_dirs']}" if stats["removed_dirs"] else "")
        )
        return stats
