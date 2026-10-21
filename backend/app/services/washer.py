"""核心洗板逻辑：CD2(CMS) 新文件落盘后的「版本对决」。

流程与原脚本 ``process_xiban`` 完全一致：
1. 目标文件必须是 JAV 番号，否则跳过；
2. 在上一级目录里雷达搜索所有同番号（含前置数字互洗）的兄弟文件夹；
3. 汇总这些文件夹内的全部视频文件，按「主干名|扩展名|权重|是否分盘」聚成版本队列；
4. 版本 > 1 时按 (权重, 目录 mtime, 文件 mtime) 降序选王者，其余全部淘汰：
   - 权重低于王者 => 低优画质，整组文件+关联元数据全删；
   - 权重相同且为分盘碎片 => 同质碎片冗余，全删；
   - 权重相同且为单体 => 同质单体冗余，只删 json（保留覆盖件）；
5. 清理后删除空目录，记录洗板档案，最后唤醒 CMS。
"""

from __future__ import annotations

import os
import re

from .. import config
from ..logging_setup import categorized
from ..services import cms, fs, history
from ..services.fanhao import (
    VIDEO_EXTS,
    get_weight_and_fanhao,
    is_jav,
    parse_fanhao_info,
    version_hash,
)

# 本模块日志统一打上分类标签，供前端「活动日志」按标签页筛选
logger = categorized("wash")

_PART_DISPLAY_RE = re.compile(
    r"[-_]?(CD[1-9]|PART[A-E])(?![a-zA-Z0-9])$|[-_][AB](?![a-zA-Z0-9])$", re.IGNORECASE
)


def build_versions(competitors: list[dict]) -> list[dict]:
    """把候选文件聚类成版本队列（查重接口复用同一实现，保持行为一致）。"""
    version_groups: dict[str, dict] = {}
    for c in competitors:
        if not c["is_part"]:
            stripped = c["basename"]
            display_name = c["basename"]
        else:
            stripped = _PART_DISPLAY_RE.sub("", c["basename"])
            display_name = f"{stripped} (分盘编队)"

        v_hash = version_hash(c)

        if v_hash not in version_groups:
            version_groups[v_hash] = {
                "weight": c["weight"],
                "is_part": c["is_part"],
                "files": [],
                "folder_mtime": c["folder_mtime"],
                "file_mtime": c["file_mtime"],
                "v_hash": v_hash,
                "display_name": display_name,
            }
        version_groups[v_hash]["files"].append(c)
        version_groups[v_hash]["folder_mtime"] = max(version_groups[v_hash]["folder_mtime"], c["folder_mtime"])
        version_groups[v_hash]["file_mtime"] = max(version_groups[v_hash]["file_mtime"], c["file_mtime"])

    return list(version_groups.values())


def process_xiban(file_path: str) -> None:
    target_canonical, target_raw, _ = parse_fanhao_info(os.path.basename(file_path))
    if not target_canonical or not is_jav(target_canonical):
        logger.info(f"ℹ️ [跳过] 目标 [{os.path.basename(file_path)}] 非 JAV 番号，不参与洗板。")
        return

    _, target_weight, target_is_part = get_weight_and_fanhao(os.path.basename(file_path))
    parent_dir = os.path.dirname(os.path.dirname(file_path))

    suspect_dirs = []
    if os.path.exists(parent_dir):
        try:
            with os.scandir(parent_dir) as it:
                for entry in it:
                    if entry.is_dir():
                        d_canonical, d_raw, _ = parse_fanhao_info(entry.name)
                        if (
                            d_canonical == target_canonical
                            or (target_canonical in entry.name.upper())
                            or (target_raw in entry.name.upper())
                        ):
                            suspect_dirs.append(entry.path)
        except Exception:  # noqa: BLE001
            pass

    if len(suspect_dirs) > 1:
        logger.info(
            f"📡 [雷达] 发现标的物核心特征 [{target_canonical}] 分散在 {len(suspect_dirs)} 个文件夹中，双向跨目录雷达启动。"
        )

    all_files = []
    for d in suspect_dirs:
        try:
            with os.scandir(d) as it:
                for entry in it:
                    if entry.is_file() and entry.name.lower().endswith(VIDEO_EXTS):
                        all_files.append(entry.path)
        except Exception:  # noqa: BLE001
            pass

    competitors = []
    for f in all_files:
        c_canonical, c_raw, _ = parse_fanhao_info(os.path.basename(f))
        if c_canonical == target_canonical:
            _, weight, is_part = get_weight_and_fanhao(os.path.basename(f))
            competitors.append(
                {
                    "path": f,
                    "weight": weight,
                    "is_part": is_part,
                    "folder_mtime": os.path.getmtime(os.path.dirname(f)),
                    "file_mtime": os.path.getmtime(f),
                    "basename": os.path.splitext(os.path.basename(f))[0],
                    "raw_fanhao": c_raw,
                }
            )

    versions = build_versions(competitors)

    if len(versions) <= 1:
        logger.info(f"ℹ️ [跳过] 库中 JAV 目标 [{target_canonical}] 仅有 1 个版本队列，无需洗板。")
        cms.trigger_cms("发现新文件落盘(仅单队列)")
        return

    actually_killed_metadata: set[str] = set()
    washed_anything = False
    dry_run = config.get_dry_run()

    versions.sort(key=lambda x: (x["weight"], x["folder_mtime"], x["file_mtime"]), reverse=True)
    king_version = versions[0]

    logger.info(
        f"🎯 [执行] [{target_canonical}] 锁定 {len(versions)} 个不同版本阵列，触发公平决斗！"
        f"保留王者: {king_version['display_name']} (权重:{king_version['weight']})"
    )

    for loser_version in versions[1:]:
        if loser_version["weight"] < king_version["weight"]:
            reason = "低优画质"
            only_delete_json = False
        else:
            if loser_version["is_part"]:
                reason = "同质碎片冗余"
                only_delete_json = False
            else:
                reason = "同质单体冗余"
                only_delete_json = True

        for loser in loser_version["files"]:
            loser_basename = loser["basename"]
            loser_dir = os.path.dirname(loser["path"])
            meta_dir_path = fs.meta_dir_of(loser_dir)

            if not dry_run:
                actually_killed_metadata.add(loser_basename)

                if os.path.exists(loser["path"]):
                    try:
                        os.remove(loser["path"])
                        logger.info(f"   [-] ✅ 成功降维删除 {reason}: {os.path.basename(loser['path'])}")
                        washed_anything = True
                    except Exception as e:  # noqa: BLE001
                        logger.error(f"   [-] ❌ 删除视频失败: {e}")

                d1 = fs.clean_exact_metadata(loser_basename, loser_dir, only_delete_json)
                d2 = fs.clean_exact_metadata(loser_basename, meta_dir_path, only_delete_json)
                actually_killed_metadata.update(d1)
                actually_killed_metadata.update(d2)

                fs.cleanup_dir_if_empty(meta_dir_path)
                fs.cleanup_dir_if_empty(loser_dir)
            else:
                logger.info(f"   [-] [Dry Run] 准备删除 {reason}: {os.path.basename(loser['path'])}")

    if actually_killed_metadata:
        history.record(actually_killed_metadata)
        logger.info(
            f"   [🛡️ 拦截器就绪] 准确记录了 {len(actually_killed_metadata)} 项清理特征进入档案，等待防御 Emby 延迟通知。"
        )

    if washed_anything or len(versions) > 1:
        cms.trigger_cms("洗板判定与清理流程结束")
