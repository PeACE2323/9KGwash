"""素人番号规则（amateur）的测试。

规则语义：
  素人组命中子规则后，若番号前「紧挨着数字」（如 259LUXU-1903），
  则要看标记加在哪：
    - 标记加在【番号前】 -> 跳过该子规则（不生效）
    - 标记加在【番号后】 -> 正常执行
  若番号前没有紧挨数字（如 98T@LUXU-1903、STCV-001），
  无论标记位置都正常执行。
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_amateur")
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
from app.services import deliver  # noqa: E402

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


def build_cfg(position):
    return deliver.normalize_deliver_config(
        {
            "GROUPS": [
                {
                    "name": "素人番号",
                    "kind": "amateur",
                    "target_dir": "/out",
                    "action": "move",
                    "enabled": True,
                    "children": [
                        {
                            "keywords": ["laf", "luxu", "mium", "stcv"],
                            "mark": "U",
                            "mark_position": position,
                        }
                    ],
                }
            ]
        }
    )


def run(fn, position):
    """返回 (是否命中, 结果文件名)。"""
    hits = deliver.resolve_matches_by_group(fn, build_cfg(position))
    if not hits:
        return False, fn
    return True, hits[-1]["name_after"]


# ===========================================================================
print("\n=== A. 类型与规范化 ===")
check("amateur 是合法类型", "amateur" in deliver.VALID_KINDS, deliver.VALID_KINDS)

cfg = deliver.normalize_deliver_config(
    {
        "GROUPS": [
            {
                "name": "素人",
                "kind": "amateur",
                "target_dir": "/o",
                "children": [{"keywords": ["laf"], "mark": "-U", "mark_position": "before"}],
            }
        ]
    }
)
g = cfg["GROUPS"][0]
check("kind 保留为 amateur", g["kind"] == "amateur", g["kind"])
check("素人组保留标记（不像欧美/兜底那样清空）", g["children"][0]["mark"] == "-U",
      g["children"][0]["mark"])
check("标记位置保留", g["children"][0]["mark_position"] == "before")

# ===========================================================================
print("\n=== B. 标记加在番号前（before） ===")
# 番号前有数字 -> 跳过
hit, out = run("259LAF-1903.mp4", "before")
check("259LAF-1903 番号前有数字 -> 跳过", hit is False, out)

hit, out = run("300MIUM-1457.mp4", "before")
check("300MIUM-1457 番号前有数字 -> 跳过", hit is False, out)

# 番号前无数字 -> 正常执行
hit, out = run("98T@LAF-1903.mp4", "before")
check("98T@LAF-1903 前无数字 -> 执行", hit is True, out)
check("标记插在番号前", out == "98T@U-LAF-1903.mp4", out)

hit, out = run("STCV-001.mp4", "before")
check("STCV-001 前无数字 -> 执行", hit is True, out)
check("结果 U-STCV-001.mp4", out == "U-STCV-001.mp4", out)

hit, out = run("LAF-1903.mp4", "before")
check("LAF-1903 前无数字 -> 执行", hit is True, out)
check("结果 U-LAF-1903.mp4", out == "U-LAF-1903.mp4", out)

# ===========================================================================
print("\n=== C. 标记加在番号后（after） ===")
for fn in ("259LAF-1903.mp4", "300MIUM-1457.mp4", "98T@LAF-1903.mp4", "STCV-001.mp4"):
    hit, out = run(fn, "after")
    check(f"{fn} 标记在番号后 -> 执行", hit is True, out)

hit, out = run("259LAF-1903.mp4", "after")
check("259LAF-1903 -> 259LAF-1903-U.mp4", out == "259LAF-1903-U.mp4", out)

hit, out = run("98T@LAF-1903.mp4", "after")
check("98T@LAF-1903 -> 98T@LAF-1903-U.mp4", out == "98T@LAF-1903-U.mp4", out)

# ===========================================================================
print("\n=== D. 未命中关键字时不受影响 ===")
hit, out = run("ABC-123.mp4", "before")
check("不含关键字 -> 不命中", hit is False, out)

# ===========================================================================
print("\n=== E. 前端结构 ===")
HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()
JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(BASE, "app", "static", "css", "app.css"), encoding="utf-8").read()

check("下拉框有素人选项", 'value="amateur">素人番号规则' in HTML)
check("有素人规则说明", "group-amateur-tip" in HTML)
check("JS 有素人徽标", 'amateur: ["素人"' in JS)
check("JS 切换 is-amateur 类", 'kind === "amateur"' in JS)
check("CSS 有素人徽标样式", ".badge-amateur" in CSS)
check("CSS 有素人提示样式", ".group-amateur-tip" in CSS)
check("素人提示默认隐藏", ".group-amateur-tip {" in CSS and "display: none" in CSS)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
