"""文件投递功能的端到端测试（不依赖 pytest）。

覆盖：
  A. 番号智能定位（跳过 www.98T.la@ 等前缀、前置数字厂牌、误判防护）
  B. 标记插入（用户原始例子、幂等、已有标记不重复）
  C. 关键字匹配（不分大小写、多关键字映射同一标记）
  D. 投递引擎：体积阈值删除、JAV 投递、普通规则投递、移动/复制、覆盖、空目录清理
  E. API：配置读写、目录浏览、预览、立即执行
"""

from __future__ import annotations

import io
import json
import os
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_deliver")
if os.path.isdir(TMP):
    shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)

CONFIG_DIR = os.path.join(TMP, "config")
SCAN = os.path.join(TMP, "inbox")
OUT_JAV = os.path.join(TMP, "out", "jav")
OUT_UNCENSORED = os.path.join(TMP, "out", "uncensored")
OUT_OTHER = os.path.join(TMP, "out", "other")
for d in (CONFIG_DIR, SCAN, OUT_JAV, OUT_UNCENSORED, OUT_OTHER):
    os.makedirs(d, exist_ok=True)

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

PASS = 0
FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def clean(*dirs):
    for d in dirs:
        if os.path.isdir(d):
            for name in os.listdir(d):
                p = os.path.join(d, name)
                if os.path.isdir(p):
                    shutil.rmtree(p, ignore_errors=True)
                else:
                    os.remove(p)


def touch(path, size_bytes=1024):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"0" * size_bytes)
    return path


MB = 1024 * 1024

# ===========================================================================
print("\n=== A. 番号智能定位 ===")
USER_FILE = (
    "www.98T.la@DASS-787無防備に黒パンストを見せつける姉ちゃんのデカ尻を揉み倒して"
    "中出ししてしまった僕。橘メアリー_restored_wm.mp4"
)

info = naming.find_fanhao(USER_FILE)
check("用户例子识别出 DASS-787", info and info["fanhao"].upper() == "DASS-787", info)
check("跳过 www.98T.la@ 前缀", info and info["prefix"].lower() == "www.98t.la@", info["prefix"] if info else None)
check("未误判 98T", info and "98T" not in info["fanhao"].upper())

check("普通番号 DASS-787", (naming.find_fanhao("DASS-787.mp4") or {}).get("fanhao", "").upper() == "DASS-787")
check("[ThZu.Cc] 前缀", (naming.find_fanhao("[ThZu.Cc]ABP-159-C.mp4") or {}).get("fanhao", "").upper() == "ABP-159")
check("hhd800.com@ 前缀", (naming.find_fanhao("hhd800.com@SSIS-088中文字幕.mp4") or {}).get("fanhao", "").upper() == "SSIS-088")

lux = naming.find_fanhao("259LUXU-1900.mp4")
check("前置数字厂牌 259LUXU-1900 整体识别", lux and lux["fanhao"] == "259LUXU-1900", lux)
check("259LUXU 标记前置数字为 True", lux and lux["has_leading_digits"] is True, lux)
check("www前缀 + 259LUXU",
      (naming.find_fanhao("www.98T.la@259LUXU-1901.zh-CN.mp4") or {}).get("fanhao", "") == "259LUXU-1901")

check("非番号 hello world -> None", naming.find_fanhao("hello world.mp4") is None)
check("非番号 纯数字 -> None", naming.find_fanhao("123456.mp4") is None)
check("排除伪番号 hd-1080", naming.find_fanhao("hd-1080p.mkv") is None,
      naming.find_fanhao("hd-1080p.mkv"))

# ===========================================================================
print("\n=== B. 标记插入 ===")
marked = naming.insert_mark_after_fanhao(USER_FILE, "-U")
EXPECTED = USER_FILE.replace("DASS-787", "DASS-787-U")
check("用户原始场景完全匹配", marked == EXPECTED, f"\n    got: {marked}\n    exp: {EXPECTED}")
check("其余部分不变", marked.rsplit("-U", 1)[1] == EXPECTED.rsplit("-U", 1)[1])

check("幂等：已带 -U 不重复添加", naming.insert_mark_after_fanhao(marked, "-U") == marked)
check("不同标记可叠加到已有标记前",
      naming.insert_mark_after_fanhao("DASS-787-C.mp4", "-U") == "DASS-787-U-C.mp4")

