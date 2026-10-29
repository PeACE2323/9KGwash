"""任务页独立扫描路径 + 洗版配置页 + 导航对比度的测试。

需求：
  1. 猎犬页 / 查重页各自设置扫描路径，按设置的路径执行
  2. 左侧导航新增「洗版配置」页，迁入权重与存储路径
  3. 导航悬停文字与背景对比度过低
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_taskpath")
shutil.rmtree(TMP, ignore_errors=True)
MOUNT = os.path.join(TMP, "media", "115open")
META = os.path.join(TMP, "meta")
CFG = os.path.join(TMP, "cfg")
os.makedirs(CFG)

# 两个不同的扫描目录，用于验证「按设定路径执行」
A = os.path.join(META, "9KG", "STRM")
B = os.path.join(META, "OTHER")
os.makedirs(os.path.join(A, "AAA-111"), exist_ok=True)
os.makedirs(os.path.join(B, "BBB-222"), exist_ok=True)
os.makedirs(os.path.join(B, "EMPTY-999"), exist_ok=True)  # 空壳
for fn in ("AAA-111.strm", "AAA-111-C.strm"):
    with open(os.path.join(A, "AAA-111", fn), "w") as f:
        f.write("x")
with open(os.path.join(B, "BBB-222", "BBB-222.strm"), "w") as f:
    f.write("x")

os.environ["XIBAN_CONFIG_DIR"] = CFG
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"

with open(os.path.join(CFG, "config.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": MOUNT.replace("\\", "/"),
            "METADATA_BASE_PATH": META.replace("\\", "/"),
            "WATCHED_DIRS": ["/9KG/STRM"],
            "DRY_RUN": True,
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


A_N = A.replace("\\", "/")
B_N = B.replace("\\", "/")

# ===========================================================================
print("\n=== A. 默认回退到元数据侧监听目录 ===")
check("hound 默认路径", config.hound_scan_paths() == [A_N], config.hound_scan_paths())
check("dedup 默认路径", config.dedup_scan_paths() == [A_N], config.dedup_scan_paths())

st = client.get("/api/status").get_json()
check("status 返回 hound_scan_paths", st.get("hound_scan_paths") == [A_N], st.get("hound_scan_paths"))
check("status 返回 dedup_scan_paths", st.get("dedup_scan_paths") == [A_N], st.get("dedup_scan_paths"))
check("status 返回 default_scan_paths", st.get("default_scan_paths") == [A_N])

# ===========================================================================
print("\n=== B. 查重按设定路径执行 ===")
d = client.get("/api/scan_duplicates").get_json()
check("默认扫到 AAA-111 重复组", len(d["duplicates"]) == 1, len(d["duplicates"]))
check("返回 scan_paths", d.get("scan_paths") == [A_N], d.get("scan_paths"))

r = client.post("/api/config", json={"DEDUP_SCAN_PATHS": [B_N]})
check("保存查重路径", r.get_json().get("success") is True, r.get_json())

d = client.get("/api/scan_duplicates").get_json()
check("查重改扫 B 目录", d.get("scan_paths") == [B_N], d.get("scan_paths"))
check("B 目录没有重复组", len(d["duplicates"]) == 0, len(d["duplicates"]))

# 通过 query 参数临时指定
d = client.get(f"/api/scan_duplicates?paths={A_N}").get_json()
check("可用 paths 参数临时指定", d.get("scan_paths") == [A_N], d.get("scan_paths"))
check("临时指定扫到重复", len(d["duplicates"]) == 1)

# 恢复
client.post("/api/config", json={"DEDUP_SCAN_PATHS": []})
d = client.get("/api/scan_duplicates").get_json()
check("清空后回退默认", d.get("scan_paths") == [A_N], d.get("scan_paths"))

# ===========================================================================
print("\n=== C. 猎犬按设定路径执行 ===")
h = client.get("/api/scan_empty_folders").get_json()
check("默认扫 A 目录", h.get("scan_paths") == [A_N], h.get("scan_paths"))

r = client.post("/api/config", json={"HOUND_SCAN_PATHS": [B_N]})
check("保存猎犬路径", r.get_json().get("success") is True)

h = client.get("/api/scan_empty_folders").get_json()
check("猎犬改扫 B 目录", h.get("scan_paths") == [B_N], h.get("scan_paths"))
names = [os.path.basename(p) for p in h.get("empty_meta", [])]
check("B 目录下的空壳被识别", any("EMPTY-999" in n for n in names), names)

client.post("/api/config", json={"HOUND_SCAN_PATHS": []})

# ===========================================================================
print("\n=== D. 路径规范化与去重 ===")
r = client.post("/api/config", json={"DEDUP_SCAN_PATHS": [A_N + "/", A_N, "", "  "]})
d = r.get_json()
check("保存后自动去重去尾斜杠",
      config.dedup_scan_paths() == [A_N], config.dedup_scan_paths())
client.post("/api/config", json={"DEDUP_SCAN_PATHS": []})

# 不存在的目录会被过滤
d = client.get("/api/scan_duplicates?paths=" + os.path.join(TMP, "NOPE").replace("\\", "/")).get_json()
check("不存在的目录被过滤", d.get("scan_paths") == [], d.get("scan_paths"))

# ===========================================================================
print("\n=== E. 前端：洗版配置页与导航 ===")
HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()
JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(BASE, "app", "static", "css", "app.css"), encoding="utf-8").read()

check("导航含洗版配置", 'data-view="washcfg"' in HTML)
check("有 washcfg 视图", 'id="view-washcfg"' in HTML)
check("洗版权重卡片在该视图内", "洗板权重顺序" in HTML)
check("存储路径卡片在该视图内", "💾 存储路径" in HTML)
check("VIEW_META 含 washcfg", "washcfg:" in JS)

# 权重与路径卡片应已从运行配置页移走：view-config 内不再包含它们
i_cfg = HTML.find('id="view-config"')
i_next = HTML.find("</section>", i_cfg)
cfg_block = HTML[i_cfg:i_next]
check("运行配置页不再有权重卡片", "洗板权重顺序" not in cfg_block)
check("运行配置页不再有存储路径卡片", "存储路径" not in cfg_block)

check("猎犬页有路径输入", 'id="hound-paths"' in HTML)
check("查重页有路径输入", 'id="dedup-paths"' in HTML)
check("有保存按钮", 'class="btn btn-ghost btn-sm scan-path-save"' in HTML)
check("JS 有 loadScanPaths", "function loadScanPaths" in JS)
check("JS 有 saveScanPaths", "function saveScanPaths" in JS)
check("视图切换会加载扫描路径", 'name === "hound" || name === "dedup"' in JS)

# ===========================================================================
print("\n=== F. 导航悬停对比度 ===")
check("导航悬停不再用纯白", ".nav-item:hover { background: rgba(148, 163, 184, 0.1); color: #fff; }" not in CSS)
i = CSS.find(".nav-item:hover")
block = CSS[i : CSS.find("}", i)]
check("悬停使用强调色", "pink" in block, block[:120])
check("浅色主题悬停也有覆盖", 'html[data-theme="light"] .nav-item:hover' in CSS)

i2 = CSS.find('html[data-theme="light"] .nav-item:hover')
b2 = CSS[i2 : CSS.find("}", i2)]
check("浅色悬停不再是灰底", "rgba(15, 23, 42, 0.05)" not in b2, b2[:120])

# ===========================================================================
print("\n=== G. 存储路径提示不再误导 ===")
check("网盘根提示说明用途", "视频文件目录可访问" in JS)
check("元数据根提示说明用途", "元数据目录可访问" in JS)
check("两处提示文案不同", "视频文件目录可访问" != "元数据目录可访问")

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
