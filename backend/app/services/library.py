"""全库影片查重 + 查重洗板（含空间猎犬）。

与原脚本两类扫描/清理流程保持完全一致的判定与删除路径：
- 查重扫描：只扫描监听目录对应的「本地元数据目录」，按番号聚类，
  版本 > 1 或 同源番号 > 1 或 分布目录 > 1 即判为重复组，并按权重排序标记王者；
- 查重洗板：王者目录不同的输家 => 本地目录 + 网盘对应目录整目录粉碎；
           同目录输家 => 仅删单体媒体 + 网盘同名视频 + 关联元数据；
- 空间猎犬：找出「本地无 STRM」的僵尸空壳目录，可选一键删除。
"""

from __future__ import annotations

import os

from .. import config
from ..logging_setup import categorized
from ..services import fs, history
from ..services.fanhao import (
    LOCAL_MEDIA_EXTS,
    MOVIE_AUX_DIRS,
    VIDEO_EXTS,
    get_weight_and_fanhao,
    is_jav,
    parse_fanhao_info,
    version_hash,
)


class _LibraryLogger:
    """library 同时承载「查重」与「空间猎犬」两套流程，
    这里按日志正文里的业务标识自动归类，避免逐条改写日志文案。"""

    __slots__ = ("_dedup", "_hound")

    def __init__(self):
        self._dedup = categorized("dedup")
        self._hound = categorized("hound")

    def _pick(self, msg):
        text = str(msg)
        if "空间猎犬" in text or "空间清理" in text:
            return self._hound
        return self._dedup

    def info(self, msg, *a, **k):
        self._pick(msg).info(msg, *a, **k)

    def warning(self, msg, *a, **k):
        self._pick(msg).warning(msg, *a, **k)

    def error(self, msg, *a, **k):
        self._pick(msg).error(msg, *a, **k)


# 本模块日志按业务自动打分类标签，供前端「活动日志」按标签页筛选
logger = _LibraryLogger()


def _aux_dirs() -> set:
    """影片辅助目录集合（extrafanart / trailers / subs ...），扫描时跳过。"""
    return MOVIE_AUX_DIRS


def meta_scan_paths(kind: str = "dedup") -> list[str]:
    """返回实际用于扫描的路径列表（已过滤掉不存在的）。

    kind:
        "dedup" —— 影片查重，优先用 DEDUP_SCAN_PATHS
        "hound" —— 空间猎犬，优先用 HOUND_SCAN_PATHS
    两者未单独配置时，回退到「各监听目录对应的元数据侧路径」。
    """
    if kind == "hound":
        candidates = config.hound_scan_paths()
    else:
        candidates = config.dedup_scan_paths()

    paths = []
    for p in candidates:
        if p and os.path.isdir(p) and p not in paths:
            paths.append(p)
    return paths