# ===========================================================================
print("\n=== C. 关键字匹配（不分大小写）===")
check("Restored 大小写不敏感", naming.match_any_keyword(USER_FILE, ["RESTORED"]) == "RESTORED")
check("restored", naming.match_any_keyword(USER_FILE, ["restored"]) == "restored")
check("多关键字命中第一个", naming.match_any_keyword("abc_破解_def.mp4", ["xx", "破解", "yy"]) == "破解")
check("未命中 -> None", naming.match_any_keyword("plain.mp4", ["zzz"]) is None)

# ===========================================================================
print("\n=== D. 投递引擎 ===")

RULES = [
    {
        "id": "r1", "name": "还原版", "kind": "jav", "enabled": True,
        "keywords": ["restored"], "mark": "-U", "target_dir": OUT_JAV, "action": "move",
    },
    {
        "id": "r2", "name": "无码", "kind": "jav", "enabled": True,
        "keywords": ["uncensored", "无码"], "mark": "-U", "target_dir": OUT_UNCENSORED, "action": "move",
    },
    {
        "id": "r3", "name": "其它视频", "kind": "other", "enabled": True,
        "keywords": ["documentary"], "mark": "", "target_dir": OUT_OTHER, "action": "move",
    },
]


def base_cfg(**over):
    cfg = {
        "SCAN_DIR": SCAN,
        "MIN_SIZE_MB": 1,
        "RECURSIVE": True,
        "REMOVE_EMPTY_DIRS": True,
        "OVERWRITE": True,
        "SETTLE_SECONDS": 0,
        "RULES": [dict(r) for r in RULES],
    }
    cfg.update(over)
    return cfg


# --- D1: 完整流程 ---
clean(SCAN, OUT_JAV, OUT_UNCENSORED, OUT_OTHER)
touch(os.path.join(SCAN, USER_FILE), 2 * MB)                       # 大文件，命中 restored
touch(os.path.join(SCAN, "sub", "ABC-123-uncensored.mp4"), 2 * MB)  # 子目录，命中无码
touch(os.path.join(SCAN, "small.mp4"), 100)                          # 小文件，应删除
touch(os.path.join(SCAN, "tiny.nfo"), 10)                            # 小非媒体，同样删除
touch(os.path.join(SCAN, "My.Holiday.Documentary.2024.mp4"), 2 * MB)  # 非JAV，命中 other
touch(os.path.join(SCAN, "no_rule.mp4"), 2 * MB)                     # 无关键字，保留
touch(os.path.join(SCAN, "restored_no_fanhao.mp4"), 2 * MB)          # 命中但无番号，跳过

stats = deliver.run_deliver(base_cfg(), dry_run=False)
check("扫描到 7 个文件", stats["scanned"] == 7, stats["scanned"])
check("删除小文件 2 个", stats["deleted"] == 2, stats)
check("移动 3 个", stats["moved"] == 3, stats)

jav_out = os.path.join(OUT_JAV, EXPECTED)
check("用户文件已改名并移动到 jav 目录", os.path.exists(jav_out), os.listdir(OUT_JAV) if os.path.isdir(OUT_JAV) else "目录不存在")
check("jav 目录内文件名正确", os.listdir(OUT_JAV) == [EXPECTED], os.listdir(OUT_JAV))

unc = os.listdir(OUT_UNCENSORED) if os.path.isdir(OUT_UNCENSORED) else []
check("无码文件投递正确（标记 -U）", unc == ["ABC-123-U-uncensored.mp4"], unc)

other = os.listdir(OUT_OTHER) if os.path.isdir(OUT_OTHER) else []
check("非JAV按关键字投递且不改名", other == ["My.Holiday.Documentary.2024.mp4"], other)

check("无规则文件保留在源目录", os.path.exists(os.path.join(SCAN, "no_rule.mp4")))
check("命中但无番号的文件保留", os.path.exists(os.path.join(SCAN, "restored_no_fanhao.mp4")))
check("小文件已删除", not os.path.exists(os.path.join(SCAN, "small.mp4")))
check("小 nfo 已删除", not os.path.exists(os.path.join(SCAN, "tiny.nfo")))
check("空的子目录已清理", not os.path.exists(os.path.join(SCAN, "sub")))

