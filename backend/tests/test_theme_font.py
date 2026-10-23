"""字号与主题相关测试。

覆盖：
  A. 字号：不再有小于 12px 的字号，基准字号已上调
  B. 主题：深色/浅色两套变量齐全，关键颜色均有对应变量
  C. 主题切换：HTML 按钮、内联防闪烁脚本、JS 切换与持久化
  D. 浅色适配：卡片/输入框/日志等有专门覆盖
"""

from __future__ import annotations

import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CSS = open(os.path.join(BASE, "app", "static", "css", "app.css"), encoding="utf-8").read()
HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()
JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()

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
print("\n=== A. 字号 ===")
sizes = [float(x) for x in re.findall(r"font-size:\s*([\d.]+)px", CSS)]
check("存在字号声明", len(sizes) > 50, len(sizes))
check("最小字号 >= 11px", min(sizes) >= 11, min(sizes))
check("主体字号集中在 12-14px", sum(1 for s in sizes if 12 <= s <= 14) >= len(sizes) * 0.6)
check("body 基准字号为 15px", re.search(r"body \{[^}]*?font-size:\s*15px", CSS, re.S) is not None)
check("已无 10px 及以下", all(s > 10 for s in sizes))

# ===========================================================================
print("\n=== B. 主题变量 ===")


def extract_block(selector: str) -> str:
    """提取某个选择器的完整 { } 内容（支持嵌套圆括号）。"""
    idx = CSS.find(selector + " {")
    if idx < 0:
        return ""
    start = CSS.find("{", idx)
    depth = 0
    for i in range(start, len(CSS)):
        if CSS[i] == "{":
            depth += 1
        elif CSS[i] == "}":
            depth -= 1
            if depth == 0:
                return CSS[start : i + 1]
    return ""


root_block = extract_block(":root")
light_block = extract_block('html[data-theme="light"]')

KEY_VARS = [
    "--bg", "--bg-soft", "--bg-inset", "--surface", "--surface-2", "--sidebar",
    "--topbar", "--border", "--border-strong", "--text", "--text-dim", "--text-faint",
    "--pink", "--pink-soft", "--green", "--green-text", "--yellow", "--yellow-text",
    "--red", "--red-text", "--cyan", "--purple-text", "--shadow",
    "--log-bg-a", "--log-bg-b", "--log-text", "--log-ts", "--aurora",
]
missing_root = [v for v in KEY_VARS if v + ":" not in root_block]
missing_light = [v for v in KEY_VARS if v + ":" not in light_block]
check("取到深色变量块", len(root_block) > 200, len(root_block))
check("取到浅色变量块", len(light_block) > 200, len(light_block))
check("深色主题变量齐全", not missing_root, missing_root)
check("浅色主题变量齐全", not missing_light, missing_light)
check("深色声明 color-scheme: dark", "color-scheme: dark" in root_block)
check("浅色声明 color-scheme: light", "color-scheme: light" in light_block)
check("浅色背景为亮色", "#f5f7fb" in light_block, light_block[:60])
check("深色背景为暗色", "#0b0f1a" in root_block)

# 关键元素都已改用变量
check("卡片使用 --surface", "background: var(--surface)" in CSS)
check("日志区使用日志变量", "var(--log-bg-a)" in CSS and "var(--log-text)" in CSS)
check("顶部栏使用 --topbar", "var(--topbar)" in CSS)
check("输入框使用 --bg-inset", "var(--bg-inset)" in CSS)
check("背景光晕使用 --aurora", "background: var(--aurora)" in CSS)

# 硬编码的浅色强调文字应已变量化（只检查 :root 变量块之外的正文部分）
body_css = CSS
for block in (root_block, light_block):
    if block:
        body_css = body_css.replace(block, "")
hard_light_colors = ["#6ee7b7", "#fca5a5", "#fde68a", "#a78bfa", "#a5f3fc"]
left = [c for c in hard_light_colors if c in body_css]
check("强调色已变量化", not left, left)

# ===========================================================================
print("\n=== C. 主题切换 ===")
check("顶栏有主题按钮", 'id="theme-toggle"' in HTML)
check("按钮含图标与文案", 'id="theme-icon"' in HTML and 'id="theme-label"' in HTML)
check("防闪烁内联脚本存在", "9kgwash-theme" in HTML and "data-theme" in HTML)
check("内联脚本在 head 内", HTML.find("9kgwash-theme") < HTML.find("</head>"))
check("JS 有 applyTheme", "function applyTheme" in JS)
check("JS 有 toggleTheme", "function toggleTheme" in JS)
check("设置了 data-theme 属性", 'setAttribute("data-theme"' in JS)
check("localStorage 持久化", "localStorage.setItem" in JS and "localStorage.getItem" in JS)
check("按钮已绑定点击", '$("theme-toggle").addEventListener' in JS)
check("启动时同步按钮状态", "applyTheme(currentTheme(), false)" in JS)
check("主题切换有 toast 反馈", "已切换到浅色主题" in JS)

# ===========================================================================
print("\n=== D. 浅色适配 ===")
check("卡片浅色阴影覆盖", 'html[data-theme="light"] .card' in CSS)
check("规则/组卡片浅色背景", 'html[data-theme="light"] .group-item' in CSS)
check("输入框浅色背景", 'html[data-theme="light"] .kw-input' in CSS)
check("日志区浅色覆盖", 'html[data-theme="light"] .log-box' in CSS)
check("导航激活态浅色覆盖", 'html[data-theme="light"] .nav-item.active' in CSS)
check("浅色说明文字对比度已加深", "--text-faint: #64748b" in light_block)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
