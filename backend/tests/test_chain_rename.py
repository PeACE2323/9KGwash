"""跨组链式改名逻辑的测试。

需求（用户原话）：
  「一个文件在同组中只根据第一个命中规则修改文件名，但在不同组中命中都修改文件名」

约定语义：
  - 组内：只取第一条命中的子规则，改一次名，不叠加；
  - 跨组：后一组基于前一组改名后的结果继续改名（链式）；
  - 投递：只投「最后一个命中组」那一份。
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_chain")
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

from app.factory import create_app  # noqa: E402
from app.services import deliver  # noqa: E402

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


MB = 1024 * 1024

# ===========================================================================
print("\n=== A. 同组内只改一次名 ===")
cfg_same = deliver.normalize_deliver_config({
    "GROUPS": [
        {"name": "G", "kind": "jav", "target_dir": "/g", "children": [
            {"keywords": ["kw"], "mark": "-FIRST", "mark_position": "after"},
            {"keywords": ["kw"], "mark": "-SECOND", "mark_position": "after"},
            {"keywords": ["kw"], "mark": "-THIRD", "mark_position": "before"},
        ]},
    ]
})
hits = deliver.resolve_matches_by_group("AAA-111_kw.mp4", cfg_same)
check("同组命中只产生 1 条记录", len(hits) == 1, len(hits))
check("同组使用第一条子规则的标记", hits[0]["name_after"] == "AAA-111-FIRST_kw.mp4", hits[0]["name_after"])
check("同组不叠加其它子规则的标记",
      "-SECOND" not in hits[0]["name_after"] and "-THIRD" not in hits[0]["name_after"])

# 组内第一条不适用时，应继续尝试组内下一条
cfg_skip = deliver.normalize_deliver_config({
    "GROUPS": [
        {"name": "G", "kind": "western", "target_dir": "/g", "children": [
            {"keywords": ["kw"], "mark": ""},   # 命中但需日期，文件无日期 -> 不适用
        ]},
        {"name": "H", "kind": "jav", "target_dir": "/h", "children": [
            {"keywords": ["kw"], "mark": "-H"},
        ]},
    ]
})
hits = deliver.resolve_matches_by_group("AAA-111_kw.mp4", cfg_skip)
check("欧美组不适用时跳过该组", [h["group"]["name"] for h in hits] == ["H"], [h["group"]["name"] for h in hits])

# ===========================================================================
print("\n=== B. 跨组链式改名 ===")
cfg_chain = deliver.normalize_deliver_config({
    "GROUPS": [
        {"name": "组A", "kind": "jav", "target_dir": "/A", "children": [
            {"keywords": ["both"], "mark": "-A", "mark_position": "after"},
        ]},
        {"name": "组B", "kind": "jav", "target_dir": "/B", "children": [
            {"keywords": ["both"], "mark": "B", "mark_position": "before"},
        ]},
        {"name": "组C", "kind": "other", "target_dir": "/C", "children": [
            {"keywords": ["both"], "mark": "_c", "mark_position": "after"},
        ]},
    ]
})
fn = "AAA-111_both.mp4"
hits = deliver.resolve_matches_by_group(fn, cfg_chain)
check("跨 3 组命中产生 3 条记录", len(hits) == 3, len(hits))
check("组A 改名", hits[0]["name_after"] == "AAA-111-A_both.mp4", hits[0]["name_after"])
check("组B 基于组A结果继续改名",
      hits[1]["name_before"] == hits[0]["name_after"] and hits[1]["name_after"] == "B-AAA-111-A_both.mp4",
      (hits[1]["name_before"], hits[1]["name_after"]))
check("组C 基于组B结果继续改名",
      hits[2]["name_before"] == hits[1]["name_after"] and hits[2]["name_after"] == "B-AAA-111-A_both_c.mp4",
      (hits[2]["name_before"], hits[2]["name_after"]))
check("最终文件名包含全部标记",
      all(x in hits[-1]["name_after"] for x in ("-A", "B-", "_c")), hits[-1]["name_after"])

# 只命中部分组时，链式仍成立
cfg_partial = deliver.normalize_deliver_config({
    "GROUPS": [
        {"name": "组A", "kind": "jav", "target_dir": "/A", "children": [
            {"keywords": ["only-a"], "mark": "-A"}]},
        {"name": "组B", "kind": "jav", "target_dir": "/B", "children": [
            {"keywords": ["only-b"], "mark": "-B"}]},
    ]
})
hits = deliver.resolve_matches_by_group("BBB-222_only-b.mp4", cfg_partial)
check("只命中组B -> 1 条记录", len(hits) == 1 and hits[0]["group"]["name"] == "组B")
check("只命中组B 时正常改名", hits[0]["name_after"] == "BBB-222-B_only-b.mp4", hits[0]["name_after"])

# 未命中任何组
hits = deliver.resolve_matches_by_group("CCC-333_none.mp4", cfg_chain)
check("未命中返回空列表", hits == [])

# ===========================================================================
print("\n=== C. classify_file 返回最后命中组 ===")
g, c, kw = deliver.classify_file(fn, cfg_chain)
check("classify_file 返回最后一个组", g["name"] == "组C", g["name"])
check("classify_file 返回对应子规则", c["mark"] == "_c")

# ===========================================================================
print("\n=== D. 投递引擎：只投最后命中的组 ===")
INBOX = os.path.join(TMP, "inbox")
OUT = os.path.join(TMP, "out")
shutil.rmtree(INBOX, ignore_errors=True)
os.makedirs(INBOX, exist_ok=True)
for d in ("A", "B", "C", "FB"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)

with open(os.path.join(INBOX, "AAA-111_both.mp4"), "wb") as f:
    f.write(b"0" * (3 * MB))

run_groups = [
    {"name": "组A", "kind": "jav", "action": "move", "target_dir": os.path.join(OUT, "A"),
     "children": [{"keywords": ["both"], "mark": "-A", "mark_position": "after"}]},
    {"name": "组B", "kind": "jav", "action": "move", "target_dir": os.path.join(OUT, "B"),
     "children": [{"keywords": ["both"], "mark": "B", "mark_position": "before"}]},
    {"name": "组C", "kind": "other", "action": "move", "target_dir": os.path.join(OUT, "C"),
     "children": [{"keywords": ["both"], "mark": "_c", "mark_position": "after"}]},
]
stats = deliver.run_deliver(
    {"SCAN_DIR": INBOX, "MIN_SIZE_MB": 1, "SETTLE_SECONDS": 0, "GROUPS": run_groups}, dry_run=False
)
check("只处理 1 个文件", stats["moved"] == 1, stats)

a = os.listdir(os.path.join(OUT, "A"))
b = os.listdir(os.path.join(OUT, "B"))
c = os.listdir(os.path.join(OUT, "C"))
check("组A 目录为空（不投递）", a == [], a)
check("组B 目录为空（不投递）", b == [], b)
check("组C 目录得到链式改名后的文件",
      c == ["B-AAA-111-A_both_c.mp4"], c)

detail = next((d for d in stats["details"] if d.get("file") == "AAA-111_both.mp4"), None)
check("结果记录链式轨迹", detail and len(detail.get("chain", [])) == 3,
      detail.get("chain") if detail else None)
check("结果记录最终投递组", detail and detail.get("rule") == "组C", detail.get("rule") if detail else None)

# ===========================================================================
print("\n=== E. 预览接口回显链式结果 ===")
r = client.post("/api/deliver/preview", json={"files": [fn], "groups": cfg_chain["GROUPS"]})
d = r.get_json()
res = d["results"][0]
check("预览 output 为链式最终名", res["output"] == "B-AAA-111-A_both_c.mp4", res["output"])
check("预览 matched_group 为最后一组", res["matched_group"] == "组C", res["matched_group"])
check("预览 chain 长度 3", len(res["chain"]) == 3, len(res["chain"]))
check("预览 chain 顺序正确",
      [x["group"] for x in res["chain"]] == ["组A", "组B", "组C"],
      [x["group"] for x in res["chain"]])
check("预览 hit_count 正确", res["hit_count"] == 3)

# 未命中 -> 兜底
cfg_fb = deliver.normalize_deliver_config({
    "GROUPS": [
        {"name": "组A", "kind": "jav", "target_dir": "/A", "children": [{"keywords": ["zzz"]}]},
        {"name": "兜底", "kind": "fallback", "target_dir": "/FB", "children": [{"keywords": []}]},
    ]
})
r = client.post("/api/deliver/preview", json={"files": ["DDD-444_none.mp4"], "groups": cfg_fb["GROUPS"]})
res = r.get_json()["results"][0]
check("预览兜底不改名", res["output"] == "DDD-444_none.mp4", res["output"])
check("预览标记 fallback", res["fallback"] is True)
check("预览兜底 chain 为空", res["chain"] == [])

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
