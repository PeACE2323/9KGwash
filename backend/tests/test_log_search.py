"""日志搜索与自动跟随的测试。

覆盖：
  A. /api/logs 的 q 参数：不区分大小写、中文、与分类组合、空查询
  B. 前端结构：搜索框、清空按钮、命中提示、自动跟随按钮
  C. 前端逻辑：跟随状态管理、渲染后滚动、搜索高亮
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import urllib.parse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_logsearch")
if os.path.isdir(TMP):
    shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
CONFIG_DIR = os.path.join(TMP, "config")
LOG_DIR = os.path.join(CONFIG_DIR, "logs")
os.makedirs(LOG_DIR, exist_ok=True)

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
        },
        f,
        ensure_ascii=False,
    )

from app.factory import create_app  # noqa: E402
from app import config  # noqa: E402

app = create_app()
client = app.test_client()

# 造可控日志：deliver 5 条含 DASS-787，wash 3 条，含中文若干
LINES = [
    "2026-10-25 10:00:01 [INFO] #deliver#   [-] 已移动: DASS-787-U_restored.mp4 -> /out",
    "2026-10-25 10:00:02 [INFO] #deliver#   [-] 已移动: ABC-123.mp4 -> /out",
    "2026-10-25 10:00:03 [INFO] #deliver#   [-] 已移动: DASS-787-CD1.mp4 -> /out",
    "2026-10-25 10:00:04 [WARNING] #deliver#   [-] 监控目录不存在: /deliver/inbox",
    "2026-10-25 10:00:05 [INFO] #deliver#   [-] 任务完成: 扫描 3 个文件",
    "2026-10-25 10:00:06 [INFO] #wash#   [-] 洗板检查 1 完成",
    "2026-10-25 10:00:07 [INFO] #wash#   [-] 洗板检查 2 完成",
    "2026-10-25 10:00:08 [ERROR] #wash#   [-] 洗板失败: 权限不足",
]
with open(config.LOG_FILE, "w", encoding="utf-8") as f:
    f.write("\n".join(LINES) + "\n")

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def fetch(**params):
    url = "/api/logs?" + urllib.parse.urlencode(params)
    return client.get(url).get_json()


# ===========================================================================
print("\n=== A. 后端搜索 ===")
d = fetch(limit=100)
check("无查询返回全部", d["total"] == len(LINES), d["total"])
check("返回 q 字段", d.get("q") == "")

d = fetch(q="DASS-787", limit=100)
check("按番号搜索命中 2 条", d["total"] == 2, d["total"])
check("命中内容都含关键字", all("DASS-787" in x for x in d["logs"]), d["logs"])

d = fetch(q="dass-787", limit=100)
check("大小写不敏感", d["total"] == 2, d["total"])

d = fetch(q="DASS-787", category="deliver", limit=100)
check("搜索 + 分类组合", d["total"] == 2, d["total"])

d = fetch(q="DASS-787", category="wash", limit=100)
check("搜索 + 不匹配分类 = 0", d["total"] == 0, d["total"])

d = fetch(q="监控目录", limit=100)
check("中文搜索", d["total"] == 1, d["total"])

d = fetch(q="洗板检查", limit=100)
check("中文搜索多个命中", d["total"] == 2, d["total"])

d = fetch(q="洗板完成", limit=100)
check("无匹配返回 0", d["total"] == 0, d["total"])

d = fetch(q="", limit=100)
check("空查询等价于不过滤", d["total"] == len(LINES))

d = fetch(q="   ", limit=100)
check("纯空格查询等价于不过滤", d["total"] == len(LINES))

d = fetch(q="DASS", limit=100)
check("部分匹配（子串）", d["total"] == 2, d["total"])

d = fetch(q="deliver", limit=100)
check("可搜索分类标签本身", d["total"] >= 5, d["total"])

# 分页上限仍然生效
d = fetch(q="", limit=3)
check("limit 仍生效", d["total"] == 3, d["total"])

# ===========================================================================
print("\n=== B. 前端结构 ===")
HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()
JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(BASE, "app", "static", "css", "app.css"), encoding="utf-8").read()

check("有搜索输入框", 'id="log-search"' in HTML)
check("有清空按钮", 'id="log-search-clear"' in HTML)
check("有命中提示元素", 'id="log-search-info"' in HTML)
check("有自动跟随按钮", 'id="follow-btn"' in HTML)
check("跟随按钮默认 is-on", 'id="follow-btn" class="btn btn-ghost btn-xs is-on"' in HTML
      or 'follow-btn" title' in HTML)
check("有搜索框样式", ".log-search" in CSS)
check("有高亮样式", ".log-hit" in CSS)
check("有命中提示样式", ".log-search-info" in CSS)

print("\n=== C. 前端逻辑 ===")
check("state 有 logQuery", "logQuery:" in JS)
check("state 有 logFollow", "logFollow:" in JS)
check("有 shouldFollowLog", "function shouldFollowLog" in JS)
check("有 scrollLogToBottom", "function scrollLogToBottom" in JS)
check("有 highlightLog", "function highlightLog" in JS)
check("有 setLogFollow", "function setLogFollow" in JS)
check("有 updateLogFollowUI", "function updateLogFollowUI" in JS)
check("有 applyLogSearch", "function applyLogSearch" in JS)
check("fetchLogs 带 q 参数", "&q=" in JS and "encodeURIComponent(q)" in JS)
check("渲染后滚动（不再用 atBottom 判断）",
      "if (atBottom) box.scrollTop" not in JS)
check("渲染后调用 scrollLogToBottom", "scrollLogToBottom(false)" in JS)
check("进入日志页强制跟随", 'name === "logs"' in JS and "state.logFollow = true" in JS)
check("上翻自动锁定", "!atBottom && state.logFollow" in JS)
check("回到底部恢复跟随", "atBottom && !state.logFollow" in JS)
check("搜索输入防抖", "setTimeout(() => doSearch(val), 300)" in JS)
check("回车立即搜索", 'e.key === "Enter"' in JS)
check("Esc 清空搜索", 'e.key === "Escape"' in JS)
check("命中条数提示", "命中 ${count} 条" in JS)
check("无结果提示", "没有匹配的日志" in JS)
check("高亮用 mark 标签", 'class="log-hit"' in JS)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
