"""自定义洗板权重档位的测试。

需求：
  - 可添加多个自定义档位，每个档位一组关键字
  - 命中关键字的文件参与洗板，权重由拖动顺序决定
  - 一个文件同时命中多个档位时，取顺序最高（权重最大）的那一个
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_wcustom")
if os.path.isdir(TMP):
    shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
CFG = os.path.join(TMP, "config")
os.makedirs(CFG, exist_ok=True)

os.environ["XIBAN_CONFIG_DIR"] = CFG
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"

with open(os.path.join(CFG, "config.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": "/media/115open",
            "METADATA_BASE_PATH": "/metadata",
            "WATCHED_DIRS": ["/9KG/STRM"],
            "DRY_RUN": True,
            "IGNORE_LEADING_DIGITS": True,
        },
        f,
        ensure_ascii=False,
    )

from app import config  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import fanhao  # noqa: E402

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


def W(fn):
    return fanhao.get_weight_and_fanhao(fn)[1]


def reset_weight():
    config.save_config({"WEIGHT_CUSTOM": [], "WEIGHT_ORDER": ["UC", "C", "U", "NORMAL"]})
    config.reload_config()


# ===========================================================================
print("\n=== A. 无自定义档时行为不变 ===")
reset_weight()
check("默认顺序", config.get_weight_order() == ["UC", "C", "U", "NORMAL"], config.get_weight_order())
check("UC = 3", W("ABF-391-UC.mp4") == 3, W("ABF-391-UC.mp4"))
check("C = 2", W("ABF-391-C.mp4") == 2, W("ABF-391-C.mp4"))
check("U(破解) = 1", W("ABF-391-破解.mp4") == 1, W("ABF-391-破解.mp4"))
check("普通 = 0", W("ABF-391.mp4") == 0, W("ABF-391.mp4"))
check("无番号 = -1", W("hello.mp4") == -1, W("hello.mp4"))

# ===========================================================================
print("\n=== B. 规范化自定义档位 ===")
nc = config.normalize_weight_custom
c1 = nc([{"id": "k4", "name": "4K", "keywords": ["4K", "4k", "2160p"]}])
check("关键字去重（不分大小写）", c1[0]["keywords"] == ["4K", "2160p"], c1)
c2 = nc([{"keywords": "4K, 2160p，UHD"}])
check("字符串关键字被拆分", c2[0]["keywords"] == ["4K", "2160p", "UHD"], c2)
check("空名称自动命名", c2[0]["name"].startswith("自定义"), c2[0]["name"])
check("无关键字的档位被丢弃", nc([{"name": "空", "keywords": []}]) == [])
check("非数组返回空", nc("bad") == [])
c3 = nc([{"id": "x", "name": "A", "keywords": ["a"]}, {"id": "x", "name": "B", "keywords": ["b"]}])
check("重复 id 自动去重", c3[0]["id"] != c3[1]["id"], [c["id"] for c in c3])
check("空白关键字被清理", nc([{"name": "A", "keywords": ["  ", "x"]}])[0]["keywords"] == ["x"])

# ===========================================================================
print("\n=== C. 顺序规范化（含自定义档）===")
custom = nc([{"id": "k4", "name": "4K", "keywords": ["4K"]}])
no = config.normalize_weight_order(["custom:k4", "UC"], custom)
check("自定义档保留在首位", no[0] == "custom:k4", no)
check("内置档自动补齐 4 个", all(t in no for t in ["UC", "C", "U", "NORMAL"]), no)
check("总档位数 = 5", len(no) == 5, no)

no2 = config.normalize_weight_order([], custom)
check("空顺序也补齐自定义档", "custom:k4" in no2, no2)
check("空顺序内置档按默认排列",
      no2[:4] == ["UC", "C", "U", "NORMAL"], no2)

invalid = config.normalize_weight_order(["custom:notexist", "UC"], custom)
check("不存在的自定义档被过滤", "custom:notexist" not in invalid, invalid)

# ===========================================================================
print("\n=== D. 自定义档位参与洗板 ===")
config.save_config(
    {
        "WEIGHT_CUSTOM": [{"id": "k4", "name": "4K", "keywords": ["4K", "2160p"]}],
        "WEIGHT_ORDER": ["custom:k4", "UC", "C", "U", "NORMAL"],
    }
)
config.reload_config()
check("权重表 5 档", len(config.get_weight_map()) == 5, config.get_weight_map())
check("自定义档权重最高 = 4", config.get_weight_for_tier("custom:k4") == 4,
      config.get_weight_for_tier("custom:k4"))
check("4K 文件权重 = 4", W("ABF-391-4K.mp4") == 4, W("ABF-391-4K.mp4"))
check("4K-C 文件取最高的 4K 档 = 4", W("ABF-391-4K-C.mp4") == 4, W("ABF-391-4K-C.mp4"))
check("2160p 也算命中", W("ABF-391-2160p.mp4") == 4, W("ABF-391-2160p.mp4"))
check("大小写不敏感", W("ABF-391-4k.mp4") == 4, W("ABF-391-4k.mp4"))
check("无 4K 的普通文件 = 0", W("ABF-391.mp4") == 0, W("ABF-391.mp4"))
check("无 4K 的 C 文件 = 2", W("ABF-391-C.mp4") == 2, W("ABF-391-C.mp4"))

# ===========================================================================
print("\n=== E. 拖动顺序改变后权重随之变化 ===")
config.save_config(
    {
        "WEIGHT_CUSTOM": [{"id": "k4", "name": "4K", "keywords": ["4K"]}],
        "WEIGHT_ORDER": ["UC", "C", "U", "NORMAL", "custom:k4"],
    }
)
config.reload_config()
check("4K 拖到最底 -> 权重 0", config.get_weight_for_tier("custom:k4") == 0,
      config.get_weight_for_tier("custom:k4"))
check("UC 变成最高 4", config.get_weight_for_tier("UC") == 4,
      config.get_weight_for_tier("UC"))
check("4K 文件权重 0", W("ABF-391-4K.mp4") == 0, W("ABF-391-4K.mp4"))
check("4K-C 文件取更高的 C 档 = 3", W("ABF-391-4K-C.mp4") == 3, W("ABF-391-4K-C.mp4"))

# ===========================================================================
print("\n=== F. 多个自定义档位 ===")
config.save_config(
    {
        "WEIGHT_CUSTOM": [
            {"id": "k4", "name": "4K", "keywords": ["4K"]},
            {"id": "hq", "name": "高码率", "keywords": ["HEVC", "x265"]},
        ],
        "WEIGHT_ORDER": ["custom:k4", "custom:hq", "UC", "C", "U", "NORMAL"],
    }
)
config.reload_config()
check("总档位 6", len(config.get_weight_map()) == 6, config.get_weight_map())
check("4K = 5", W("ABF-391-4K.mp4") == 5, W("ABF-391-4K.mp4"))
check("HEVC = 4", W("ABF-391-hevc.mp4") == 4, W("ABF-391-hevc.mp4"))
check("4K+HEVC 取 4K = 5", W("ABF-391-4K-hevc.mp4") == 5, W("ABF-391-4K-hevc.mp4"))
check("仅 C = 2（顺序 k4,hq,UC,C,U,NORMAL）", W("ABF-391-C.mp4") == 2, W("ABF-391-C.mp4"))

# ===========================================================================
print("\n=== G. API 往返 ===")
reset_weight()
r = client.get("/api/weight")
d = r.get_json()
check("GET success", d["success"] is True)
check("GET 返回 tiers", len(d["tiers"]) == 4, len(d["tiers"]))
check("GET 返回 custom（空）", d.get("custom") == [], d.get("custom"))

r = client.post(
    "/api/weight",
    json={
        "order": ["custom:x1", "UC", "C", "U", "NORMAL"],
        "custom": [{"id": "x1", "name": "测试档", "keywords": ["测试"]}],
    },
)
d = r.get_json()
check("POST success", d["success"] is True, d.get("error"))
check("POST 返回自定义", len(d.get("custom", [])) == 1, d.get("custom"))

r = client.get("/api/weight")
d = r.get_json()
check("持久化后 GET 到自定义档", len(d.get("custom", [])) == 1, d.get("custom"))
tier0 = d["tiers"][0]
check("自定义档排第一", tier0["key"] == "custom:x1", tier0["key"])
check("is_custom 标记正确", tier0["is_custom"] is True)
check("带 keywords", tier0["keywords"] == ["测试"], tier0["keywords"])
check("权重值 4", tier0["weight"] == 4, tier0["weight"])

r = client.post("/api/weight", json={"order": []})
check("空 order 被拒绝", r.status_code == 400, r.status_code)

# 删除自定义档
r = client.post("/api/weight", json={"order": ["UC", "C", "U", "NORMAL"], "custom": []})
d = r.get_json()
check("清空自定义档成功", d["success"] is True and d.get("custom") == [], d)
check("顺序回到 4 档", d["order"] == ["UC", "C", "U", "NORMAL"], d["order"])

# ===========================================================================
print("\n=== H. 前端结构 ===")
HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()
JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(BASE, "app", "static", "css", "app.css"), encoding="utf-8").read()

check("有添加自定义档位按钮", 'id="weight-add-custom"' in HTML)
check("有自定义档位弹窗", 'id="weight-modal"' in HTML)
check("弹窗有名称输入", 'id="weight-name"' in HTML)
check("弹窗有关键字输入", 'id="weight-keywords"' in HTML)
check("模板含编辑按钮", "weight-edit" in HTML)
check("模板含删除按钮", "weight-del" in HTML)
check("模板含关键字展示位", "weight-kws" in HTML)
check("JS 有 openWeightModal", "function openWeightModal" in JS)
check("JS 有 confirmWeightModal", "function confirmWeightModal" in JS)
check("JS 有 removeCustomTier", "function removeCustomTier" in JS)
check("保存时提交 custom", "custom: (state.weightCustom" in JS)
check("自定义档位有专用样式", ".weight-item.is-custom" in CSS)
check("自定义徽章样式", ".w-badge-custom" in CSS)

reset_weight()
print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