# --- D2: Dry Run 不做任何改动 ---
clean(SCAN, OUT_JAV)
touch(os.path.join(SCAN, "XYZ-999-restored.mp4"), 2 * MB)
stats = deliver.run_deliver(base_cfg(), dry_run=True)
check("Dry Run 标记 dry-run", all(d["action"] == "dry-run" for d in stats["details"]), stats["details"])
check("Dry Run 源文件仍在", os.path.exists(os.path.join(SCAN, "XYZ-999-restored.mp4")))
check("Dry Run 目标为空", os.listdir(OUT_JAV) == [], os.listdir(OUT_JAV))

# --- D3: 复制模式保留源文件 ---
clean(SCAN, OUT_JAV)
touch(os.path.join(SCAN, "CPY-111-restored.mp4"), 2 * MB)
copy_rules = [dict(RULES[0], action="copy")]
stats = deliver.run_deliver(base_cfg(RULES=copy_rules), dry_run=False)
check("复制模式统计 copied=1", stats["copied"] == 1, stats)
check("复制模式源文件仍在", os.path.exists(os.path.join(SCAN, "CPY-111-restored.mp4")))
check("复制模式目标存在且改名", os.path.exists(os.path.join(OUT_JAV, "CPY-111-U-restored.mp4")),
      os.listdir(OUT_JAV))

# --- D4: 覆盖模式 ---
clean(SCAN, OUT_JAV)
touch(os.path.join(OUT_JAV, "OVR-222-U-restored.mp4"), 5)
check("覆盖前旧文件存在", os.path.exists(os.path.join(OUT_JAV, "OVR-222-U-restored.mp4")))
touch(os.path.join(SCAN, "OVR-222-restored.mp4"), 2 * MB)
deliver.run_deliver(base_cfg(), dry_run=False)
p = os.path.join(OUT_JAV, "OVR-222-U-restored.mp4")
check("覆盖后文件被替换（体积变为 2MB）", os.path.exists(p) and os.path.getsize(p) == 2 * MB,
      os.path.getsize(p) if os.path.exists(p) else "缺失")
check("覆盖模式不产生 _1 副本", len(os.listdir(OUT_JAV)) == 1, os.listdir(OUT_JAV))

# --- D5: 不覆盖模式产生唯一名 ---
clean(SCAN, OUT_JAV)
touch(os.path.join(OUT_JAV, "NOV-333-U-restored.mp4"), 5)
touch(os.path.join(SCAN, "NOV-333-restored.mp4"), 2 * MB)
deliver.run_deliver(base_cfg(OVERWRITE=False), dry_run=False)
names = sorted(os.listdir(OUT_JAV))
check("不覆盖模式生成 _1 副本", names == ["NOV-333-U-restored.mp4", "NOV-333-U-restored_1.mp4"], names)

# --- D6: 体积阈值边界 ---
clean(SCAN, OUT_JAV)
touch(os.path.join(SCAN, "EXACT-444-restored.mp4"), 1 * MB)   # 正好等于阈值 -> 处理
touch(os.path.join(SCAN, "UNDER-555-restored.mp4"), MB - 1)   # 小于阈值 -> 删除
stats = deliver.run_deliver(base_cfg(MIN_SIZE_MB=1), dry_run=False)
check("等于阈值不被删除", os.path.exists(os.path.join(OUT_JAV, "EXACT-444-U-restored.mp4")))
check("小于阈值被删除", not os.path.exists(os.path.join(SCAN, "UNDER-555-restored.mp4")))

# --- D7: 未配置监控目录 ---
stats = deliver.run_deliver(base_cfg(SCAN_DIR=""), dry_run=False)
check("空监控目录返回失败", stats["success"] is False and "未配置" in stats["error"], stats)

stats = deliver.run_deliver(base_cfg(SCAN_DIR=os.path.join(TMP, "not_exist")), dry_run=False)
check("目录不存在返回失败", stats["success"] is False, stats)

# ===========================================================================
print("\n=== E. API ===")
r = client.get("/api/deliver/config")
d = r.get_json()
check("GET config 成功", d["success"] is True and "config" in d)

