"""猎犬内容后缀 + 网盘侧检查 + 并发/原子写入修复的测试。

覆盖本轮改动：
  A. 内容后缀白名单（默认值、归一化、空值回退）
  B. 猎犬判定：含真实视频的目录不再被误判为空壳
  C. 网盘侧检查：empty / has_video / missing / unmapped 四种状态
  D. 「删除本地」与「两边都删」两种删除语义
  E. meta_to_mount_dir 无法映射时返回 None（不再静默给错路径）
  F. deliver.json / wash_history.json 原子写入
  G. 洗板并发锁 + webhook 异步队列存在
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_hound_cloud")
shutil.rmtree(TMP, ignore_errors=True)

MOUNT = os.path.join(TMP, "cloud", "115open")
META = os.path.join(TMP, "media")
CFG = os.path.join(TMP, "cfg")
os.makedirs(CFG)


def mk(p, files=()):
    os.makedirs(p, exist_ok=True)
    for f, size in files:
        with open(os.path.join(p, f), "wb") as fh:
            fh.write(b"0" * size)


L = os.path.join(META, "STRM", "日本有码")
C = os.path.join(MOUNT, "9KG", "STRM", "日本有码")

mk(os.path.join(L, "ABP-123"))                             # 本地空
mk(os.path.join(L, "ABP-456"))                             # 本地空
mk(os.path.join(L, "ABP-789"), [("ABP-789.mp4", 64)])      # 本地有视频 -> 不算空壳
mk(os.path.join(L, "ABP-000"), [("ABP-000.nfo", 8)])       # 只有 nfo -> 算空壳
mk(os.path.join(META, "挂机", "ZZZ-999"))                   # 无法映射

mk(os.path.join(C, "ABP-123"))                             # 网盘空
mk(os.path.join(C, "ABP-456"), [("ABP-456.mp4", 64)])      # 网盘有视频
mk(os.path.join(C, "ABP-000"))                             # 网盘空

os.environ["XIBAN_CONFIG_DIR"] = CFG
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"

with open(os.path.join(CFG, "config.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": MOUNT.replace("\\", "/"),
            "METADATA_BASE_PATH": META.replace("\\", "/"),
            "WATCHED_DIRS": ["/9KG/STRM"],
            "META_SUBDIR_OVERRIDE": "/STRM",
            "HOUND_SCAN_PATHS": [
                os.path.join(META, "STRM").replace("\\", "/"),
                os.path.join(META, "挂机").replace("\\", "/"),
            ],
            "DRY_RUN": False,
        },
        f,
        ensure_ascii=False,
    )

from app import config  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import deliver_store, fs, history, library, washer, webhooks  # noqa: E402

app = create_app()
config.init_config()
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


def norm(p):
    return p.replace("\\", "/")


# ===========================================================================
print("\n=== A. 内容后缀白名单 ===")
defs = config.DEFAULT_HOUND_CONTENT_EXTS
check("默认含 .strm", ".strm" in defs, defs[:5])
check("默认含 .mp4/.mkv", ".mp4" in defs and ".mkv" in defs)
check("默认数量 >= 15", len(defs) >= 15, len(defs))

nx = config.normalize_ext_list
check("补点并小写", nx(["strm", "MP4"]) == [".strm", ".mp4"], nx(["strm", "MP4"]))
check("去重", nx([".strm", ".STRM"]) == [".strm"], nx([".strm", ".STRM"]))
check("逗号分隔", nx(".strm,.mp4") == [".strm", ".mp4"], nx(".strm,.mp4"))
check("中文逗号", nx(".strm，.mp4") == [".strm", ".mp4"], nx(".strm，.mp4"))
check("补点后合法（abc -> .abc）", nx(["abc"]) == [".abc"], nx(["abc"]))
check("过滤非法项", nx(["!!!", "a-b", ".ok1"]) == [".ok1"], nx(["!!!", "a-b", ".ok1"]))
check("空值 -> 空列表", nx([]) == [])

client.post("/api/config", json={"HOUND_CONTENT_EXTS": []})
check("空配置回退默认", config.hound_content_exts() == list(defs), config.hound_content_exts())

client.post("/api/config", json={"HOUND_CONTENT_EXTS": ["strm"]})
check("自定义生效", config.hound_content_exts() == [".strm"], config.hound_content_exts())

st = client.get("/api/status").get_json()
check("status 返回生效后缀", ".strm" in st.get("hound_content_exts"), st.get("hound_content_exts"))
check("status 返回默认后缀", len(st.get("default_hound_content_exts") or []) >= 15)

# 恢复默认（写回空列表）
client.post("/api/config", json={"HOUND_CONTENT_EXTS": []})

# ===========================================================================
print("\n=== B. 含真实视频的目录不再被误判为空壳（关键修复） ===")
r = library.scan_empty_folders()
items = r.get("items") or []
locals_ = [norm(i["local"]) for i in items]

check("扫描成功", r.get("success") is True)
check("ABP-789（含 mp4）不算空壳",
      not any(x.endswith("ABP-789") for x in locals_), locals_)
check("ABP-123（真空）算空壳", any(x.endswith("ABP-123") for x in locals_), locals_)
check("ABP-000（仅 nfo）算空壳", any(x.endswith("ABP-000") for x in locals_), locals_)

# 自定义后缀只留 .strm 时，含 mp4 的目录应重新被认定为空壳
client.post("/api/config", json={"HOUND_CONTENT_EXTS": [".strm"]})
r2 = library.scan_empty_folders()
locals2 = [norm(i["local"]) for i in r2.get("items") or []]
check("只留 .strm 时 ABP-789 变为空壳",
      any(x.endswith("ABP-789") for x in locals2), locals2)
client.post("/api/config", json={"HOUND_CONTENT_EXTS": []})

# ===========================================================================
print("\n=== C. 网盘侧检查 ===")
r = library.scan_empty_folders()
by_name = {norm(i["local"]).rsplit("/", 1)[-1]: i for i in r.get("items") or []}

check("ABP-123 网盘也为空", by_name["ABP-123"]["mount_state"] == "empty",
      by_name["ABP-123"]["mount_state"])
check("ABP-456 网盘有视频", by_name["ABP-456"]["mount_state"] == "has_video",
      by_name["ABP-456"]["mount_state"])
check("ABP-456 视频计数为 1", by_name["ABP-456"]["mount_video_count"] == 1,
      by_name["ABP-456"]["mount_video_count"])
check("ZZZ-999 无法映射", by_name["ZZZ-999"]["mount_state"] == "unmapped")
check("无法映射时 mount 为 None", by_name["ZZZ-999"]["mount"] is None)

summary = r.get("summary") or {}
check("summary 统计正确",
      summary.get("both_empty") == 2 and summary.get("mount_has_video") == 1
      and summary.get("unmapped") == 1, summary)

# 网盘目录不存在 -> missing
shutil.rmtree(os.path.join(C, "ABP-000"), ignore_errors=True)
r3 = library.scan_empty_folders()
by3 = {norm(i["local"]).rsplit("/", 1)[-1]: i for i in r3.get("items") or []}
check("网盘目录已不存在 -> missing", by3["ABP-000"]["mount_state"] == "missing",
      by3["ABP-000"]["mount_state"])

# ===========================================================================
print("\n=== D. 两种删除语义 ===")
# 先重建 ABP-000 的网盘空目录
mk(os.path.join(C, "ABP-000"))

r = library.scan_empty_folders()
items = r.get("items") or []

# 「删除本地」：只删本地，不动网盘
before_mount_456 = os.path.isdir(os.path.join(C, "ABP-456"))
res = library.delete_empty_folders([], include_mount=False, items=items)
check("删除本地成功", res.get("success") is True)
check("未删除任何网盘目录", res.get("mount_deleted") == 0, res)
check("网盘 ABP-456 仍在", os.path.isdir(os.path.join(C, "ABP-456")))
check("网盘 ABP-123 仍在（本地删除不动网盘）", os.path.isdir(os.path.join(C, "ABP-123")))
check("本地 ABP-123 已删除", not os.path.isdir(os.path.join(L, "ABP-123")))
check("删除本地也会清掉无法映射的本地目录（不依赖网盘映射）",
      not os.path.isdir(os.path.join(META, "挂机", "ZZZ-999")))

# 重建本地空壳（含无法映射的那条），测「两边都删」
mk(os.path.join(L, "ABP-123"))
mk(os.path.join(L, "ABP-456"))
mk(os.path.join(META, "挂机", "ZZZ-999"))
r = library.scan_empty_folders()
items = r.get("items") or []
res = library.delete_empty_folders([], include_mount=True, items=items)
check("两边都删成功", res.get("success") is True)
check("删除了网盘空目录", res.get("mount_deleted") >= 1, res)
check("网盘 ABP-123 已删", not os.path.isdir(os.path.join(C, "ABP-123")))
check("网盘 ABP-456（有视频）被跳过", os.path.isdir(os.path.join(C, "ABP-456")))
check("网盘 ABP-456 视频完好",
      os.path.isfile(os.path.join(C, "ABP-456", "ABP-456.mp4")))
check("网盘有视频时本地也不删（整条跳过）", os.path.isdir(os.path.join(L, "ABP-456")))
check("无法映射条目被跳过", res.get("skipped_unmapped") >= 1, res)
check("原目录仍存在（无法映射不删）", os.path.isdir(os.path.join(META, "挂机", "ZZZ-999")))

# ===========================================================================
print("\n=== E. meta_to_mount_dir 不可映射时返回 None ===")
check("正常映射",
      fs.meta_to_mount_dir(os.path.join(L, "ABP-456")) is not None,
      fs.meta_to_mount_dir(os.path.join(L, "ABP-456")))
check("扫描路径之外的目录 -> None",
      fs.meta_to_mount_dir(os.path.join(META, "挂机", "ZZZ-999")) is None,
      fs.meta_to_mount_dir(os.path.join(META, "挂机", "ZZZ-999")))
check("元数据根本身 -> None", fs.meta_to_mount_dir(META) is None)
check("完全无关的路径 -> None", fs.meta_to_mount_dir("/tmp/whatever") is None)

mapped = norm(fs.meta_to_mount_dir(os.path.join(L, "ABP-456")))
check("映射结果落在网盘监听目录下",
      mapped.endswith("115open/9KG/STRM/日本有码/ABP-456"), mapped)

# ===========================================================================
print("\n=== F. 原子写入 ===")
ok, _ = deliver_store.save_deliver_config({"SCAN_DIR": "/x"})
check("deliver 保存成功", ok)
check("deliver 无 .tmp 残留", not os.path.exists(deliver_store.DELIVER_CONFIG_FILE + ".tmp"))
check("deliver 内容可解析",
      isinstance(json.load(open(deliver_store.DELIVER_CONFIG_FILE, encoding="utf-8")), dict))

history.record(["ABP-123"])
check("档案保存成功", os.path.exists(config.WASH_HISTORY_FILE))
check("档案无 .tmp 残留", not os.path.exists(config.WASH_HISTORY_FILE + ".tmp"))
check("档案内容可解析",
      isinstance(json.load(open(config.WASH_HISTORY_FILE, encoding="utf-8")), dict))

# ===========================================================================
print("\n=== G. 并发保护 ===")
check("washer 导入 threading", hasattr(washer, "threading") or "threading" in dir(washer))
check("有按番号锁 _lock_for", hasattr(washer, "_lock_for"))
check("process_xiban 是包装后的入口", hasattr(washer, "_process_xiban_locked"))
check("同一番号复用同一把锁",
      washer._lock_for("ABP-123") is washer._lock_for("ABP-123"))
check("不同番号锁不同",
      washer._lock_for("ABP-123") is not washer._lock_for("XYZ-999"))

check("webhook 有异步入口", hasattr(webhooks, "enqueue_cd2"))
check("webhook 有排队计数", hasattr(webhooks, "pending_count"))
check("排队上限存在", webhooks._MAX_PENDING >= 1)

# webhook 路由应立即返回（不阻塞等待）
import time as _t  # noqa: E402

t0 = _t.time()
resp = client.post("/webhook", json={"data": []})
elapsed = _t.time() - t0
check("webhook 立即返回 200", resp.status_code == 200, resp.status_code)
check("webhook 未阻塞（< 1 秒）", elapsed < 1.0, f"{elapsed:.2f}s")

# ===========================================================================
print("\n=== H. 前端结构 ===")
HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()
JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(BASE, "app", "static", "css", "app.css"), encoding="utf-8").read()

check("有内容后缀输入容器", 'id="hound-exts-list"' in HTML)
check("有恢复默认按钮", 'id="hound-exts-reset"' in HTML)
check("有删除本地按钮", 'id="hound-del-btn"' in HTML)
check("有两边都删按钮", 'id="hound-del-both-btn"' in HTML)
check("有网盘状态徽标渲染", "shell-badge" in JS)
check("有 HOUND_BADGE 映射", "HOUND_BADGE" in JS)
check("CSS 有徽标样式", ".shell-badge.shell-ok" in CSS and ".shell-badge.shell-bad" in CSS)
check("CSS 有两行布局", ".shell-item .shell-info" in CSS)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