# ---------------------------------------------------------------------------
# 1. 全库查重扫描
# ---------------------------------------------------------------------------
def scan_duplicates(paths: list[str] | None = None) -> dict:
    """全库查重。

    paths 为 None 时使用配置里的查重扫描路径；
    传入时只扫描这些目录（供 API 按界面设定执行）。
    """
    logger.info("🔍 [全库查重] 启动本地媒体库查重扫描，正在进行极速预检...")

    if paths is None:
        scan_paths = meta_scan_paths("dedup")
    else:
        scan_paths = [p for p in paths if p and os.path.isdir(p)]

    if not scan_paths:
        logger.warning(
            "🔍 [全库查重] 未发现有效的扫描目录！请检查「影片查重」页设置的扫描路径。"
        )
        return {"success": True, "duplicates": [], "scan_paths": []}

    logger.info("🔍 [全库查重] 本次扫描目录: " + ", ".join(scan_paths))

    def count_target_folders(base_path):
        count = 0
        for root, dirs, _files in os.walk(base_path):
            if os.path.basename(root).lower() in _aux_dirs():
                continue
            dirs[:] = [d for d in dirs if d.lower() not in _aux_dirs()]
            count += 1
        return count

    total_folders = 0
    for p in scan_paths:
        total_folders += count_target_folders(p)

    if total_folders == 0:
        logger.info("🔍 [全库查重] 本地目录为空，未发现任何可检查节点。")
        return {"success": True, "duplicates": [], "scan_paths": scan_paths}

    logger.info(f"🔍 [全库查重] 本地预检完毕！共锁定 {total_folders} 个目录节点，正式开始查重与聚类...")
    scanned_count = 0
    total_files_indexed = 0
    fanhao_groups: dict[str, list] = {}

    try:
        for base_p in scan_paths:
            for root, dirs, files in os.walk(base_p):
                if os.path.basename(root).lower() in _aux_dirs():
                    continue
                dirs[:] = [d for d in dirs if d.lower() not in _aux_dirs()]

                scanned_count += 1
                if scanned_count % 100 == 0 or scanned_count == total_folders:
                    percent = int((scanned_count / total_folders) * 100)
                    logger.info(
                        f"🔍 [全库查重] 本地扫描进度: {percent}% ({scanned_count}/{total_folders}) | "
                        f"累计索引 {total_files_indexed} 部媒体，归类 {len(fanhao_groups)} 个番号组"
                    )

                for f in files:
                    if f.lower().endswith(LOCAL_MEDIA_EXTS):
                        c_canonical, c_raw, _ = parse_fanhao_info(f)
                        if not c_canonical or not is_jav(c_canonical):
                            dir_name = os.path.basename(root)
                            c_canonical, c_raw, _ = parse_fanhao_info(dir_name)

                        if c_canonical and is_jav(c_canonical):
                            f_path = os.path.join(root, f)
                            _, weight, is_part = get_weight_and_fanhao(f)
                            if c_canonical not in fanhao_groups:
                                fanhao_groups[c_canonical] = []

                            total_files_indexed += 1
                            fanhao_groups[c_canonical].append(
                                {
                                    "path": f_path,
                                    "name": f,
                                    "folder": os.path.basename(root),
                                    # 目录完整相对路径：用于判断「是否真的在不同目录」。
                                    # 只用 basename 会漏判——同番号分别位于
                                    # 「日本有码/300MIUM/300MIUM-1457泷本雫叶」与
                                    # 「素人/300MIUM/300MIUM-1457泷本雫叶」时，
                                    # 父目录名相同但实际是两个不同位置。
                                    "folder_path": os.path.relpath(root, base_p).replace("\\", "/"),
                                    "raw_fanhao": c_raw,
                                    "weight": weight,
                                    "is_part": is_part,
                                    "folder_mtime": os.path.getmtime(root),
                                    "file_mtime": os.path.getmtime(f_path),
                                    "basename": os.path.splitext(f)[0],
                                }
                            )

        logger.info(f"🔍 [全库查重] 目录遍历完成，正比对 {len(fanhao_groups)} 组番号的多版本画质阵列...")
        duplicates_result = []
        for canonical, files in fanhao_groups.items():
            version_groups: dict[str, list] = {}
            for c in files:
                v_hash = version_hash(c)
                version_groups.setdefault(v_hash, []).append(c)

            distinct_raw = set(f["raw_fanhao"] for f in files)
            # 用「目录完整路径」而不是 basename 判断是否分散在不同目录
            distinct_folders = set(f.get("folder_path") or f["folder"] for f in files)

            if len(version_groups) > 1 or len(distinct_folders) > 1 or len(distinct_raw) > 1:
                files.sort(key=lambda x: (x["weight"], x["folder_mtime"], x["file_mtime"]), reverse=True)
                for idx, item in enumerate(files):
                    item["is_winner"] = idx == 0

                duplicates_result.append(
                    {
                        "canonical_fanhao": canonical,
                        "raw_fanhaos": list(distinct_raw),
                        "files": files,
                    }
                )

        logger.info(
            f"🔍 [全库查重] 查重比对圆满完成！共检索 {total_files_indexed} 部本地媒体，锁定 {len(duplicates_result)} 组重复影片。"
        )
        return {"success": True, "duplicates": duplicates_result, "scan_paths": scan_paths}
    except Exception as e:  # noqa: BLE001
        logger.error(f"🔍 [全库查重] 扫描异常中断: {e}")
        return {"success": False, "error": str(e)}


