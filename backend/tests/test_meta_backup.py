"""元数据备份 / 回滚（META_BACKUP_PATH）的测试。

要解决的问题
------------
流程是「先刮削、再洗板」。同一番号的不同版本放在同一个元数据目录里、
共用一张 ``poster.jpg``。新版本落盘后刮削会用自己的截图覆盖海报；
如果洗板判定新版权重更低、把它删掉，海报却已经被换成「已被删除的版本」。

方案
----
维护一份「洗板前快照」，洗板后按胜负自动处理：
  ① 只有一个版本（无需洗板）      -> 把新影片的元数据写入备份
  ② 洗板后新版本胜出（触发文件没被删）-> 把当前元数据覆盖到备份
  ③ 洗板后新版本落败（触发文件被删）  -> 用备份把旧元数据还原回去
  ④ 影片被真正删除（Emby 手动删）      -> 备份里对应的那份也一起删掉

备份范围：图片 + nfo + json + 字幕；**不含 .strm**（由 CMS 生成）。
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_meta_backup")
shutil.rmtree(TMP, ignore_errors=True)
MOUNT = os.path.join(TMP, "cloud", "115open")
METAROOT = os.path.join(TMP, "meta")
META = os.path.join(METAROOT, "STRM")
BACKUP = os.path.join(TMP, "backup")
CFG = os.path.join(TMP, "cfg")
os.makedirs(CFG)
SUB = os.path.join("9KG", "STRM")

os.environ["XIBAN_CONFIG_DIR"] = CFG
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"

BASE_CFG = {
    "MOUNT_BASE_PATH": MOUNT.replace("\\", "/"),
    "METADATA_BASE_PATH": METAROOT.replace("\\", "/"),
    "WATCHED_DIRS": ["/9KG/STRM"],
    "META_SUBDIR_OVERRIDE": "/STRM",
    "META_BACKUP_PATH": BACKUP.replace("\\", "/"),
    "DRY_RUN": False,
}


def write_cfg(extra=None):
    c = dict(BASE_CFG)
    if extra:
        c.update(extra)
    with open(os.path.join(CFG, "config.json"), "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)


write_cfg()

from app import config  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import meta_backup, webhooks  # noqa: E402

# 洗板后的「保存快照」默认延迟 30 秒（等刮削落盘），测试里改成同步执行
meta_backup.BACKUP_DELAY_SEC = 0

app = create_app()
client = app.test_client()

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def wait():
    for _ in range(600):
        if webhooks.pending_count() == 0:
            time.sleep(0.05)
            if webhooks.pending_count() == 0:
                return
        time.sleep(0.02)


def reset():
    shutil.rmtree(MOUNT, ignore_errors=True)
    shutil.rmtree(METAROOT, ignore_errors=True)
    shutil.rmtree(BACKUP, ignore_errors=True)


def mk(p, files=()):
    os.makedirs(p, exist_ok=True)
    for f in files:
        path = os.path.join(p, f)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as fh:
            fh.write(f.encode())


def mk_mount(name, files):
    mk(os.path.join(MOUNT, SUB, name), files)


def mk_meta(name, files):
    mk(os.path.join(META, name), files)


def put(path, data: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data.encode())


def read(path):
    try:
        with open(path, "rb") as fh:
            return fh.read().decode()
    except OSError:
        return None


def hit(name, folder=None):
    """模拟 CD2 推送。folder 默认与 name 相同（一版一目录）；
    同番号多版本共用一个目录时传 folder=番号目录名。"""
    folder = folder or name
    r = client.post(
        "/webhook",
        json={
            "data": [
                {
                    "action": "create",
                    "is_dir": "false",
                    "destination_file": f"/115open/9KG/STRM/{folder}/{name}.mp4",
                }
            ]
        },
    )
    wait()
    return r


# ===========================================================================
print("\n=== A. 配置项 ===")
check("DEFAULT_CONFIG 含 META_BACKUP_PATH", "META_BACKUP_PATH" in config.DEFAULT_CONFIG)
check("默认值为空（不启用）", config.DEFAULT_CONFIG["META_BACKUP_PATH"] == "")
check("访问器返回配置值", config.meta_backup_path() == BACKUP.replace("\\", "/"),
      config.meta_backup_path())
check("meta_backup_enabled() 为 True", config.meta_backup_enabled() is True)

st = client.get("/api/status").get_json()
check("/api/status 返回 meta_backup_path",
      st.get("meta_backup_path") == BACKUP.replace("\\", "/"), st.get("meta_backup_path"))

client.post("/api/config", json={"META_BACKUP_PATH": ""})
check("清空后不启用", config.meta_backup_enabled() is False)
st = client.get("/api/meta_backup/status").get_json()
check("status 反映未启用", st.get("enabled") is False, st)
client.post("/api/config", json={"META_BACKUP_PATH": BACKUP.replace("\\", "/")})
check("重新启用", config.meta_backup_enabled() is True)

# 只改备份目录不应影响其它开关
client.post("/api/config", json={"DRY_RUN": False})
check("其它开关未被误改", config.get_dry_run() is False)

# ===========================================================================
print("\n=== B. 备份范围：只要元数据，不要 strm ===")
check("poster.jpg 会被备份", meta_backup.is_backup_file("poster.jpg"))
check("fanart.png 会被备份", meta_backup.is_backup_file("fanart.png"))
check("nfo 会被备份", meta_backup.is_backup_file("ABP-100.nfo"))
check("字幕会被备份", meta_backup.is_backup_file("ABP-100.srt"))
check(".strm 不会被备份", not meta_backup.is_backup_file("ABP-100.strm"))
check("视频不会被备份", not meta_backup.is_backup_file("ABP-100.mp4"))
check("视频不会被备份(mkv)", not meta_backup.is_backup_file("ABP-100.mkv"))

# ===========================================================================
print("\n=== C. 单目录备份 backup_one ===")
reset()
mk_meta("ABP-100", [
    "ABP-100.nfo", "ABP-100.strm", "ABP-100.srt",
    "poster.jpg", "fanart.jpg", "extrafanart/01.jpg",
])
r = meta_backup.backup_one(os.path.join(META, "ABP-100"))
check("备份成功", r.get("ok") is True, r)
bk_dir = os.path.join(BACKUP, "STRM", "ABP-100")
check("备份目录结构一致", os.path.isdir(bk_dir), bk_dir)
check("nfo 已备份", os.path.exists(os.path.join(bk_dir, "ABP-100.nfo")))
check("海报已备份", os.path.exists(os.path.join(bk_dir, "poster.jpg")))
check("字幕已备份", os.path.exists(os.path.join(bk_dir, "ABP-100.srt")))
check("子目录图片已备份", os.path.exists(os.path.join(bk_dir, "extrafanart", "01.jpg")))
check("strm 未被备份", not os.path.exists(os.path.join(bk_dir, "ABP-100.strm")))

r2 = meta_backup.backup_one(os.path.join(META, "ABP-100"))
check("第二次备份全部跳过（增量）", r2.get("copied") == 0 and r2.get("skipped") > 0, r2)

# ===========================================================================
print("\n=== D. 单目录回滚 restore_one ===")
put(os.path.join(META, "ABP-100", "poster.jpg"), "SCRAPED-NEW-POSTER-DATA")
put(os.path.join(META, "ABP-100", "ABP-100.nfo"), "SCRAPED-NEW-NFO")
r = meta_backup.restore_one(os.path.join(META, "ABP-100"))
check("回滚成功", r.get("ok") is True, r)
check("海报已还原为旧版", read(os.path.join(META, "ABP-100", "poster.jpg")) == "poster.jpg",
      read(os.path.join(META, "ABP-100", "poster.jpg")))
check("nfo 已还原", read(os.path.join(META, "ABP-100", "ABP-100.nfo")) == "ABP-100.nfo")
check("回滚不会删掉元数据目录里多出来的文件",
      os.path.exists(os.path.join(META, "ABP-100", "ABP-100.strm")))

# 回滚时跳过「本次被删版本」的元数据，避免复活孤儿 nfo
put(os.path.join(BACKUP, "STRM", "ABP-100", "ABP-100-X.nfo"), "GHOST")
r = meta_backup.restore_one(os.path.join(META, "ABP-100"), skip_basenames={"ABP-100-X"})
check("被删版本的 nfo 不会被复活",
      not os.path.exists(os.path.join(META, "ABP-100", "ABP-100-X.nfo")), r)

# 映射失败 / 未启用
check("不在元数据根下的目录无法映射",
      meta_backup.backup_one(os.path.join(TMP, "elsewhere")).get("reason") == "unmapped")
check("备份目录不存在时回滚返回 no_backup",
      meta_backup.restore_one(os.path.join(META, "NOPE-999")).get("reason") == "no_backup")

# ===========================================================================
print("\n=== E. 全量同步 sync_all ===")
reset()
mk_meta("AAA-001", ["AAA-001.nfo", "AAA-001.strm", "poster.jpg"])
mk_meta("BBB-002", ["BBB-002.nfo", "BBB-002.strm", "fanart.jpg"])
r = meta_backup.sync_all()
check("同步成功", r.get("ok") is True, r)
check("统计到 4 个元数据文件", r.get("files") == 4, r.get("files"))
check("首次全部为新增", r.get("copied") == 4, r.get("copied"))
check("备份根目录已建立", os.path.isdir(os.path.join(BACKUP, "STRM")))
check("AAA 的 nfo 已同步", os.path.exists(os.path.join(BACKUP, "STRM", "AAA-001", "AAA-001.nfo")))
check("AAA 的 strm 未同步",
      not os.path.exists(os.path.join(BACKUP, "STRM", "AAA-001", "AAA-001.strm")))

r = meta_backup.sync_all()
check("二次同步全部跳过", r.get("copied") == 0 and r.get("skipped") == 4, r)

# 备份目录放在元数据根「内部」：允许（很多人的备份就在同一个媒体盘里），
# 但遍历元数据根时必须跳过备份目录自身，否则会把备份再备份一遍。
NESTED = os.path.join(METAROOT, "_backup")
client.post("/api/config", json={"META_BACKUP_PATH": NESTED.replace("\\", "/")})
st = client.get("/api/meta_backup/status").get_json()
check("备份目录放在元数据根内部不再报冲突", st.get("conflict") == "", st.get("conflict"))
check("status 标记为 nested", st.get("nested") is True, st.get("nested"))
r = meta_backup.sync_all()
check("嵌套时同步照常成功", r.get("ok") is True, r)
check("嵌套时仍然只统计 4 个元数据文件", r.get("files") == 4, r.get("files"))
check("嵌套时备份落在元数据根内部",
      os.path.exists(os.path.join(NESTED, "STRM", "AAA-001", "AAA-001.nfo")))
check("嵌套时没有把备份自己再备份一遍", not os.path.exists(os.path.join(NESTED, "_backup")))
check("嵌套时没有把备份目录当成影片目录", not os.path.isdir(os.path.join(NESTED, "_backup", "STRM")))
r = meta_backup.sync_all()
check("嵌套时二次同步仍然全部跳过", r.get("copied") == 0 and r.get("skipped") == 4, r)
shutil.rmtree(NESTED, ignore_errors=True)

# 真正无法安全运行的两种重叠仍然拒绝
client.post("/api/config", json={"META_BACKUP_PATH": METAROOT.replace("\\", "/")})
st = client.get("/api/meta_backup/status").get_json()
check("备份目录等于元数据根目录被拒绝", bool(st.get("conflict")), st.get("conflict"))
check("此时 nested 为 False", st.get("nested") is False, st.get("nested"))
r = meta_backup.sync_all()
check("路径相同时同步被拒绝", r.get("ok") is False, r)

client.post("/api/config", json={"META_BACKUP_PATH": TMP.replace("\\", "/")})
st = client.get("/api/meta_backup/status").get_json()
check("元数据根目录在备份目录内部被拒绝", bool(st.get("conflict")), st.get("conflict"))
r = meta_backup.prune_orphans()
check("这种配置下清理孤儿备份也被拒绝", r.get("ok") is False, r)

client.post("/api/config", json={"META_BACKUP_PATH": BACKUP.replace("\\", "/")})
check("恢复合法路径后不再冲突", meta_backup.status().get("conflict") == "")
check("恢复合法路径后 nested 为 False", meta_backup.status().get("nested") is False)

# API
r = client.get("/api/meta_backup/status").get_json()
check("状态接口返回文件数", r.get("files") == 4, r.get("files"))
r = client.post("/api/meta_backup/sync").get_json()
check("同步接口可用", r.get("ok") is True, r)

# ===========================================================================
print("\n=== F. 洗板集成①：只有一个版本 -> 写入备份 ===")
reset()
mk_mount("CAT-300", ["CAT-300.mp4"])
mk_meta("CAT-300", ["CAT-300.nfo", "CAT-300.strm", "poster.jpg"])
hit("CAT-300")
bk = os.path.join(BACKUP, "STRM", "CAT-300")
check("新影片元数据已进入备份", os.path.exists(os.path.join(bk, "poster.jpg")), bk)
check("新影片 nfo 已进入备份", os.path.exists(os.path.join(bk, "CAT-300.nfo")))
check("strm 依然不进备份", not os.path.exists(os.path.join(bk, "CAT-300.strm")))
check("视频文件未被误删", os.path.exists(os.path.join(MOUNT, SUB, "CAT-300", "CAT-300.mp4")))

# ===========================================================================
print("\n=== G. 洗板集成②：新版本胜出 -> 覆盖备份 ===")
# 真实场景：同一番号的不同版本放在【同一个目录】里，共用一张 poster.jpg
reset()
mk_mount("DOG-400", ["DOG-400.mp4"])          # 旧版本，权重 0
mk_meta("DOG-400", ["DOG-400.nfo", "DOG-400.strm", "poster.jpg"])
meta_backup.sync_all()                         # 先建立「旧版本」快照

mk_mount("DOG-400", ["DOG-400-C.mp4"])        # 新版本，权重更高，落在同一目录
put(os.path.join(META, "DOG-400", "poster.jpg"), "NEW-POSTER-FROM-SCRAPER")
put(os.path.join(META, "DOG-400", "DOG-400-C.nfo"), "DOG-400-C.nfo")
hit("DOG-400-C", folder="DOG-400")

check("新版本胜出后旧版本被删",
      not os.path.exists(os.path.join(MOUNT, SUB, "DOG-400", "DOG-400.mp4")))
check("新版本仍在", os.path.exists(os.path.join(MOUNT, SUB, "DOG-400", "DOG-400-C.mp4")))
check("备份已更新为新版海报",
      read(os.path.join(BACKUP, "STRM", "DOG-400", "poster.jpg")) == "NEW-POSTER-FROM-SCRAPER",
      read(os.path.join(BACKUP, "STRM", "DOG-400", "poster.jpg")))
check("新版 nfo 已进入备份",
      os.path.exists(os.path.join(BACKUP, "STRM", "DOG-400", "DOG-400-C.nfo")))

# ===========================================================================
print("\n=== H. 洗板集成③：新版本落败 -> 回滚元数据 ===")
reset()
mk_mount("EGG-500", ["EGG-500-C.mp4"])        # 旧版本，权重高（-C 字幕档）
mk_meta("EGG-500", ["EGG-500-C.nfo", "EGG-500-C.strm", "poster.jpg", "fanart.jpg"])
meta_backup.sync_all()                         # 快照：旧版本的海报
old_poster = read(os.path.join(BACKUP, "STRM", "EGG-500", "poster.jpg"))
check("快照里有旧版海报", old_poster == "poster.jpg", old_poster)

mk_mount("EGG-500", ["EGG-500.mp4"])          # 新版本，权重低，同一目录
# 刮削已经把共享海报覆盖成新版本的了
put(os.path.join(META, "EGG-500", "poster.jpg"), "NEW-POSTER-FROM-SCRAPER")
put(os.path.join(META, "EGG-500", "fanart.jpg"), "NEW-FANART-FROM-SCRAPER")
put(os.path.join(META, "EGG-500", "EGG-500.nfo"), "EGG-500.nfo")

hit("EGG-500")

check("新版本落败被删",
      not os.path.exists(os.path.join(MOUNT, SUB, "EGG-500", "EGG-500.mp4")))
check("旧版本保留", os.path.exists(os.path.join(MOUNT, SUB, "EGG-500", "EGG-500-C.mp4")))
check("海报已回滚为旧版",
      read(os.path.join(META, "EGG-500", "poster.jpg")) == "poster.jpg",
      read(os.path.join(META, "EGG-500", "poster.jpg")))
check("fanart 已回滚为旧版",
      read(os.path.join(META, "EGG-500", "fanart.jpg")) == "fanart.jpg",
      read(os.path.join(META, "EGG-500", "fanart.jpg")))
check("被删版本的 nfo 未被复活",
      not os.path.exists(os.path.join(META, "EGG-500", "EGG-500.nfo")))
check("保留版本的 nfo 仍在",
      os.path.exists(os.path.join(META, "EGG-500", "EGG-500-C.nfo")))

# ===========================================================================
print("\n=== I. Dry Run 不做备份/回滚 ===")
reset()
client.post("/api/config", json={"DRY_RUN": True})
mk_mount("FOX-600-C", ["FOX-600-C.mp4"])
mk_meta("FOX-600-C", ["FOX-600-C.nfo", "poster.jpg"])
mk_mount("FOX-600", ["FOX-600.mp4"])
put(os.path.join(META, "FOX-600-C", "poster.jpg"), "NEW-POSTER-FROM-SCRAPER")
hit("FOX-600")
check("Dry Run 不删除任何文件",
      os.path.exists(os.path.join(MOUNT, SUB, "FOX-600", "FOX-600.mp4")))
check("Dry Run 不回滚海报",
      read(os.path.join(META, "FOX-600-C", "poster.jpg")) == "NEW-POSTER-FROM-SCRAPER")
client.post("/api/config", json={"DRY_RUN": False})

# ===========================================================================
print("\n=== J. 未启用备份时洗板照常工作 ===")
reset()
client.post("/api/config", json={"META_BACKUP_PATH": ""})
mk_mount("GUN-700", ["GUN-700.mp4"])
mk_mount("GUN-700-C", ["GUN-700-C.mp4"])
mk_meta("GUN-700", ["GUN-700.nfo", "poster.jpg"])
hit("GUN-700-C")
check("未启用备份也能正常洗板",
      not os.path.exists(os.path.join(MOUNT, SUB, "GUN-700", "GUN-700.mp4")))
check("未启用时不创建备份目录", not os.path.isdir(BACKUP))
client.post("/api/config", json={"META_BACKUP_PATH": BACKUP.replace("\\", "/")})

# ===========================================================================
print("\n=== L. Emby 手动删除影片 -> 同步删除备份 ===")


@contextlib.contextmanager
def fast_emby():
    """跳过 handle_emby 里 2.5 秒的「幽灵删除」等待，避免测试变慢。"""
    orig = time.sleep
    webhooks.time.sleep = lambda *_a, **_k: None
    try:
        yield
    finally:
        webhooks.time.sleep = orig


def emby_deleted(path, name):
    payload = {
        "Event": "library.deleted",
        "Item": {"Type": "Movie", "Path": path, "Name": name},
    }
    with fast_emby():
        r = client.post("/emby_webhook", json=payload)
    return r.get_data(as_text=True)


# L1：JAV 影片真实删除 -> 备份快照一起删掉
reset()
mk_meta("ABP-800", ["ABP-800.nfo", "ABP-800.strm", "poster.jpg"])
meta_backup.sync_all()
bk800 = os.path.join(BACKUP, "STRM", "ABP-800")
check("快照已建立", os.path.isdir(bk800), bk800)
strm800 = os.path.join(META, "ABP-800", "ABP-800.strm")
shutil.rmtree(os.path.join(META, "ABP-800"))  # Emby 删影片时连元数据目录一起删
body = emby_deleted(strm800, "ABP-800")
check("删除事件被放行转发", "Forwarded" in body, body)
check("备份快照已同步删除", not os.path.isdir(bk800), bk800)
check("备份根目录本身不会被删掉", os.path.isdir(BACKUP))

# L2：幽灵删除（文件还在）-> 备份不动
reset()
mk_meta("ABP-801", ["ABP-801.nfo", "ABP-801.strm", "poster.jpg"])
meta_backup.sync_all()
bk801 = os.path.join(BACKUP, "STRM", "ABP-801")
body = emby_deleted(os.path.join(META, "ABP-801", "ABP-801.strm"), "ABP-801")
check("文件还在时判定为幽灵删除", "Phantom" in body, body)
check("幽灵删除不动备份", os.path.isdir(bk801), bk801)

# L3：非 JAV 影片被删 -> 也清理备份
reset()
mk_meta("Some Movie 2020", ["Some Movie 2020.nfo", "poster.jpg"])
meta_backup.sync_all()
bk_west = os.path.join(BACKUP, "STRM", "Some Movie 2020")
check("非 JAV 快照已建立", os.path.isdir(bk_west), bk_west)
shutil.rmtree(os.path.join(META, "Some Movie 2020"))
body = emby_deleted(os.path.join(META, "Some Movie 2020", "Some Movie 2020.strm"), "Some Movie 2020")
check("非 JAV 影片被删也清理备份", not os.path.isdir(bk_west), bk_west)

# L4：drop_one 的安全限制
reset()
mk_meta("ABP-802", ["ABP-802.nfo", "poster.jpg"])
meta_backup.sync_all()
check("不允许删除备份根目录",
      meta_backup.drop_one(METAROOT).get("reason") == "root")
check("元数据目录还在时不删快照",
      meta_backup.drop_one(os.path.join(META, "ABP-802")).get("reason") == "kept")
check("不在元数据根下的目录无法映射",
      meta_backup.drop_one(os.path.join(TMP, "elsewhere")).get("reason") == "unmapped")
check("没有备份时返回 no_backup",
      meta_backup.drop_one(os.path.join(META, "NOPE-999")).get("reason") == "no_backup")
check("强制删除可以越过 require_missing",
      meta_backup.drop_one(os.path.join(META, "ABP-802"), require_missing=False).get("ok") is True)
check("强制删除后快照确实没了",
      not os.path.isdir(os.path.join(BACKUP, "STRM", "ABP-802")))

# L5：没配 Path 时按番号兜底
reset()
mk_meta("ABP-803", ["ABP-803.nfo", "poster.jpg"])
meta_backup.sync_all()
shutil.rmtree(os.path.join(META, "ABP-803"))
r = meta_backup.drop_for_deleted_item("", item_name="ABP-803", fanhao="ABP-803")
check("按番号兜底找到并删除", r.get("ok") is True, r)
check("兜底删除后快照确实没了",
      not os.path.isdir(os.path.join(BACKUP, "STRM", "ABP-803")))

# ===========================================================================
print("\n=== M. 清理孤儿备份 prune_orphans ===")
reset()
mk_meta("ABP-810", ["ABP-810.nfo", "poster.jpg"])
mk_meta("ABP-811", ["ABP-811.nfo", "poster.jpg"])
meta_backup.sync_all()
shutil.rmtree(os.path.join(META, "ABP-810"))
r = meta_backup.prune_orphans()
check("清理成功", r.get("ok") is True, r)
check("孤儿备份被删除", not os.path.isdir(os.path.join(BACKUP, "STRM", "ABP-810")))
check("仍在的影片备份保留", os.path.isdir(os.path.join(BACKUP, "STRM", "ABP-811")))
check("清理数量正确", r.get("removed") == 1, r.get("removed"))

shutil.rmtree(METAROOT)
r = meta_backup.prune_orphans()
check("元数据根不存在时拒绝清理", r.get("ok") is False, r)
check("拒绝清理时备份原样保留", os.path.isdir(os.path.join(BACKUP, "STRM", "ABP-811")))

os.makedirs(os.path.join(META, "ABP-811"), exist_ok=True)
r = client.post("/api/meta_backup/prune").get_json()
check("prune 接口可用", r.get("ok") is True, r)
r = client.post("/api/meta_backup/drop", json={"dir": "STRM/ABP-811"}).get_json()
check("drop 接口：元数据目录还在时拒绝",
      r.get("success") is False and r.get("reason") == "kept", r)

# ===========================================================================
print("\n=== N. 备份目录放在元数据根内部时，猎犬扫描跳过它 ===")
reset()
from app.services import library  # noqa: E402

NESTED = os.path.join(METAROOT, "_backup")
client.post("/api/config", json={"META_BACKUP_PATH": NESTED.replace("\\", "/")})
mk_meta("ABP-820", ["ABP-820.nfo", "ABP-820.strm", "poster.jpg"])
# 备份目录里的内容没有 .strm，如果没被跳过就会被猎犬当成「空壳」删掉
os.makedirs(os.path.join(NESTED, "STRM", "ABP-821"), exist_ok=True)
with open(os.path.join(NESTED, "STRM", "ABP-821", "poster.jpg"), "w", encoding="utf-8") as f:
    f.write("x")
res = library.scan_empty_folders([METAROOT])
flagged = [str(p) for p in (res.get("empty_meta") or [])]
check("猎犬没有把备份目录当成空壳", not any("_backup" in p for p in flagged), flagged)
check("备份目录里的文件原样保留",
      os.path.isfile(os.path.join(NESTED, "STRM", "ABP-821", "poster.jpg")))
check("正常影片目录也没被误判", not any("ABP-820" in p for p in flagged), flagged)
client.post("/api/config", json={"META_BACKUP_PATH": BACKUP.replace("\\", "/")})

# ===========================================================================
print("\n=== K. 前端结构 ===")
HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()
JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(BASE, "app", "static", "css", "app.css"), encoding="utf-8").read()

check("运行配置页有备份目录输入", 'id="cfg-backup"' in HTML)
check("有「立即同步」按钮", 'id="bk-sync"' in HTML)
check("有「保存备份目录」按钮", 'id="bk-save"' in HTML)
check("有「清理孤儿备份」按钮", 'id="bk-prune"' in HTML)
check("有状态展示区", 'id="bk-status"' in HTML and 'id="bk-files"' in HTML)
check("说明里写清不含 strm", "不含 .strm" in HTML)
check("JS 实现同步逻辑", "async function syncBackup" in JS)
check("JS 实现清理逻辑", "async function pruneBackup" in JS)
check("JS 实现状态刷新", "async function loadBackupStatus" in JS)
check("切换视图时加载备份状态", "loadBackupStatus();" in JS)
check("CSS 有备份样式", ".bk-actions" in CSS and ".bk-status" in CSS)
check("说明里写清备份目录可以放在元数据根内部", "内部" in HTML and "自动跳过它" in HTML)
check("JS 会显示嵌套提示", "st.nested" in JS and "备份目录在元数据根内部" in JS)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
