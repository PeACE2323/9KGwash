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


def meta_scan_paths() -> list[str]:
    paths = []
    meta_base = config.metadata_base_path().rstrip("/")
    for w in config.watched_dirs():
        meta_path = (meta_base + w.replace("/115open/", "/")).rstrip("/")
        if os.path.exists(meta_path):
            paths.append(meta_path)
    return paths


# ---------------------------------------------------------------------------
# 1. 全库查重扫描
# ---------------------------------------------------------------------------
def scan_duplicates() -> dict:
    logger.info("🔍 [全库查重] 启动本地媒体库查重扫描，正在进行极速预检...")
    scan_paths = meta_scan_paths()

    if not scan_paths:
        logger.warning("🔍 [全库查重] 未发现有效的本地元数据监听目录！")
        return {"success": True, "duplicates": []}

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
        return {"success": True, "duplicates": []}

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
        return {"success": True, "duplicates": duplicates_result}
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
def scan_empty_folders() -> dict:
    empty_meta: list[str] = []
    logger.info("🧽 [空间猎犬] 启动本地空壳目录扫描，正在进行极速预检...")
    total_folders = 0
    aux = _aux_dirs()

    def count_target_folders(base_path):
        count = 0
        for root, dirs, files in os.walk(base_path):
            if os.path.basename(root).lower() in aux:
                continue
            dirs[:] = [d for d in dirs if d.lower() not in aux]
            if any(f.lower().endswith((".strm", ".nfo")) for f in files):
                dirs.clear()
            count += 1
        return count

    try:
        meta_paths = []
        for w in config.watched_dirs():
            meta_path = config.metadata_base_path().rstrip("/") + w.replace("/115open/", "/")
            meta_path = meta_path.rstrip("/")
            if os.path.exists(meta_path):
                meta_paths.append(meta_path)
                total_folders += count_target_folders(meta_path)

        if total_folders == 0:
            logger.info("🧽 [空间猎犬] 预检完毕，未发现任何本地监听目录。")
            return {"success": True, "empty_mount": [], "empty_meta": []}

        logger.info(f"🧽 [空间猎犬] 预检完成！共计 {total_folders} 个本地目录节点，正式开始查杀排雷...")
        scanned_count = 0
        for meta_path in meta_paths:
            for root, dirs, files in os.walk(meta_path):
                if os.path.basename(root).lower() in aux:
                    continue
                dirs[:] = [d for d in dirs if d.lower() not in aux]
                has_strm = any(f.lower().endswith(".strm") for f in files)
                is_movie_dir = has_strm or any(f.lower().endswith(".nfo") for f in files)
                if is_movie_dir:
                    dirs.clear()

                scanned_count += 1
                if scanned_count % 100 == 0 or scanned_count == total_folders:
                    percent = int((scanned_count / total_folders) * 100)
                    logger.info(
                        f"🧽 [空间猎犬] 本地扫描进度: {percent}% ({scanned_count}/{total_folders}) | "
                        f"累计揪出 {len(empty_meta)} 个空壳"
                    )

                if not dirs and root != meta_path:
                    if not has_strm:
                        empty_meta.append(root)

        logger.info(
            f"🧽 [空间猎犬] 扫描圆满结束！100% 本地极速排查完毕。最终锁定 {len(empty_meta)} 个僵尸目录。"
        )
        return {"success": True, "empty_mount": [], "empty_meta": empty_meta}
    except Exception as e:  # noqa: BLE001
        logger.error(f"🧽 [空间猎犬] 扫描过程遭遇异常: {e}")
        return {"success": False, "error": str(e)}


def delete_empty_folders(folders: list[str]) -> dict:
    deleted = 0
    failed = 0

    if config.get_dry_run():
        logger.info(f"🧽 [Dry Run] 模拟清理了 {len(folders)} 个本地僵尸目录。")
        return {"success": True, "deleted": len(folders), "failed": 0}

    for f in folders or []:
        if fs.safe_delete_dir(f):
            deleted += 1
        else:
            failed += 1

    if deleted > 0:
        logger.info(f"🧹 [空间清理] 成功斩杀了 {deleted} 个无STRM的本地空壳目录。")

    return {"success": True, "deleted": deleted, "failed": failed}
