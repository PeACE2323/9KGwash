"""洗板权重（自由组合）的测试。

模型：
  版本标签 → 勾选组合成一条（组合内 AND）→ 组合列表顺序即优先级。

需求（参考用户提供的界面）：
  - 默认组合 字幕+破解 / 字幕 / 破解 / 普通，完全等价于旧的 UC > C > U > 普通（3/2/1/0）
  - 组合可自由增删、拖动排序，权重随位置变化
  - 组合内部是「同时满足」关系
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


def w(key):
    return fanhao.get_weight_and_fanhao(SAMPLES[key])[1]


def wf(fn):
    return fanhao.get_weight_and_fanhao(fn)[1]


def set_combos(combos):
    return client.post("/api/weight", json={"combos": combos}).get_json()


DEFAULT = [
    {"tags": ["sub", "crack"]},
    {"tags": ["sub"]},
    {"tags": ["crack"]},
    {"tags": ["normal"]},
]


# ===========================================================================
print("\n=== A. 默认组合与旧逻辑等价 ===")
combos = config.get_weight_combos()
check("默认 4 个组合", len(combos) == 4, combos)
check("默认顺序 字幕+破解 > 字幕 > 破解 > 普通",
      [c["tags"] for c in combos]
      == [["sub", "crack"], ["sub"], ["crack"], ["normal"]],
      [c["tags"] for c in combos])
check("默认权重 3/2/1/0",
      config.combo_weight_map() == {"w1": 3, "w2": 2, "w3": 1, "w4": 0},
      config.combo_weight_map())
check("UC 样本权重 3", w("UC") == 3, w("UC"))
check("C 样本权重 2", w("C") == 2, w("C"))
check("U 样本权重 1", w("U") == 1, w("U"))
check("普通样本权重 0", w("NORMAL") == 0, w("NORMAL"))

# ===========================================================================
print("\n=== B. 调整组合顺序后权重随之变化 ===")
d = set_combos([
    {"tags": ["crack"]},
    {"tags": ["sub", "crack"]},
    {"tags": ["sub"]},
    {"tags": ["normal"]},
])
check("保存成功", d.get("success") is True, d)
check("顺序已更新",
      [c["tags"] for c in config.get_weight_combos()][:3]
      == [["crack"], ["sub", "crack"], ["sub"]],
      config.get_weight_combos())

# 破解排第一 -> 权重 3；-UC 同时命中 破解 与 字幕+破解，取更靠前的「破解」
check("破解变为最高权重 3", w("U") == 3, w("U"))
check("UC 样本命中更靠前的「破解」= 3", w("UC") == 3, w("UC"))
check("字幕变为 1", w("C") == 1, w("C"))
check("普通仍为 0", w("NORMAL") == 0, w("NORMAL"))

# 把普通放到最前面
set_combos([
    {"tags": ["normal"]},
    {"tags": ["crack"]},
    {"tags": ["sub"]},
    {"tags": ["sub", "crack"]},
])
check("普通放最高 -> 权重 3", w("NORMAL") == 3, w("NORMAL"))
check("此时所有样本都命中「普通」（恒真）-> 全部 3",
      w("UC") == 3 and w("C") == 3 and w("U") == 3,
      (w("UC"), w("C"), w("U")))

# ===========================================================================
print("\n=== C. 组合内部是 AND（同时满足） ===")
set_combos([
    {"tags": ["4k", "sub"]},
    {"tags": ["4k"]},
    {"tags": ["sub"]},
    {"tags": ["normal"]},
])
check("4K+字幕 排第一", config.get_weight_combos()[0]["tags"] == ["4k", "sub"])

check("只有 4K 的文件 -> 命中「4K」组合 = 2", wf("ABF-391-4K.mp4") == 2,
      wf("ABF-391-4K.mp4"))
check("只有字幕的文件 -> 命中「字幕」组合 = 1", wf("ABF-391-C.mp4") == 1,
      wf("ABF-391-C.mp4"))
check("4K+字幕 同时满足 -> 命中最高组合 = 3", wf("ABF-391-4K-C.mp4") == 3,
      wf("ABF-391-4K-C.mp4"))
check("什么都没有 -> 普通 = 0", wf("ABF-391.mp4") == 0, wf("ABF-391.mp4"))
check("4K+破解（无字幕）只命中 4K = 2", wf("ABF-391-4K-破解.mp4") == 2,
      wf("ABF-391-4K-破解.mp4"))

# ===========================================================================
print("\n=== D. 权重值 = 列表长度 - 1 - 位置 ===")
set_combos([
    {"tags": ["4k"]},
    {"tags": ["8k"]},
    {"tags": ["sub"]},
    {"tags": ["crack"]},
    {"tags": ["normal"]},
])
m = config.combo_weight_map()
check("5 个组合时最高权重为 4", max(m.values()) == 4, m)
check("最低权重为 0", min(m.values()) == 0, m)
check("权重严格递减",
      [m[c["id"]] for c in config.get_weight_combos()] == [4, 3, 2, 1, 0],
      [m[c["id"]] for c in config.get_weight_combos()])

# ===========================================================================
print("\n=== E. 标签命中判定 ===")
tags_hit = fanhao.match_weight_tags("ABF-391-4K-C")
check("普通恒为真", "normal" in tags_hit, tags_hit)
check("4K 命中", "4k" in tags_hit, tags_hit)
check("字幕命中", "sub" in tags_hit, tags_hit)
check("破解未命中", "crack" not in tags_hit, tags_hit)

check("2160P 也算 4K", "4k" in fanhao.match_weight_tags("ABF-391-2160p"))
check("大小写不敏感", "4k" in fanhao.match_weight_tags("abf-391-4k"))
check("中文「无码」算破解", "crack" in fanhao.match_weight_tags("ABF-391-无码"))
check("中文「流出」算破解", "crack" in fanhao.match_weight_tags("ABF-391-流出"))
check("「无码」同时命中无码标签", "uncensored" in fanhao.match_weight_tags("ABF-391-无码"))

# ===========================================================================
print("\n=== F. 恢复默认 ===")
d = set_combos([{"tags": ["sub", "crack"]}, {"tags": ["sub"]},
                {"tags": ["crack"]}, {"tags": ["normal"]}])
check("恢复成功", d.get("success") is True, d)
check("恢复后权重回到 3/2/1/0",
      config.combo_weight_map() == {"w1": 3, "w2": 2, "w3": 1, "w4": 0},
      config.combo_weight_map())

# ===========================================================================
print("\n=== G. API 结构 ===")
data = client.get("/api/weight").get_json()
check("GET 返回 tags", isinstance(data.get("tags"), list) and len(data["tags"]) >= 10,
      len(data.get("tags") or []))
check("GET 返回 combos", isinstance(data.get("combos"), list), data.get("combos"))
check("combos 带 labels", all("labels" in c for c in data["combos"]), data["combos"][0])
check("combos 带 weight", all("weight" in c for c in data["combos"]), data["combos"][0])
check("GET 返回默认值（供恢复默认）",
      isinstance(data.get("default_combos"), list) and len(data["default_combos"]) == 4,
      data.get("default_combos"))

# 空组合列表：回退到默认（保证不会把权重配置清空）
r = client.post("/api/weight", json={"combos": []})
check("空组合列表不会报错", r.status_code in (200, 400), r.status_code)
check("空列表回退为至少一个组合", len(config.get_weight_combos()) >= 1,
      config.get_weight_combos())
check("回退后仍能判定出权重", w("NORMAL") >= 0, w("NORMAL"))

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
