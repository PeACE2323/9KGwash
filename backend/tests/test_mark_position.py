"""标记位置（前/后）与规则类型约束的测试。

覆盖：
  A. JAV 番号：标记加在番号前 / 后，全部变体与幂等性
  B. 普通关键字规则：标记加在文件名前 / 后
  C. 欧美规则、兜底规则不提供标记（配置里被强制清空）
  D. 投递引擎按 mark_position 实际改名
  E. 预览接口输出正确
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_markpos")
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
            "DRY_RUN": False,
            "DB_ONLINE_WEBHOOK": "",
            "STRM_WEBHOOK": "",
            "IGNORE_LEADING_DIGITS": True,
        },
        f,
        ensure_ascii=False,
    )

from app.factory import create_app  # noqa: E402
from app.services import deliver, naming  # noqa: E402

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


MB = 1024 * 1024
F = "www.98T.la@DASS-787無防備に黒パンスト.mp4"

# ===========================================================================
print("\n=== A. JAV 番号：标记位置 ===")
after = naming.apply_mark_to_fanhao(F, "-U", "after")
before = naming.apply_mark_to_fanhao(F, "U", "before")
check("后置标记", after == "www.98T.la@DASS-787-U無防備に黒パンスト.mp4", after)
check("前置标记", before == "www.98T.la@U-DASS-787無防備に黒パンスト.mp4", before)
check("发布站前缀保留在最前", before.startswith("www.98T.la@U-DASS-787"))

# 各种标记写法的归一化
for raw in ("U", "U-", "-U", "_U"):
    got = naming.apply_mark_to_fanhao(F, raw, "before")
    check(f"前置标记 {raw!r} 归一为 U-", got == "www.98T.la@U-DASS-787無防備に黒パンスト.mp4", got)

check("方括号标记 [U] 原样使用",
      naming.apply_mark_to_fanhao("DASS-787.mp4", "[U]", "before") == "[U]DASS-787.mp4",
      naming.apply_mark_to_fanhao("DASS-787.mp4", "[U]", "before"))

# 幂等
for pos, mark in (("after", "-U"), ("before", "U")):
    once = naming.apply_mark_to_fanhao(F, mark, pos)
    twice = naming.apply_mark_to_fanhao(once, mark, pos)
    check(f"{pos} 幂等不重复叠加", once == twice, (once, twice))

# 前置数字厂牌
check("前置数字厂牌支持前置标记",
      naming.apply_mark_to_fanhao("259LUXU-1900.mp4", "U", "before") == "U-259LUXU-1900.mp4")
check("前置数字厂牌支持后置标记",
      naming.apply_mark_to_fanhao("259LUXU-1900.mp4", "-U", "after") == "259LUXU-1900-U.mp4")

# 不含番号时原样返回
check("无番号不动", naming.apply_mark_to_fanhao("no fanhao.mp4", "U", "before") == "no fanhao.mp4")

print("\n=== B. 普通关键字规则：标记位置 ===")
plain = "Some.Video.2024.mp4"
check("普通后置（自动加下划线分隔）",
      naming.apply_mark(plain, "restored", "after", prefer_fanhao=False) == "Some.Video.2024_restored.mp4",
      naming.apply_mark(plain, "restored", "after", prefer_fanhao=False))
check("普通后置（带下划线标记）",
      naming.apply_mark(plain, "_restored", "after", prefer_fanhao=False) == "Some.Video.2024_restored.mp4")
check("普通前置",
      naming.apply_mark(plain, "U", "before", prefer_fanhao=False) == "U-Some.Video.2024.mp4",
      naming.apply_mark(plain, "U", "before", prefer_fanhao=False))
check("普通路径保留扩展名",
      naming.apply_mark(plain, "x", "after", prefer_fanhao=False).endswith(".mp4"))

print("\n=== C. 欧美/兜底规则禁用标记 ===")
cfg = deliver.normalize_deliver_config(
    {
        "RULES": [
            {"kind": "western", "keywords": ["blackedraw"], "mark": "-U", "target_dir": "/a"},
            {"kind": "fallback", "keywords": [], "mark": "-U", "target_dir": "/b"},
            {"kind": "jav", "keywords": ["x"], "mark": "-U", "target_dir": "/c"},
        ]
    }
)
check("欧美规则标记被清空", cfg["RULES"][0]["mark"] == "", cfg["RULES"][0]["mark"])
check("兜底规则标记被清空", cfg["RULES"][1]["mark"] == "", cfg["RULES"][1]["mark"])
check("JAV 规则标记保留", cfg["RULES"][2]["mark"] == "-U")

cfg2 = deliver.normalize_deliver_config(
    {"RULES": [{"kind": "jav", "keywords": ["x"], "mark": "U", "mark_position": "before", "target_dir": "/c"}]}
)
check("mark_position 保存为 before", cfg2["RULES"][0]["mark_position"] == "before")
cfg3 = deliver.normalize_deliver_config(
    {"RULES": [{"kind": "jav", "keywords": ["x"], "mark": "U", "mark_position": "乱写", "target_dir": "/c"}]}
)
check("非法 mark_position 回退 after", cfg3["RULES"][0]["mark_position"] == "after")

# ===========================================================================
print("\n=== D. 投递引擎实际改名 ===")
INBOX = os.path.join(TMP, "inbox")
OUT = os.path.join(TMP, "out")
shutil.rmtree(INBOX, ignore_errors=True)
os.makedirs(INBOX, exist_ok=True)
os.makedirs(OUT, exist_ok=True)


def touch(name, size=3 * MB):
    p = os.path.join(INBOX, name)
    with open(p, "wb") as f:
        f.write(b"0" * size)
    return p


touch("PRE-111_restored.mp4")          # JAV 前 + restored
touch("POST-222_restored.mp4")         # JAV 后 + restored
touch("BlackedRaw.26.09.23.Title.mp4")
touch("plainvideo_restored.mp4")       # 无番号

rules = [
    {"name": "前置", "kind": "jav", "keywords": ["pre-111"], "mark": "U", "mark_position": "before",
     "target_dir": os.path.join(OUT, "pre"), "action": "move"},
    {"name": "后置", "kind": "jav", "keywords": ["post-222"], "mark": "-U", "mark_position": "after",
     "target_dir": os.path.join(OUT, "post"), "action": "move"},
    {"name": "欧美", "kind": "western", "keywords": ["blackedraw"], "mark": "-U",
     "target_dir": os.path.join(OUT, "west"), "action": "move"},
    {"name": "普通", "kind": "other", "keywords": ["plainvideo"], "mark": "_chs", "mark_position": "after",
     "target_dir": os.path.join(OUT, "other"), "action": "move"},
]
stats = deliver.run_deliver(
    {"SCAN_DIR": INBOX, "MIN_SIZE_MB": 1, "SETTLE_SECONDS": 0, "RULES": rules}, dry_run=False
)
check("处理了 4 个文件", stats["moved"] == 4, stats)

pre_files = os.listdir(os.path.join(OUT, "pre"))
post_files = os.listdir(os.path.join(OUT, "post"))
west_files = os.listdir(os.path.join(OUT, "west"))
other_files = os.listdir(os.path.join(OUT, "other"))

check("前置：#U 在番号前", pre_files == ["U-PRE-111_restored.mp4"], pre_files)
check("后置：-U 在番号后", post_files == ["POST-222-U_restored.mp4"], post_files)
check("欧美规则不改名", west_files == ["BlackedRaw.26.09.23.Title.mp4"], west_files)
check("普通规则后置标记", other_files == ["plainvideo_restored_chs.mp4"], other_files)

# ===========================================================================
print("\n=== E. 预览接口 ===")
shutil.rmtree(INBOX, ignore_errors=True)
os.makedirs(INBOX, exist_ok=True)
for n in ("PRE-111_restored.mp4", "POST-222_restored.mp4"):
    touch(n)

r = client.post(
    "/api/deliver/preview",
    json={"files": ["PRE-111_restored.mp4", "POST-222_restored.mp4", "BlackedRaw.26.09.23.x.mp4"], "rules": rules},
)
d = r.get_json()
outs = [x["output"] for x in d["results"]]
check("预览前置", outs[0] == "U-PRE-111_restored.mp4", outs)
check("预览后置", outs[1] == "POST-222-U_restored.mp4", outs)
check("预览欧美不改名", outs[2] == "BlackedRaw.26.09.23.x.mp4", outs)
check("预览返回 mark_position", d["results"][0]["mark_position"] == "before", d["results"][0])

# 配置保存后能被读回
r = client.post("/api/deliver/config", json={"RULES": rules})
saved = r.get_json()["config"]["RULES"]
check("配置保存 mark_position", saved[0]["mark_position"] == "before" and saved[1]["mark_position"] == "after",
      [x.get("mark_position") for x in saved])
r = client.get("/api/deliver/config")
back = r.get_json()["config"]["RULES"]
check("配置读回 mark_position 一致", back[0]["mark_position"] == "before")
check("欧美规则读回后标记为空", back[2]["mark"] == "")

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
