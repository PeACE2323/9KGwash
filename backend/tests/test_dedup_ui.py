"""影片查重页 UI 细节的测试。

对应问题：
  1) 鼠标悬停在番号上时文字变白，浅色主题下看不清
  2) 组标题和后面的 📋 番号徽章都能点击复制，功能重复
"""

from __future__ import annotations

import os
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()
JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(BASE, "app", "static", "css", "app.css"), encoding="utf-8").read()

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
print("\n=== A. 悬停不再变白 ===")
check("dup-title 悬停不再设置 color:#fff", ".dup-title:hover { color: #fff" not in CSS)
check("dup-title 悬停用 filter 提亮", ".dup-title:hover" in CSS and "brightness" in CSS)
check("dup-title 用中性主文本色", "color: var(--text);" in CSS)
check("dup-title 光标为 default", "cursor: default;" in CSS)

# 提取 .dup-title 规则块检查
i = CSS.find(".dup-title {")
block = CSS[i : CSS.find("}", i)]
check("标题块内没有 pink 色", "pink" not in block, block[:120])

# ===========================================================================
print("\n=== B. 复制功能不重复 ===")
check("组标题不再带 data-copy", 'dup-title" data-copy' not in JS)
check("组标题 title 不再提示复制", "点击复制主番号" not in JS)
check("番号徽章仍可复制", 'class="chip" data-copy=' in JS)
check("徽章 title 明确为复制", "点击复制 " in JS)
check("页面提示只保留一个复制入口", JS.count('data-copy=') >= 1)

# ===========================================================================
print("\n=== C. 次要文字可读性 ===")
check("权重标签用实色文字", "color: var(--text);" in CSS)
i = CSS.find(".dup-file .tag")
tag_block = CSS[i : CSS.find("}", i)]
check("权重标签不再用半透明文字", "opacity" not in tag_block, tag_block[:150])
check("权重标签有背景", "background" in tag_block)

# ===========================================================================
print("\n=== D. 原有功能未受影响 ===")
check("王者仍有皇冠标记", "👑" in JS)
check("输家仍有删除标记", "🗑️" in JS)
check("仍有权重显示", '"权:"' in JS or "权:" in JS)
check("dup-group 结构完整", "dup-group" in JS and "dup-head" in JS)
check("dup-raws 容器存在", "dup-raws" in JS)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
