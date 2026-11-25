"""元数据备份 / 回滚：解决「刮削覆盖海报，新版本又被洗掉」的问题。

背景
----
流程是「先刮削、再洗板」。同一番号的不同版本放在同一个元数据目录里、
共用一张 ``poster.jpg``。新版本落盘后刮削器会用自己的截图覆盖海报；
如果洗板判定新版权重更低、把它删掉，海报却已经被换成「已被删除的版本」，
旧版本的封面就再也回不来了。

做法
----
在「元数据根目录」之外再放一份 **洗板前快照**（目录结构完全一致）。
备份目录放在元数据根目录之外或之内都可以：放在内部时，所有遍历都会
自动跳过备份目录自身，不会出现「备份的备份」。

* 手动「同步」：把整个元数据根目录里的元数据复制到备份目录，做初始快照；
* 洗板结束后按胜负自动维护：
  1. 只有一个版本（无需洗板）→ 把新影片的元数据**写入**备份；
  2. 洗板后新版本胜出（触发文件没被删）→ 把当前元数据**覆盖**到备份；
  3. 洗板后新版本落败（触发文件被删）→ 把备份里的元数据**回滚**到元数据目录。
* 影片被真正删除时，备份里对应的那份也要**跟着删掉**，否则快照会不断膨胀：
  - Emby 手动删除影片（`library.deleted` 事件）→ 按事件里的路径精确删除；
  - 容器停机期间发生的删除 → 用「🧹 清理孤儿备份」扫描一次补齐。

备份范围
--------
只备份「元数据」：图片、nfo、json、字幕。

**不备份 .strm**：strm 由 CMS 按网盘里的实际文件生成，回滚一张指向
已删除文件的旧 strm 只会制造死链，不如让 CMS 自己重建。

延迟写入
--------
第 1、2 种情况要保存的是「刮削之后」的元数据，而洗板可能早于刮削完成。
因此这两类写入会延迟 ``BACKUP_DELAY_SEC`` 秒在后台执行，给刮削留出落盘时间；
第 3 种情况（回滚）必须立刻执行，否则旧海报就找不回来了。
"""

from __future__ import annotations

import os
import shutil
import threading
import time

from .. import config
from ..logging_setup import categorized

logger = categorized("wash")

# 允许进入备份的文件后缀（白名单）。
# 刻意不含 .strm 与任何视频后缀：strm 由 CMS 生成，视频不属于元数据。
BACKUP_EXTS = frozenset(
    {
        # 图片
        ".jpg",
        ".jpeg",
        ".png",
        ".webp",
        ".gif",
        ".bmp",
        ".tbn",
        ".ico",
        # 影片信息
        ".nfo",
        ".json",
        ".xml",
        # 字幕
        ".srt",
        ".ass",
        ".ssa",
        ".vtt",
        ".sub",
        ".smi",
    }
)

# 明确排除（即使将来白名单里误加，也不会备份/回滚 strm）
SKIP_EXTS = frozenset({".strm"})

# 系统残留目录，遍历时一律跳过
_SYSTEM_DIRS = ("@eaDir", "#recycle", ".@__thumb")

# 洗板后延迟备份的秒数（给刮削留时间）。测试可 monkeypatch 成 0。
#
# 注意：CMS 唤醒现在走 ``cms.CMS_DEBOUNCE_SEC``（10 秒）的落盘缓冲窗口，
# 也就是说「洗板结束」到「CMS 真正开始刮削」之间还要再等约 10 秒。
# 这个延迟必须把那段也算进去，否则会在刮削还没写完时就拍下不完整的快照
# （典型症状：备份里只有 nfo / 没有海报）。
BACKUP_DELAY_SEC = 45

# 串行化：同步按钮与洗板后的自动备份可能同时跑
_LOCK = threading.RLock()

# 最近一次全量同步的统计（供前端展示）
_last_sync: dict = {
    "at": 0.0,
    "ok": False,
    "dirs": 0,
    "files": 0,
    "copied": 0,
    "skipped": 0,
    "bytes": 0,
    "errors": [],
    "message": "",
}


# ---------------------------------------------------------------------------
# 路径映射
# ---------------------------------------------------------------------------


