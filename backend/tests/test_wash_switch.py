"""自动洗板总开关（WASH_ENABLED）的测试。

语义：
  WASH_ENABLED=True  -> CD2 推送新文件后自动触发洗板对决（默认值）
  WASH_ENABLED=False -> 只接收通知并记日志，不等待、不洗板、不删任何文件

范围（用户确认）：只管「CD2 自动触发」的洗板；
「影片查重」页的手动「一键洗板清理」不受此开关影响。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_wash_switch")
shutil.rmtree(TMP, ignore_errors=True)
MOUNT = os.path.join(TMP, "cloud", "115open")
METAROOT = os.path.join(TMP, "meta")
META = os.path.join(METAROOT, "STRM")
CFG = os.path.join(TMP, "cfg")
os.makedirs(CFG)
SUB = os.path.join("9KG", "STRM")

os.environ["XIBAN_CONFIG_DIR"] = CFG
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"

BASE_CFG = {
    "MOUNT_BASE_PATH": MOUNT.replace("\\", "/"),
    "METADATA_BASE_PATH": METAROOT.replace("\\", "/"),
    "WATCHED_DIRS": ["/9KG/STRM"],
    "META_SUBDIR_OVERRIDE": "/STRM",
    "DEDUP_SCAN_PATHS": [META.replace("\\", "/")],
    "DRY_RUN": False,
}


def write_cfg(extra=None):
    c = dict(BASE_CFG)
    if extra:
        c.update(extra)
    with open(os.path.join(CFG, "config.json"), "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)


write_cfg()

from app import config  # noqa: E402
from app.factory import create_app  # noqa: E402
from app.services import library, webhooks  # noqa: E402

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


def wait():
    """等待后台线程池完成（webhook 是异步的）。"""
    for _ in range(600):
        if webhooks.pending_count() == 0:
            time.sleep(0.05)
            if webhooks.pending_count() == 0:
                return
        time.sleep(0.02)


def reset():
    shutil.rmtree(MOUNT, ignore_errors=True)
    shutil.rmtree(METAROOT, ignore_errors=True)


def mk(p, files=()):
    os.makedirs(p, exist_ok=True)
    for f in files:
        with open(os.path.join(p, f), "wb") as fh:
            fh.write(b"0" * 64)


def mk_mount(name, files):
    mk(os.path.join(MOUNT, SUB, name), files)


def mk_meta(name, files):
    mk(os.path.join(META, name), files)


def hit(name):
    r = client.post(
        "/webhook",
        json={
            "data": [
                {
                    "action": "create",
                    "is_dir": "false",
                    "destination_file": f"/115open/9KG/STRM/{name}/{name}.mp4",
                }
            ]
        },
    )
    wait()
    return r


def loser_alive(name):
    return os.path.exists(os.path.join(MOUNT, SUB, name, f"{name}.mp4"))


def set_switch(on: bool):
    client.post("/api/config", json={"WASH_ENABLED": on})


# ===========================================================================
print("\n=== A. 默认值与配置项 ===")
check("DEFAULT_CONFIG 含 WASH_ENABLED", "WASH_ENABLED" in config.DEFAULT_CONFIG)
check("默认值为 True（升级后不悄悄关掉洗板）",
      config.DEFAULT_CONFIG["WASH_ENABLED"] is True, config.DEFAULT_CONFIG["WASH_ENABLED"])
check("访问器返回 True", config.get_wash_enabled() is True)
st = client.get("/api/status").get_json()
check("status 返回 wash_enabled", st.get("wash_enabled") is True, st.get("wash_enabled"))

# 老配置（没有该键）应默认开启
write_cfg()
config.init_config()
check("老配置缺该键时默认开启", config.get_wash_enabled() is True)

# ===========================================================================
print("\n=== B. 开启状态：正常洗板 ===")
reset()
mk_mount("ABF-100", ["ABF-100.mp4"])
mk_mount("ABF-100-C", ["ABF-100-C.mp4"])
mk_meta("ABF-100", ["ABF-100.nfo"])
set_switch(True)
check("开关为开启", config.get_wash_enabled() is True)
hit("ABF-100-C")
check("王者保留", os.path.exists(os.path.join(MOUNT, SUB, "ABF-100-C", "ABF-100-C.mp4")))
check("低权重被删除", not loser_alive("ABF-100"))

# ===========================================================================
print("\n=== C. 关闭状态：不洗板 ===")
reset()
mk_mount("CDE-200", ["CDE-200.mp4"])
mk_mount("CDE-200-C", ["CDE-200-C.mp4"])
mk_meta("CDE-200", ["CDE-200.nfo"])
set_switch(False)
check("开关为关闭", config.get_wash_enabled() is False)

r = hit("CDE-200-C")
check("webhook 仍返回 200（CD2 不会重试）", r.status_code == 200, r.status_code)
check("低权重文件未被删除", loser_alive("CDE-200"))
check("王者文件仍在", os.path.exists(os.path.join(MOUNT, SUB, "CDE-200-C", "CDE-200-C.mp4")))
check("元数据未被连带清理",
      os.path.exists(os.path.join(META, "CDE-200", "CDE-200.nfo")))

st = client.get("/api/status").get_json()
check("status 反映关闭状态", st.get("wash_enabled") is False, st.get("wash_enabled"))

# ===========================================================================
print("\n=== D. 关闭状态：手动查重洗板不受影响 ===")
reset()
mk_mount("FGH-300", ["FGH-300.mp4"])
mk_mount("FGH-300-C", ["FGH-300-C.mp4"])
mk_meta("FGH-300", ["FGH-300.strm", "FGH-300.nfo"])
mk_meta("FGH-300-C", ["FGH-300-C.strm", "FGH-300-C.nfo"])
check("洗板开关仍为关闭", config.get_wash_enabled() is False)

groups = library.scan_duplicates()
dups = groups.get("duplicates", [])
check("查重能扫出重复组", len(dups) >= 1, len(dups))

res = library.resolve_duplicates(dups)
check("手动洗板执行成功", res.get("success") is True, res)
check("手动洗板确实删除了低权重", res.get("deleted", 0) >= 1, res)
check("低权重文件已删除", not loser_alive("FGH-300"))

# ===========================================================================
print("\n=== E. 重新开启：恢复自动洗板 ===")
reset()
mk_mount("IJK-400", ["IJK-400.mp4"])
mk_mount("IJK-400-C", ["IJK-400-C.mp4"])
mk_meta("IJK-400", ["IJK-400.nfo"])
set_switch(True)
check("开关恢复开启", config.get_wash_enabled() is True)
hit("IJK-400-C")
check("低权重被删除", not loser_alive("IJK-400"))

# ===========================================================================
print("\n=== F. 热更新立即生效（无需重启） ===")
set_switch(False)
check("API 保存后立即关闭", config.get_wash_enabled() is False)
set_switch(True)
check("API 保存后立即开启", config.get_wash_enabled() is True)

# 持久化：写入磁盘后重新加载仍保持
set_switch(False)
write_cfg({"WASH_ENABLED": False})
config.init_config()
check("配置持久化（重新加载仍为关闭）", config.get_wash_enabled() is False)
set_switch(True)

# ===========================================================================
print("\n=== G. 不与其他开关互相干扰 ===")
client.post("/api/config", json={"WASH_ENABLED": False, "DRY_RUN": False,
                                 "IGNORE_LEADING_DIGITS": True})
cfg_now = config.current_config()
check("WASH_ENABLED 已保存", cfg_now.get("WASH_ENABLED") is False, cfg_now.get("WASH_ENABLED"))
check("DRY_RUN 未被误改", cfg_now.get("DRY_RUN") is False, cfg_now.get("DRY_RUN"))
check("IGNORE_LEADING_DIGITS 未被误改",
      cfg_now.get("IGNORE_LEADING_DIGITS") is True, cfg_now.get("IGNORE_LEADING_DIGITS"))

# 只改 DRY_RUN 时不应重置 WASH_ENABLED
client.post("/api/config", json={"DRY_RUN": True})
check("只改 DRY_RUN 不影响 WASH_ENABLED",
      config.get_wash_enabled() is False, config.get_wash_enabled())
set_switch(True)

# ===========================================================================
print("\n=== H. 前端结构 ===")
HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()
JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()

check("运行配置页有开关控件", 'id="wash_enabled"' in HTML)
check("开关有开启/关闭两个选项", 'value="false"' in HTML and "自动洗板总开关" in HTML)
check("模板渲染使用 wash_enabled 变量", "{% if wash_enabled %}" in HTML)
check("保存逻辑提交 WASH_ENABLED", "WASH_ENABLED:" in JS)
check("概览页显示开关状态", "stat-mode-hint" in HTML and "stat-mode-hint" in JS)
check("原「运行模式」不再叫「洗板开关」（避免歧义）",
      "运行模式 (洗板开关)" not in HTML)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
