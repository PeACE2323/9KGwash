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
        # 关键字规则：命中任一条即执行
        #   kind = "jav"    -> 命中后加标记 + 投递到番号目标目录
        #   kind = "other"  -> 命中后直接投递到 target_dir（不改名）
        "RULES": [],
    }


def _normalize_rule(raw: dict, idx: int = 0) -> dict:
    """把前端传来的规则规范化，保证字段完整、类型正确。

    kind:
        jav     命中后在 JAV 番号（厂牌-数字）后插标记，投递到目标目录
        western 命中厂牌且含「年.月.日」时，在日期后插标记，投递到目标目录
        other   命中后原样投递到目标目录，不改名
        fallback 兜底：前面所有规则都没命中时执行（仅媒体文件且过体积阈值）
    """
    kind = str(raw.get("kind", "jav")).lower()
    if kind not in ("jav", "western", "other", "fallback"):
        kind = "jav"

    keywords = raw.get("keywords", [])
    if isinstance(keywords, str):
        # 允许前端用逗号/换行分隔
        keywords = [k.strip() for k in keywords.replace("，", ",").replace("\n", ",").split(",")]
    keywords = [str(k).strip() for k in (keywords or []) if str(k).strip()]

    return {
        "id": str(raw.get("id") or f"rule{idx + 1}"),
        "name": str(raw.get("name") or f"规则{idx + 1}").strip(),
        "kind": kind,
        "enabled": bool(raw.get("enabled", True)),
        "keywords": keywords,
        "mark": str(raw.get("mark") or "").strip(),
        "target_dir": str(raw.get("target_dir") or "").strip(),
        "action": "copy" if str(raw.get("action", "move")).lower() == "copy" else "move",
    }


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

    rules = raw.get("RULES", [])
    if isinstance(rules, list):
        cfg["RULES"] = [_normalize_rule(r, i) for i, r in enumerate(rules) if isinstance(r, dict)]
    return cfg


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
def classify_file(filename: str, rules: list[dict]) -> tuple[dict | None, str | None]:
    """按规则顺序（从上到下）匹配，先命中且「适用」者生效。

    返回 (命中的规则, 命中的关键字/厂牌)。
    - jav / western / other：需要 keyword 命中；且 jav/western 还需文件名
      能被对应类型解析（不含番号/日期的候选会视为「不适用」，继续尝试下一条规则）
    - fallback：不参与关键字匹配，仅当以上全部未命中时兜底
    """
    for rule in rules:
        if not rule.get("enabled", True):
            continue
        kind = rule.get("kind")
        if kind == "fallback":
            continue

        kw = naming.match_any_keyword(filename, rule.get("keywords", []))
        if not kw:
            continue

        # 类型适用性校验：命中了关键字但格式对不上，交给后续规则
        if kind == "jav" and not naming.find_fanhao(filename):
            logger.info(f"   [-] ↩️ [投递] 规则[{rule.get('name')}]命中关键词[{kw}]但无日本番号，改试下一条规则")
            continue
        if kind == "western" and not naming.find_western(filename):
            logger.info(f"   [-] ↩️ [投递] 规则[{rule.get('name')}]命中厂牌[{kw}]但无欧美日期，改试下一条规则")
            continue

        return rule, kw
    return None, None


def find_fallback_rule(rules: list[dict]) -> dict | None:
    """返回第一条启用中的兜底规则。"""
    for rule in rules:
        if rule.get("enabled", True) and rule.get("kind") == "fallback":
            return rule
    return None


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

    # ---- 3. 按顺序匹配关键字规则 ----
    rules = cfg.get("RULES", [])
    rule, kw = classify_file(filename, rules)
    fallback = False

    # 兜底：所有关键字规则都没命中时，交给兜底规则
    if not rule:
        rule = find_fallback_rule(rules)
        if rule:
            fallback = True
            result["reason"] = "未命中关键字，走兜底规则"
        else:
            result["reason"] = "未命中任何关键字规则"
            return result

    target_root = rule.get("target_dir", "").strip()
    if not target_root:
        result["reason"] = f"规则[{rule['name']}]未配置目标目录"
        logger.warning(f"   [-] ⚠️ [投递] {filename} 命中规则[{rule['name']}]但未配置目标目录，跳过")
        return result

    new_name = filename
    final_dir = target_root
    kind = rule.get("kind")

    if kind == "jav":
        info = naming.find_fanhao(filename)
        if not info:
            result["reason"] = f"命中关键词[{kw}]但文件名不含日本番号，不投递"
            logger.warning(f"   [-] ⚠️ [投递] {filename} 命中JAV规则但未识别到番号，跳过")
            return result
        # 未填写标记则不改名
        if rule.get("mark"):
            new_name = naming.insert_mark_after_fanhao(filename, rule["mark"])
        logger.info(f"   [-] 🏷️ [投递] 命中JAV规则[{rule['name']}]关键词[{kw}] "
                    f"番号={info['fanhao']} 标记={rule.get('mark') or '无'}")

    elif kind == "western":
        info = naming.find_western(filename)
        if not info:
            result["reason"] = f"命中厂牌[{kw}]但文件名不含「年.月.日」，不投递"
            logger.warning(f"   [-] ⚠️ [投递] {filename} 命中欧美规则但未识别到日期，跳过")
            return result
        if rule.get("mark"):
            new_name = naming.insert_mark_after_western(filename, rule["mark"])
        logger.info(f"   [-] 🏷️ [投递] 命中欧美规则[{rule['name']}]厂牌[{kw}] "
                    f"日期={info['date']} 标记={rule.get('mark') or '无'}")

    elif kind == "fallback":
        logger.info(f"   [-] 🏷️ [投递] 未命中任何关键字，执行兜底规则[{rule['name']}]")

    else:  # other
        logger.info(f"   [-] 🏷️ [投递] 命中普通规则[{rule['name']}]关键词[{kw}]")

    result["new_name"] = new_name
    result["rule"] = rule.get("name")
    result["fallback"] = fallback

    # ---- 4. 执行投递 ----
    action = rule.get("action", "move")
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