def _abspath(p: str) -> str:
    return os.path.abspath(str(p)) if p else ""


def rel_meta(meta_dir: str) -> str | None:
    """元数据目录 -> 相对元数据根的路径（根目录本身返回 ""）。

    不在元数据根之下时返回 None（无法映射，调用方应跳过）。
    """
    base = _abspath(config.metadata_base_path())
    p = _abspath(meta_dir)
    if not base or not p:
        return None
    try:
        rel = os.path.relpath(p, base)
    except ValueError:
        # Windows 跨盘符
        return None
    if rel == ".":
        return ""
    if rel == ".." or rel.startswith(".." + os.sep) or rel.startswith("../"):
        return None
    return rel


def backup_dir_for(meta_dir: str) -> str | None:
    """元数据目录 -> 备份目录。未启用备份或无法映射时返回 None。"""
    root = config.meta_backup_path()
    if not root:
        return None
    rel = rel_meta(meta_dir)
    if rel is None:
        return None
    return os.path.join(root, rel) if rel else root


def is_enabled() -> bool:
    return bool(config.meta_backup_path())


def backup_root() -> str:
    return config.meta_backup_path()


def _backup_root_abs() -> str:
    """备份根目录的规范绝对路径（小写化，便于路径比较）；未启用返回空串。"""
    root = backup_root()
    return os.path.normcase(os.path.abspath(root)) if root else ""


def _backup_inside_meta() -> bool:
    """备份目录是否位于元数据根目录内部。

    这种布局是允许的（很多人的备份就放在同一个媒体盘里），但遍历元数据根时
    必须把备份目录自身排除掉，否则「同步」会把自己的备份再备份一遍。
    """
    root = _backup_root_abs()
    if not root:
        return False
    base = config.metadata_base_path()
    if not base:
        return False
    b = os.path.normcase(os.path.abspath(base))
    return root.startswith(b + os.sep) or root.startswith(b + "/")


def _backup_root_conflict() -> str:
    """备份目录与元数据根重叠到「无法安全运行」时返回错误说明（否则返回空串）。

    只拦两种情况：
      * 备份目录 == 元数据根目录（备份和原文件混在一起，无法区分）；
      * 元数据根目录在备份目录内部（清理孤儿备份时会扫到原文件并误删）。

    「备份目录在元数据根目录内部」是允许的，遍历时自动跳过它（见 _walk_files）。
    """
    root = config.meta_backup_path()
    if not root:
        return ""
    base = config.metadata_base_path()
    if not base:
        return ""
    r = _abspath(root)
    b = _abspath(base)
    if r == b:
        return "备份目录不能与元数据根目录相同"
    if b.startswith(r + os.sep) or b.startswith(r + "/"):
        return f"元数据根目录不能放在备份目录内部（{root}）"
    return ""


# ---------------------------------------------------------------------------
# 单文件复制
# ---------------------------------------------------------------------------


def is_backup_file(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    if ext in SKIP_EXTS:
        return False
    return ext in BACKUP_EXTS


def _copy_if_needed(src: str, dst: str) -> str:
    """按大小 + mtime 增量复制。返回 copied / skipped。"""
    try:
        st_src = os.stat(src)
        if os.path.exists(dst):
            st_dst = os.stat(dst)
            if st_dst.st_size == st_src.st_size and int(st_dst.st_mtime) == int(st_src.st_mtime):
                return "skipped"
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)
        return "copied"
    except OSError:
        raise


def _walk_files(root: str):
    """遍历目录下所有文件（跳过系统残留文件与备份目录自身）。"""
    skip = _backup_root_abs()
    for cur, dirs, files in os.walk(root):
        dirs[:] = [
            d
            for d in dirs
            if d not in _SYSTEM_DIRS
            and os.path.normcase(os.path.abspath(os.path.join(cur, d))) != skip
        ]
        for fn in files:
            if fn.startswith("._"):
                continue
            yield os.path.join(cur, fn)


# ---------------------------------------------------------------------------
# 单个目录的备份 / 回滚
# ---------------------------------------------------------------------------


