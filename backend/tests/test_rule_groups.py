"""规则分组（组 + 子规则）的测试。

覆盖：
  A. 配置规范化：组/子规则字段完整、非法值回退
  B. 旧版扁平 RULES 自动迁移为 GROUPS
  C. 优先级：组序优先于组内序；命中即停
  D. 组内子规则的使用：不同关键字/标记/位置各自生效
  E. 兜底组、停用组、停用子规则的跳过逻辑
  F. 投递引擎按组配置实际改名与投递
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_groups")
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
print("\n=== A. 配置规范化 ===")
cfg = deliver.normalize_deliver_config(
    {
        "GROUPS": [
            {
                "name": "组1",
                "kind": "jav",
                "action": "copy",
                "target_dir": "/a",
                "children": [
                    {"keywords": "restored，LTX\n破解", "mark": "-U", "mark_position": "before"},
                    {"keywords": ["x"], "enabled": False},
                ],
            }
        ]
    }
)
g = cfg["GROUPS"][0]
check("组数正确", len(cfg["GROUPS"]) == 1)
check("组字段完整", all(k in g for k in ("id", "name", "kind", "enabled", "target_dir", "action", "children")))
check("子规则数正确", len(g["children"]) == 2)
check("关键字分隔符兼容（中文逗号/换行）",
      g["children"][0]["keywords"] == ["restored", "LTX", "破解"], g["children"][0]["keywords"])
check("mark_position 保存", g["children"][0]["mark_position"] == "before")
check("子规则 enabled 保存", g["children"][1]["enabled"] is False)

# 非法值回退
cfg_bad = deliver.normalize_deliver_config(
    {"GROUPS": [{"kind": "不存在的类型", "children": [{"mark_position": "瞎写"}]}]}
)
check("非法 kind 回退 jav", cfg_bad["GROUPS"][0]["kind"] == "jav")
check("非法 mark_position 回退 after", cfg_bad["GROUPS"][0]["children"][0]["mark_position"] == "after")

# 欧美/兜底组不保留标记
cfg_nm = deliver.normalize_deliver_config(
    {
        "GROUPS": [
            {"kind": "western", "children": [{"keywords": ["x"], "mark": "-U"}]},
            {"kind": "fallback", "children": [{"keywords": [], "mark": "-U"}]},
        ]
    }
)
check("欧美组子规则标记被清空", cfg_nm["GROUPS"][0]["children"][0]["mark"] == "")
check("兜底组子规则标记被清空", cfg_nm["GROUPS"][1]["children"][0]["mark"] == "")

# 组上直接挂 keywords 的简写
cfg_short = deliver.normalize_deliver_config(
    {"GROUPS": [{"kind": "jav", "keywords": ["a", "b"], "mark": "-X", "target_dir": "/t"}]}
)
check("简写 keywords 自动生成子规则",
      len(cfg_short["GROUPS"][0]["children"]) == 1
      and cfg_short["GROUPS"][0]["children"][0]["keywords"] == ["a", "b"])

print("\n=== B. 旧配置自动迁移 ===")
old = {
    "RULES": [
        {"name": "旧规则1", "kind": "jav", "keywords": ["restored"], "mark": "-U",
         "target_dir": "/old1", "action": "move", "enabled": True},
        {"name": "旧规则2", "kind": "western", "keywords": ["blacked"], "mark": "",
         "target_dir": "/old2", "action": "copy", "enabled": True},
    ]
}
mig = deliver.normalize_deliver_config(old)
check("迁移出 2 个组", len(mig["GROUPS"]) == 2, len(mig["GROUPS"]))
check("迁移保留组名", mig["GROUPS"][0]["name"] == "旧规则1")
check("迁移保留类型", mig["GROUPS"][1]["kind"] == "western")
check("迁移保留目录与动作",
      mig["GROUPS"][0]["target_dir"] == "/old1" and mig["GROUPS"][1]["action"] == "copy")
check("迁移后每组一个子规则",
      all(len(g["children"]) == 1 for g in mig["GROUPS"]))
check("迁移保留关键字与标记",
      mig["GROUPS"][0]["children"][0]["keywords"] == ["restored"]
      and mig["GROUPS"][0]["children"][0]["mark"] == "-U")

# GROUPS 优先于 RULES
both = deliver.normalize_deliver_config(
    {"GROUPS": [{"name": "新组", "kind": "jav", "children": [{"keywords": ["n"]}]}],
     "RULES": [{"name": "旧规则", "kind": "jav", "keywords": ["o"]}]}
)
check("GROUPS 优先于 RULES", len(both["GROUPS"]) == 1 and both["GROUPS"][0]["name"] == "新组")

# ===========================================================================
print("\n=== C. 优先级：组序 → 组内序 → 命中即停 ===")
groups = {
    "GROUPS": [
        {"name": "组A", "kind": "jav", "target_dir": "/A", "children": [
            {"keywords": ["alpha"], "mark": "-A1"},
            {"keywords": ["beta"], "mark": "-A2"},
        ]},
        {"name": "组B", "kind": "jav", "target_dir": "/B", "children": [
            {"keywords": ["beta", "gamma"], "mark": "-B1"},
        ]},
    ]
}
c = deliver.normalize_deliver_config(groups)

g1, ch1, kw1 = deliver.classify_file("ABC-001_alpha.mp4", c)
check("命中组A第一条", g1["name"] == "组A" and kw1 == "alpha" and ch1["mark"] == "-A1")

g2, ch2, kw2 = deliver.classify_file("ABC-002_beta.mp4", c)
check("beta 同时在组A第二条和组B第一条 -> 命中组A（组序优先）",
      g2["name"] == "组A" and ch2["mark"] == "-A2", (g2["name"], ch2))

g3, ch3, kw3 = deliver.classify_file("ABC-003_gamma.mp4", c)
check("gamma 只在组B -> 命中组B", g3["name"] == "组B" and kw3 == "gamma")

g4, ch4, kw4 = deliver.classify_file("ABC-004_none.mp4", c)
check("都不命中 -> None", g4 is None)

# 组内优先级：第一条与第二条都命中时取第一条
c2 = deliver.normalize_deliver_config({
    "GROUPS": [{"name": "G", "kind": "jav", "target_dir": "/g", "children": [
        {"keywords": ["both"], "mark": "-FIRST"},
        {"keywords": ["both"], "mark": "-SECOND"},
    ]}]
})
_, ch, _ = deliver.classify_file("XYZ-100_both.mp4", c2)
check("组内多条命中 -> 取组内第一条", ch["mark"] == "-FIRST", ch)

print("\n=== D. 组内子规则各自生效 ===")
c3 = deliver.normalize_deliver_config({
    "GROUPS": [{"name": "G", "kind": "jav", "target_dir": "/g", "children": [
        {"keywords": ["r1"], "mark": "-U", "mark_position": "after"},
        {"keywords": ["r2"], "mark": "U", "mark_position": "before"},
        {"keywords": ["r3"], "mark": ""},
    ]}]
})
from app.services import naming  # noqa: E402

_, ch, _ = deliver.classify_file("AAA-111_r1.mp4", c3)
out = naming.apply_mark_to_fanhao("AAA-111_r1.mp4", ch["mark"], ch["mark_position"])
check("子规则1 后置标记", out == "AAA-111-U_r1.mp4", out)

_, ch, _ = deliver.classify_file("BBB-222_r2.mp4", c3)
out = naming.apply_mark_to_fanhao("BBB-222_r2.mp4", ch["mark"], ch["mark_position"])
check("子规则2 前置标记", out == "U-BBB-222_r2.mp4", out)

_, ch, _ = deliver.classify_file("CCC-333_r3.mp4", c3)
check("子规则3 无标记 -> mark 为空", ch["mark"] == "")

print("\n=== E. 停用逻辑 ===")
c4 = deliver.normalize_deliver_config({
    "GROUPS": [
        {"name": "停用组", "kind": "jav", "enabled": False, "target_dir": "/off",
         "children": [{"keywords": ["x"]}]},
        {"name": "启用组", "kind": "jav", "enabled": True, "target_dir": "/on",
         "children": [
             {"keywords": ["x"], "enabled": False},
             {"keywords": ["x"], "enabled": True, "mark": "-OK"},
         ]},
    ]
})
g, ch, _ = deliver.classify_file("DDD-444_x.mp4", c4)
check("停用组被跳过，停用子规则被跳过", g["name"] == "启用组" and ch["mark"] == "-OK", (g, ch))

# 兜底
c5 = deliver.normalize_deliver_config({
    "GROUPS": [
        {"name": "普通", "kind": "jav", "target_dir": "/n", "children": [{"keywords": ["zzz"]}]},
        {"name": "兜底", "kind": "fallback", "target_dir": "/fb", "children": [{"keywords": []}]},
    ]
})
g, ch, kw = deliver.classify_file("EEE-555_other.mp4", c5)
check("关键字组不命中", g is None)
fb_g, fb_c = deliver.find_fallback_group(c5)
check("找到兜底组", fb_g is not None and fb_g["name"] == "兜底", fb_g)
check("兜底组不参与关键字匹配", fb_c is not None)

# ===========================================================================
print("\n=== F. 投递引擎按组执行 ===")
INBOX = os.path.join(TMP, "inbox")
OUT = os.path.join(TMP, "out")
shutil.rmtree(INBOX, ignore_errors=True)
os.makedirs(INBOX, exist_ok=True)
for d in ("A", "B", "FB"):
    os.makedirs(os.path.join(OUT, d), exist_ok=True)


def touch(name):
    with open(os.path.join(INBOX, name), "wb") as f:
        f.write(b"0" * (3 * MB))


touch("AAA-111_alpha.mp4")   # 组A 第一子规则
touch("BBB-222_beta.mp4")    # 组A 第二子规则
touch("CCC-333_gamma.mp4")   # 组B
touch("DDD-444_none.mp4")    # 兜底

run_groups = [
    {"name": "组A", "kind": "jav", "action": "move", "target_dir": os.path.join(OUT, "A"),
     "children": [
         {"keywords": ["alpha"], "mark": "-A1", "mark_position": "after"},
         {"keywords": ["beta"], "mark": "A2", "mark_position": "before"},
     ]},
    {"name": "组B", "kind": "jav", "action": "move", "target_dir": os.path.join(OUT, "B"),
     "children": [{"keywords": ["beta", "gamma"], "mark": "-B1", "mark_position": "after"}]},
    {"name": "兜底", "kind": "fallback", "action": "move", "target_dir": os.path.join(OUT, "FB"),
     "children": [{"keywords": []}]},
]
stats = deliver.run_deliver(
    {"SCAN_DIR": INBOX, "MIN_SIZE_MB": 1, "SETTLE_SECONDS": 0, "GROUPS": run_groups}, dry_run=False
)
check("4 个文件全部处理", stats["moved"] == 4, stats)

a_files = sorted(os.listdir(os.path.join(OUT, "A")))
b_files = sorted(os.listdir(os.path.join(OUT, "B")))
fb_files = sorted(os.listdir(os.path.join(OUT, "FB")))
check("组A：alpha 后置标记", "AAA-111-A1_alpha.mp4" in a_files, a_files)
check("组A：beta 前置标记", "A2-BBB-222_beta.mp4" in a_files, a_files)
check("组B：gamma 命中", b_files == ["CCC-333-B1_gamma.mp4"], b_files)
check("兜底：原样投递", fb_files == ["DDD-444_none.mp4"], fb_files)
check("beta 未被组B重复投递", "BBB-222-B1_beta.mp4" not in b_files)

# API：保存与读回
r = client.post("/api/deliver/config", json={"GROUPS": run_groups})
check("API 保存分组成功", r.get_json()["success"] is True)
saved = r.get_json()["config"]["GROUPS"]
check("API 读回组数正确", len(saved) == 3, len(saved))
check("API 读回子规则正确", len(saved[0]["children"]) == 2)

r = client.get("/api/deliver/config")
check("GET 配置含 GROUPS", "GROUPS" in r.get_json()["config"])

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