# ---------------------------------------------------------------------------
# 2. 查重洗板（全端清退）
# ---------------------------------------------------------------------------
def resolve_duplicates(duplicates: list[dict]) -> dict:
    deleted_count = 0
    actually_killed_metadata: set[str] = set()
    dry_run = config.get_dry_run()

    for item in duplicates or []:
        files = item.get("files", [])
        if len(files) <= 1:
            continue
        king = files[0]
        losers = files[1:]

        king_dir = os.path.dirname(king["path"])
        king_mount_dir = fs.meta_to_mount_dir(king_dir)

        for loser in losers:
            loser_path = loser["path"]
            loser_basename = loser["basename"]
            loser_dir = os.path.dirname(loser_path)
            mount_dir = fs.meta_to_mount_dir(loser_dir)

            # 收集被淘汰目录内的所有特征主干，严密防御 Emby 延迟删除误报
            actually_killed_metadata.add(loser_basename)
            if os.path.exists(loser_dir):
                actually_killed_metadata.add(os.path.basename(loser_dir))
                try:
                    for entry in os.scandir(loser_dir):
                        if entry.is_file():
                            actually_killed_metadata.add(os.path.splitext(entry.name)[0])
                except Exception:  # noqa: BLE001
                    pass

            # 区分：若输家与王者位于不同目录，执行彻底的「本地+网盘整文件夹粉碎」
            if loser_dir != king_dir:
                if not dry_run:
                    if mount_dir and mount_dir != king_mount_dir:
                        if fs.safe_delete_dir(mount_dir):
                            logger.info(f"   [-] ✅ [查重洗板] 成功粉碎网盘冗余目录: {mount_dir}")
                        else:
                            logger.warning(f"   [-] ⚠️ [查重洗板] 网盘目录未能直接删除或受保护: {mount_dir}")

                    if fs.safe_delete_dir(loser_dir):
                        deleted_count += 1
                        logger.info(f"   [-] ✅ [查重洗板] 成功粉碎本地媒体所属目录: {loser_dir}")
                    else:
                        logger.warning(f"   [-] ⚠️ [查重洗板] 本地目录未能直接删除或受保护: {loser_dir}")
                else:
                    deleted_count += 1
                    logger.info(f"   [-] [Dry Run 查重洗板] 准备粉碎本地冗余目录: {loser_dir}")
                    if mount_dir and mount_dir != king_mount_dir:
                        logger.info(f"   [-] [Dry Run 查重洗板] 准备粉碎网盘对应目录: {mount_dir}")
            else:
                only_delete_json = loser["weight"] == king["weight"] and not loser["is_part"]
                if not dry_run:
                    if os.path.exists(loser_path):
                        try:
                            os.remove(loser_path)
                            deleted_count += 1
                            logger.info(
                                f"   [-] ✅ [查重洗板] 同目录降维，成功删除本地冗余媒体: {os.path.basename(loser_path)}"
                            )
                        except Exception as e:  # noqa: BLE001
                            logger.error(f"   [-] ❌ 删除本地媒体失败: {e}")

                    if mount_dir and os.path.exists(mount_dir):
                        for v_ext in VIDEO_EXTS:
                            cloud_video = os.path.join(mount_dir, loser_basename + v_ext)
                            if os.path.exists(cloud_video):
                                try:
                                    os.remove(cloud_video)
                                    logger.info(
                                        f"   [-] ✅ [查重洗板] 成功清理网盘原视频: {os.path.basename(cloud_video)}"
                                    )
                                except Exception as e:  # noqa: BLE001
                                    logger.error(f"   [-] ❌ 删除网盘视频失败: {e}")

                    d1 = fs.clean_exact_metadata(loser_basename, loser_dir, only_delete_json)
                    actually_killed_metadata.update(d1)
                    if mount_dir:
                        d2 = fs.clean_exact_metadata(loser_basename, mount_dir, only_delete_json)
                        actually_killed_metadata.update(d2)
                else:
                    deleted_count += 1
                    logger.info(f"   [-] [Dry Run 查重洗板] 模拟清除同目录下冗余文件: {loser_basename}")

    history.record(actually_killed_metadata)
    if deleted_count > 0:
        from ..services import cms

        cms.trigger_cms("查重洗板执行完毕")

    return {"success": True, "deleted": deleted_count}


