"""活动日志分类与投递日志精简的测试。

覆盖：
  A. 分类标签的生成与解析
  B. 各业务模块的日志是否带上正确的分类标签
  C. /api/logs 按分类过滤、兼容旧的无参调用
  D. /api/logs/categories 计数正确
  E. 投递日志不再输出扫描目录、只保留逐文件结果与汇总
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_logcat")
if os.path.isdir(TMP):
    shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
CONFIG_DIR = os.path.join(TMP, "config")
os.makedirs(CONFIG_DIR, exist_ok=True)

os.environ["XIBAN_CONFIG_DIR"] = CONFIG_DIR
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"
os.environ["XIBAN_BROWSE_ROOTS"] = TMP

with open(os.path.join(CONFIG_DIR, "config.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": os.path.join(TMP, "media").replace("\\", "/"),
            "METADATA_BASE_PATH": os.path.join(TMP, "metadata").replace("\\", "/"),
            "WATCHED_DIRS": ["/115open/9KG/STRM"],
            "DRY_RUN": True,
            "DB_ONLINE_WEBHOOK": "",
            "STRM_WEBHOOK": "",
            "IGNORE_LEADING_DIGITS": True,
        },
        f,
        ensure_ascii=False,
    )

from app import logging_setup as LS  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import deliver, library, washer  # noqa: E402

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


# ===========================================================================
print("\n=== A. 分类标签读写 ===")
check("tag 生成", LS.tag("deliver", "测试") == "#deliver#测试", LS.tag("deliver", "测试"))
check("all 不加标签", LS.tag("all", "测试") == "测试")
check("未知分类不加标签", LS.tag("nope", "测试") == "测试")
check("parse 分类", LS.parse_category("#deliver#测试") == "deliver")
check("parse 无标签 -> system", LS.parse_category("测试") == "system")
check("strip 去标签", LS.strip_category("#deliver#测试") == "测试")
check("strip 无标签原样", LS.strip_category("测试") == "测试")
check("parse 只在开头匹配", LS.parse_category("正文里出现 #deliver# 不算") == "system")

print("\n=== B. 各模块日志带正确分类 ===")
LS.logger.info("banner")  # 系统日志（无标签）
deliver.logger.info("投递测试消息")
library.logger.info("🔍 [全库查重] 查重消息")
library.logger.info("🧽 [空间猎犬] 猎犬消息")
washer.logger.info("洗板消息")

from app.services import webhooks as WH  # noqa: E402

WH.logger.info("Emby 消息")

with open(os.path.join(CONFIG_DIR, "logs", "xiban.log"), encoding="utf-8") as f:
    raw = f.read()
check("投递日志带 #deliver#", "#deliver#投递测试消息" in raw)
check("查重日志带 #dedup#", "#dedup#🔍 [全库查重] 查重消息" in raw)
check("猎犬日志带 #hound#", "#hound#🧽 [空间猎犬] 猎犬消息" in raw)
check("洗板日志带 #wash#", "#wash#洗板消息" in raw)
check("Emby 日志带 #emby#", "#emby#Emby 消息" in raw)
check("系统日志无标签", "[INFO] banner" in raw)


# ===========================================================================
print("\n=== C. /api/logs 分类过滤 ===")
r = client.get("/api/logs")
d = r.get_json()
check("无参调用兼容旧格式", "logs" in d and isinstance(d["logs"], list))
check("无参默认 all", d.get("category") == "all", d.get("category"))

r = client.get("/api/logs?category=deliver")
d = r.get_json()
check("过滤 deliver 有结果", d["total"] > 0, d["total"])
check("过滤结果只含投递日志", all("投递测试消息" in x or "[投递]" in x or "投递" in x for x in d["logs"]),
      d["logs"][:3])

r = client.get("/api/logs?category=hound")
d = r.get_json()
check("过滤 hound 有结果", d["total"] > 0)
check("hound 结果不含查重", all("全库查重" not in x for x in d["logs"]))

r = client.get("/api/logs?category=wash")
d = r.get_json()
check("过滤 wash 命中洗板消息", any("洗板消息" in x for x in d["logs"]))

r = client.get("/api/logs?category=dedup")
d = r.get_json()
check("过滤 dedup 命中查重消息", any("查重消息" in x for x in d["logs"]))

r = client.get("/api/logs?category=emby")
d = r.get_json()
check("过滤 emby 命中", any("Emby 消息" in x for x in d["logs"]))

r = client.get("/api/logs?category=system")
d = r.get_json()
check("过滤 system 命中 banner", any("banner" in x for x in d["logs"]))

r = client.get("/api/logs?category=notexist")
d = r.get_json()
check("非法分类回退 all", d.get("category") == "all", d.get("category"))

r = client.get("/api/logs?strip=0")
d = r.get_json()
check("strip=0 保留分类标签", any("#deliver#" in x for x in d["logs"]))

r = client.get("/api/logs?limit=3&strip=1")
d = r.get_json()
check("limit 生效", len(d["logs"]) <= 3, len(d["logs"]))
check("strip 默认去掉标签", all("#deliver#" not in x for x in d["logs"]))

print("\n=== D. /api/logs/categories ===")
r = client.get("/api/logs/categories")
d = r.get_json()
keys = [c["key"] for c in d["categories"]]
check("包含全部分类", keys == ["all", "wash", "hound", "dedup", "deliver", "emby", "system"], keys)
counts = {c["key"]: c["count"] for c in d["categories"]}
check("deliver 计数 > 0", counts["deliver"] > 0, counts)
check("all 计数等于各行总数", counts["all"] == sum(v for k, v in counts.items() if k != "all"), counts)
check("每个分类都有中文标签", all(c["label"] for c in d["categories"]))

# ===========================================================================
print("\n=== E. 投递日志精简（不刷扫描目录）===")
INBOX = os.path.join(TMP, "inbox")
OUT = os.path.join(TMP, "out")
shutil.rmtree(INBOX, ignore_errors=True)
os.makedirs(INBOX, exist_ok=True)
os.makedirs(OUT, exist_ok=True)
for name, size in [
    ("ABC-123_restored.mp4", 3 * 1024 * 1024),
    ("XYZ-999.mp4", 3 * 1024 * 1024),
    ("tiny.nfo", 100),
]:
    with open(os.path.join(INBOX, name), "wb") as f:
        f.write(b"0" * size)

mark_before = len(raw)
cfg = {
    "SCAN_DIR": INBOX,
    "MIN_SIZE_MB": 1,
    "SETTLE_SECONDS": 0,
    "RULES": [
        {"name": "还原", "kind": "jav", "keywords": ["restored"], "mark": "-U",
         "target_dir": os.path.join(OUT, "jav"), "action": "move"},
        {"name": "兜底", "kind": "fallback", "keywords": [], "mark": "",
         "target_dir": os.path.join(OUT, "fb"), "action": "move"},
    ],
}
stats = deliver.run_deliver(cfg, dry_run=False)
check("投递执行成功", stats["moved"] == 2 and stats["deleted"] == 1, stats)

r = client.get("/api/logs?category=deliver&limit=50&strip=1")
logs = r.get_json()["logs"]
joined = "\n".join(logs)
check("含任务完成汇总行", "任务完成" in joined)
check("汇总行含扫描数", "扫描 3 个文件" in joined, [x for x in logs if "任务完成" in x])
check("不含「开始扫描 + 目录路径」噪音", "开始扫描" not in joined)
check("日志中不出现监控目录绝对路径", INBOX not in joined, [x for x in logs if INBOX in x])
check("含逐文件处理记录", "ABC-123" in joined and "XYZ-999" in joined)
check("含小文件删除记录", "删除小文件" in joined)

# 空目录时不刷日志：再跑一次，应无新增汇总
before_cnt = client.get("/api/logs/categories").get_json()
before_deliver = {c["key"]: c["count"] for c in before_cnt["categories"]}["deliver"]
deliver.run_deliver(cfg, dry_run=False)
after_cnt = client.get("/api/logs/categories").get_json()
after_deliver = {c["key"]: c["count"] for c in after_cnt["categories"]}["deliver"]
check("空扫描不产生新日志", after_deliver == before_deliver, (before_deliver, after_deliver))

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
