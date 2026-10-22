"""洗板权重档位可配置的测试。

需求：UC / C / U / 普通 四个档位的优先级可在前端拖动调整（严格排序，无并列）。
默认 UC > C > U > NORMAL，对应权重 3/2/1/0（与原逻辑完全等价）。
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_weight")
if os.path.isdir(TMP):
    shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
CONFIG_DIR = os.path.join(TMP, "config")
os.makedirs(CONFIG_DIR, exist_ok=True)

os.environ["XIBAN_CONFIG_DIR"] = CONFIG_DIR
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"

with open(os.path.join(CONFIG_DIR, "config.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": "/media/CloudDrive",
            "METADATA_BASE_PATH": "/metadata",
            "WATCHED_DIRS": ["/115open/9KG/STRM"],
            "DRY_RUN": False,
            "DB_ONLINE_WEBHOOK": "",
            "STRM_WEBHOOK": "",
            "IGNORE_LEADING_DIGITS": True,
        },
        f,
        ensure_ascii=False,
    )

from app import config  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import fanhao  # noqa: E402

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


SAMPLES = {
    "UC": "ABF-391-UC.mp4",
    "C": "ABF-391-C.mp4",
    "U": "ABF-391-破解.mp4",
    "NORMAL": "ABF-391.mp4",
}


def w(tier):
    return fanhao.get_weight_and_fanhao(SAMPLES[tier])[1]


def set_order(order):
    r = client.post("/api/weight", json={"order": order})
    return r.get_json()


# ===========================================================================
print("\n=== A. 默认顺序与原逻辑等价 ===")
check("默认顺序 UC>C>U>NORMAL",
      config.get_weight_order() == ["UC", "C", "U", "NORMAL"], config.get_weight_order())
check("默认权重 3/2/1/0",
      config.get_weight_map() == {"UC": 3, "C": 2, "U": 1, "NORMAL": 0},
      config.get_weight_map())
check("UC 权重 3", w("UC") == 3)
check("C 权重 2", w("C") == 2)
check("U 权重 1", w("U") == 1)
check("普通 权重 0", w("NORMAL") == 0)

# ===========================================================================
print("\n=== B. 调整顺序后权重随之变化 ===")
d = set_order(["U", "UC", "C", "NORMAL"])
check("保存成功", d.get("success") is True, d)
check("顺序已更新", config.get_weight_order() == ["U", "UC", "C", "NORMAL"], config.get_weight_order())
check("U 变为最高权重 3", w("U") == 3, w("U"))
check("UC 变为 2", w("UC") == 2, w("UC"))
check("C 变为 1", w("C") == 1, w("C"))
check("普通 仍为 0", w("NORMAL") == 0, w("NORMAL"))

d = set_order(["NORMAL", "U", "C", "UC"])
check("普通放最高 -> 权重 3", w("NORMAL") == 3, w("NORMAL"))
check("UC 放最低 -> 权重 0", w("UC") == 0, w("UC"))

# ===========================================================================
print("\n=== C. 输入规范化 ===")
d = set_order(["U", "UC", "NORMAL", "C", "BOGUS"])
check("非法档位被过滤", d["order"] == ["U", "UC", "NORMAL", "C"], d["order"])

d = set_order(["U", "UC"])
check("缺失档位自动补全为 4 个", len(d["order"]) == 4, d["order"])
check("补全后包含全部档位", set(d["order"]) == {"UC", "C", "U", "NORMAL"}, d["order"])

d = set_order(["uc", "c", "u", "normal"])
check("小写输入被规范为大写", d["order"] == ["UC", "C", "U", "NORMAL"], d["order"])

d = set_order(["UC", "UC", "UC", "UC"])
check("重复档位去重", d["order"] == ["UC", "C", "U", "NORMAL"], d["order"])

d = set_order(["U", "U", "C"])
check("去重后仍补齐 4 档", len(d["order"]) == 4 and len(set(d["order"])) == 4, d["order"])

r = client.post("/api/weight", json={"order": []})
check("空数组被拒绝", r.status_code == 400, r.status_code)
r = client.post("/api/weight", json={})
check("缺少 order 被拒绝", r.status_code == 400, r.status_code)

# ===========================================================================
print("\n=== D. 配置持久化 ===")
set_order(["U", "UC", "C", "NORMAL"])
saved = json.load(open(os.path.join(CONFIG_DIR, "config.json"), encoding="utf-8"))
check("WEIGHT_ORDER 已落盘", saved.get("WEIGHT_ORDER") == ["U", "UC", "C", "NORMAL"],
      saved.get("WEIGHT_ORDER"))

# 模拟重启：重新读取配置后顺序保持
config.reload_config()
check("重载后顺序保持", config.get_weight_order() == ["U", "UC", "C", "NORMAL"],
      config.get_weight_order())

# 配置文件缺字段时回退默认
with open(os.path.join(CONFIG_DIR, "config.json"), "w", encoding="utf-8") as f:
    json.dump({"MOUNT_BASE_PATH": "/media/CloudDrive"}, f)
config.reload_config()
check("缺少 WEIGHT_ORDER 时回退默认",
      config.get_weight_order() == ["UC", "C", "U", "NORMAL"], config.get_weight_order())

# 配置文件里是脏数据时也能规范化
with open(os.path.join(CONFIG_DIR, "config.json"), "w", encoding="utf-8") as f:
    json.dump({"WEIGHT_ORDER": ["xxx", "U", "U"]}, f)
config.reload_config()
check("脏数据被规范为合法 4 档",
      set(config.get_weight_order()) == {"UC", "C", "U", "NORMAL"} and len(config.get_weight_order()) == 4,
      config.get_weight_order())

# ===========================================================================
print("\n=== E. GET 接口结构 ===")
set_order(["UC", "C", "U", "NORMAL"])
d = client.get("/api/weight").get_json()
check("GET 成功", d["success"] is True)
check("GET 含 order", d["order"] == ["UC", "C", "U", "NORMAL"])
check("GET 含 weights", d["weights"] == {"UC": 3, "C": 2, "U": 1, "NORMAL": 0}, d["weights"])
check("GET 含 4 个档位详情", len(d["tiers"]) == 4)
check("每个档位含 key/label/desc/weight",
      all(set(["key", "label", "desc", "weight"]) <= set(t.keys()) for t in d["tiers"]))
check("档位顺序与 order 一致",
      [t["key"] for t in d["tiers"]] == d["order"], [t["key"] for t in d["tiers"]])
check("默认顺序一并返回", d["default_order"] == ["UC", "C", "U", "NORMAL"], d["default_order"])

# ===========================================================================
print("\n=== F. 洗板对决按新权重判定 ===")
# U 提到最高时，U 版本应击败 C 版本
set_order(["U", "UC", "C", "NORMAL"])
_, wu, _ = fanhao.get_weight_and_fanhao("X-1-破解.mp4")
_, wc, _ = fanhao.get_weight_and_fanhao("X-1-C.mp4")
check("U(3) > C(1) 按新顺序成立", wu > wc, (wu, wc))

set_order(["C", "UC", "U", "NORMAL"])
_, wu, _ = fanhao.get_weight_and_fanhao("X-1-破解.mp4")
_, wc, _ = fanhao.get_weight_and_fanhao("X-1-C.mp4")
check("C 提到 U 前面后 C > U", wc > wu, (wc, wu))

# 非番号文件权重固定 -1，不受档位顺序影响
set_order(["NORMAL", "UC", "C", "U"])
_, wn, _ = fanhao.get_weight_and_fanhao("no fanhao.mp4")
check("非番号文件权重固定 -1", wn == -1, wn)

# 恢复默认
set_order(["UC", "C", "U", "NORMAL"])
check("恢复默认成功", config.get_weight_order() == ["UC", "C", "U", "NORMAL"])

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