# ---------------------------------------------------------------------------
# 3. 空间猎犬：本地空壳目录扫描 / 删除
# ---------------------------------------------------------------------------
def scan_empty_folders(paths: list[str] | None = None) -> dict:
    """空间猎犬：扫描空壳目录，并检查其对应的网盘目录状态。

    判定「空壳」的依据是 config.hound_content_exts()（内容后缀白名单）：
    目录内只要存在任一该后缀的文件，就认为有内容、不算空壳。
    默认白名单含 .strm 与常见视频格式，避免把「有真实视频但还没生成 .strm」
    的目录误判为空壳并删除。

    扫描完成后再对每个空壳目录做「网盘侧检查」：
      - 通过 fs.meta_to_mount_dir 映射到网盘目录；无法映射则标记 unmapped 并跳过；
      - 并发（8 线程）统计网盘目录内的视频文件数，判断网盘侧是否也是空目录。
    """
    from concurrent.futures import ThreadPoolExecutor

    logger.info("🧽 [空间猎犬] 启动本地空壳目录扫描，正在进行极速预检...")
    content_exts = [e.lower() for e in config.hound_content_exts()]
    video_exts = [e.lower() for e in VIDEO_EXTS]
    aux = _aux_dirs()
    total_folders = 0

    def dir_has_content(root: str, files) -> bool:
        """目录内是否存在白名单后缀的文件。"""
        for fn in files:
            if os.path.splitext(fn)[1].lower() in content_exts:
                return True
        return False

    def count_target_folders(base_path):
        count = 0
        for root, dirs, files in os.walk(base_path):
            if os.path.basename(root).lower() in aux:
                continue
            dirs[:] = [d for d in dirs if d.lower() not in aux]
            if dir_has_content(root, files):
                dirs.clear()
            count += 1
        return count

    try:
        if paths is None:
            candidates = config.hound_scan_paths()
        else:
            candidates = [p for p in paths if p]

        meta_paths = []
        for meta_path in candidates:
            if meta_path and os.path.isdir(meta_path) and meta_path not in meta_paths:
                meta_paths.append(meta_path)
                total_folders += count_target_folders(meta_path)

        if not meta_paths:
            logger.warning(
                "🧽 [空间猎犬] 未发现有效的扫描目录！请检查「空间猎犬」页设置的扫描路径。"
            )
            return {
                "success": True,
                "empty_mount": [],
                "empty_meta": [],
                "items": [],
                "scan_paths": [],
                "total_folders": 0,
                "content_exts": content_exts,
            }

        logger.info("🧽 [空间猎犬] 本次扫描目录: " + ", ".join(meta_paths))
        logger.info("🧽 [空间猎犬] 内容判定后缀: " + " ".join(content_exts))

        if total_folders == 0:
            logger.info("🧽 [空间猎犬] 预检完毕，扫描目录下没有任何节点。")
            return {
                "success": True,
                "empty_mount": [],
                "empty_meta": [],
                "items": [],
                "scan_paths": meta_paths,
                "total_folders": 0,
                "content_exts": content_exts,
            }

        logger.info(f"🧽 [空间猎犬] 预检完成！共计 {total_folders} 个本地目录节点，正式开始查杀排雷...")
        scanned_count = 0
        empty_dirs: list[str] = []

        for meta_path in meta_paths:
            for root, dirs, files in os.walk(meta_path):
                if os.path.basename(root).lower() in aux:
                    continue
                dirs[:] = [d for d in dirs if d.lower() not in aux]

                has_content = dir_has_content(root, files)
                if has_content:
                    # 目录本身已有内容 -> 不再深入它的子目录
                    dirs.clear()

                scanned_count += 1
                if scanned_count % 100 == 0 or scanned_count == total_folders:
                    percent = int((scanned_count / total_folders) * 100)
                    logger.info(
                        f"🧽 [空间猎犬] 本地扫描进度: {percent}% ({scanned_count}/{total_folders}) | "
                        f"累计揪出 {len(empty_dirs)} 个空壳"
                    )

                if not dirs and root != meta_path and not has_content:
                    empty_dirs.append(root)

        logger.info(
            f"🧽 [空间猎犬] 本地扫描完成，锁定 {len(empty_dirs)} 个空壳目录，"
            f"开始检查对应的网盘目录..."
        )

        # ---------------- 网盘侧检查（并发） ----------------
        def check_one(local_dir: str) -> dict:
            mount_dir = fs.meta_to_mount_dir(local_dir)
            item = {
                "local": local_dir,
                "mount": mount_dir,
                "mapped": bool(mount_dir),
                "mount_exists": False,
                "mount_state": "unmapped",
                "mount_video_count": 0,
            }
            if not mount_dir:
                return item

            if not os.path.isdir(mount_dir):
                item["mount_state"] = "missing"
                return item

            item["mount_exists"] = True
            cnt = fs.count_files_by_ext(mount_dir, video_exts, recursive=True)
            if cnt < 0:
                item["mount_state"] = "unknown"
            elif cnt == 0:
                item["mount_state"] = "empty"
            else:
                item["mount_state"] = "has_video"
                item["mount_video_count"] = cnt
            return item

        items: list[dict] = []
        if empty_dirs:
            workers = min(8, max(1, len(empty_dirs)))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for item in pool.map(check_one, empty_dirs):
                    items.append(item)

        mapped = [i for i in items if i["mapped"]]
        unmapped = [i for i in items if not i["mapped"]]
        both_empty = [i for i in mapped if i["mount_state"] in ("empty", "missing")]
        mount_has_video = [i for i in mapped if i["mount_state"] == "has_video"]
        mount_unknown = [i for i in mapped if i["mount_state"] == "unknown"]

        logger.info(
            f"🧽 [空间猎犬] 网盘侧检查完成：可映射 {len(mapped)} 个 | "
            f"网盘也为空 {len(both_empty)} 个 | "
            f"网盘仍有视频 {len(mount_has_video)} 个 | "
            f"无法确认 {len(mount_unknown)} 个 | "
            f"无法映射 {len(unmapped)} 个"
        )
        for it in mount_has_video:
            logger.info(
                f"   [-] ⚠️ [空间猎犬] 本地空壳但网盘仍有 {it['mount_video_count']} 个视频: "
                f"{it['mount']}"
            )
        for it in unmapped:
            logger.info(f"   [-] ⏭️ [空间猎犬] 无法映射到网盘目录，已跳过: {it['local']}")

        logger.info(
            f"🧽 [空间猎犬] 扫描圆满结束！最终锁定 {len(items)} 个空壳目录。"
        )
        return {
            "success": True,
            # 兼容旧字段：只返回可安全处理的本地路径
            "empty_mount": [i["mount"] for i in both_empty if i["mount"]],
            "empty_meta": [i["local"] for i in items],
            "items": items,
            "scan_paths": meta_paths,
            "total_folders": total_folders,
            "content_exts": content_exts,
            "summary": {
                "total": len(items),
                "mapped": len(mapped),
                "both_empty": len(both_empty),
                "mount_has_video": len(mount_has_video),
                "mount_unknown": len(mount_unknown),
                "unmapped": len(unmapped),
            },
        }
    except Exception as e:  # noqa: BLE001
        logger.error(f"🧽 [空间猎犬] 扫描过程遭遇异常: {e}")
        return {"success": False, "error": str(e)}