def backup_one(meta_dir: str) -> dict:
    """把某个元数据目录里的元数据复制到备份目录（覆盖同名文件）。"""
    res = {"ok": False, "dir": meta_dir, "copied": 0, "skipped": 0, "bytes": 0, "reason": ""}
    if not is_enabled():
        res["reason"] = "disabled"
        return res
    conflict = _backup_root_conflict()
    if conflict:
        res["reason"] = conflict
        return res

    dst_root = backup_dir_for(meta_dir)
    if dst_root is None:
        res["reason"] = "unmapped"
        return res
    if not os.path.isdir(meta_dir):
        res["reason"] = "missing"
        return res

    with _LOCK:
        for src in _walk_files(meta_dir):
            fn = os.path.basename(src)
            if not is_backup_file(fn):
                continue
            dst = os.path.join(dst_root, os.path.relpath(src, meta_dir))
            try:
                status = _copy_if_needed(src, dst)
            except OSError as e:  # noqa: BLE001
                logger.error(f"   [💾 备份] ❌ 复制失败 [{src}]: {e}")
                continue
            if status == "copied":
                res["copied"] += 1
                try:
                    res["bytes"] += os.path.getsize(src)
                except OSError:
                    pass
            else:
                res["skipped"] += 1

    res["ok"] = True
    return res


def restore_one(meta_dir: str, skip_basenames=None) -> dict:
    """把备份目录里的元数据回滚到元数据目录（覆盖同名文件）。

    ``skip_basenames``：本次洗板删掉的版本主干名集合。
    备份里如果还留着这些版本的 nfo / 字幕（例如快照建立得比新文件落盘晚），
    回滚它们会凭空复活「已被删除的版本」的元数据，变成孤儿文件。
    因此这类文件一律跳过；共享的海报/封面（poster.jpg 等）照常恢复。
    """
    res = {"ok": False, "dir": meta_dir, "copied": 0, "skipped": 0, "bytes": 0, "reason": ""}
    if not is_enabled():
        res["reason"] = "disabled"
        return res
    conflict = _backup_root_conflict()
    if conflict:
        res["reason"] = conflict
        return res

    src_root = backup_dir_for(meta_dir)
    if src_root is None:
        res["reason"] = "unmapped"
        return res
    if not os.path.isdir(src_root):
        res["reason"] = "no_backup"
        return res

    skip = {str(b).lower() for b in (skip_basenames or ()) if str(b).strip()}

    with _LOCK:
        for src in _walk_files(src_root):
            fn = os.path.basename(src)
            if not is_backup_file(fn):
                continue
            stem = os.path.splitext(fn)[0]
            # 共享封面（poster.jpg / fanart.jpg / <番号>-poster.jpg）的主干名
            # 不会等于某个版本的 basename，因此不会被误跳过。
            if skip and stem.lower() in skip:
                res["skipped"] += 1
                continue
            dst = os.path.join(meta_dir, os.path.relpath(src, src_root))
            try:
                status = _copy_if_needed(src, dst)
            except OSError as e:  # noqa: BLE001
                logger.error(f"   [💾 回滚] ❌ 复制失败 [{src}]: {e}")
                continue
            if status == "copied":
                res["copied"] += 1
                try:
                    res["bytes"] += os.path.getsize(src)
                except OSError:
                    pass
            else:
                res["skipped"] += 1

    res["ok"] = True
    return res


# ---------------------------------------------------------------------------
# 洗板后的自动维护
# ---------------------------------------------------------------------------


def backup_after_wash(meta_dir: str) -> None:
    """洗板后「保存当前元数据」——延迟执行，等刮削写完。"""
    if not is_enabled():
        return

    def _run() -> None:
        try:
            r = backup_one(meta_dir)
            if r["ok"]:
                logger.info(
                    f"   [💾 备份] 已保存元数据快照 {r['copied']} 个文件（跳过 {r['skipped']} 个未变化）: {meta_dir}"
                )
            elif r["reason"] not in ("disabled",):
                logger.warning(f"   [💾 备份] 跳过（{r['reason']}）: {meta_dir}")
        except Exception as e:  # noqa: BLE001
            logger.error(f"   [💾 备份] ❌ 异常 [{meta_dir}]: {e}")

    delay = max(0, int(BACKUP_DELAY_SEC))
    if delay <= 0:
        _run()
        return
    t = threading.Timer(delay, _run)
    t.daemon = True
    t.start()


