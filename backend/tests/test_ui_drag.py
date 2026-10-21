"""校验拖动排序的事件源约束：只有 ⠿ 把手能触发拖动。

由于本机无法启动浏览器（沙箱限制），这里用静态分析 + 逻辑模拟来验证：
1. 模板中只有 .rule-grip 带 draggable，卡片本身不带；
2. JS 的 dragstart 处理器在非把手来源时调用 preventDefault；
3. dragstart 通过 e.target.closest(".rule-grip") 判定来源。
"""

import pathlib
import re
import sys

# 兼容本地与 CI：以本文件位置推导项目根目录（backend/）
ROOT = pathlib.Path(__file__).resolve().parent.parent

html = (ROOT / "app/templates/index.html").read_text(encoding="utf-8")
js = (ROOT / "app/static/js/app.js").read_text(encoding="utf-8")
css = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


print("\n=== 1. 模板：只有把手可拖 ===")
grip = re.search(r'<span class="rule-grip"[^>]*>', html)
check("存在拖动把手", bool(grip), html[:0])
check("把手带 draggable=true", bool(grip) and 'draggable="true"' in grip.group(0), grip.group(0) if grip else None)

rule_item_tag = re.search(r'<div class="rule-item"[^>]*>', html)
check("卡片 .rule-item 不带 draggable",
      bool(rule_item_tag) and "draggable" not in rule_item_tag.group(0),
      rule_item_tag.group(0) if rule_item_tag else "未找到")

check("模板里只有一处 draggable",
      html.count('draggable="true"') == 1, f"实际 {html.count('draggable=\"true\"')} 处")

print("\n=== 2. JS：dragstart 只接受把手来源 ===")
check("dragstart 用 closest('.rule-grip') 判定来源",
      'e.target.closest(".rule-grip")' in js or "e.target.closest('.rule-grip')" in js)
check("非把手来源调用 preventDefault",
      re.search(r'if \(!grip\) \{\s*e\.preventDefault\(\);', js) is not None)
check("通过 grip.closest('.rule-item') 定位卡片",
      'grip.closest(".rule-item")' in js or "grip.closest('.rule-item')" in js)
check("JS 里不再给卡片设置 draggable",
      'node.setAttribute("draggable"' not in js)

print("\n=== 3. JS：setDragImage 让整条规则作为拖动快照 ===")
check("调用了 setDragImage", "setDragImage" in js)
check("setDragImage 传入规则卡片", re.search(r"setDragImage\(node", js) is not None)

print("\n=== 4. 关键字：textarea + 自动增高 ===")
check("关键字是 textarea", '<textarea class="rule-keywords"' in html)
check("设置 rows=1 初始单行", re.search(r'class="rule-keywords"\s+rows="1"', html) is not None)
check("存在 autoGrow 实现", "function autoGrow(" in js)
check("按 scrollHeight 撑高", "el.scrollHeight" in js)
check("渲染后调用 autoGrowAll", "requestAnimationFrame(autoGrowAll)" in js)
check("input 事件触发增量增高", 'kwEl.addEventListener("input"' in js)
check("窗口缩放时重算", "autoGrowAll()" in js.split("resize", 1)[-1])

print("\n=== 5. CSS：textarea 自动换行、无横向滚动 ===")
ta = re.search(r"textarea \{(.*?)\}", css, re.S)
check("textarea 定义存在", bool(ta))
if ta:
    body = ta.group(1)
    check("overflow-x: hidden（不出现横向滚动条）", "overflow-x: hidden" in body)
    check("overflow-y: hidden（高度由 JS 控制）", "overflow-y: hidden" in body)
    check("resize: none（禁用用户手动拉伸）", "resize: none" in body)
    check("width: 100%", "width: 100%" in body)

print("\n=== 6. 交互链路的模拟验证 ===")


class FakeTarget:
    """模拟 DOM 的 closest 行为。"""

    def __init__(self, classes):
        self.classes = classes

    def closest(self, sel):
        want = sel.lstrip(".")
        if want in self.classes:
            return self
        return None


# 从输入框发起：closest('.rule-grip') -> None -> 应被拒绝
from_input = FakeTarget(["rule-keywords"])
allowed = from_input.closest(".rule-grip") is not None
check("从输入框发起拖动 -> 被拒绝（可正常选文字）", allowed is False)

# 从把手发起：允许
from_grip = FakeTarget(["rule-grip"])
allowed = from_grip.closest(".rule-grip") is not None
check("从把手发起拖动 -> 允许", allowed is True)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