def delete_empty_folders(
    folders: list[str],
    include_mount: bool = False,
    items: list[dict] | None = None,
) -> dict:
    """删除空壳目录。

    参数：
        folders       本地目录列表（兼容旧调用）
        include_mount 是否同时删除对应的网盘目录（「两边都删」）
        items         扫描结果详情；提供时按其中的映射关系决定网盘删除范围

    安全约束：
      1) 只有「可映射」且「网盘侧无视频」的条目才会删网盘目录；
      2) 删网盘目录前会**再次现场校验**是否仍无视频，
         避免扫描后到删除前又有文件落盘（TOCTOU）；
      3) 无法映射的条目一律跳过，只记录日志，不做任何删除。
    """
    deleted = 0
    failed = 0
    mount_deleted = 0
    mount_failed = 0
    skipped_unmapped = 0
    skipped_mount_busy = 0

    video_exts = [e.lower() for e in VIDEO_EXTS]

    # 归一化 items：无 items 时按纯本地列表处理
    plan: list[dict] = []
    if items:
        for it in items:
            if not isinstance(it, dict):
                continue
            local = str(it.get("local") or "").strip()
            if not local:
                continue
            plan.append(
                {
                    "local": local,
                    "mount": str(it.get("mount") or "").strip() or None,
                    "mapped": bool(it.get("mapped")),
                    "mount_state": str(it.get("mount_state") or "unmapped"),
                }
            )
    else:
        for f in folders or []:
            plan.append({"local": f, "mount": None, "mapped": False, "mount_state": "unmapped"})

    if config.get_dry_run():
        if include_mount:
            logger.info(
                f"🧽 [Dry Run] 模拟执行「两边都删」：本地 {len(plan)} 个目录，"
                f"其中网盘侧可删 {sum(1 for p in plan if p['mapped'] and p['mount_state'] == 'empty')} 个。"
            )
        else:
            logger.info(f"🧽 [Dry Run] 模拟清理了 {len(plan)} 个本地僵尸目录。")
        return {
            "success": True,
            "deleted": len(plan),
            "failed": 0,
            "mount_deleted": 0,
            "mount_failed": 0,
            "skipped_unmapped": 0,
            "skipped_mount_busy": 0,
        }

    for p in plan:
        local = p["local"]
        mapped = p["mapped"] and bool(p["mount"])
        state = p["mount_state"]

        # ---- 「两边都删」：必须能映射，且网盘侧也为空（或已不存在）----
        # 网盘仍有视频时整条跳过（本地也不删），避免「视频还在、元数据却被清掉」。
        # 若只想清本地，请用「删除本地」。
        if include_mount:
            if not mapped:
                skipped_unmapped += 1
                logger.info(f"   [-] ⏭️ [空间猎犬] 无法映射网盘目录，已跳过: {local}")
                continue
            if state not in ("empty", "missing"):
                skipped_mount_busy += 1
                logger.info(
                    f"   [-] ⏭️ [空间猎犬] 网盘侧仍有内容，整条跳过（本地也未删除）: {p['mount']}"
                )
                continue
            if state == "empty" and p["mount"]:
                # TOCTOU 防护：删除前再确认一次没有视频
                cnt = fs.count_files_by_ext(p["mount"], video_exts, recursive=True)
                if cnt != 0:
                    skipped_mount_busy += 1
                    logger.warning(
                        f"   [-] ⚠️ [空间猎犬] 复查发现网盘目录已有内容，整条跳过: {p['mount']}"
                    )
                    continue
                if fs.safe_delete_dir(p["mount"]):
                    mount_deleted += 1
                    logger.info(f"   [-] 🧹 [空间猎犬] 已删除网盘空目录: {p['mount']}")
                else:
                    mount_failed += 1
                    logger.warning(f"   [-] ⚠️ [空间猎犬] 网盘目录删除失败或受保护: {p['mount']}")

        # ---- 本地侧 ----
        if fs.safe_delete_dir(local):
            deleted += 1
        else:
            failed += 1

    if deleted or mount_deleted:
        logger.info(
            f"🧹 [空间清理] 删除本地空壳 {deleted} 个"
            + (f"，删除网盘空目录 {mount_deleted} 个" if include_mount else "")
            + (f"，跳过（无法映射）{skipped_unmapped} 个" if skipped_unmapped else "")
            + (f"，跳过（网盘仍有内容）{skipped_mount_busy} 个" if skipped_mount_busy else "")
            + "。"
        )

    return {
        "success": True,
        "deleted": deleted,
        "failed": failed,
        "mount_deleted": mount_deleted,
        "mount_failed": mount_failed,
        "skipped_unmapped": skipped_unmapped,
        "skipped_mount_busy": skipped_mount_busy,
    }
