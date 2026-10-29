"""路径配置可编辑、目录浏览、告警去重的测试。

对应问题：
  1) 容器内 /media 映射下看不到全部文件夹（realpath 误判越界）
  2) 路径配置只读、英文标签，需改文件重启才生效
  3) 监控目录不存在时日志疯狂刷屏
  4) 代码里写死 "/115open/"，目录名一变元数据推导全错
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_pathcfg")
if os.path.isdir(TMP):
    shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)

# 模拟容器结构：/media/CloudDrive = 网盘根，/metadata = 元数据根
MOUNT = os.path.join(TMP, "media", "CloudDrive")
META = os.path.join(TMP, "metadata")
for p in (
    os.path.join(MOUNT, "115open", "9KG", "STRM", "日本有码"),
    os.path.join(MOUNT, "115open", "9KG", "重命名"),
    os.path.join(MOUNT, "115open", "9KG", "待整理"),
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
            "MOUNT_BASE_PATH": MOUNT,
            "METADATA_BASE_PATH": META,
            "WATCHED_DIRS": ["/115open/9KG/STRM"],
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
from app.services import deliver  # noqa: E402

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


# ===========================================================================
print("\n=== A. 不再依赖写死的 115open ===")
check("meta_subdir_for 去掉 115open 层",
      config.meta_subdir_for("/115open/9KG/STRM") == "/9KG/STRM",
      config.meta_subdir_for("/115open/9KG/STRM"))
check("meta_path_for 拼接正确",
      config.meta_path_for("/115open/9KG/STRM").replace("\\", "/") == (META + "/9KG/STRM").replace("\\", "/"),
      config.meta_path_for("/115open/9KG/STRM"))
check("mount_path_for 拼接正确",
      config.mount_path_for("/115open/9KG/STRM").replace("\\", "/")
      == (MOUNT + "/115open/9KG/STRM").replace("\\", "/"),
      config.mount_path_for("/115open/9KG/STRM"))

# 目录名不叫 115open 时也不出错
check("非 115open 目录原样保留",
      config.meta_subdir_for("/other/9KG/STRM") == "/other/9KG/STRM",
      config.meta_subdir_for("/other/9KG/STRM"))

# 路径里不含多余前导斜杠
mp = config.mount_path_for("/115open/9KG/STRM")
check("拼接结果没有 /E:/ 这类畸形前导", not mp.startswith("/E:") and not mp.startswith("/C:"), mp)

# ===========================================================================
print("\n=== B. /api/status 路径推导 ===")
st = client.get("/api/status").get_json()
check("返回 watched", len(st.get("watched", [])) == 1)
w = st["watched"][0]
check("watch_path 正确", w["watch_path"] == "/115open/9KG/STRM", w["watch_path"])
check("mount_ok 为 True", w["mount_ok"] is True, w["mount_path"])
check("meta_ok 为 True", w["meta_ok"] is True, w["meta_path"])
check("mount_path 指向真实目录", os.path.isdir(w["mount_path"]), w["mount_path"])
check("meta_path 指向真实目录", os.path.isdir(w["meta_path"]), w["meta_path"])

# ===========================================================================
print("\n=== C. 目录浏览：能看到子文件夹 ===")
r = client.get("/api/deliver/browse?path=" + MOUNT)
d = r.get_json()
check("浏览网盘根成功", d.get("success") is True, d)
names = [x["name"] for x in d.get("dirs", [])]
check("能看到 115open", "115open" in names, names)

r = client.get("/api/deliver/browse?path=" + MOUNT + "/115open/9KG")
d = r.get_json()
names = [x["name"] for x in d.get("dirs", [])]
check("能看到 9KG 下的三个目录", set(names) == {"STRM", "待整理", "重命名"}, names)

check("返回 roots 供快捷跳转", isinstance(d.get("roots"), list) and len(d["roots"]) >= 1, d.get("roots"))

# 越权路径应被拒绝
r = client.get("/api/deliver/browse?path=/etc")
check("越权路径被拒绝", r.status_code == 403, r.status_code)

# 不存在的目录给出明确错误
r = client.get("/api/deliver/browse?path=" + MOUNT + "/nope")
check("不存在目录返回 404", r.status_code == 404, r.status_code)

# ===========================================================================
print("\n=== D. 路径配置可编辑且立即生效 ===")
new_watch = "/115open/9KG/重命名"
r = client.post("/api/config", json={"WATCHED_DIRS": [new_watch]})
check("保存成功", r.get_json().get("success") is True, r.get_json())

st2 = client.get("/api/status").get_json()
check("无需重启即生效", st2["watched"][0]["watch_path"] == new_watch,
      st2["watched"][0]["watch_path"])
check("新监听目录可访问", st2["watched"][0]["mount_ok"] is True, st2["watched"][0]["mount_path"])

# 改网盘根
new_mount = os.path.join(TMP, "media")
r = client.post("/api/config", json={"MOUNT_BASE_PATH": new_mount})
check("改网盘根成功", r.get_json().get("success") is True)
check("网盘根立即生效",
      config.mount_base_path().replace("\\", "/").rstrip("/") == new_mount.replace("\\", "/").rstrip("/"),
      config.mount_base_path())
# 还原
client.post("/api/config", json={"MOUNT_BASE_PATH": MOUNT})

# 校验
r = client.post("/api/config", json={"MOUNT_BASE_PATH": ""})
check("空网盘根被拒绝", r.status_code == 400, r.status_code)
r = client.post("/api/config", json={"WATCHED_DIRS": []})
check("空监听目录被拒绝", r.status_code == 400, r.status_code)

# 写入文件也已更新
with open(os.path.join(CFG, "config.json"), encoding="utf-8") as f:
    on_disk = json.load(f)
check("配置文件已落盘", on_disk.get("WATCHED_DIRS") == [new_watch], on_disk.get("WATCHED_DIRS"))

# ===========================================================================
print("\n=== E. 监控目录不存在：告警去重 ===")
deliver._warned.clear()
cfg = {
    "SCAN_DIR": os.path.join(TMP, "NOT_EXIST"),
    "MIN_SIZE_MB": 1,
    "SETTLE_SECONDS": 0,
    "OVERWRITE": True,
    "GROUPS": [],
}
for _ in range(5):
    res = deliver.run_deliver(cfg, dry_run=True)
check("每次都返回错误", res["success"] is False)
check("告警只记录一次（去重生效）", len(deliver._warned) == 1, deliver._warned)

# 目录恢复后应清除记忆
os.makedirs(os.path.join(TMP, "exists_ok"), exist_ok=True)
cfg["SCAN_DIR"] = os.path.join(TMP, "exists_ok")
deliver.run_deliver(cfg, dry_run=True)
check("目录恢复后清除告警记忆", len(deliver._warned) == 0, deliver._warned)

# ===========================================================================
print("\n=== F. 前端：中文标签与可编辑表单 ===")
HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()
JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()

check("标题为中文「路径配置」", "📁 路径配置" in HTML)
check("不再有「只读」字样", "路径配置（只读）" not in HTML)
check("中文标签：网盘根目录", "网盘根目录" in HTML)
check("中文标签：元数据根目录", "元数据根目录" in HTML)
check("中文标签：监听目录", "监听目录" in HTML)
check("有保存按钮", 'id="path-save"' in HTML)
check("有浏览按钮", 'class="btn btn-ghost btn-sm path-browse"' in HTML)
check("有容器内路径提示", 'id="cfg-mount-hint"' in HTML)
check("有目录快捷跳转容器", 'id="browser-quick"' in HTML)
check("JS 有 savePaths", "function savePaths" in JS)
check("JS 有 fillPathForm", "function fillPathForm" in JS)
check("进入配置页会加载 status", 'if (name === "config")' in JS and "loadStatus()" in JS)
check("浏览默认从网盘根开始", "state.lastStatus.mount_base_path" in JS)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