def restore_after_wash(meta_dir: str, skip_basenames=None) -> dict:
    """洗板后「回滚元数据」——必须同步立即执行。"""
    if not is_enabled():
        return {"ok": False, "reason": "disabled"}
    r = restore_one(meta_dir, skip_basenames=skip_basenames)
    if r["ok"]:
        logger.info(
            f"   [💾 回滚] 已用备份还原元数据 {r['copied']} 个文件（跳过 {r['skipped']} 个未变化）: {meta_dir}"
        )
    else:
        reason = {
            "no_backup": "备份目录里没有该影片，无法回滚（请先点「立即同步」建立快照）",
            "unmapped": "该目录不在元数据根目录之下，无法映射到备份目录",
            "disabled": "未启用元数据备份目录",
        }.get(r["reason"], r["reason"])
        logger.warning(f"   [💾 回滚] ⚠️ {reason}: {meta_dir}")
    return r


# ---------------------------------------------------------------------------
# 删除备份：影片真的没了，快照也要跟着没
# ---------------------------------------------------------------------------

def _inside_backup(path: str) -> bool:
    """路径是否严格位于备份根目录内部（不含根目录本身）。"""
    root = _abspath(backup_root())
    p = _abspath(path)
    if not root or not p:
        return False
    if p == root:
        return False
    return p.startswith(root + os.sep) or p.startswith(root + "/")


def _prune_empty_parents(start: str) -> None:
    """删完子目录后，把变空的父目录一路删掉，但不越过备份根。"""
    root = _abspath(backup_root())
    cur = _abspath(start)
    while cur and cur != root and _inside_backup(cur):
        try:
            if os.listdir(cur):
                return
            os.rmdir(cur)
        except OSError:
            return
        cur = _abspath(os.path.dirname(cur))


def drop_one(meta_dir: str, require_missing: bool = True) -> dict:
    """删除备份目录里与某个元数据目录对应的那份快照。

    ``require_missing=True``（默认）时，只有**元数据目录已经不存在**才删，
    避免刮削重建目录的瞬间把还需要的快照误删。
    """
    res = {"ok": False, "dir": meta_dir, "reason": "", "removed": ""}
    if not is_enabled():
        res["reason"] = "disabled"
        return res
    conflict = _backup_root_conflict()
    if conflict:
        res["reason"] = conflict
        return res

    rel = rel_meta(meta_dir)
    if rel is None:
        res["reason"] = "unmapped"
        return res
    if not rel:
        # 元数据根目录本身，绝不整体删除
        res["reason"] = "root"
        return res

    dst = backup_dir_for(meta_dir)
    if not dst or not _inside_backup(dst):
        res["reason"] = "unmapped"
        return res
    if not os.path.isdir(dst):
        res["reason"] = "no_backup"
        return res
    if require_missing and os.path.isdir(meta_dir):
        res["reason"] = "kept"
        return res

    with _LOCK:
        try:
            shutil.rmtree(dst)
        except OSError as e:  # noqa: BLE001
            res["reason"] = f"error: {e}"
            return res
        _prune_empty_parents(os.path.dirname(dst))

    res["ok"] = True
    res["removed"] = dst
    return res


def drop_by_fanhao(fanhao: str, require_missing: bool = True) -> dict:
    """兜底：在备份树里按目录名包含的番号找到快照并删除。

    只有在拿不到准确路径（Emby 事件里没带 Path）时才用。
    """
    key = str(fanhao or "").strip().lower()
    res = {"ok": False, "reason": "", "removed": 0, "dirs": []}
    if not is_enabled():
        res["reason"] = "disabled"
        return res
    if not key:
        res["reason"] = "no_key"
        return res
    root = backup_root()
    meta_root = config.metadata_base_path()
    if not root or not os.path.isdir(root):
        res["reason"] = "no_backup"
        return res

    hits: list[str] = []
    for cur, dirs, _files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in _SYSTEM_DIRS]
        base = os.path.basename(cur).lower()
        if base and key in base:
            hits.append(cur)
            dirs[:] = []  # 命中就不再往里找

    if not hits:
        res["reason"] = "no_backup"
        return res

    removed = 0
    with _LOCK:
        for hit in hits:
            rel = os.path.relpath(hit, root)
            meta_dir = os.path.join(meta_root, rel)
            if require_missing and os.path.isdir(meta_dir):
                continue
            try:
                shutil.rmtree(hit)
            except OSError as e:  # noqa: BLE001
                logger.error(f"   [💾 清理] ❌ 删除备份失败 [{hit}]: {e}")
                continue
            removed += 1
            res["dirs"].append(rel)
            _prune_empty_parents(os.path.dirname(hit))

    res["ok"] = removed > 0
    if not res["ok"]:
        res["reason"] = "kept"
    res["removed"] = removed
    return res


