"""标记严格原样插入 + 前置标记不破坏链式改名的测试。

背景：
  1) 用户要求标记严格按填写内容插入，代码不得自动补 "-" 等符号
  2) 严格插入后出现 A2BBB-222 这种前置标记紧贴番号的情况，
     曾导致番号识别失败、跨组链式改名中断
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_strictmark")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(os.path.join(TMP, "cfg"))
os.environ["XIBAN_CONFIG_DIR"] = os.path.join(TMP, "cfg")
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"

with open(os.path.join(TMP, "cfg", "config.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": "/clouddrive/115open",
            "METADATA_BASE_PATH": "/media",
            "WATCHED_DIRS": ["/9KG/STRM"],
            "DRY_RUN": True,
        },
        f,
        ensure_ascii=False,
    )

from app import config  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import deliver, naming  # noqa: E402

app = create_app()
config.init_config()

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
print("\n=== A. 标记严格原样插入（不自动补符号） ===")
F = "ABP-123.mp4"
cases_after = [
    ("U", "ABP-123U.mp4"),
    ("-U", "ABP-123-U.mp4"),
    ("_U", "ABP-123_U.mp4"),
    (".4K", "ABP-123.4K.mp4"),
]
for mark, expect in cases_after:
    got = naming.apply_mark_to_fanhao(F, mark, "after")
    check(f"后置 {mark!r} -> {expect}", got == expect, got)

cases_before = [
    ("U", "UABP-123.mp4"),
    ("-U", "-UABP-123.mp4"),
    ("U-", "U-ABP-123.mp4"),
    ("[U]", "[U]ABP-123.mp4"),
]
for mark, expect in cases_before:
    got = naming.apply_mark_to_fanhao(F, mark, "before")
    check(f"前置 {mark!r} -> {expect}", got == expect, got)

# 普通文件名
check("普通后置不加下划线",
      naming.apply_mark("Some.Video.mp4", "restored", "after", prefer_fanhao=False)
      == "Some.Videorestoredext" or
      naming.apply_mark("Some.Video.mp4", "restored", "after", prefer_fanhao=False)
      == "Some.Videorestored.mp4",
      naming.apply_mark("Some.Video.mp4", "restored", "after", prefer_fanhao=False))
check("普通后置（自带下划线）",
      naming.apply_mark("Some.Video.mp4", "_restored", "after", prefer_fanhao=False)
      == "Some.Video_restored.mp4",
      naming.apply_mark("Some.Video.mp4", "_restored", "after", prefer_fanhao=False))

# ===========================================================================
print("\n=== B. 幂等（加了标记后仍不重复加） ===")
for pos, mark in (("after", "-U"), ("before", "U")):
    once = naming.apply_mark_to_fanhao(F, mark, pos)
    twice = naming.apply_mark_to_fanhao(once, mark, pos)
    check(f"{pos} 幂等", once == twice, (once, twice))

# ===========================================================================
print("\n=== C. 前置标记紧贴番号后仍能识别番号 ===")
for fn, expect in (
    ("BBB-222.mp4", "BBB-222"),
    ("A2BBB-222_beta.mp4", "BBB-222"),
    ("UABP-123.mp4", "UABP-123"),
    ("ABP-123.mp4", "ABP-123"),
    ("259LUXU-1900.mp4", "259LUXU-1900"),
):
    info = naming.find_fanhao(fn)
    got = info["fanhao"] if info else None
    check(f"{fn} -> {expect}", got == expect, got)

# ===========================================================================
print("\n=== D. 链式改名不因前置标记而中断 ===")
cfg = deliver.normalize_deliver_config(
    {
        "GROUPS": [
            {
                "name": "组A",
                "kind": "jav",
                "target_dir": "/A",
                "children": [
                    {"keywords": ["beta"], "mark": "A2", "mark_position": "before"}
                ],
            },
            {
                "name": "组B",
                "kind": "jav",
                "target_dir": "/B",
                "children": [
                    {"keywords": ["beta"], "mark": "-B1", "mark_position": "after"}
                ],
            },
        ]
    }
)
hits = deliver.resolve_matches_by_group("BBB-222_beta.mp4", cfg)
check("两组都命中", [h["group"]["name"] for h in hits] == ["组A", "组B"],
      [h["group"]["name"] for h in hits])
check("组A 前置标记紧贴番号",
      hits[0]["name_after"] == "A2BBB-222_beta.mp4", hits[0]["name_after"])
check("组B 在组A结果上继续改名",
      hits[1]["name_before"] == hits[0]["name_after"]
      and hits[1]["name_after"] == "A2BBB-222-B1_beta.mp4",
      (hits[1]["name_before"], hits[1]["name_after"]))
check("最终只投最后命中组", deliver.classify_file("BBB-222_beta.mp4", cfg)[0]["name"] == "组B")

# ===========================================================================
print("\n=== E. 带连字符的前置标记也正常 ===")
cfg2 = deliver.normalize_deliver_config(
    {
        "GROUPS": [
            {
                "name": "组A",
                "kind": "jav",
                "target_dir": "/A",
                "children": [
                    {"keywords": ["beta"], "mark": "A2-", "mark_position": "before"}
                ],
            },
            {
                "name": "组B",
                "kind": "jav",
                "target_dir": "/B",
                "children": [
                    {"keywords": ["beta"], "mark": "-B1", "mark_position": "after"}
                ],
            },
        ]
    }
)
h2 = deliver.resolve_matches_by_group("BBB-222_beta.mp4", cfg2)
check("两组都命中", len(h2) == 2, len(h2))
check("结果是 A2-BBB-222-B1_beta.mp4",
      h2[-1]["name_after"] == "A2-BBB-222-B1_beta.mp4", h2[-1]["name_after"])

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