payload = {
    "ENABLED": True,
    "SCAN_DIR": SCAN,
    "MIN_SIZE_MB": 50,
    "RULES": RULES,
}
r = client.post("/api/deliver/config", json=payload)
d = r.get_json()
check("POST config 保存成功", d["success"] is True, d)
check("配置已落盘", os.path.exists(os.path.join(CONFIG_DIR, "deliver.json")))
saved = json.load(open(os.path.join(CONFIG_DIR, "deliver.json"), encoding="utf-8"))
check("MIN_SIZE_MB 保存正确", saved["MIN_SIZE_MB"] == 50, saved.get("MIN_SIZE_MB"))
check("规则数保存正确", len(saved["RULES"]) == 3, len(saved.get("RULES", [])))

# 关键字字符串形式的规范化
r = client.post("/api/deliver/config", json={"RULES": [dict(RULES[0], keywords="restored，uncensored, foo")]})
saved = r.get_json()["config"]
check("逗号/中文逗号分隔的关键字被拆分",
      saved["RULES"][0]["keywords"] == ["restored", "uncensored", "foo"], saved["RULES"][0]["keywords"])

# 预览接口
r = client.post("/api/deliver/preview", json={"files": [USER_FILE, "plain.mp4"], "keywords": ["restored"], "mark": "-U"})
d = r.get_json()
check("预览接口成功", d["success"] is True and len(d["results"]) == 2)
check("预览输出与预期一致", d["results"][0]["output"] == EXPECTED, d["results"][0]["output"])
check("预览识别番号", d["results"][0]["fanhao"].upper() == "DASS-787")
check("预览未命中不改名", d["results"][1]["output"] == "plain.mp4")

# 目录浏览
r = client.get(f"/api/deliver/browse?path={TMP}")
d = r.get_json()
check("浏览目录成功", d["success"] is True, d)
check("可看到 inbox", any(x["name"] == "inbox" for x in d["dirs"]), d.get("dirs"))

# 立即执行
clean(SCAN, OUT_JAV)
touch(os.path.join(SCAN, "RUN-777-restored.mp4"), 2 * MB)
r = client.post("/api/deliver/run", json={"dry_run": False, "MIN_SIZE_MB": 1, "RULES": RULES, "SCAN_DIR": SCAN})
d = r.get_json()
check("run 接口成功", d["success"] is True, d)
check("run 实际移动文件", os.path.exists(os.path.join(OUT_JAV, "RUN-777-U-restored.mp4")))

r = client.post("/api/deliver/run", json={"dry_run": True, "SCAN_DIR": SCAN, "MIN_SIZE_MB": 1, "RULES": RULES})
check("run 支持 dry_run", r.get_json()["dry_run"] is True)

# 状态接口
r = client.get("/api/deliver/status")
d = r.get_json()
check("status 返回 watcher", d["success"] is True and "watcher" in d)

# ===========================================================================
print("\n=== F. 欧美番号（厂牌.年.月.日）===")
WEST_FILE = (
    "BlackedRaw.26.09.23.Delilah.Dagger.And.Julia.James.Fresh.Faces."
    "Get.Their.Pussies.Stretched.XXX.1080p.MP4-P2P[XC].mp4"
)

w = naming.find_western(WEST_FILE)
check("识别欧美厂牌 BlackedRaw", w and w["studio"] == "BlackedRaw", w)
check("识别日期 26.09.23", w and w["date"] == "26.09.23", w)
check("4 位年份也支持", (naming.find_western("Tushy.2024.01.05.Title.mp4") or {}).get("date") == "2024.01.05")

wmarked = naming.insert_mark_after_western(WEST_FILE, "-U")
EXPECTED_W = WEST_FILE.replace("26.09.23", "26.09.23-U")
check("底层函数仍支持日期后加标记", wmarked == EXPECTED_W, f"\n    got: {wmarked}\n    exp: {EXPECTED_W}")
check("欧美标记幂等", naming.insert_mark_after_western(wmarked, "-U") == wmarked)
check("欧美不加标记时原样返回", naming.insert_mark_after_western(WEST_FILE, "") == WEST_FILE)

# 按需求：欧美「规则」不提供标记功能，即使配置里填了 mark 也会被清空
_wcfg = deliver.normalize_deliver_config(
    {"RULES": [{"kind": "western", "keywords": ["blackedraw"], "mark": "-U", "target_dir": "/x"}]}
)
check("欧美规则配置层清除标记", _wcfg["RULES"][0]["mark"] == "", _wcfg["RULES"][0])