def drop_for_deleted_item(item_path: str, item_name: str = "", fanhao: str = "") -> dict:
    """Emby 报告「影片被真正删除」时调用：把备份里对应的快照删掉。

    路径解析优先级：
      1. ``item_path`` 在元数据根目录下 -> 直接取它所在目录；
      2. ``item_path`` 在网盘挂载根下   -> 换算成元数据目录；
      3. 都失败 -> 退化成按番号在备份树里匹配。
    """
    res = {"ok": False, "reason": "", "removed": "", "dir": ""}
    if not is_enabled():
        res["reason"] = "disabled"
        return res

    meta_root = _abspath(config.metadata_base_path())
    mount_base = _abspath(config.mount_base_path())
    p = _abspath(item_path) if item_path else ""

    candidates: list[str] = []
    if p:
        parent = os.path.dirname(p)
        if meta_root and rel_meta(parent) is not None:
            candidates.append(parent)
        if mount_base and (
            p == mount_base or p.startswith(mount_base + os.sep) or p.startswith(mount_base + "/")
        ):
            try:
                from ..services import fs  # 延迟导入，避免模块级循环依赖

                candidates.append(fs.meta_dir_of(parent))
            except Exception:  # noqa: BLE001
                pass

    seen: set[str] = set()
    for cand in candidates:
        key = _abspath(cand).lower()
        if not key or key in seen:
            continue
        seen.add(key)
        r = drop_one(cand, require_missing=True)
        if r["ok"]:
            return r
        res = {**r, "dir": cand}

    fallback_key = fanhao or item_name
    if fallback_key:
        r = drop_by_fanhao(fallback_key, require_missing=True)
        if r["ok"]:
            return r
        if not res.get("reason"):
            res = {**res, "reason": r.get("reason", "")}

    return res


def prune_orphans() -> dict:
    """清理孤儿备份：备份里有、元数据根目录里已经不存在的那一份，整份删掉。

    用于补齐「容器停机期间发生的删除」。为了安全：
    元数据根目录不存在时**直接拒绝执行**，避免挂载掉线时把备份清空。
    """
    res = {
        "ok": False,
        "removed": 0,
        "dirs": [],
        "errors": [],
        "message": "",
    }
    if not is_enabled():
        res["message"] = "未配置元数据备份目录"
        return res
    conflict = _backup_root_conflict()
    if conflict:
        res["message"] = conflict
        return res

    root = backup_root()
    meta_root = config.metadata_base_path()
    if not os.path.isdir(meta_root):
        res["message"] = f"元数据根目录不存在，已取消清理以免误删全部备份：{meta_root}"
        return res
    if not os.path.isdir(root):
        res["ok"] = True
        res["message"] = "备份目录还不存在，无需清理"
        return res

    with _LOCK:
        for cur, dirs, _files in os.walk(root, topdown=True):
            keep = []
            for d in list(dirs):
                if d in _SYSTEM_DIRS:
                    keep.append(d)
                    continue
                full = os.path.join(cur, d)
                rel = os.path.relpath(full, root)
                if os.path.isdir(os.path.join(meta_root, rel)):
                    keep.append(d)
                    continue
                try:
                    shutil.rmtree(full)
                except OSError as e:  # noqa: BLE001
                    if len(res["errors"]) < 20:
                        res["errors"].append(f"{rel}: {e}")
                    keep.append(d)
                    continue
                res["removed"] += 1
                if len(res["dirs"]) < 200:
                    res["dirs"].append(rel)
            dirs[:] = keep

    res["ok"] = True
    res["message"] = f"清理完成：删除 {res['removed']} 个孤儿备份目录"
    if res["errors"]:
        res["message"] += f"，{len(res['errors'])} 个删除失败"
    if res["removed"]:
        logger.info(f"💾 [备份] {res['message']}")
    return res


