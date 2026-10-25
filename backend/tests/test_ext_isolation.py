"""洗板「扩展名隔离」规则的测试。

背景（用户实际事故 IPZZ-937）：
    库中同时存在 IPZZ-937-C.mp4 与 IPZZ-937-C.mkv。
    CMS 按 115 目录逐文件生成 STRM，Emby 把同主干的 .mp4/.mkv 视作同一影片项。
    洗板删掉其中一个后，CMS 会删掉它对应的 STRM，Emby 又移除整个 Movie 项，
    导致另一个版本的 STRM 也被连带清空 —— 表现为「影片消失、只剩元数据」。

修复：按扩展名分桶，每个扩展名内部各自选王者，扩展名之间互不淘汰。
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_extiso")
if os.path.isdir(TMP):
    shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(TMP, exist_ok=True)
CFG = os.path.join(TMP, "config")
os.makedirs(CFG, exist_ok=True)

os.environ["XIBAN_CONFIG_DIR"] = CFG
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"

with open(os.path.join(CFG, "config.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": os.path.join(TMP, "media").replace("\\", "/"),
            "METADATA_BASE_PATH": os.path.join(TMP, "meta").replace("\\", "/"),
            "WATCHED_DIRS": ["/115open/9KG/STRM"],
            "DRY_RUN": False,
            "IGNORE_LEADING_DIGITS": True,
        },
        f,
        ensure_ascii=False,
    )

from app import config  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import washer  # noqa: E402

app = create_app()
config.init_config()

MOUNT = os.path.join(TMP, "media", "115open", "9KG", "STRM")
META = os.path.join(TMP, "meta", "9KG", "STRM")

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def touch(p):
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "wb") as f:
        f.write(b"0" * 512)
    return p


def clean():
    for d in (MOUNT, META):
        if os.path.isdir(d):
            shutil.rmtree(d, ignore_errors=True)


def run(folder, trigger, files):
    clean()
    d = os.path.join(MOUNT, folder)
    for f in files:
        touch(os.path.join(d, f))
    washer.process_xiban(os.path.join(d, trigger))
    return sorted(os.listdir(d))


# ===========================================================================
print("\n=== A. 同名不同扩展名：都保留（IPZZ-937 事故） ===")
files = run("IPZZ-937樱空桃", "IPZZ-937-C.mkv", ["IPZZ-937-C.mp4", "IPZZ-937-C.mkv"])
check("mp4 与 mkv 都保留", files == ["IPZZ-937-C.mkv", "IPZZ-937-C.mp4"], files)

files = run("ABC-100", "ABC-100-C.mkv",
            ["ABC-100-C.mp4", "ABC-100-C.mkv", "ABC-100-C.avi"])
check("三种扩展名都保留", len(files) == 3, files)

print("\n=== B. 不同扩展名 + 不同权重：都保留 ===")
files = run("ABC-200", "ABC-200-C.mkv", ["ABC-200.mp4", "ABC-200-C.mkv"])
check("低权 mp4 与高权 mkv 都保留", files == ["ABC-200-C.mkv", "ABC-200.mp4"], files)

print("\n=== C. 同扩展名内仍然正常淘汰（低优画质） ===")
files = run("ABC-300", "ABC-300-C.mp4", ["ABC-300.mp4", "ABC-300-C.mp4"])
check("同扩展名内低权重被删", files == ["ABC-300-C.mp4"], files)

files = run("ABC-301", "ABC-301-C.mp4", ["ABC-301-C.mp4", "ABC-301-UC.mp4"])
check("UC(权重3) 击败 C(权重2)", files == ["ABC-301-UC.mp4"], files)

print("\n=== D. 同扩展名同权重：同质单体只删 json ===")
clean()
d = os.path.join(MOUNT, "ABC-400")
touch(os.path.join(d, "ABC-400-C.mp4"))
touch(os.path.join(d, "ABC-400-C.mkv"))
# 同目录下制造同权重同扩展名冲突：用 mkv 另一个版本
touch(os.path.join(d, "ABC-400-C2.mkv"))  # 不同主干，不算同版本
md = os.path.join(META, "ABC-400")
touch(os.path.join(md, "ABC-400-C.strm"))
touch(os.path.join(md, "ABC-400-C.json"))
washer.process_xiban(os.path.join(d, "ABC-400-C.mp4"))
after_d = sorted(os.listdir(d))
check("同目录不同扩展名均保留", "ABC-400-C.mp4" in after_d and "ABC-400-C.mkv" in after_d, after_d)

print("\n=== E. 跨目录 + 跨扩展名 ===")
clean()
touch(os.path.join(MOUNT, "ABC-500", "ABC-500-C.mp4"))
touch(os.path.join(MOUNT, "ABC-500-C", "ABC-500-C.mkv"))
washer.process_xiban(os.path.join(MOUNT, "ABC-500-C", "ABC-500-C.mkv"))
a = os.path.isdir(os.path.join(MOUNT, "ABC-500"))
b = os.path.isdir(os.path.join(MOUNT, "ABC-500-C"))
check("跨目录不同扩展名均保留", a and b, (a, b))

print("\n=== F. 单扩展名多版本（原有行为不变） ===")
files = run("ABC-600", "ABC-600-C.mp4",
            ["ABC-600.mp4", "ABC-600-C.mp4", "ABC-600-UC.mp4"])
check("同扩展名只留最高权重", files == ["ABC-600-UC.mp4"], files)

print("\n=== G. 单文件不触发任何删除 ===")
files = run("ABC-700", "ABC-700-C.mp4", ["ABC-700-C.mp4"])
check("单文件原样保留", files == ["ABC-700-C.mp4"], files)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
