"""路径整合与填法容错的测试。

对应问题（用户截图）：
    网盘根 = /media/115open
    监听目录填了 /media/115open/9KG/STRM（完整路径）
    旧逻辑把它当相对路径，拼成
        /media/115open/media/115open/9KG/STRM
    导致「容器内找不到」。

修复：
  1) 监听目录支持填完整路径，自动剥离网盘根
  2) 保存时统一归一为相对路径，不会重复拼接
  3) 新增 /api/path_check 做保存前校验
  4) UI 拆分：「存储路径」（网盘根/元数据根）留在运行配置，
     监控目录归入「文件投递」页
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_pathmerge")
if os.path.isdir(TMP):
    shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)

# 模拟用户真实结构：/xxx/media/115open 就是网盘根，下面直接 9KG
MOUNT = os.path.join(TMP, "media", "115open")
META = os.path.join(TMP, "metadata")
for p in (
    os.path.join(MOUNT, "9KG", "STRM", "日本有码"),
    os.path.join(MOUNT, "9KG", "重命名"),
    os.path.join(META, "9KG", "STRM", "日本有码"),
):
    os.makedirs(p, exist_ok=True)

CFG = os.path.join(TMP, "config")
os.makedirs(CFG, exist_ok=True)
os.environ["XIBAN_CONFIG_DIR"] = CFG
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"
os.environ["XIBAN_BROWSE_ROOTS"] = TMP

with open(os.path.join(CFG, "config.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": MOUNT.replace("\\", "/"),
            "METADATA_BASE_PATH": META.replace("\\", "/"),
            "WATCHED_DIRS": ["/9KG/STRM"],
            "DRY_RUN": True,
            "DB_ONLINE_WEBHOOK": "",
            "STRM_WEBHOOK": "",
            "IGNORE_LEADING_DIGITS": True,
        },
        f,
        ensure_ascii=False,
    )

from app import config  # noqa: E402
from app.factory import create_app  # noqa: E402

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


MB = MOUNT.replace("\\", "/")
MBASE = META.replace("\\", "/")

# ===========================================================================
print("\n=== A. 完整路径 / 相对路径都能正确解析 ===")
resolve = config.resolve_watch_dir

rel, err = resolve("/9KG/STRM", MB)
check("相对路径原样", rel == "/9KG/STRM" and err is None, (rel, err))

rel, err = resolve(MB + "/9KG/STRM", MB)
check("完整路径被剥离网盘根", rel == "/9KG/STRM" and err is None, (rel, err))

rel, err = resolve("9KG/STRM", MB)
check("无前导斜杠也能识别", rel == "/9KG/STRM" and err is None, (rel, err))

rel, err = resolve("", MB)
check("空值报错", err is not None, err)

rel, err = resolve(MB, MB)
check("等于网盘根本身报错", err is not None, err)

# ===========================================================================
print("\n=== B. 关键：不会重复拼接网盘根（用户截图 bug） ===")
mp = config.mount_path_for(MB + "/9KG/STRM")
check("完整路径拼接后无重复", mp.replace("\\", "/") == MB + "/9KG/STRM", mp)
check("不存在 media/115open/media/115open", "115open/media" not in mp.replace("\\", "/"), mp)

mp2 = config.mount_path_for("/9KG/STRM")
check("相对路径拼接正确", mp2.replace("\\", "/") == MB + "/9KG/STRM", mp2)
check("两种填法结果一致", mp.replace("\\", "/") == mp2.replace("\\", "/"))

# 元数据侧一致
for inp in ("/9KG/STRM", MB + "/9KG/STRM"):
    mpath = config.meta_path_for(inp).replace("\\", "/")
    check(f"元数据侧正确({inp[:18]}...)", mpath == MBASE + "/9KG/STRM", mpath)

# ===========================================================================
print("\n=== C. 保存时归一化为相对路径 ===")
ok, _ = config.save_config({"WATCHED_DIRS": [MB + "/9KG/STRM"]})
check("保存成功", ok)
check("存为相对路径", config.watched_dirs() == ["/9KG/STRM"], config.watched_dirs())

with open(os.path.join(CFG, "config.json"), encoding="utf-8") as f:
    on_disk = json.load(f)
check("落盘也是相对路径", on_disk.get("WATCHED_DIRS") == ["/9KG/STRM"], on_disk.get("WATCHED_DIRS"))

# 通过 HTTP 保存同样归一
r = client.post("/api/config", json={"WATCHED_DIRS": [MB + "/9KG/STRM"]})
check("API 保存成功", r.get_json().get("success") is True, r.get_json())
check("API 保存后已归一", config.watched_dirs() == ["/9KG/STRM"], config.watched_dirs())

# ===========================================================================
print("\n=== D. /api/path_check 校验接口 ===")
r = client.post("/api/path_check", json={"WATCHED_DIRS": [MB + "/9KG/STRM"]})
d = r.get_json()
check("返回 success", d.get("success") is True, d.get("errors"))
check("返回 resolved", len(d.get("resolved", [])) == 1)
res = d["resolved"][0]
check("解析出相对路径", res["watch_dir"] == "/9KG/STRM", res["watch_dir"])
check("网盘侧存在", res["mount_ok"] is True, res["mount_path"])
check("元数据侧存在", res["meta_ok"] is True, res["meta_path"])
check("无 errors", d.get("errors") == [], d.get("errors"))

# 不存在的目录 -> warning
r = client.post("/api/path_check", json={"WATCHED_DIRS": [MB + "/NOT_EXIST"]})
d = r.get_json()
check("不存在目录使 mount_ok=False", d["resolved"][0]["mount_ok"] is False)
check("给出 warning", len(d.get("warnings", [])) >= 1, d.get("warnings"))

# 空监听目录 -> error
r = client.post("/api/path_check", json={"WATCHED_DIRS": []})
d = r.get_json()
check("空监听目录返回 error", d.get("success") is False and d.get("errors"), d)

# 空网盘根 -> error
r = client.post("/api/path_check", json={"MOUNT_BASE_PATH": "", "WATCHED_DIRS": ["/9KG/STRM"]})
d = r.get_json()
check("空网盘根返回 error", d.get("success") is False, d.get("errors"))

# ===========================================================================
print("\n=== E. UI 拆分 ===")
HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()
JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()

check("存储路径卡片标题", "💾 存储路径" in HTML)
check("存储路径只有网盘根/元数据根", "网盘根目录" in HTML and "元数据根目录" in HTML)
check("运行配置页不再有监听目录输入框", 'id="cfg-watched"' not in HTML)
check("保存按钮文案为「保存存储路径」", "保存存储路径" in HTML)
check("投递页监控目录标注完整路径", "监控目录（容器内完整路径）" in HTML)
check("投递页有路径提示位", 'id="deliver-scan-dir-hint"' in HTML)
check("JS 不再引用 cfg-watched", "cfg-watched" not in JS)
check("JS 有 updateScanDirHint", "function updateScanDirHint" in JS)
check("JS 调用 /api/path_check", "/api/path_check" in JS)
check("保存前会先校验", "apiPost(\"/api/path_check\", payload)" in JS)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