# ---------------------------------------------------------------------------
# 全量同步（前端「立即同步」按钮）
# ---------------------------------------------------------------------------


def sync_all() -> dict:
    """把整个元数据根目录的元数据同步到备份目录（增量复制）。"""
    global _last_sync

    res = {
        "ok": False,
        "dirs": 0,
        "files": 0,
        "copied": 0,
        "skipped": 0,
        "bytes": 0,
        "errors": [],
        "message": "",
        "elapsed": 0.0,
    }
    started = time.time()

    if not is_enabled():
        res["message"] = "未配置元数据备份目录"
        _last_sync = {**res, "at": time.time()}
        return res

    conflict = _backup_root_conflict()
    if conflict:
        res["message"] = conflict
        _last_sync = {**res, "at": time.time()}
        return res

    src_root = config.metadata_base_path()
    dst_root = backup_root()
    if not os.path.isdir(src_root):
        res["message"] = f"元数据根目录不存在：{src_root}"
        _last_sync = {**res, "at": time.time()}
        return res

    try:
        os.makedirs(dst_root, exist_ok=True)
    except OSError as e:
        res["message"] = f"无法创建备份目录：{e}"
        _last_sync = {**res, "at": time.time()}
        return res

    logger.info(f"💾 [备份] 开始同步元数据：{src_root} -> {dst_root}")

    with _LOCK:
        dst_abs = _backup_root_abs()
        for cur, dirs, files in os.walk(src_root):
            # 备份目录可能就在元数据根内部，必须跳过，否则会把备份再备份一遍
            dirs[:] = [
                d
                for d in dirs
                if d not in _SYSTEM_DIRS
                and os.path.normcase(os.path.abspath(os.path.join(cur, d))) != dst_abs
            ]
            res["dirs"] += 1
            for fn in files:
                if fn.startswith("._"):
                    continue
                if not is_backup_file(fn):
                    continue
                res["files"] += 1
                src = os.path.join(cur, fn)
                dst = os.path.join(dst_root, os.path.relpath(src, src_root))
                try:
                    status = _copy_if_needed(src, dst)
                except OSError as e:  # noqa: BLE001
                    msg = f"{src}: {e}"
                    if len(res["errors"]) < 20:
                        res["errors"].append(msg)
                    continue
                if status == "copied":
                    res["copied"] += 1
                    try:
                        res["bytes"] += os.path.getsize(src)
                    except OSError:
                        pass
                else:
                    res["skipped"] += 1

    res["ok"] = True
    res["elapsed"] = round(time.time() - started, 1)
    res["message"] = (
        f"同步完成：{res['files']} 个元数据文件，新增/更新 {res['copied']} 个，"
        f"跳过 {res['skipped']} 个未变化，用时 {res['elapsed']} 秒"
    )
    if res["errors"]:
        res["message"] += f"，{len(res['errors'])} 个文件复制失败"
    logger.info(f"💾 [备份] {res['message']}")

    _last_sync = {**res, "at": time.time()}
    return res


def last_sync() -> dict:
    return dict(_last_sync)


def status() -> dict:
    """备份功能状态：是否启用、备份目录规模、最近一次同步。"""
    out = {
        "enabled": is_enabled(),
        "root": backup_root(),
        "meta_root": config.metadata_base_path(),
        "conflict": _backup_root_conflict(),
        "nested": _backup_inside_meta(),
        "dirs": 0,
        "files": 0,
        "bytes": 0,
        "last_sync": last_sync(),
    }
    root = backup_root()
    if not root or not os.path.isdir(root):
        return out

    dirs = files = total = 0
    for cur, subdirs, names in os.walk(root):
        dirs += 1
        for fn in names:
            if not is_backup_file(fn):
                continue
            files += 1
            try:
                total += os.path.getsize(os.path.join(cur, fn))
            except OSError:
                pass
    out["dirs"] = dirs
    out["files"] = files
    out["bytes"] = total
    return out
