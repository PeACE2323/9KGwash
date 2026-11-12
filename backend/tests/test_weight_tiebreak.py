"""同权重（同级别）时的决胜规则：新文件取代旧文件。

规则：
  权重相同 -> 比较**文件修改时间**（新的赢）
            -> 文件时间也相同 -> 比较所在目录时间（新的赢）

旧实现是「目录时间优先」，遇到「目录新但文件旧」会误留旧文件，本测试锁定新规则。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_tiebreak")
shutil.rmtree(TMP, ignore_errors=True)
MOUNT = os.path.join(TMP, "cloud", "115open")
METAROOT = os.path.join(TMP, "meta")
CFG = os.path.join(TMP, "cfg")
os.makedirs(CFG)
SUB = os.path.join("9KG", "STRM")

os.environ["XIBAN_CONFIG_DIR"] = CFG
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"

with open(os.path.join(CFG, "config.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": MOUNT.replace("\\", "/"),
            "METADATA_BASE_PATH": METAROOT.replace("\\", "/"),
            "WATCHED_DIRS": ["/9KG/STRM"],
            "META_SUBDIR_OVERRIDE": "/STRM",
            "DRY_RUN": False,
        },
        f,
        ensure_ascii=False,
    )

from app import config  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import fanhao, washer  # noqa: E402

app = create_app()
config.init_config()

PASS = FAIL = 0
NOW = time.time()


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def mk(dirname, filename, dir_age_days, file_age_days):
    """建一个文件，并分别设置目录时间与文件时间。"""
    p = os.path.join(MOUNT, SUB, dirname)
    os.makedirs(p, exist_ok=True)
    fp = os.path.join(p, filename)
    with open(fp, "wb") as fh:
        fh.write(b"0" * 64)
    t_file = NOW - file_age_days * 86400
    t_dir = NOW - dir_age_days * 86400
    os.utime(fp, (t_file, t_file))
    os.utime(p, (t_dir, t_dir))
    return fp


def reset():
    shutil.rmtree(MOUNT, ignore_errors=True)


# ===========================================================================
print("\n=== A. 同权重 + 不同目录：文件新的赢 ===")
reset()
old_f = mk("ABF-500-C", "ABF-500-C.mp4", 10, 10)        # 都旧
new_f = mk("ABF-500-中字", "ABF-500-中字.mp4", 1, 1)     # 都新

_, w1, _ = fanhao.get_weight_and_fanhao(os.path.basename(old_f))
_, w2, _ = fanhao.get_weight_and_fanhao(os.path.basename(new_f))
check("两个文件权重相同（都是字幕档）", w1 == w2, (w1, w2))

washer.process_xiban(new_f)
check("旧文件被清理", not os.path.exists(old_f))
check("新文件保留", os.path.exists(new_f))

# ===========================================================================
print("\n=== B. 目录时间与文件时间矛盾时：以文件时间为准 ===")
reset()
# A: 目录很旧(30天)，文件很新(1天)
a = mk("ABF-700-C", "ABF-700-C.mp4", 30, 1)
# B: 目录很新(1天)，文件很旧(30天)
b = mk("ABF-700-中字", "ABF-700-中字.mp4", 1, 30)

washer.process_xiban(b)
check("文件新的 A 保留（不再被目录时间误导）", os.path.exists(a))
check("文件旧的 B 被清理", not os.path.exists(b))

# ===========================================================================
print("\n=== C. 文件时间相同：回退比较目录时间 ===")
reset()
d_old = os.path.join(MOUNT, SUB, "ABF-800-C")
d_new = os.path.join(MOUNT, SUB, "ABF-800-中字")
os.makedirs(d_old, exist_ok=True)
os.makedirs(d_new, exist_ok=True)
same = NOW - 5 * 86400
f_old = os.path.join(d_old, "ABF-800-C.mp4")
f_new = os.path.join(d_new, "ABF-800-中字.mp4")
for fp in (f_old, f_new):
    with open(fp, "wb") as fh:
        fh.write(b"0" * 64)
    os.utime(fp, (same, same))          # 文件时间完全相同
os.utime(d_old, (NOW - 10 * 86400,) * 2)  # 旧目录
os.utime(d_new, (NOW - 1 * 86400,) * 2)   # 新目录

washer.process_xiban(f_new)
check("目录较新的那个保留", os.path.exists(f_new))
check("目录较旧的那个被清理", not os.path.exists(f_old))

# ===========================================================================
print("\n=== D. sort_key 与 washer 排序一致 ===")
sa = {"weight": 2, "folder_mtime": 300, "file_mtime": 100}
sb = {"weight": 2, "folder_mtime": 100, "file_mtime": 300}
best = sorted([sa, sb], key=fanhao.sort_key, reverse=True)[0]
check("sort_key：文件时间新的排在前面", best is sb, best)
check("sort_key：权重优先于时间",
      sorted(
          [{"weight": 1, "folder_mtime": 999, "file_mtime": 999},
           {"weight": 3, "folder_mtime": 1, "file_mtime": 1}],
          key=fanhao.sort_key, reverse=True,
      )[0]["weight"] == 3)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
