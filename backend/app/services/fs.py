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
        paths.append(config.mount_path_for(w))
        paths.append(config.meta_path_for(w))
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
    """根据本地元数据目录推导对应的网盘挂载目录。

    **无法可靠映射时返回 None**，不再像旧实现那样静默返回一个
    「网盘根 + 相对路径」的错误路径。

    原因：调用方会拿这个结果去删网盘目录（查重洗板）或判断网盘是否为空
    （空间猎犬）。若静默给出错误路径，可能导致：
      - 删除错误的网盘目录；
      - 把「网盘侧还有文件」误判成「网盘侧为空」，进而误删本地。

    映射可靠的判定：该目录必须落在某个「监听目录对应的元数据子路径」之下。
    推导链条与 config.meta_subdir_for / mount_path_for 保持完全一致，
    保证「本地路径 <-> 网盘路径」双向自洽。
    """
    if not meta_dir:
        return None

    meta_base = _norm(config.metadata_base_path()).rstrip("/")
    p = _norm(meta_dir)
    if not p or p == meta_base or not p.startswith(meta_base + "/"):
        return None

    rel = p[len(meta_base):].lstrip("/")
    if not rel:
        return None

    for w in config.watched_dirs():
        w_meta_sub = _norm(config.meta_subdir_for(w, meta_base)).strip("/")
        if not w_meta_sub:
            continue
        if not (rel == w_meta_sub or rel.startswith(w_meta_sub + "/")):
            continue

        tail = rel[len(w_meta_sub):].lstrip("/")
        mount_root = _norm(config.mount_path_for(w)).rstrip("/")
        if not mount_root:
            continue

        cand = mount_root + ("/" + tail if tail else "")

        # 优先返回真实存在的路径（兼容网盘端大小写差异）
        if os.path.isdir(cand):
            return cand
        parent = os.path.dirname(cand)
        if os.path.isdir(parent):
            target = os.path.basename(cand).upper()
            try:
                for entry in os.scandir(parent):
                    if entry.is_dir() and entry.name.upper() == target:
                        return entry.path
            except OSError:
                pass
        # 目录可能已被删除，但映射关系本身是确定的，仍然返回路径
        return cand

    # 没有任何监听目录能解释这个本地路径 -> 无法映射
    return None


def count_files_by_ext(dir_path: str, exts, recursive: bool = True) -> int:
    """统计目录下（可递归）匹配指定后缀的文件数量。

    用于判断网盘目录「是否为空」。
    目录不存在时返回 0；读取异常也按 0 处理（调用方据此决定是否删除，
    因此这里宁可保守：异常时返回 -1 表示「无法确认」）。
    """
    ext_set = {str(e).lower() for e in (exts or []) if str(e).strip()}
    if not os.path.isdir(dir_path):
        return 0
    if not ext_set:
        return -1

    count = 0
    try:
        if recursive:
            for root, _dirs, files in os.walk(dir_path):
                for fn in files:
                    if os.path.splitext(fn)[1].lower() in ext_set:
                        count += 1
                # 已经找到内容就不必继续遍历
                if count:
                    return count
        else:
            with os.scandir(dir_path) as it:
                for entry in it:
                    try:
                        if entry.is_file() and os.path.splitext(entry.name)[1].lower() in ext_set:
                            count += 1
                    except OSError:
                        continue
    except OSError:
        return -1
    return count


def meta_dir_of(dir_path: str) -> str:
    """本地媒体目录 -> 对应元数据目录。

    不再依赖写死的 "/115open/"，改为按监听目录的相对关系推导：
    先找该媒体目录属于哪个监听目录的网盘侧，再换算到元数据侧。

    注意：比较前必须把分隔符统一（Windows 上 dir_path 可能是反斜杠，
    而配置里的根路径已被规范成正斜杠），否则前缀匹配失败会走错分支。
    """
    p = str(dir_path or "").replace("\\", "/")

    for w in config.watched_dirs():
        w_mount_root = config.mount_path_for(w).replace("\\", "/").rstrip("/")
        if not w_mount_root:
            continue
        if p == w_mount_root or p.startswith(w_mount_root + "/"):
            tail = p[len(w_mount_root):]
            return config.meta_path_for(w).replace("\\", "/").rstrip("/") + tail

    # 兜底：直接按根目录替换
    mount_base = config.mount_base_path().replace("\\", "/").rstrip("/")
    meta_base = config.metadata_base_path().replace("\\", "/").rstrip("/")
    if mount_base and p.startswith(mount_base):
        return meta_base + p[len(mount_base):]
    return p.replace(mount_base, meta_base)


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
