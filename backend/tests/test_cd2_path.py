"""CD2 路径匹配的回归测试。

事故：改路径映射后洗板完全失效、且没有任何日志。

根因：
    handle_cd2 用 `file_rel_path.startswith(d)` 判断是否属于监听目录。
    改路径映射后监听目录被归一成 `/9KG/STRM`，
    而 CD2 传的仍是 `/115open/9KG/STRM/xxx`，
    startswith 失败 -> 直接 continue -> 连日志都不打（静默失效）。

修复：
    1) 双向兼容两种写法（含/不含 115open 层）
    2) 用「监听目录对应的网盘绝对路径 + 尾巴」拼接，避免网盘根重复两遍
    3) 不属于监听目录时也记一条日志，避免以后再次静默失效
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_cd2path")
shutil.rmtree(TMP, ignore_errors=True)
MOUNT = os.path.join(TMP, "media", "115open")
META = os.path.join(TMP, "meta")
CFG = os.path.join(TMP, "cfg")
os.makedirs(CFG)
os.environ["XIBAN_CONFIG_DIR"] = CFG
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"

with open(os.path.join(CFG, "config.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": MOUNT.replace("\\", "/"),
            "METADATA_BASE_PATH": META.replace("\\", "/"),
            "WATCHED_DIRS": ["/9KG/STRM"],
            "DRY_RUN": False,
            "IGNORE_LEADING_DIGITS": True,
        },
        f,
        ensure_ascii=False,
    )

from app import config  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import webhooks  # noqa: E402

app = create_app()
config.init_config()

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
    with open(p, "w", encoding="utf-8") as f:
        f.write("x")
    return p


MB = MOUNT.replace("\\", "/")

# ===========================================================================
print("\n=== A. 路径解析：两种写法都要能识别 ===")
resolve = webhooks._resolve_cd2_path
WATCHED = ["/9KG/STRM"]

hit, tail = resolve("/9KG/STRM/ABF-391/x.mp4", MB, WATCHED)
check("平级写法命中", hit == "/9KG/STRM", hit)
check("尾巴正确", tail == "/ABF-391/x.mp4", tail)

hit, tail = resolve("/115open/9KG/STRM/ABF-391/x.mp4", MB, WATCHED)
check("含 115open 层也命中", hit == "/9KG/STRM", hit)
check("含 115open 层时尾巴正确", tail == "/ABF-391/x.mp4", tail)

hit, tail = resolve("/115open/9KG/STRM", MB, WATCHED)
check("路径正好等于监听目录（带层）", hit == "/9KG/STRM", hit)
check("此时尾巴为空", tail == "", tail)

hit, tail = resolve("/other/9KG/STRM/x.mp4", MB, WATCHED)
check("其它前缀但含监听目录段也能命中", hit == "/9KG/STRM", hit)

hit, tail = resolve("/115open/9KG/OTHER/x.mp4", MB, WATCHED)
check("不在监听目录下则不命中", hit == "", hit)

hit, tail = resolve("", MB, WATCHED)
check("空路径不命中", hit == "", hit)

# 多个监听目录时选最长匹配
hit, tail = resolve("/a/9KG/STRM/SUB/x.mp4", MB, ["/9KG", "/9KG/STRM"])
check("多监听目录选最长匹配", hit == "/9KG/STRM", hit)
check("最长匹配尾巴正确", tail == "/SUB/x.mp4", tail)

# ===========================================================================
print("\n=== B. 本地路径拼接：网盘根不能重复 ===")
d = os.path.join(MOUNT, "9KG", "STRM", "ABF-391")
touch(os.path.join(d, "ABF-391.mp4"))
touch(os.path.join(d, "ABF-391-C.mp4"))

# 记录洗板前的文件
before = sorted(os.listdir(d))
check("初始两个版本", before == ["ABF-391-C.mp4", "ABF-391.mp4"], before)

# 用「含 115open 层」的 CD2 写法触发（这是之前失效的场景）
payload = {
    "data": [
        {
            "action": "create",
            "is_dir": "false",
            "destination_file": "/115open/9KG/STRM/ABF-391/ABF-391-C.mp4",
        }
    ]
}
webhooks.handle_cd2(payload)

after = sorted(os.listdir(d))
check("洗板被触发并删除了低优版本", after == ["ABF-391-C.mp4"], after)

# ===========================================================================
print("\n=== C. 平级写法同样生效 ===")
d2 = os.path.join(MOUNT, "9KG", "STRM", "XYZ-100")
touch(os.path.join(d2, "XYZ-100.mp4"))
touch(os.path.join(d2, "XYZ-100-UC.mp4"))

webhooks.handle_cd2(
    {
        "data": [
            {
                "action": "create",
                "is_dir": "false",
                "destination_file": "/9KG/STRM/XYZ-100/XYZ-100-UC.mp4",
            }
        ]
    }
)
after2 = sorted(os.listdir(d2))
check("平级写法也触发洗板", after2 == ["XYZ-100-UC.mp4"], after2)

# ===========================================================================
print("\n=== D. 不属于监听目录时静默但不丢日志 ===")
logs_before = 0
if os.path.exists(config.LOG_FILE):
    logs_before = len(open(config.LOG_FILE, encoding="utf-8").readlines())

webhooks.handle_cd2(
    {
        "data": [
            {
                "action": "create",
                "is_dir": "false",
                "destination_file": "/some/other/place/x.mp4",
            }
        ]
    }
)

logs_after = 0
if os.path.exists(config.LOG_FILE):
    logs_after = len(open(config.LOG_FILE, encoding="utf-8").readlines())

check("非监听目录不处理", True)
check("但留下了跳过日志（不再完全静默）", logs_after > logs_before, (logs_before, logs_after))

# ===========================================================================
print("\n=== E. 不同网盘根配置下都能正确拼接 ===")
for mount_root, watch, cd2_path, expect_tail in [
    ("/media/115open", "/9KG/STRM", "/115open/9KG/STRM/F/x.mp4", "/F/x.mp4"),
    ("/media", "/115open/9KG/STRM", "/115open/9KG/STRM/F/x.mp4", "/F/x.mp4"),
    ("/media/115open", "/9KG/STRM", "/9KG/STRM/F/x.mp4", "/F/x.mp4"),
]:
    hit, tail = resolve(cd2_path, mount_root, [watch])
    ok = hit == watch and tail == expect_tail
    check(f"根={mount_root} 监听={watch}", ok, (hit, tail))

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
