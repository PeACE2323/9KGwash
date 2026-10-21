"""文件系统层：安全删除、路径推导、空壳目录探测、精确关联文件清理。

所有删除动作都经过 ``safe_delete_dir`` 的安全锁，规则与原脚本完全一致：
只有位于监听根目录之下、且不是受保护根目录本身的路径才允许删除。
"""

from __future__ import annotations

import os
import shutil

from .. import config
from ..logging_setup import logger

CLEAN_EXACT_EXTENSIONS = (".strm", ".nfo", ".json", ".srt", ".ass", ".vtt")


def protected_paths() -> list[str]:
    mount_base = config.mount_base_path().rstrip("/")
    meta_base = config.metadata_base_path().rstrip("/")
    paths = [mount_base, meta_base]
    for w in config.watched_dirs():
        paths.append((mount_base + w).rstrip("/"))
        paths.append((meta_base + w.replace("/115open/", "/")).rstrip("/"))
    return paths


def _norm(p: str) -> str:
    """统一分隔符并去掉尾部斜杠，便于跨平台前缀比较。

    容器内（Linux）路径本身只用 ``/``，此函数等价于恒等变换；
    在 Windows 上开发/测试时可避免 ``C:\\a/b`` 与 ``C:\\a`` 前缀判断失误，
    使安全锁行为与生产环境保持一致。
    """
    if not p:
        return p
    q = str(p).replace("\\", "/") if os.sep == "\\" else str(p)
    q = q.rstrip("/")
    return q or "/"


def safe_delete_dir(dir_path: str) -> bool:
    """删除整个目录（含安全边界校验）。"""
    if not dir_path:
        return False
    protected = [_norm(p) for p in protected_paths()]
    target = _norm(dir_path)

    is_safe = False
    for p in protected:
        if target.startswith(p) and target != p:
            is_safe = True
            break

    if not is_safe or target in protected:
        logger.warning(f"⚠️ [安全锁] 试图删除越界或受保护的根目录 {dir_path}，已强制拦截！")
        return False

    if os.path.exists(dir_path):
        try:
            shutil.rmtree(dir_path)
            return True
        except Exception as e:  # noqa: BLE001
            logger.error(f"❌ [删除失败] 物理删除目录异常 [{dir_path}]: {e}")
            return False
    return False


def fast_check_folder_exists(base_path: str, keyword: str, max_depth: int = 3) -> bool:
    """广度优先、限定深度的目录名关键字探测（用于 Emby 幽灵删除判断）。"""
    if not os.path.exists(base_path):
        return False
    keyword = keyword.upper()
    dirs_to_scan = [(base_path, 0)]

    while dirs_to_scan:
        current_path, depth = dirs_to_scan.pop(0)
        if depth > max_depth:
            continue
        try:
            with os.scandir(current_path) as it:
                for entry in it:
                    if entry.is_dir():
                        if keyword in entry.name.upper():
                            return True
                        dirs_to_scan.append((entry.path, depth + 1))
        except Exception:  # noqa: BLE001
            continue
    return False


def meta_to_mount_dir(meta_dir: str):
    """根据本地元数据目录推导对应的网盘挂载目录，支持自适应容错。"""
    meta_base = config.metadata_base_path().rstrip("/")
    mount_base = config.mount_base_path()
    if not meta_dir or not meta_dir.startswith(meta_base):
        return None
    rel = os.path.relpath(meta_dir, meta_base)
    for w in config.watched_dirs():
        w_meta_sub = w.replace("/115open/", "/").strip("/")
        if rel == w_meta_sub or rel.startswith(w_meta_sub + os.sep) or rel.startswith(w_meta_sub + "/"):
            w_mount_sub = w.strip("/")
            mount_rel = rel.replace(w_meta_sub, w_mount_sub, 1)
            mount_path = os.path.join(mount_base, mount_rel)
            if os.path.exists(mount_path):
                return mount_path
            # 兼容大小写或网盘端轻微命名差异
            parent_mount = os.path.dirname(mount_path)
            if os.path.exists(parent_mount):
                target_name = os.path.basename(meta_dir).upper()
                try:
                    for entry in os.scandir(parent_mount):
                        if entry.is_dir() and entry.name.upper() == target_name:
                            return entry.path
                except Exception:  # noqa: BLE001
                    pass
            return mount_path
    return os.path.join(mount_base, rel)


def meta_dir_of(dir_path: str) -> str:
    """本地媒体目录 -> 对应元数据目录（原脚本的 replace 推导方式）。"""
    return dir_path.replace(config.mount_base_path(), config.metadata_base_path()).replace("/115open/", "/")


def clean_exact_metadata(loser_basename: str, folder_path: str, only_delete_json: bool = False) -> set:
    """精确清理与输家同名的关联文件，返回实际删除的主干名集合。

    dry-run 时直接返回空集合（与原脚本一致：不产生任何删除行为）。
    """
    deleted_basenames: set[str] = set()
    if config.get_dry_run():
        return deleted_basenames
    if not folder_path or not os.path.exists(folder_path):
        return deleted_basenames

    try:
        with os.scandir(folder_path) as it:
            for entry in it:
                if entry.is_file():
                    clean_f = entry.name[2:] if entry.name.startswith("._") else entry.name
                    f_base, f_ext = os.path.splitext(clean_f)
                    f_ext = f_ext.lower()

                    if f_base == loser_basename:
                        if only_delete_json:
                            if f_ext == ".json":
                                os.remove(entry.path)
                                deleted_basenames.add(loser_basename)
                                logger.info(f"   [-] ✅ 成功清理关联文件 (保留覆盖件): {entry.name}")
                        else:
                            if f_ext in CLEAN_EXACT_EXTENSIONS:
                                os.remove(entry.path)
                                deleted_basenames.add(loser_basename)
                                logger.info(f"   [-] ✅ 成功连带清理关联文件: {entry.name}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"   [-] ❌ 扫描文件夹清理失败 [{folder_path}]: {e}")

    return deleted_basenames


def cleanup_dir_if_empty(dir_path: str) -> bool:
    """目录若已空则删除（内部调用 safe_delete_dir）。"""
    try:
        if os.path.exists(dir_path) and not os.listdir(dir_path):
            return safe_delete_dir(dir_path)
    except Exception:  # noqa: BLE001
        pass
    return False
