"""配置持久化与元数据子路径的测试。

三个问题的回归测试：
  A. 保存任一字段时不得重置其他配置（严重回归）
  B. 元数据子路径覆盖：网盘侧与元数据侧层级不一致时能正确映射
  C. 导航分组：洗版权重与路径属于洗板中心
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_persist")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(os.path.join(TMP, "cfg"))
CFG_PATH = os.path.join(TMP, "cfg", "config.json")

os.environ["XIBAN_CONFIG_DIR"] = os.path.join(TMP, "cfg")
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"

with open(CFG_PATH, "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": "/clouddrive/115open",
            "METADATA_BASE_PATH": "/media",
            "WATCHED_DIRS": ["/9KG/STRM"],
            "DRY_RUN": False,
            "DB_ONLINE_WEBHOOK": "http://10.0.0.5:8080/webhook",
            "STRM_WEBHOOK": "http://10.0.0.5:8080/api/trigger",
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


def disk():
    with open(CFG_PATH, encoding="utf-8") as f:
        return json.load(f)


ORIG = {
    "DRY_RUN": False,
    "DB_ONLINE_WEBHOOK": "http://10.0.0.5:8080/webhook",
    "STRM_WEBHOOK": "http://10.0.0.5:8080/api/trigger",
    "IGNORE_LEADING_DIGITS": True,
}


def assert_orig(tag):
    d = disk()
    ok = all(d.get(k) == v for k, v in ORIG.items())
    check(f"{tag} 后其他配置未被重置", ok, {k: d.get(k) for k in ORIG})


# ===========================================================================
print("\n=== A. 保存某字段不得重置其他配置（核心回归） ===")
assert_orig("初始")

client.post("/api/config", json={"MOUNT_BASE_PATH": "/clouddrive/115open", "METADATA_BASE_PATH": "/media"})
assert_orig("只保存存储路径")

client.post("/api/config", json={"DEDUP_SCAN_PATHS": ["/media/STRM"]})
assert_orig("只保存查重扫描路径")

client.post("/api/config", json={"HOUND_SCAN_PATHS": ["/media/STRM"]})
assert_orig("只保存猎犬扫描路径")

client.post("/api/config", json={"META_SUBDIR_OVERRIDE": "/STRM"})
assert_orig("只保存元数据子路径")

client.post("/api/config", json={"WATCHED_DIRS": ["/9KG/STRM"]})
assert_orig("只保存监听目录")

# 真正改了开关字段时才应生效
client.post("/api/config", json={"DRY_RUN": True})
check("显式提交 DRY_RUN 时才改变", disk().get("DRY_RUN") is True, disk().get("DRY_RUN"))
client.post("/api/config", json={"DRY_RUN": False})
check("可以改回", disk().get("DRY_RUN") is False)

# webhook 显式置空
client.post("/api/config", json={"DB_ONLINE_WEBHOOK": ""})
check("显式提交空 webhook 会被清空", disk().get("DB_ONLINE_WEBHOOK") == "", disk().get("DB_ONLINE_WEBHOOK"))
client.post("/api/config", json={"DB_ONLINE_WEBHOOK": ORIG["DB_ONLINE_WEBHOOK"]})

# ===========================================================================
print("\n=== B. 元数据子路径覆盖 ===")
# 先清空覆盖，确保测的是「自动推导」的默认行为
client.post("/api/config", json={"META_SUBDIR_OVERRIDE": ""})
config.reload_config()
check("默认自动推导带 9KG", config.meta_subdir_for("/9KG/STRM") == "/9KG/STRM",
      config.meta_subdir_for("/9KG/STRM"))
check("默认元数据侧", config.meta_path_for("/9KG/STRM").endswith("/media/9KG/STRM"),
      config.meta_path_for("/9KG/STRM"))

r = client.post("/api/config", json={"META_SUBDIR_OVERRIDE": "/STRM"})
check("保存覆盖成功", r.get_json().get("success") is True)
check("覆盖后子路径", config.meta_subdir_for("/9KG/STRM") == "/STRM",
      config.meta_subdir_for("/9KG/STRM"))
check("覆盖后元数据侧", config.meta_path_for("/9KG/STRM").endswith("/media/STRM"),
      config.meta_path_for("/9KG/STRM"))
check("网盘侧不受影响", config.mount_path_for("/9KG/STRM").endswith("/115open/9KG/STRM"),
      config.mount_path_for("/9KG/STRM"))

st = client.get("/api/status").get_json()
check("status 返回覆盖值", st.get("meta_subdir_override") == "/STRM", st.get("meta_subdir_override"))

# 清空
client.post("/api/config", json={"META_SUBDIR_OVERRIDE": ""})
check("清空后回到自动推导", config.meta_subdir_for("/9KG/STRM") == "/9KG/STRM",
      config.meta_subdir_for("/9KG/STRM"))

# 覆盖值规范化
client.post("/api/config", json={"META_SUBDIR_OVERRIDE": "STRM"})
check("无前导斜杠也能识别", config.meta_subdir_for("/9KG/STRM") == "/STRM",
      config.meta_subdir_for("/9KG/STRM"))
client.post("/api/config", json={"META_SUBDIR_OVERRIDE": "/"})
check("覆盖为 / 视为清空", disk().get("META_SUBDIR_OVERRIDE") == "",
      disk().get("META_SUBDIR_OVERRIDE"))

# ===========================================================================
print("\n=== C. 前端：导航分组与表单 ===")
HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()
JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()

check("分组名为「洗板中心」", "洗板中心" in HTML)
check("旧分组名「洗板任务」已移除", "洗板任务" not in HTML)
check("有元数据子路径输入框", 'id="cfg-meta-sub"' in HTML)
check("有子路径提示位", 'id="cfg-meta-sub-hint"' in HTML)
check("JS 收集 META_SUBDIR_OVERRIDE", "META_SUBDIR_OVERRIDE" in JS)
check("JS 回填 meta_subdir_override", "meta_subdir_override" in JS)

# 导航顺序：washcfg 应在「洗板中心」组内，且在「系统」组之前
i_center = HTML.find("洗板中心")
i_system = HTML.find("系统</span>") if "系统</span>" in HTML else HTML.find('nav-group">系统')
i_washcfg = HTML.find('data-view="washcfg"')
check("washcfg 在洗板中心组内", i_center < i_washcfg < i_system if i_system > 0 else True,
      (i_center, i_washcfg, i_system))

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