check("JAV 文件不误判为欧美", naming.find_western("DASS-787.mp4") is None)
check("普通影片不误判为欧美", naming.find_western("Some.Movie.2024.1080p.mp4") is None)

# ===========================================================================
print("\n=== G. 投递引擎：欧美规则 + 兜底 ===")
OUT_WEST = os.path.join(TMP, "out", "western")
OUT_FALLBACK = os.path.join(TMP, "out", "fallback")
for d in (OUT_WEST, OUT_FALLBACK):
    os.makedirs(d, exist_ok=True)

WEST_RULES = [
    {
        "id": "w1", "name": "欧美厂牌", "kind": "western", "enabled": True,
        "keywords": ["blackedraw", "tushy", "vixen"], "mark": "-U",
        "target_dir": OUT_WEST, "action": "move",
    },
    {
        "id": "f1", "name": "兜底", "kind": "fallback", "enabled": True,
        "keywords": [], "mark": "", "target_dir": OUT_FALLBACK, "action": "move",
    },
]

# --- G1: 厂牌命中 -> 投递到欧美目录 ---
clean(SCAN, OUT_WEST, OUT_FALLBACK)
touch(os.path.join(SCAN, WEST_FILE), 2 * MB)
stats = deliver.run_deliver(base_cfg(RULES=WEST_RULES, MIN_SIZE_MB=1), dry_run=False)
west_files = os.listdir(OUT_WEST) if os.path.isdir(OUT_WEST) else []
check("欧美文件投递到 western 目录（不改名）", west_files == [WEST_FILE], west_files)
check("欧美文件未进兜底目录", os.listdir(OUT_FALLBACK) == [], os.listdir(OUT_FALLBACK))

# --- G2: 厂牌不匹配 -> 落兜底 ---
clean(SCAN, OUT_WEST, OUT_FALLBACK)
OTHER_WEST = "Brazzers.26.01.02.Some.Title.XXX.1080p.mp4"
touch(os.path.join(SCAN, OTHER_WEST), 2 * MB)
stats = deliver.run_deliver(base_cfg(RULES=WEST_RULES, MIN_SIZE_MB=1), dry_run=False)
check("未命中厂牌的文件进兜底目录", os.listdir(OUT_FALLBACK) == [OTHER_WEST], os.listdir(OUT_FALLBACK))
check("兜底不改名", os.listdir(OUT_FALLBACK) == [OTHER_WEST])

# --- G3: 兜底只处理媒体文件且过阈值 ---
clean(SCAN, OUT_WEST, OUT_FALLBACK)
touch(os.path.join(SCAN, "no_kw_video.mp4"), 2 * MB)      # 媒体 + 过阈值 -> 兜底
touch(os.path.join(SCAN, "note.txt"), 2 * MB)             # 非媒体 -> 不兜底（体积够）
stats = deliver.run_deliver(base_cfg(RULES=WEST_RULES, MIN_SIZE_MB=1), dry_run=False)
check("兜底转走媒体文件", os.path.exists(os.path.join(OUT_FALLBACK, "no_kw_video.mp4")))
check("兜底不动非媒体文件", os.path.exists(os.path.join(SCAN, "note.txt")))
check("兜底不动小文件（已被删除）", not os.path.exists(os.path.join(SCAN, "small.mp4")))

# --- G4: 关键字规则优先于兜底（顺序决定） ---
clean(SCAN, OUT_WEST, OUT_FALLBACK, OUT_JAV)
jav_first = [
    dict(RULES[0], keywords=["restored"], target_dir=OUT_JAV),   # restored
    dict(WEST_RULES[1]),                                          # 兜底在后
]
touch(os.path.join(SCAN, "ABC-123-restored.mp4"), 2 * MB)
deliver.run_deliver(base_cfg(RULES=jav_first, MIN_SIZE_MB=1), dry_run=False)
check("命中关键字时优先走关键字规则（不进兜底）",
      os.path.exists(os.path.join(OUT_JAV, "ABC-123-U-restored.mp4"))
      and os.listdir(OUT_FALLBACK) == [],
      {"jav": os.listdir(OUT_JAV), "fb": os.listdir(OUT_FALLBACK)})

