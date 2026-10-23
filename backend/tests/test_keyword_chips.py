"""关键字标签输入相关测试。

覆盖：
  A. 后端关键字规范化：中文逗号/顿号/换行拆分、去空白、不分大小写去重
  B. 旧格式配置（逗号分隔字符串）自动迁移为数组
  C. 前端模板：关键字改为标签容器 + 折叠预览
  D. 前端 JS：标签组件关键行为存在（回车/逗号确认、粘贴拆分、叉号删除、退格）
  E. 端到端：保存后读回关键字列表正确
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_kw")
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
            "DRY_RUN": True,
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

HTML = (BASE + "/app/templates/index.html").read_text(encoding="utf-8") if False else open(
    os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8"
).read()
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
print("\n=== A. 后端关键字规范化 ===")
sp = deliver._split_keywords

check("英文字符串", sp("a, b, c") == ["a", "b", "c"], sp("a, b, c"))
check("中文逗号", sp("a，b，c") == ["a", "b", "c"], sp("a，b，c"))
check("顿号", sp("a、b、c") == ["a", "b", "c"], sp("a、b、c"))
check("换行", sp("a\nb\nc") == ["a", "b", "c"], sp("a\nb\nc"))
check("混合分隔符", sp("a, b，c、d\nn e") == ["a", "b", "c", "d", "n e"], sp("a, b，c、d\nn e"))
check("去除首尾空白", sp("  a  ,  b  ") == ["a", "b"], sp("  a  ,  b  "))
check("空项被丢弃", sp("a,,b,  ,c") == ["a", "b", "c"], sp("a,,b,  ,c"))
check("不分大小写去重（保留首次写法）", sp("SMD, smd, Smd") == ["SMD"], sp("SMD, smd, Smd"))
check("数组输入", sp(["a", "b"]) == ["a", "b"], sp(["a", "b"]))
check("数组元素内含分隔符也拆", sp(["a,b", "c、d"]) == ["a", "b", "c", "d"], sp(["a,b", "c、d"]))
check("None -> 空列表", sp(None) == [])
check("空串 -> 空列表", sp("") == [])
check("分号也拆", sp("a;b；c") == ["a", "b", "c"], sp("a;b；c"))
check("中文关键字保留", sp("破解，流出，无码") == ["破解", "流出", "无码"], sp("破解，流出，无码"))

# ===========================================================================
print("\n=== B. 旧格式自动迁移 ===")
cfg = deliver.normalize_deliver_config({
    "GROUPS": [{
        "name": "G", "kind": "jav", "target_dir": "/g",
        "children": [
            {"keywords": "restored， LTX ,  破解 、流出,  RESTORED ", "mark": "-U"},
            {"keywords": ["SMD", "smd", "LAF"], "mark": "U"},
        ],
    }]
})
c0 = cfg["GROUPS"][0]["children"][0]
c1 = cfg["GROUPS"][0]["children"][1]
check("旧字符串被拆成标签列表", c0["keywords"] == ["restored", "LTX", "破解", "流出"], c0["keywords"])
check("大小写重复被去重", c1["keywords"] == ["SMD", "LAF"], c1["keywords"])

# 组上简写 keywords 也走同一规范化
cfg2 = deliver.normalize_deliver_config({
    "GROUPS": [{"name": "G", "kind": "jav", "keywords": "a，b、c", "mark": "-X", "target_dir": "/g"}]
})
check("组简写 keywords 也被规范化",
      cfg2["GROUPS"][0]["children"][0]["keywords"] == ["a", "b", "c"],
      cfg2["GROUPS"][0]["children"][0]["keywords"])

# 旧版扁平 RULES 迁移后关键字同样规范
cfg3 = deliver.normalize_deliver_config({
    "RULES": [{"name": "old", "kind": "jav", "keywords": "x，y z、w", "mark": "-U", "target_dir": "/o"}]
})
check("旧 RULES 迁移后关键字被规范化",
      cfg3["GROUPS"][0]["children"][0]["keywords"] == ["x", "y z", "w"],
      cfg3["GROUPS"][0]["children"][0]["keywords"])

# ===========================================================================
print("\n=== C. 前端模板结构 ===")
check("关键字不再是单行 input",
      'class="kw-input child-keywords"' in HTML
      and not re.search(r'<input[^>]*class="child-keywords"', HTML))
check("关键字改为标签容器", 'class="kw-input child-keywords"' in HTML)
check("容器带 placeholder 属性", 'data-placeholder=' in HTML)
check("存在折叠态预览容器样式", ".group-kw-preview" in CSS)
check("存在小标签样式", ".kw-mini" in CSS)
check("存在 +N 样式", ".kw-mini-more" in CSS)
check("存在删除按钮样式", ".kw-chip-del" in CSS)

print("\n=== D. 前端 JS 组件行为 ===")
check("存在 normalizeKeywords", "function normalizeKeywords" in JS)
check("存在 createKeywordInput", "function createKeywordInput" in JS)
check("回车确认", 'e.key === "Enter"' in JS)
check("逗号立即确认", 'e.key === ","' in JS)
check("中文逗号立即确认", 'e.key === "，"' in JS)
check("顿号立即确认", 'e.key === "、"' in JS)
check("退格删除末个标签", 'e.key === "Backspace"' in JS)
check("粘贴自动拆分", '"paste"' in JS and '/[,，、\\n]/' in JS.replace("\\\\", "\\"))
check("失焦自动转标签", '"blur"' in JS)
check("点击容器聚焦输入", "input.focus()" in JS)
check("叉号删除标签", 'className = "kw-chip-del"' in JS)
check("收集时读取标签", "c._kw ? c._kw.get()" in JS)
check("折叠预览函数存在", "function refreshGroupKeywordPreview" in JS)
check("预览最多显示 3 个", "const LIMIT = 3" in JS)
check("超出显示 +N", "+${all.length - LIMIT}" in JS or "+${all.length" in JS)
check("去重不分大小写", "toLowerCase()" in JS)

# ===========================================================================
print("\n=== E. 端到端 ===")
payload = {
    "GROUPS": [{
        "name": "测试组", "kind": "jav", "target_dir": "/t", "action": "move", "enabled": True,
        "children": [{"keywords": "a，b、c  , A", "mark": "-U", "mark_position": "after", "enabled": True}],
    }]
}
r = client.post("/api/deliver/config", json=payload)
d = r.get_json()
check("保存成功", d["success"] is True, d)
saved_kw = d["config"]["GROUPS"][0]["children"][0]["keywords"]
check("保存后关键字为规范化列表", saved_kw == ["a", "b", "c"], saved_kw)

r = client.get("/api/deliver/config")
back = r.get_json()["config"]["GROUPS"][0]["children"][0]["keywords"]
check("读回一致", back == ["a", "b", "c"], back)

# 保存字符串数组（前端标签组件的输出形式）
payload2 = {
    "GROUPS": [{
        "name": "测试组2", "kind": "jav", "target_dir": "/t2",
        "children": [{"keywords": ["restored", "LTX", "破解"], "mark": "-U"}],
    }]
}
r = client.post("/api/deliver/config", json=payload2)
kw2 = r.get_json()["config"]["GROUPS"][0]["children"][0]["keywords"]
check("数组形式原样保存", kw2 == ["restored", "LTX", "破解"], kw2)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
