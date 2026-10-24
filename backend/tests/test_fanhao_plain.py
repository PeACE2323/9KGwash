"""番号识别规则的测试（含无连字符写法）。

覆盖：
  A. 规范写法（带连字符）行为不变 —— 与原脚本一致
  B. 无连字符写法识别：katu00118 / ABP00123 / katu00118hhb
  C. 防误判：hhd800、1080p、x264、4k 等伪番号
  D. 标记插入：保留原写法，数字后字母作后缀
  E. 洗板同一性：katu00118 与 KATU-118 视为同一番号
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_fanhao")
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
            "DB_LEADING": True,
            "IGNORE_LEADING_DIGITS": True,
        },
        f,
        ensure_ascii=False,
    )

from app import config  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import fanhao, naming  # noqa: E402

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


def fh(name):
    info = naming.find_fanhao(name)
    return info["fanhao"] if info else None


# ===========================================================================
print("\n=== A. 规范写法行为不变 ===")
check("DASS-787", fh("DASS-787.mp4") == "DASS-787")
check("带前后缀", fh("www.98T.la@DASS-787無防備.mp4") == "DASS-787")
check("分盘后缀", fh("ABP-159-C.mp4") == "ABP-159")
check("前置数字厂牌", fh("259LUXU-1900.mp4") == "259LUXU-1900")
check("方括号前缀", fh("[ThZu.Cc]ABP-159-C.mp4") == "ABP-159")
check("欧美不被当番号", fh("BlackedRaw.26.09.23.Title.mp4") is None)

# ===========================================================================
print("\n=== B. 无连字符写法 ===")
INFO = "katu00118hhb_restored"
info = naming.find_fanhao(INFO)
check("katu00118hhb 识别为 katu00118", info and info["fanhao"] == "katu00118", info)
check("后缀 hhb 被单独记录", info and info["suffix"] == "hhb", info.get("suffix") if info else None)
check("style 标记为 plain", info and info["style"] == "plain")
check("katu00118 无后缀", naming.find_fanhao("katu00118.mp4")["suffix"] == "")
check("ABP00123", fh("ABP00123.mp4") == "ABP00123")
check("SSIS00888-C", fh("SSIS00888-C.mp4") == "SSIS00888")
check("大写保留原样", fh("KATU00118.mp4") == "KATU00118")

# ===========================================================================
print("\n=== C. 防误判 ===")
check("hhd800.com@SSIS-088 取规范番号",
      fh("hhd800.com@SSIS-088.mp4") == "SSIS-088", fh("hhd800.com@SSIS-088.mp4"))
check("video1080p 非番号", fh("video1080p.mp4") is None, fh("video1080p.mp4"))
check("x264 非番号", fh("some.web.1080p.x264.mp4") is None, fh("some.web.1080p.x264.mp4"))
check("restored_4k 非番号", fh("restored_4k.mp4") is None, fh("restored_4k.mp4"))
check("纯文字非番号", fh("hello world.mp4") is None)
check("纯数字非番号", fh("123456.mp4") is None)
check("www 开头不误判", fh("www.example.com.mp4") is None, fh("www.example.com.mp4"))
check("MP4 不误判", fh("MP4.mp4") is None)

# ===========================================================================
print("\n=== D. 标记插入 ===")
F = "katu00118hhb_restored.mp4"
after = naming.apply_mark_to_fanhao(F, "-U", "after")
before = naming.apply_mark_to_fanhao(F, "U", "before")
check("后置标记：加在数字后、后缀前", after == "katu00118-Uhhb_restored.mp4", after)
check("前置标记：加在番号前", before == "U-katu00118hhb_restored.mp4", before)
check("后置幂等", naming.apply_mark_to_fanhao(after, "-U", "after") == after)

F2 = "katu00118.mp4"
check("无后缀后置", naming.apply_mark_to_fanhao(F2, "-U", "after") == "katu00118-U.mp4",
      naming.apply_mark_to_fanhao(F2, "-U", "after"))
check("规范写法不受影响",
      naming.apply_mark_to_fanhao("DASS-787.mp4", "-U", "after") == "DASS-787-U.mp4")

# ===========================================================================
print("\n=== E. 洗板同一性 ===")
a = fanhao.get_weight_and_fanhao("katu00118hhb_restored.mp4")
b = fanhao.get_weight_and_fanhao("KATU-118.mp4")
check("katu00118 归一为 KATU-118", a[0] == "KATU-118", a[0])
check("与 KATU-118 同一番号", a[0] == b[0], (a[0], b[0]))
check("无连字符文件权重正常（非 -1）", a[1] >= 0, a[1])
check("ABP00123-C 权重与 ABP-123-C 一致",
      fanhao.get_weight_and_fanhao("ABP00123-C.mp4")[1]
      == fanhao.get_weight_and_fanhao("ABP-123-C.mp4")[1] == 2)
check("is_jav 支持无连字符", fanhao.is_jav("katu00118"))
check("is_jav 仍拒非番号", not fanhao.is_jav("hello world"))

# 原有番号权重未变
check("ABF-391-破解 权重 1", fanhao.get_weight_and_fanhao("ABF-391-破解.mp4")[1] == 1)
check("ABF-391-C 权重 2", fanhao.get_weight_and_fanhao("ABF-391-C.mp4")[1] == 2)
check("ABF-391-UC 权重 3", fanhao.get_weight_and_fanhao("ABF-391-UC.mp4")[1] == 3)
check("ABF-391 权重 0", fanhao.get_weight_and_fanhao("ABF-391.mp4")[1] == 0)
check("100ABC-123 归一 ABC-123",
      fanhao.get_weight_and_fanhao("100ABC-123.mp4")[0] == "ABC-123")

# ===========================================================================
print("\n=== F. 投递规则端到端 ===")
groups = [{
    "name": "有码破解", "kind": "jav", "target_dir": "/out", "action": "move", "enabled": True,
    "children": [{"keywords": ["restored"], "mark": "-U", "mark_position": "after", "enabled": True}],
}]
r = client.post("/api/deliver/preview", json={"files": [F, "DASS-787.mp4"], "groups": groups})
d = r.get_json()
outs = [x["output"] for x in d["results"]]
check("预览：katu00118 被识别并改名", outs[0] == "katu00118-Uhhb_restored.mp4", outs)
check("预览：返回番号", d["results"][0]["fanhao"] == "katu00118", d["results"][0]["fanhao"])
check("预览：规范写法不受影响", outs[1] == "DASS-787.mp4", outs)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