# --- G5: 顺序颠倒 -> 结论应该一致（关键字规则仍优先于 fallback）---
clean(SCAN, OUT_WEST, OUT_FALLBACK, OUT_JAV)
fallback_first = [dict(WEST_RULES[1]), dict(RULES[0], keywords=["restored"], target_dir=OUT_JAV)]
touch(os.path.join(SCAN, "DEF-456-restored.mp4"), 2 * MB)
deliver.run_deliver(base_cfg(RULES=fallback_first, MIN_SIZE_MB=1), dry_run=False)
check("兜底排在前面也不会抢占关键字命中",
      os.path.exists(os.path.join(OUT_JAV, "DEF-456-U-restored.mp4")),
      {"jav": os.listdir(OUT_JAV), "fb": os.listdir(OUT_FALLBACK)})

# --- G6: 两条关键字规则顺序不同 -> 结果不同（验证「顺序在上先执行」）---
clean(SCAN, OUT_JAV, OUT_WEST, OUT_FALLBACK)
BOTH = "BlackedRaw.26.09.23.restored.Title.mp4"   # 同时命中 restored 与 blackedraw
r_west_first = [
    dict(WEST_RULES[0]),
    dict(RULES[0], keywords=["restored"], target_dir=OUT_JAV),
]
touch(os.path.join(SCAN, BOTH), 2 * MB)
deliver.run_deliver(base_cfg(RULES=r_west_first, MIN_SIZE_MB=1), dry_run=False)
check("顺序：欧美规则在前 -> 投到欧美目录（不改名）",
      os.path.exists(os.path.join(OUT_WEST, BOTH)),
      {"west": os.listdir(OUT_WEST), "jav": os.listdir(OUT_JAV)})

clean(SCAN, OUT_JAV, OUT_WEST, OUT_FALLBACK)
r_jav_first = [
    dict(RULES[0], keywords=["restored"], target_dir=OUT_JAV),
    dict(WEST_RULES[0]),
]
touch(os.path.join(SCAN, BOTH), 2 * MB)
deliver.run_deliver(base_cfg(RULES=r_jav_first, MIN_SIZE_MB=1), dry_run=False)
# 关键行为：JAV 规则虽排在前面且命中关键字，但该文件无日本番号 -> 不适用，
# 应当继续尝试下一条（欧美规则）而不是丢弃，最终投到欧美目录
check("JAV 规则不适用时继续尝试下一条（投到欧美目录）",
      os.path.exists(os.path.join(OUT_WEST, BOTH)),
      {"west": os.listdir(OUT_WEST), "jav": os.listdir(OUT_JAV)})
check("JAV 规则未误收无番号文件", os.listdir(OUT_JAV) == [], os.listdir(OUT_JAV))

# --- G7: 不填标记则不改名 ---
clean(SCAN, OUT_WEST, OUT_FALLBACK)
no_mark_rules = [dict(WEST_RULES[0], mark="")]
touch(os.path.join(SCAN, WEST_FILE), 2 * MB)
deliver.run_deliver(base_cfg(RULES=no_mark_rules, MIN_SIZE_MB=1), dry_run=False)
check("欧美规则不填标记时保持原名", WEST_FILE in os.listdir(OUT_WEST), os.listdir(OUT_WEST))

# --- G8: 禁用兜底规则 -> 未命中时保留原处 ---
clean(SCAN, OUT_WEST, OUT_FALLBACK)
disabled_fb = [dict(WEST_RULES[0]), dict(WEST_RULES[1], enabled=False)]
touch(os.path.join(SCAN, "orphan.mp4"), 2 * MB)
deliver.run_deliver(base_cfg(RULES=disabled_fb, MIN_SIZE_MB=1), dry_run=False)
check("兜底被禁用时不转走", os.path.exists(os.path.join(SCAN, "orphan.mp4")))

# --- G9: 预览接口支持 rules 与欧美类型 ---
r = client.post("/api/deliver/preview", json={"files": [WEST_FILE], "rules": WEST_RULES})
d = r.get_json()
check("预览接口识别欧美", d["results"][0]["western_studio"] == "BlackedRaw", d["results"][0])
check("预览输出欧美不改名", d["results"][0]["output"] == WEST_FILE, d["results"][0]["output"])
check("预览标记 rule_kind=western", d["results"][0]["rule_kind"] == "western")

r = client.post("/api/deliver/preview", json={"files": ["plain.mp4"], "rules": WEST_RULES})
d = r.get_json()
check("预览未命中标记 fallback=True", d["results"][0]["fallback"] is True, d["results"][0])

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
