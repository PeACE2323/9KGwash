"""核心洗板逻辑：CD2(CMS) 新文件落盘后的「版本对决」。

流程（在原脚本基础上增加了一条安全约束）：
1. 目标文件必须是 JAV 番号，否则跳过；
2. 在上一级目录里雷达搜索所有同番号（含前置数字互洗）的兄弟文件夹；
3. 汇总这些文件夹内的全部视频文件，按「主干名|扩展名|权重|是否分盘」聚成版本队列；
4. **扩展名同权豁免**：与王者**权重相同**且**扩展名不同**的版本予以保留
   （避免删 .mp4 时连带导致同主干的 .mkv 的 STRM 被 CMS/Emby 一并清掉）；
   权重不同则照常按权重淘汰，不受扩展名影响；
5. 桶内版本 > 1 时按 (权重, 目录 mtime, 文件 mtime) 降序选王者，其余淘汰：
   - 权重低于王者 => 低优画质，整组文件+关联元数据全删；
   - 权重相同且为分盘碎片 => 同质碎片冗余，全删；
   - 权重相同且为单体 => 同质单体冗余，只删 json（保留覆盖件）；
6. 清理后删除空目录，记录洗板档案，最后唤醒 CMS。
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

    # ------------------------------------------------------------------
    # 扩展名「同权豁免」规则：
    #
    #   只有当两个版本【权重相同】且【扩展名不同】时，才两个都保留；
    #   权重不同时，即使扩展名不同也照常按权重洗（低权照删）。
    #
    # 原因（实测 IPZZ-937 事故）：
    #   库中同时存在 IPZZ-937-C.mp4 与 IPZZ-937-C.mkv（权重都是 1）。
    #   CMS 按 115 目录逐文件生成 STRM，Emby 又把同主干的 .mp4/.mkv
    #   视作同一个影片项。洗板删掉其中任意一个后：
    #     1) CMS 会连带删除它对应的 STRM；
    #     2) Emby 收到删除事件会移除整个 Movie 项，把另一个版本的 STRM 也带走。
    #   结果就是「原理应保留的版本，STRM 整体失踪」。
    #
    # 但删除「低权重」版本是洗板的核心价值，不能被豁免掉，
    # 因此豁免条件收紧为：权重相同 && 扩展名不同。
    # ------------------------------------------------------------------
    def _ext_of(v: dict) -> str:
        first_file = v["files"][0]["path"] if v.get("files") else ""
        return os.path.splitext(first_file)[1].lower()

    def _is_same_weight_other_ext(a: dict, b: dict) -> bool:
        return a["weight"] == b["weight"] and _ext_of(a) != _ext_of(b)

    losers: list[tuple[dict, str, bool]] = []  # (输家版本, 原因, 是否只删 json)

    for loser_version in versions[1:]:
        # 与王者权重相同、但扩展名不同 -> 豁免保留（避免 STRM 连带丢失）
        if _is_same_weight_other_ext(loser_version, king_version):
            continue

        if loser_version["weight"] < king_version["weight"]:
            losers.append((loser_version, "低优画质", False))
        elif loser_version["is_part"]:
            losers.append((loser_version, "同质碎片冗余", False))
        else:
            losers.append((loser_version, "同质单体冗余", True))

    # 记录被豁免的跨扩展名版本，便于排查
    exempted = [
        v for v in versions[1:] if _is_same_weight_other_ext(v, king_version)
    ]

    logger.info(
        f"🎯 [执行] [{target_canonical}] 锁定 {len(versions)} 个不同版本阵列，触发公平决斗！"
        f"保留王者: {king_version['display_name']} (权重:{king_version['weight']})"
    )
    if exempted:
        desc = "、".join(f"{v['display_name']}[{_ext_of(v) or '无扩展名'}]" for v in exempted)
        logger.info(
            f"   [-] ℹ️ 以下版本与王者权重相同但扩展名不同，予以保留（避免 STRM 连带丢失）: {desc}"
        )

    for loser_version, reason, only_delete_json in losers:

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
