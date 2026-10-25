"""关键字与画质标识冲突的测试。

问题场景（用户实际遇到）：
    RKI413.FHD_restored.mp4
    组[JAV无码] 的关键字 FH，命中了画质标识 .FHD 中的 FH，
    导致文件被错误链式投递到「无码」组。

修复原则：关键字匹配前先抹掉画质/编码标识（FHD / 1080p / x264 ...），
但用户显式把画质词本身配成关键字时仍应生效。
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_quality")
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
            "IGNORE_LEADING_DIGITS": True,
        },
        f,
        ensure_ascii=False,
    )

from app import config  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import deliver, naming  # noqa: E402

app = create_app()
config.init_config()
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
F = "RKI413.FHD_restored.mp4"

# ===========================================================================
print("\n=== A. 画质标识抹除 ===")
sq = naming.strip_quality_tokens
check("FHD 被抹掉", "fhd" not in sq(F).lower(), sq(F))
check("1080p 被抹掉", "1080p" not in sq("a.1080p.b.mp4").lower(), sq("a.1080p.b.mp4"))
check("x264 被抹掉", "x264" not in sq("a.x264.b.mp4").lower(), sq("a.x264.b.mp4"))
check("hevc 被抹掉", "hevc" not in sq("a.HEVC.b.mp4").lower(), sq("a.HEVC.b.mp4"))
check("4k 被抹掉", "4k" not in sq("a.4K.b.mp4").lower(), sq("a.4K.b.mp4"))
check("普通词不被误抹", "restored" in sq(F).lower(), sq(F))
check("番号不被误抹", "rki413" in sq(F).lower(), sq(F))
check("中文不被误抹", "破解" in sq("ABP-1-破解.mp4"), sq("ABP-1-破解.mp4"))
check("FHDxxx 这种非独立词不抹", "fhd" in sq("FHDxx.mp4").lower(), sq("FHDxx.mp4"))

# ===========================================================================
print("\n=== B. 关键字不再误命中画质标识 ===")
check("FH 不命中 .FHD", naming.match_any_keyword(F, ["FH"]) is None,
      naming.match_any_keyword(F, ["FH"]))
check("HD 不命中 .FHD", naming.match_any_keyword(F, ["HD"]) is None,
      naming.match_any_keyword(F, ["HD"]))
check("FHD 不命中（关键字是整个画质词但非独立出现）",
      naming.match_any_keyword(F, ["FHD"]) == "FHD")  # FHD 是独立词，应命中
check("SD 不命中 1080p 场景", naming.match_any_keyword("a.1080p.b.mp4", ["SD"]) is None)

# 用户真实关键字列表（节选）
UNC_KW = [
    "LAF", "LAFBD", "SMD", "SMBD", "CWP", "CWPBD", "CZ", "CZBD", "SKY", "SKYHD",
    "HEYZO", "EMP", "MKBD", "MCBD", "FH", "XXX-AV", "MXX", "SHD", "MKD",
    "1Pondo", "Caribbeancom", "加勒比", "carib", "カリビアンコム",
]
hit = naming.match_any_keyword(F, UNC_KW)
check("用户无码关键字列表不再命中该文件", hit is None, hit)

# ===========================================================================
print("\n=== C. 正常关键字仍生效 ===")
check("restored 命中", naming.match_any_keyword(F, ["restored"]) == "restored")
check("FH 独立出现仍命中", naming.match_any_keyword("abc-FH-123.mp4", ["FH"]) == "FH",
      naming.match_any_keyword("abc-FH-123.mp4", ["FH"]))
check("FH 开头命中", naming.match_any_keyword("FH.mp4", ["FH"]) == "FH")
check("FH 后接字母命中（非画质场景）",
      naming.match_any_keyword("some_FH_video.mp4", ["FH"]) == "FH")
check("LAF 正常命中", naming.match_any_keyword("LAF-123.mp4", ["LAF"]) == "LAF")
check("HEYZO 正常命中", naming.match_any_keyword("HEYZO-1234.mp4", ["HEYZO"]) == "HEYZO")
check("加勒比 正常命中", naming.match_any_keyword("加勒比-001.mp4", ["加勒比"]) == "加勒比")
check("carib 大小写不敏感", naming.match_any_keyword("Carib-001.mp4", ["carib"]) == "carib")

print("\n=== D. 显式配置画质词仍可筛选 ===")
check("1080p 作为关键字生效",
      naming.match_any_keyword("a.1080p.x264.mp4", ["1080p"]) == "1080p")
check("FHD 作为关键字生效",
      naming.match_any_keyword("a.FHD.b.mp4", ["FHD"]) == "FHD")
check("720p 作为关键字生效",
      naming.match_any_keyword("a.720p.mp4", ["720p"]) == "720p")

# ===========================================================================
print("\n=== E. 端到端：用户真实配置 ===")
INBOX = os.path.join(TMP, "inbox")
OUT_CRACK = os.path.join(TMP, "out", "crack")
OUT_UNC = os.path.join(TMP, "out", "unc")
shutil.rmtree(INBOX, ignore_errors=True)
os.makedirs(INBOX, exist_ok=True)
for d in (OUT_CRACK, OUT_UNC):
    os.makedirs(d, exist_ok=True)

with open(os.path.join(INBOX, F), "wb") as f:
    f.write(b"0" * (3 * MB))

GROUPS = [
    {"name": "有码破解", "kind": "jav", "target_dir": OUT_CRACK, "action": "move", "enabled": True,
     "children": [{"keywords": ["restored"], "mark": "-破解", "mark_position": "after", "enabled": True}]},
    {"name": "JAV无码", "kind": "other", "target_dir": OUT_UNC, "action": "move", "enabled": True,
     "children": [{"keywords": UNC_KW, "mark": "", "mark_position": "after", "enabled": True}]},
]
stats = deliver.run_deliver(
    {"SCAN_DIR": INBOX, "MIN_SIZE_MB": 1, "SETTLE_SECONDS": 0, "OVERWRITE": True, "GROUPS": GROUPS},
    dry_run=False,
)
check("处理 1 个文件", stats["moved"] == 1, stats)

crack_files = os.listdir(OUT_CRACK)
unc_files = os.listdir(OUT_UNC)
check("投递到「破解」目录", crack_files == ["RKI413-破解.FHD_restored.mp4"], crack_files)
check("「无码」目录为空（不再误投）", unc_files == [], unc_files)
check("标记位置正确（番号后）", "RKI413-破解.FHD" in crack_files[0], crack_files)

# 预览接口同样正确
r = client.post("/api/deliver/preview", json={"files": [F], "groups": GROUPS})
res = r.get_json()["results"][0]
check("预览命中组为「有码破解」", res["matched_group"] == "有码破解", res["matched_group"])
check("预览输出名字正确", res["output"] == "RKI413-破解.FHD_restored.mp4", res["output"])
check("预览只命中 1 组", res["hit_count"] == 1, res["hit_count"])

# 真正无码文件仍应命中
r = client.post("/api/deliver/preview", json={"files": ["LAF-123.mp4"], "groups": GROUPS})
res = r.get_json()["results"][0]
check("真正无码文件仍命中「JAV无码」", res["matched_group"] == "JAV无码", res["matched_group"])

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
