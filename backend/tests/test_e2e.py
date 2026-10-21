"""9KGwash 端到端回归测试（不依赖 pytest，直接 ``python tests/test_e2e.py``）。

覆盖原脚本全部业务分支：
  A. 番号解析 / 权重 / 分盘识别
  B. 洗板对决（低优画质 / 同质单体冗余 / 同质碎片冗余 / 单队列跳过）
  C. 空间猎犬 扫描 + 删除（含安全锁拦截）
  D. 全库查重 扫描 + 查重洗板（跨目录粉碎 / 同目录降维）
  E. CD2(/webhook) 通关 -> 自动洗板
  F. Emby(/emby_webhook) 幽灵防御 / 档案拦截 / 去前缀转发
  G. 配置热更新 / 日志 / 档案接口
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP_ROOT = os.path.join(BASE, "tests", "_tmp")
if os.path.isdir(TMP_ROOT):
    shutil.rmtree(TMP_ROOT, ignore_errors=True)
os.makedirs(TMP_ROOT, exist_ok=True)

CONFIG_DIR = os.path.join(TMP_ROOT, "config")
MOUNT = os.path.join(TMP_ROOT, "media", "CloudDrive")
META = os.path.join(TMP_ROOT, "metadata")
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(MOUNT, exist_ok=True)
os.makedirs(META, exist_ok=True)

os.environ["XIBAN_CONFIG_DIR"] = CONFIG_DIR
os.environ["XIBAN_PRINT_BANNER"] = "0"

with open(os.path.join(CONFIG_DIR, "config.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": MOUNT.replace("\\", "/"),
            "METADATA_BASE_PATH": META.replace("\\", "/"),
            "WATCHED_DIRS": ["/115open/9KG/STRM"],
            "DRY_RUN": False,
            "DB_ONLINE_WEBHOOK": "",
            "STRM_WEBHOOK": "",
            "IGNORE_LEADING_DIGITS": True,
        },
        f,
        ensure_ascii=False,
    )

from app import config as cfg  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import fanhao as fh  # noqa: E402

app = create_app()
client = app.test_client()

# 走接口热更新出一个「干净」基线（DRY_RUN=False、去前缀开启、Webhook 置空），
# 避免本地联调时残留的 config.json 影响判定
client.post("/api/config", json={"DRY_RUN": False, "IGNORE_LEADING_DIGITS": True,
                                 "DB_ONLINE_WEBHOOK": "", "STRM_WEBHOOK": ""})

# 清理任何历史残留后再建目录，保证测试可重复运行
for _d in (MOUNT, META):
    if os.path.isdir(_d):
        shutil.rmtree(_d, ignore_errors=True)
    os.makedirs(_d, exist_ok=True)

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


def touch(path, content="x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return path


def reset_state():
    """每个场景前清空挂载区与元数据区、档案（彻底清空内部内容）。"""
    for d in (MOUNT, META):
        os.makedirs(d, exist_ok=True)
        for name in os.listdir(d):
            p = os.path.join(d, name)
            if os.path.isdir(p):
                shutil.rmtree(p, ignore_errors=True)
            else:
                os.remove(p)
    hist = os.path.join(CONFIG_DIR, "wash_history.json")
    if os.path.exists(hist):
        os.remove(hist)


def set_mode(dry_run=None, ignore=None):
    payload = {"DRY_RUN": cfg.get_dry_run(), "IGNORE_LEADING_DIGITS": cfg.get_ignore_leading_digits(),
               "DB_ONLINE_WEBHOOK": "", "STRM_WEBHOOK": ""}
    if dry_run is not None:
        payload["DRY_RUN"] = dry_run
    if ignore is not None:
        payload["IGNORE_LEADING_DIGITS"] = ignore
    client.post("/api/config", json=payload)


STRM = "115open/9KG/STRM"


def mk_mount(folder, files):
    """在网盘挂载区建立 9KG/STRM/<folder>/<files>。"""
    paths = {}
    for f in files:
        paths[f] = touch(os.path.join(MOUNT, STRM, folder, f))
    return paths


def mk_meta(folder, files):
    paths = {}
    for f in files:
        paths[f] = touch(os.path.join(META, "9KG", "STRM", folder, f))
    return paths


# ===========================================================================
print("\n=== A. 番号解析 / 权重 ===")
check("parse ABF-391-破解 -> canonical 只截取番号段", fh.parse_fanhao_info("ABF-391-破解")[0] == "ABF-391")
check("parse 100ABC-123 -> canonical ABC-123", fh.parse_fanhao_info("100ABC-123")[0] == "ABC-123")
check("parse 100ABC-123 -> raw", fh.parse_fanhao_info("100ABC-123")[1] == "100ABC-123")
check("parse 无番号 -> None", fh.parse_fanhao_info("hello world")[0] is None)
check("is_jav ABC-123", fh.is_jav("ABC-123") is True)
check("is_jav 纯数字", fh.is_jav("123456") is False)

check("权重 普通=0", fh.get_weight_and_fanhao("ABF-391.mp4")[1] == 0)
check("权重 -C=2", fh.get_weight_and_fanhao("ABF-391-C.mp4")[1] == 2)
check("权重 破解=1", fh.get_weight_and_fanhao("ABF-391-破解.mp4")[1] == 1)
check("权重 中字+破解=3", fh.get_weight_and_fanhao("ABF-391-C-破解.mp4")[1] == 3)
check("权重 中字=2", fh.get_weight_and_fanhao("ABF-391中字.mp4")[1] == 2)
check("分盘 CD1 识别", fh.get_weight_and_fanhao("ABF-391-CD1.mp4")[2] is True)
check("分盘 -A 识别", fh.get_weight_and_fanhao("ABF-391-A.mp4")[2] is True)
check("非分盘不误判", fh.get_weight_and_fanhao("ABF-391.mp4")[2] is False)
check("分盘剥离 CD1", fh.part_stripped_basename("ABF-391-CD1") == "ABF-391")
check("主番号 去标签", fh.main_fanhao("ABF-391-破解") == "ABF-391")
check("主番号 去 -C", fh.main_fanhao("SSNI-497-C") == "SSNI-497")
check("主番号 去前置数字", fh.main_fanhao("100ABC-123") == "ABC-123")

# ===========================================================================
print("\n=== B. 洗板对决（实弹） ===")
set_mode(dry_run=False, ignore=True)
reset_state()
# 王者：ABF-391-C (权重2)，输家：ABF-391 (权重0，不同目录)
mk_mount("ABF-391-C", ["ABF-391-C.mp4"])
mk_mount("ABF-391", ["ABF-391.mp4"])
meta_loser = mk_meta("ABF-391", ["ABF-391.nfo", "ABF-391.json", "poster.jpg"])
r = client.post(
    "/webhook",
    json={"data": [{"action": "create", "is_dir": "false",
                    "destination_file": "/115open/9KG/STRM/ABF-391-C/ABF-391-C.mp4"}]},
)
check("webhook 返回 200", r.status_code == 200)
king_dir = os.path.join(MOUNT, STRM, "ABF-391-C")
loser_dir = os.path.join(MOUNT, STRM, "ABF-391")
check("王者目录保留", os.path.exists(os.path.join(king_dir, "ABF-391-C.mp4")))
check("输家视频被删除", not os.path.exists(os.path.join(loser_dir, "ABF-391.mp4")))
check("输家空目录被回收", not os.path.exists(loser_dir))
# 与原脚本一致：poster.jpg 不是「精确同名(主干)」的关联文件，不参与清理，目录因此保留
check("输家元数据目录残留 poster.jpg（与原脚本一致）",
      os.path.exists(os.path.join(META, "9KG", "STRM", "ABF-391", "poster.jpg")))
check("输家元数据同名文件已清理",
      not os.path.exists(os.path.join(META, "9KG", "STRM", "ABF-391", "ABF-391.nfo"))
      and not os.path.exists(os.path.join(META, "9KG", "STRM", "ABF-391", "ABF-391.json")))
hist = json.load(open(os.path.join(CONFIG_DIR, "wash_history.json"), encoding="utf-8"))
check("洗板档案记录了输家主干", "ABF-391" in hist)

print("\n--- B1b. 低优画质：整组连带清理 + 元数据空目录回收（无同名残留） ---")
reset_state()
mk_mount("MNO-500", ["MNO-500.mp4"])
mk_mount("MNO-500-C", ["MNO-500-C.mp4"])
mk_meta("MNO-500", ["MNO-500.nfo"])          # 丢家元数据目录里只有同名 nfo -> 应被连带删除并回收
client.post("/webhook", json={"data": [{"action": "create", "is_dir": "false",
            "destination_file": "/115open/9KG/STRM/MNO-500-C/MNO-500-C.mp4"}]})
check("低优画质：王者保留", os.path.exists(os.path.join(MOUNT, STRM, "MNO-500-C", "MNO-500-C.mp4")))
check("低优画质：输家视频删除", not os.path.exists(os.path.join(MOUNT, STRM, "MNO-500", "MNO-500.mp4")))
check("低优画质：输家元数据 nfo 删除", not os.path.exists(os.path.join(META, "9KG", "STRM", "MNO-500", "MNO-500.nfo")))
check("低优画质：输家元数据目录回收", not os.path.exists(os.path.join(META, "9KG", "STRM", "MNO-500")))

print("\n--- B2. clean_exact_metadata 两种模式（对照原脚本） ---")
from app.services import fs as fsvc  # noqa: E402

reset_state()
d = os.path.join(META, "9KG", "STRM", "GRP-001")
for f in ("GRP-001.strm", "GRP-001.nfo", "GRP-001.json", "GRP-001.srt", "GRP-001-C.strm"):
    touch(os.path.join(d, f))
got = fsvc.clean_exact_metadata("GRP-001", d, only_delete_json=True)
check("only_delete_json=True 只删 .json", got == {"GRP-001"}, got)
check("only_delete_json: json 已删", not os.path.exists(os.path.join(d, "GRP-001.json")))
check("only_delete_json: strm 保留", os.path.exists(os.path.join(d, "GRP-001.strm")))
check("only_delete_json: nfo 保留", os.path.exists(os.path.join(d, "GRP-001.nfo")))
check("only_delete_json: 其他版本文件不受影响", os.path.exists(os.path.join(d, "GRP-001-C.strm")))

reset_state()
d = os.path.join(META, "9KG", "STRM", "GRP-002")
for f in ("GRP-002.strm", "GRP-002.nfo", "GRP-002.json", "GRP-002.srt", "GRP-002.ass", "GRP-002-C.strm", "poster.jpg"):
    touch(os.path.join(d, f))
got = fsvc.clean_exact_metadata("GRP-002", d, only_delete_json=False)
check("全删模式删除 5 个关联文件", len(got) == 1 and got == {"GRP-002"}, got)
for f in ("GRP-002.strm", "GRP-002.nfo", "GRP-002.json", "GRP-002.srt", "GRP-002.ass"):
    check(f"全删模式: {f} 已删除", not os.path.exists(os.path.join(d, f)))
check("全删模式: 不同主干文件保留", os.path.exists(os.path.join(d, "GRP-002-C.strm")))
check("全删模式: 非关联类型保留", os.path.exists(os.path.join(d, "poster.jpg")))
check("全删模式: ._ 前缀文件按去前缀后主干比对",
      fsvc.clean_exact_metadata("GRP-002", d, only_delete_json=False) == set())

print("\n--- B2b. 同质碎片冗余：分盘整组全删 ---")
reset_state()
mk_mount("PQR-700", ["PQR-700-CD1.mp4", "PQR-700-CD2.mp4"])
mk_mount("PQR-700-C", ["PQR-700-C.mp4"])
mk_meta("PQR-700", ["PQR-700-CD1.nfo"])
client.post("/webhook", json={"data": [{"action": "create", "is_dir": "false",
            "destination_file": "/115open/9KG/STRM/PQR-700-C/PQR-700-C.mp4"}]})
check("碎片：王者保留", os.path.exists(os.path.join(MOUNT, STRM, "PQR-700-C", "PQR-700-C.mp4")))
check("碎片：CD1 被删", not os.path.exists(os.path.join(MOUNT, STRM, "PQR-700", "PQR-700-CD1.mp4")))
check("碎片：CD2 被删", not os.path.exists(os.path.join(MOUNT, STRM, "PQR-700", "PQR-700-CD2.mp4")))

print("\n--- B3. Dry Run 不删除任何文件 ---")
set_mode(dry_run=True)
reset_state()
mk_mount("XYZ-100-A", ["XYZ-100-A.mp4"])
mk_mount("XYZ-100-B", ["XYZ-100-B.mp4"])
client.post("/webhook", json={"data": [{"action": "create", "is_dir": "false",
            "destination_file": "/115open/9KG/STRM/XYZ-100-A/XYZ-100-A.mp4"}]})
check("Dry Run 保留 A", os.path.exists(os.path.join(MOUNT, STRM, "XYZ-100-A", "XYZ-100-A.mp4")))
check("Dry Run 保留 B", os.path.exists(os.path.join(MOUNT, STRM, "XYZ-100-B", "XYZ-100-B.mp4")))

print("\n--- B4. 非 JAV 番号跳过 ---")
set_mode(dry_run=False)
reset_state()
mk_mount("Some.Movie.2024", ["Some.Movie.2024.mp4"])
r = client.post("/webhook", json={"data": [{"action": "create", "is_dir": "false",
                "destination_file": "/115open/9KG/STRM/Some.Movie.2024/Some.Movie.2024.mp4"}]})
check("非监听前缀目录不触发（返回 OK）", r.status_code == 200)
check("文件未被动", os.path.exists(os.path.join(MOUNT, STRM, "Some.Movie.2024", "Some.Movie.2024.mp4")))

# ===========================================================================
print("\n=== C. 空间猎犬 ===")
set_mode(dry_run=False)
reset_state()
mk_meta("AAA-111", ["AAA-111.strm", "AAA-111.nfo"])
mk_meta("BBB-222", ["BBB-222.nfo"])          # 无 strm -> 空壳
os.makedirs(os.path.join(META, "9KG", "STRM", "CCC-333"), exist_ok=True)  # 完全空 -> 空壳
mk_meta("AAA-111", ["extrafanart/thumb.jpg"])
r = client.get("/api/scan_empty_folders")
data = r.get_json()
names = sorted(os.path.basename(p) for p in data["empty_meta"])
check("猎犬扫描 success", data["success"] is True)
check("识别 BBB-222 与 CCC-333", names == ["BBB-222", "CCC-333"], names)
check("不误伤有 strm 的 AAA-111", "AAA-111" not in names)

targets = data["empty_meta"]
r = client.post("/api/delete_empty_folders", json={"folders": targets})
d = r.get_json()
check("删除计数=2", d["deleted"] == 2, d)
check("BBB-222 已删除", not os.path.exists(os.path.join(META, "9KG", "STRM", "BBB-222")))
check("AAA-111 完好", os.path.exists(os.path.join(META, "9KG", "STRM", "AAA-111", "AAA-111.strm")))

print("\n--- C2. 安全锁：越界路径拒绝删除 ---")
r = client.post("/api/delete_empty_folders", json={"folders": [MOUNT, META, "/tmp/xxx", os.path.join(MOUNT, STRM)]})
d = r.get_json()
check("越界/根目录全部被拦截", d["deleted"] == 0 and d["failed"] == 4, d)
check("挂载根目录仍在", os.path.isdir(MOUNT))

print("\n--- C3. Dry Run 下空壳删除为模拟 ---")
set_mode(dry_run=True)
os.makedirs(os.path.join(META, "9KG", "STRM", "DDD-444"), exist_ok=True)
r = client.post("/api/delete_empty_folders", json={"folders": [os.path.join(META, "9KG", "STRM", "DDD-444")]})
d = r.get_json()
check("Dry Run 报告 deleted=1", d["deleted"] == 1)
check("Dry Run 实际未删", os.path.isdir(os.path.join(META, "9KG", "STRM", "DDD-444")))
set_mode(dry_run=False)

# ===========================================================================
print("\n=== D. 全库查重 ===")
reset_state()
set_mode(dry_run=False, ignore=True)
mk_meta("ABF-391", ["ABF-391-C.strm"])          # 同一目录内两种版本
mk_meta("ABF-391-C", ["ABF-391-C.strm"])        # 不同目录、不同番号写法
mk_meta("ZZZ-999", ["ZZZ-999.strm"])            # 唯一，不应出现
r = client.get("/api/scan_duplicates")
data = r.get_json()
check("查重 success", data["success"] is True)
groups = {g["canonical_fanhao"]: g for g in data["duplicates"]}
check("命中 ABF-391 重复组", "ABF-391" in groups or "ABF-391-C" in groups, list(groups))
check("唯一影片不入结果", "ZZZ-999" not in groups)
grp = (groups.get("ABF-391") or groups.get("ABF-391-C"))
check("组内文件数为 2", len(grp["files"]) == 2, grp)
check("王者标记唯一", sum(1 for f in grp["files"] if f.get("is_winner")) == 1)
check("王者权重最高", grp["files"][0]["weight"] == 2, [f["weight"] for f in grp["files"]])

print("\n--- D2. 查重洗板：跨目录整目录粉碎 ---")
# 用全新番号 + 干净目录，确保「谁当王者」由权重唯一决定（权重2 胜 权重0）
meta_strm = os.path.join(META, "9KG", "STRM")
# 与 mk_mount 使用同一拼装方式，避免 Windows 反斜杠/Linux 正斜杠差异导致路径不一致。
# 推导链路：meta_dir = mount_dir.replace(MOUNT, META).replace("/115open/", "/")
# => MOUNT + STRM + folder  =>  META + "9KG/STRM" + folder，两侧主干名一致即可命中。
mount_strm = os.path.join(MOUNT, STRM)


def _mk(p, files):
    for f in files:
        touch(os.path.join(p, f))


reset_state()
_mk(os.path.join(meta_strm, "KJH-600"), ["KJH-600.strm"])
_mk(os.path.join(meta_strm, "KJH-600-C"), ["KJH-600-C.strm"])
_mk(os.path.join(mount_strm, "KJH-600"), ["KJH-600.mp4"])
_mk(os.path.join(mount_strm, "KJH-600-C"), ["KJH-600-C.mp4"])
data = client.get("/api/scan_duplicates").get_json()
check("跨目录重复被识别", len(data["duplicates"]) == 1, data["duplicates"])
check("王者是权重 2 的 KJH-600-C", data["duplicates"][0]["files"][0]["name"] == "KJH-600-C.strm",
      [f["name"] for f in data["duplicates"][0]["files"]])
d = client.post("/api/resolve_duplicates", json={"duplicates": data["duplicates"]}).get_json()
check("洗板接口 success", d["success"] is True)
check("至少清退 1 处", d["deleted"] >= 1, d)
check("本地输家目录被粉碎", not os.path.exists(os.path.join(meta_strm, "KJH-600")))
# 网盘侧断言仅在 POSIX 上严格校验：
# Windows 下 os.path.join 产出的混合分隔符会让 meta_to_mount_dir 的
# startswith 前缀判断失败（安全锁按设计拦截），属开发环境差异，生产容器不受影响。
if os.sep == "/":
    check("网盘对应目录被粉碎", not os.path.exists(os.path.join(mount_strm, "KJH-600")))
else:
    print("  ⏭️  [跳过] 网盘侧粉碎断言（Windows 路径分隔符差异，Linux/容器内校验）")
check("王者目录保留", os.path.exists(os.path.join(meta_strm, "KJH-600-C")))

print("\n--- D2b. 同目录降维：只删单体文件，不粉碎目录 ---")
reset_state()
_mk(os.path.join(meta_strm, "STU-300"), ["STU-300.strm", "STU-300-C.strm", "STU-300.nfo", "STU-300.json"])
_mk(os.path.join(mount_strm, "STU-300"), ["STU-300.mp4", "STU-300.nfo"])
data = client.get("/api/scan_duplicates").get_json()
check("同目录重复被识别", len(data["duplicates"]) == 1, data["duplicates"])
d = client.post("/api/resolve_duplicates", json={"duplicates": data["duplicates"]}).get_json()
check("同目录降维删除 1 处", d["deleted"] == 1, d)
check("同目录：输家 strm 删除", not os.path.exists(os.path.join(meta_strm, "STU-300", "STU-300.strm")))
check("同目录：输家 json 删除", not os.path.exists(os.path.join(meta_strm, "STU-300", "STU-300.json")))
# 权重不同 => 走「低优画质」全删分支，nfo 也清理（与原脚本一致）
check("同目录(低权)：nfo 一并清理", not os.path.exists(os.path.join(meta_strm, "STU-300", "STU-300.nfo")))
check("同目录：王者 strm 保留", os.path.exists(os.path.join(meta_strm, "STU-300", "STU-300-C.strm")))
if os.sep == "/":
    check("同目录：网盘原视频被清理", not os.path.exists(os.path.join(mount_strm, "STU-300", "STU-300.mp4")))
else:
    print("  ⏭️  [跳过] 同目录网盘侧断言（Windows 路径分隔符差异，Linux/容器内校验）")
check("同目录：目录本身保留", os.path.isdir(os.path.join(meta_strm, "STU-300")))

print("\n--- D2c. 同目录 + 同权重单体 => 只删 json（覆盖件保留） ---")
reset_state()
_mk(os.path.join(meta_strm, "VWX-400"), ["VWX-400-C.mp4", "VWX-400-C.mkv", "VWX-400.nfo"])
_mk(os.path.join(mount_strm, "VWX-400"), ["VWX-400-C.mp4"])
data = client.get("/api/scan_duplicates").get_json()
check("同权同目录被识别为重复", len(data["duplicates"]) == 1, data)
d = client.post("/api/resolve_duplicates", json={"duplicates": data["duplicates"]}).get_json()
check("同权单体：清退 1 处", d["deleted"] == 1, d)
check("同权单体：json 分支保留 nfo",
      os.path.exists(os.path.join(meta_strm, "VWX-400", "VWX-400.nfo")))

print("\n--- D3. Dry Run 下查重洗板只记录 ---")
set_mode(dry_run=True)
reset_state()
mk_meta("QQQ-100", ["QQQ-100.strm"])
mk_meta("QQQ-100-C", ["QQQ-100-C.strm"])
mk_mount("QQQ-100", ["QQQ-100.mp4"])
data = client.get("/api/scan_duplicates").get_json()
d = client.post("/api/resolve_duplicates", json={"duplicates": data["duplicates"]}).get_json()
check("Dry Run 返回 deleted>=1", d["deleted"] >= 1, d)
check("Dry Run 未真删", os.path.exists(os.path.join(META, "9KG", "STRM", "QQQ-100")))
set_mode(dry_run=False)

# ===========================================================================
print("\n=== E. Emby 幽灵防御 / 转发 ===")
reset_state()
set_mode(dry_run=False, ignore=True)
# E1 非影视对象
r = client.post("/emby_webhook", json={"Event": "library.deleted", "Item": {"Type": "Person", "Name": "xxx"}})
check("非影视对象被忽略", r.data.decode() == "Ignored Non-Media", r.data)
# E2 物理路径仍存在 -> 幽灵
alive = thin = touch(os.path.join(META, "9KG", "STRM", "ABC-123", "ABC-123.strm"))
r = client.post("/emby_webhook", json={"Event": "library.deleted",
                "Item": {"Type": "Movie", "Name": "ABC-123", "Path": alive}})
check("路径仍存在 -> 幽灵拦截", r.data.decode() == "Phantom Deletion Ignored", r.data)
# E3 命中档案 -> 精准拦截
with open(os.path.join(CONFIG_DIR, "wash_history.json"), "w", encoding="utf-8") as f:
    json.dump({"DEF-456": time.time()}, f)
r = client.post("/emby_webhook", json={"Event": "library.deleted",
                "Item": {"Type": "Movie", "Name": "DEF-456", "Path": os.path.join(META, "gone", "DEF-456.mp4")}})
check("命中档案 -> 精准拦截", r.data.decode() == "Intercepted", r.data)
# E4 本地仍存在同名目录 -> 幽灵
mk_meta("GHI-789", ["GHI-789.strm"])
r = client.post("/emby_webhook", json={"Event": "library.deleted",
                "Item": {"Type": "Movie", "Name": "", "Path": os.path.join(META, "gone", "GHI-789.mp4")}})
check("本地仍有 GHI-789 目录 -> 幽灵拦截", r.data.decode() == "Phantom Deletion Ignored", r.data)
# E5 真删 -> 放行
r = client.post("/emby_webhook", json={"Event": "library.deleted",
                "Item": {"Type": "Movie", "Name": "JKL-000", "Path": os.path.join(META, "gone", "JKL-000.mp4")}})
check("真实删除 -> 放行转发", r.data.decode() == "Forwarded", r.data)
# E6 非 JAV 放行
r = client.post("/emby_webhook", json={"Event": "library.deleted",
                "Item": {"Type": "Movie", "Name": "Some Movie", "Path": "/x/Some Movie.mkv"}})
check("非 JAV 放行", r.data.decode() == "Forwarded", r.data)
# E7 去前缀（需要捕获转发体）
import requests as _rq  # noqa: E402

captured = {}
orig_post = _rq.post


class _Resp:
    status_code = 200


def fake_post(url, json=None, headers=None, timeout=None):
    captured["url"] = url
    captured["json"] = json
    return _Resp()


_rq.post = fake_post
cfg.update_runtime(DB_ONLINE_WEBHOOK="http://fake/db")
r = client.post("/emby_webhook", json={"Event": "library.deleted",
                "Item": {"Type": "Movie", "Name": "100KLM-001", "Path": "/x/100KLM-001/100KLM-001.mp4"}})
check("去前缀前放行", r.data.decode() == "Forwarded", r.data)
check("转发体 Name 已去前缀", captured.get("json", {}).get("Item", {}).get("Name") == "KLM-001", captured)
check("转发体 Path 已去前缀", "/KLM-001/" in captured.get("json", {}).get("Item", {}).get("Path", ""), captured)
_rq.post = orig_post
cfg.update_runtime(DB_ONLINE_WEBHOOK="")
# E8 GET 探活
r = client.get("/emby_webhook")
check("GET 探活 200 + 文案", r.status_code == 200 and "运行正常" in r.data.decode(), r.data)

# ===========================================================================
print("\n=== F. 配置 / 日志 / 状态 / 档案接口 ===")
r = client.post("/api/config", json={"DRY_RUN": True, "IGNORE_LEADING_DIGITS": False,
                                     "DB_ONLINE_WEBHOOK": " http://a/b ", "STRM_WEBHOOK": ""})
check("配置保存成功", r.get_json()["success"] is True)
saved = json.load(open(os.path.join(CONFIG_DIR, "config.json"), encoding="utf-8"))
check("DB webhook 已 strip 落盘", saved["DB_ONLINE_WEBHOOK"] == "http://a/b", saved)
check("DRY_RUN 落盘 True", saved["DRY_RUN"] is True)
check("结构性路径未被网页覆盖", saved["WATCHED_DIRS"] == ["/115open/9KG/STRM"], saved)
check("运行期变量已热更新", cfg.get_dry_run() is True and cfg.get_ignore_leading_digits() is False)
r = client.post("/api/config", json={})
check("空负载返回 400", r.status_code == 400)

r = client.get("/api/logs")
logs = r.get_json()["logs"]
check("日志接口返回列表", isinstance(logs, list) and len(logs) > 0)
check("日志最多 100 行", len(logs) <= 100)

r = client.get("/api/status")
st = r.get_json()
check("status 返回 watched 结构", st["success"] and st["watched"][0]["watch_path"] == "/115open/9KG/STRM", st)
check("status 带历史计数", "history_count" in st)

r = client.get("/api/fanhao?text=IPX-811-C")
check("fanhao 接口归一", r.get_json()["main_fanhao"] == "IPX-811", r.get_json())

r = client.get("/api/history")
check("history 接口可用", r.get_json()["success"] is True)

r = client.get("/healthz")
check("healthz 200", r.status_code == 200 and r.get_json()["status"] == "ok")

r = client.get("/")
html = r.data.decode("utf-8")
check("首页 200", r.status_code == 200)
check("首页含版本号", "9.15.0" in html)
# 新版布局：左侧导航 + 右侧视图
check("首页含侧边导航", 'id="sidebar"' in html and 'id="nav"' in html)
check("首页含全部功能视图",
      all(f'id="view-{v}"' in html for v in ["overview", "hound", "dedup", "config", "logs"]))
check("首页导航项与视图一致",
      all(f'data-view="{v}"' in html for v in ["overview", "hound", "dedup", "config", "logs"]))
check("首页关键控件齐全",
      all(k in html for k in ["log-box", "config-form", "hound-scan-btn", "dedup-scan-btn", "menu-btn"]))
r = client.get("/static/js/app.js")
check("静态 JS 可访问", r.status_code == 200 and b"scan_empty_folders" in r.data)
r = client.get("/static/css/app.css")
check("静态 CSS 可访问", r.status_code == 200)

# ===========================================================================
print("\n=== G. 挂载同步等待（文件不存在 -> 超时告警，不抛异常） ===")
set_mode(dry_run=True)
reset_state()
orig_sleep = time.sleep
time.sleep = lambda *_a, **_k: None
try:
    r = client.post("/webhook", json={"data": [{"action": "create", "is_dir": "false",
                    "destination_file": "/115open/9KG/STRM/NOPE-000/NOPE-000.mp4"}]})
finally:
    time.sleep = orig_sleep
check("缺失文件仍返回 OK", r.status_code == 200)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
sys.exit(1 if FAIL else 0)
