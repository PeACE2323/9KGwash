"""自由组合权重：自定义标签 / 自定义组合 / 旧配置迁移 的测试。

覆盖：
  A. 自定义标签（通过 API 传 tags）能正确参与判定
  B. 自定义组合（任意标签 AND 组合）
  C. 旧「自定义档位（关键字）」配置自动迁移为「标签 + 组合」
  D. 前端结构：标签网格、添加组合、组合列表
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_weight_custom")
if os.path.isdir(TMP):
    shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
CONFIG_DIR = os.path.join(TMP, "config")
os.makedirs(CONFIG_DIR, exist_ok=True)

os.environ["XIBAN_CONFIG_DIR"] = CONFIG_DIR
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"


def write_cfg(extra=None):
    cfg = {
        "MOUNT_BASE_PATH": "/media/CloudDrive",
        "METADATA_BASE_PATH": "/metadata",
        "WATCHED_DIRS": ["/115open/9KG/STRM"],
        "DRY_RUN": False,
    }
    if extra:
        cfg.update(extra)
    with open(os.path.join(CONFIG_DIR, "config.json"), "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False)


write_cfg()

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


def wf(fn):
    return fanhao.get_weight_and_fanhao(fn)[1]


# ===========================================================================
print("\n=== A. 自定义标签参与判定 ===")
# 新增两个自定义标签：HEVC（编码）、国粤双语
CUSTOM_TAGS = [
    {"id": "normal", "name": "普通", "keywords": [], "regex": ""},
    {"id": "4k", "name": "4K", "keywords": ["4k", "2160p"], "regex": ""},
    {"id": "hevc", "name": "HEVC", "keywords": ["hevc", "h265", "x265"], "regex": ""},
    {"id": "dual", "name": "双语", "keywords": ["国粤", "双语"], "regex": ""},
]
CUSTOM_COMBOS = [
    {"tags": ["4k", "hevc"]},
    {"tags": ["4k"]},
    {"tags": ["hevc"]},
    {"tags": ["dual"]},
    {"tags": ["normal"]},
]

r = client.post("/api/weight", json={"tags": CUSTOM_TAGS, "combos": CUSTOM_COMBOS})
j = r.get_json() or {}
check("保存自定义标签与组合", j.get("success") is True, j)
check("标签已保存", len(config.get_weight_tags()) == 4, config.get_weight_tags())
check("组合已保存", len(config.get_weight_combos()) == 5, config.get_weight_combos())

check("4K 命中", "4k" in fanhao.match_weight_tags("ABF-391-4K"))
check("2160p 也算 4K", "4k" in fanhao.match_weight_tags("ABF-391-2160p"))
check("HEVC 命中", "hevc" in fanhao.match_weight_tags("ABF-391-HEVC"))
check("H265 也算 HEVC", "hevc" in fanhao.match_weight_tags("ABF-391-h265"))
check("双语命中", "dual" in fanhao.match_weight_tags("ABF-391-国粤双语"))

check("只有 4K -> 权重 3", wf("ABF-391-4K.mp4") == 3, wf("ABF-391-4K.mp4"))
check("4K+HEVC -> 权重 4（最高组合）", wf("ABF-391-4K-HEVC.mp4") == 4,
      wf("ABF-391-4K-HEVC.mp4"))
check("只有 HEVC -> 权重 2", wf("ABF-391-HEVC.mp4") == 2, wf("ABF-391-HEVC.mp4"))
check("只有双语 -> 权重 1", wf("ABF-391-国粤双语.mp4") == 1, wf("ABF-391-国粤双语.mp4"))
check("普通 -> 权重 0", wf("ABF-391.mp4") == 0, wf("ABF-391.mp4"))

# ===========================================================================
print("\n=== B. 任意标签自由组合 ===")
r = client.post("/api/weight", json={
    "tags": CUSTOM_TAGS,
    "combos": [
        {"tags": ["4k", "hevc", "dual"]},
        {"tags": ["4k", "hevc"]},
        {"tags": ["normal"]},
    ],
})
check("三元组合保存成功", (r.get_json() or {}).get("success") is True)
check("三元组合全满足 -> 权重 2",
      wf("ABF-391-4K-HEVC-国粤双语.mp4") == 2,
      wf("ABF-391-4K-HEVC-国粤双语.mp4"))
check("只满足两项 -> 权重 1",
      wf("ABF-391-4K-HEVC.mp4") == 1, wf("ABF-391-4K-HEVC.mp4"))
check("只满足一项 -> 落到普通 = 0",
      wf("ABF-391-4K.mp4") == 0, wf("ABF-391-4K.mp4"))

# ===========================================================================
print("\n=== C. 旧「自定义档位」配置自动迁移 ===")
# 模拟老用户的配置：自定义档位 4K（关键字 4K/2160p），排在 C 之上
write_cfg({
    "WEIGHT_ORDER": ["UC", "custom:k4", "C", "U", "NORMAL"],
    "WEIGHT_CUSTOM": [{"id": "k4", "name": "4K", "keywords": ["4K", "2160p"]}],
})
config.init_config()

combos = config.get_weight_combos()
tags = config.get_weight_tags()
check("迁移后组合数 = 旧档位数", len(combos) == 5, combos)
check("迁移后出现自定义标签 c_k4", any(t["id"] == "c_k4" for t in tags),
      [t["id"] for t in tags])
check("自定义标签沿用原关键字",
      next((t["keywords"] for t in tags if t["id"] == "c_k4"), None) == ["4K", "2160p"],
      next((t["keywords"] for t in tags if t["id"] == "c_k4"), None))
check("组合顺序与旧档位顺序一致",
      [c["tags"] for c in combos]
      == [["sub", "crack"], ["c_k4"], ["sub"], ["crack"], ["normal"]],
      [c["tags"] for c in combos])

check("4K 文件命中迁移后的自定义标签", wf("ABF-391-4K.mp4") == 3,
      wf("ABF-391-4K.mp4"))
check("UC 样本仍为最高权重 4", wf("ABF-391-UC.mp4") == 4, wf("ABF-391-UC.mp4"))
check("C 样本权重 2", wf("ABF-391-C.mp4") == 2, wf("ABF-391-C.mp4"))
check("普通样本权重 0", wf("ABF-391.mp4") == 0, wf("ABF-391.mp4"))

# 已配置组合的用户不会被迁移覆盖
write_cfg({
    "WEIGHT_COMBOS": [{"tags": ["normal"]}],
    "WEIGHT_CUSTOM": [{"id": "k4", "name": "4K", "keywords": ["4K"]}],
})
config.init_config()
check("已有组合时不被迁移覆盖",
      len(config.get_weight_combos()) == 1, config.get_weight_combos())

# ===========================================================================
print("\n=== D. 前端结构 ===")
HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()
JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(BASE, "app", "static", "css", "app.css"), encoding="utf-8").read()

check("有标签网格容器", 'id="tag-grid"' in HTML)
check("有添加组合按钮", 'id="combo-add"' in HTML)
check("有组合预览", 'id="combo-preview"' in HTML)
check("有组合列表", 'id="weight-list"' in HTML)
check("有恢复默认按钮", 'id="weight-reset"' in HTML)
check("组合行模板含标签位", 'class="weight-tags"' in HTML)
check("组合行模板含拖动把手", "weight-grip" in HTML)
check("组合行模板含删除按钮", "weight-del" in HTML)

check("JS 有 renderTagGrid", "function renderTagGrid" in JS)
check("JS 有 renderComboList", "function renderComboList" in JS)
check("JS 有 addCombo", "function addCombo" in JS)
check("JS 有 removeCombo", "function removeCombo" in JS)
check("保存时提交 combos", "combos," in JS)
check("旧「添加自定义档位」按钮已移除", 'id="weight-add-custom"' not in HTML)

check("CSS 有标签网格样式", ".tag-grid" in CSS)
check("CSS 有标签勾选样式", ".tag-check" in CSS)
check("CSS 有组合标签样式", ".wtag" in CSS)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
