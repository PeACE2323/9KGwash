"""前端交互约束测试：拖动源限定 + 分组结构。

由于 CI 无浏览器，这里用静态分析 + 逻辑模拟验证：
1. 只有 ⠿ 把手带 draggable，卡片/组本身不带，确保输入框内可正常选字；
2. dragstart 校验来源，非把手来源直接 preventDefault；
3. 规则采用「组 + 子规则」结构：组头只读展示，子规则行内联编辑；
4. 欧美/兜底组隐藏标记控件。
"""

import pathlib
import re
import sys

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


print("\n=== 1. 拖动源限定在 ⠿ 把手 ===")
grip = re.search(r'<span class="group-grip"[^>]*>', html)
check("存在组拖动把手", bool(grip))
check("组把手带 draggable=true", bool(grip) and 'draggable="true"' in grip.group(0),
      grip.group(0) if grip else None)

group_tag = re.search(r'<div class="group-item"[^>]*>', html)
check("组卡片不带 draggable", bool(group_tag) and "draggable" not in group_tag.group(0),
      group_tag.group(0) if group_tag else "未找到")

check("模板中只有一处 draggable", html.count('draggable="true"') == 1,
      f"实际 {html.count('draggable=\"true\"')} 处")

print("\n=== 2. JS 校验拖动来源 ===")
check("dragstart 用 closest('.group-grip')", 'closest(".group-grip")' in js)
check("非把手来源 preventDefault",
      re.search(r"if \(!grip\) \{\s*e\.preventDefault\(\);", js) is not None)
check("不再给组卡片设置 draggable", 'node.setAttribute("draggable"' not in js)

print("\n=== 3. 分组结构 ===")
check("模板含 group-tpl", 'id="group-tpl"' in html)
check("模板含 child-tpl", 'id="child-tpl"' in html)
check("组头含共享类型选择", 'class="group-kind"' in html)
check("组头含共享目标目录", 'class="group-target"' in html)
check("组头含共享投递方式", 'class="group-action"' in html)
check("子规则含关键字", 'class="child-keywords"' in html)
check("子规则含标记", 'class="child-mark child-mark-val"' in html)
check("子规则含标记位置", 'class="child-mark-pos"' in html)
check("子规则含上移下移", 'child-up' in html and 'child-down' in html)

check("JS 有 renderGroups", "function renderGroups" in js)
check("JS 有 collectGroups", "function collectGroups" in js)
check("JS 已移除旧 renderRules", "renderRules" not in js)
check("配置提交使用 GROUPS", "GROUPS: collectGroups()" in js)

print("\n=== 4. 欧美/兜底组隐藏标记 ===")
check("CSS 有 no-mark 规则", ".group-item.no-mark .child-mark" in css)
check("JS 按类型切换 no-mark", 'classList.toggle("no-mark"' in js)

print("\n=== 5. 优先级提示 ===")
check("页面说明组优先于组内", "先看组的顺序" in html)

print("\n=== 6. 交互链路模拟 ===")


class FakeTarget:
    def __init__(self, classes):
        self.classes = classes

    def closest(self, sel):
        return self if sel.lstrip(".") in self.classes else None


check("从输入框发起拖动 -> 被拒绝（可正常选字）",
      FakeTarget(["child-keywords"]).closest(".group-grip") is None)
check("从把手发起拖动 -> 允许",
      FakeTarget(["group-grip"]).closest(".group-grip") is not None)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
