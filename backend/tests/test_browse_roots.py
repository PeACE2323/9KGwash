"""目录浏览白名单的测试。

问题：用户把 Docker 映射改成
    /clouddrive → /mnt/user/CloudDrive/
    /media      → /mnt/user/Media/CloudDrive/9KG/
之后，容器 UI 里点「浏览」提示「该路径不在允许浏览的范围内」，
导致路径完全无法配置。

根因：
    browse 的白名单只写死了历史路径（/media/CloudDrive、/metadata 等），
    用户映射到 /clouddrive 这类新位置时不在白名单内，直接被 403 拒绝。

修复：
    1) 白名单自动纳入容器内所有真实挂载点（读 /proc/mounts）
    2) 预先加入 /clouddrive、/data 等常见映射位置
    3) 纳入各任务页的自定义扫描路径
    4) 系统目录（/proc /sys /dev ...）一律拒绝
    5) 403 时返回可浏览的根列表，方便用户知道该选哪里
"""

from __future__ import annotations

import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_browse")
shutil.rmtree(TMP, ignore_errors=True)
# 模拟用户的新映射结构
CD = os.path.join(TMP, "clouddrive")
MD = os.path.join(TMP, "media")
os.makedirs(os.path.join(CD, "115open", "9KG"))
os.makedirs(os.path.join(MD, "日本有码"))
CFG = os.path.join(TMP, "cfg")
os.makedirs(CFG)

os.environ["XIBAN_CONFIG_DIR"] = CFG
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"
# 把两个模拟挂载点作为额外允许根（相当于容器里的 /proc/mounts 探测结果）
os.environ["XIBAN_BROWSE_ROOTS"] = CD + "," + MD

with open(os.path.join(CFG, "config.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": CD.replace("\\", "/"),
            "METADATA_BASE_PATH": MD.replace("\\", "/"),
            "WATCHED_DIRS": ["/115open/9KG/STRM"],
            "DRY_RUN": True,
        },
        f,
        ensure_ascii=False,
    )

from app.factory import create_app  # noqa: E402
from app.routers import deliver_api  # noqa: E402

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
print("\n=== A. 白名单包含新映射位置 ===")
roots = deliver_api._safe_allowed_roots()
check("/clouddrive 在白名单", "/clouddrive" in roots, roots)
check("/media 在白名单", "/media" in roots, roots)
check("配置的网盘根在白名单", CD.replace("\\", "/") in roots or CD in roots, roots)
check("配置的元数据根在白名单", MD.replace("\\", "/") in roots or MD in roots, roots)
check("不包含 /proc", "/proc" not in roots, roots)
check("不包含 /sys", "/sys" not in roots, roots)
check("不包含 /etc", "/etc" not in roots, roots)

# ===========================================================================
print("\n=== B. 可以浏览新映射的目录 ===")
r = client.get("/api/deliver/browse?path=" + CD)
d = r.get_json()
check("浏览网盘根成功", d.get("success") is True, d.get("error"))
check("能看到 115open", "115open" in [x["name"] for x in d.get("dirs", [])],
      [x["name"] for x in d.get("dirs", [])])

r = client.get("/api/deliver/browse?path=" + CD + "/115open/9KG")
d = r.get_json()
check("浏览子目录成功", d.get("success") is True, d.get("error"))

r = client.get("/api/deliver/browse?path=" + MD)
d = r.get_json()
check("浏览元数据根成功", d.get("success") is True, d.get("error"))

check("返回 roots 列表", isinstance(d.get("roots"), list) and len(d["roots"]) >= 2, d.get("roots"))

# ===========================================================================
print("\n=== C. 系统目录被拒绝 ===")
for sys_dir in ("/proc", "/sys", "/dev", "/etc", "/var"):
    r = client.get("/api/deliver/browse?path=" + sys_dir)
    check(f"{sys_dir} 被拒绝", r.status_code == 403, r.status_code)

# ===========================================================================
print("\n=== D. 403 时给出可浏览路径提示 ===")
r = client.get("/api/deliver/browse?path=" + os.path.join(TMP, "nowhere"))
d = r.get_json()
check("返回 403", r.status_code == 403)
check("错误信息含「可浏览」提示", "可浏览" in (d.get("error") or ""), d.get("error"))
check("返回 allowed_roots", isinstance(d.get("allowed_roots"), list), d.get("allowed_roots"))

# ===========================================================================
print("\n=== E. 配置写错时仍能改回（无死锁） ===")
# 模拟用户当前配置是错的
r = client.post(
    "/api/config",
    json={"MOUNT_BASE_PATH": "/nope/missing", "METADATA_BASE_PATH": "/meida"},
)
check("可以保存「错误」路径（浏览器仍需可用）", r.get_json().get("success") is True)

# 此时仍应能浏览真实挂载点，从而改回来
r = client.get("/api/deliver/browse?path=" + CD)
check("配置写错后仍能浏览真实挂载", r.get_json().get("success") is True,
      r.get_json().get("error"))

# 改回正确路径
r = client.post(
    "/api/config",
    json={"MOUNT_BASE_PATH": CD.replace("\\", "/"), "METADATA_BASE_PATH": MD.replace("\\", "/")},
)
check("能改回正确路径", r.get_json().get("success") is True)

# ===========================================================================
print("\n=== F. 任务页自定义扫描路径也可浏览 ===")
client.post("/api/config", json={"DEDUP_SCAN_PATHS": [MD.replace("\\", "/")]})
roots2 = deliver_api._safe_allowed_roots()
check("自定义扫描路径进入白名单",
      MD.replace("\\", "/") in roots2 or MD in roots2, roots2)
client.post("/api/config", json={"DEDUP_SCAN_PATHS": []})

# ===========================================================================
print("\n=== G. 空路径与根目录 ===")
r = client.get("/api/deliver/browse?path=/")
d = r.get_json()
check("根目录可列出", d.get("success") is True, d.get("error"))

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
