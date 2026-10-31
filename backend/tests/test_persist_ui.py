"""配置持久化与 UI 结构的测试。

覆盖用户提出的三个问题：
  1. 扫描路径改为列表式（一行一个 + 添加/删除）
  2. 「洗版权重与路径」改名为「洗版权重」，存储路径挪到运行配置
  3. 配置持久化：重启/升级后设置不丢
"""

from __future__ import annotations

import glob
import json
import os
import shutil
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.path.join(BASE, "tests", "_tmp_persist2")
shutil.rmtree(TMP, ignore_errors=True)
os.makedirs(os.path.join(TMP, "cfg"))
CFG_PATH = os.path.join(TMP, "cfg", "config.json")

os.environ["XIBAN_CONFIG_DIR"] = os.path.join(TMP, "cfg")
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"

with open(CFG_PATH, "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": "/clouddrive/115open",
            "METADATA_BASE_PATH": "/media",
            "WATCHED_DIRS": ["/9KG/STRM"],
            "DRY_RUN": True,
        },
        f,
        ensure_ascii=False,
    )

from app import config  # noqa: E402
from app.factory import create_app  # noqa: E402

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


def read_disk():
    with open(CFG_PATH, encoding="utf-8") as f:
        return json.load(f)


# ===========================================================================
print("\n=== A. 全部设置写入后，模拟重启仍保留 ===")
client.post("/api/config", json={
    "DB_ONLINE_WEBHOOK": "http://10.1.1.1:8080/hook",
    "STRM_WEBHOOK": "http://10.1.1.1:8080/trigger",
    "DRY_RUN": False,
    "IGNORE_LEADING_DIGITS": False,
})
client.post("/api/config", json={
    "HOUND_SCAN_PATHS": ["/media/STRM", "/media/待整理"],
    "DEDUP_SCAN_PATHS": ["/media/STRM"],
    "META_SUBDIR_OVERRIDE": "/STRM",
})
client.post("/api/weight", json={
    "order": ["custom:k4", "UC", "C", "U", "NORMAL"],
    "custom": [{"id": "k4", "name": "4K", "keywords": ["4K", "2160p"]}],
})

# 模拟容器重启：从磁盘重新加载
config.reload_config()

check("网盘根保留", config.mount_base_path() == "/clouddrive/115open", config.mount_base_path())
check("元数据根保留", config.metadata_base_path() == "/media", config.metadata_base_path())
check("DRY_RUN 保留", config.get_dry_run() is False, config.get_dry_run())
check("DB webhook 保留", config.get_db_webhook() == "http://10.1.1.1:8080/hook",
      config.get_db_webhook())
check("STRM webhook 保留", config.get_strm_webhook() == "http://10.1.1.1:8080/trigger",
      config.get_strm_webhook())
check("忽略前置数字 保留", config.get_ignore_leading_digits() is False,
      config.get_ignore_leading_digits())
check("猎犬扫描路径 保留", config.hound_scan_paths() == ["/media/STRM", "/media/待整理"],
      config.hound_scan_paths())
check("查重扫描路径 保留", config.dedup_scan_paths() == ["/media/STRM"],
      config.dedup_scan_paths())
check("元数据子路径 保留",
      config.current_config().get("META_SUBDIR_OVERRIDE") == "/STRM",
      config.current_config().get("META_SUBDIR_OVERRIDE"))
check("权重顺序 保留",
      config.get_weight_order() == ["custom:k4", "UC", "C", "U", "NORMAL"],
      config.get_weight_order())
check("自定义权重档位 保留",
      len(config.get_weight_custom()) == 1 and config.get_weight_custom()[0]["name"] == "4K",
      config.get_weight_custom())

# ===========================================================================
print("\n=== B. 原子写入 ===")
tmp_file = CFG_PATH + ".tmp"
check("保存后无残留 .tmp 文件", not os.path.exists(tmp_file))
config.save_config({"DRY_RUN": True})
check("再次保存后仍无 .tmp", not os.path.exists(tmp_file))
check("内容完整可解析", isinstance(read_disk(), dict))

# ===========================================================================
print("\n=== C. 配置文件损坏时备份而非静默丢弃 ===")
for old in glob.glob(CFG_PATH + ".broken-*"):
    os.remove(old)
with open(CFG_PATH, "w", encoding="utf-8") as f:
    f.write("{ broken json !!!")
cfg = config.load_config()
check("损坏时回退默认值", cfg.get("MOUNT_BASE_PATH") == config.DEFAULT_CONFIG["MOUNT_BASE_PATH"],
      cfg.get("MOUNT_BASE_PATH"))
backups = glob.glob(CFG_PATH + ".broken-*")
check("损坏文件已备份", len(backups) >= 1, backups)

# 恢复一个正常文件供后续测试
with open(CFG_PATH, "w", encoding="utf-8") as f:
    json.dump({"MOUNT_BASE_PATH": "/clouddrive/115open", "METADATA_BASE_PATH": "/media",
               "WATCHED_DIRS": ["/9KG/STRM"], "DRY_RUN": True}, f)
config.reload_config()

# ===========================================================================
print("\n=== D. 局部更新不重置其他字段 ===")
client.post("/api/config", json={"DB_ONLINE_WEBHOOK": "http://keep/me", "DRY_RUN": False})
before = read_disk()
client.post("/api/config", json={"HOUND_SCAN_PATHS": ["/a"]})
after = read_disk()
check("保存扫描路径后 DRY_RUN 不变", after["DRY_RUN"] == before["DRY_RUN"],
      (before["DRY_RUN"], after["DRY_RUN"]))
check("保存扫描路径后 webhook 不变",
      after["DB_ONLINE_WEBHOOK"] == before["DB_ONLINE_WEBHOOK"],
      (before["DB_ONLINE_WEBHOOK"], after["DB_ONLINE_WEBHOOK"]))

# ===========================================================================
print("\n=== E. UI 结构 ===")
HTML = open(os.path.join(BASE, "app", "templates", "index.html"), encoding="utf-8").read()
JS = open(os.path.join(BASE, "app", "static", "js", "app.js"), encoding="utf-8").read()
CSS = open(os.path.join(BASE, "app", "static", "css", "app.css"), encoding="utf-8").read()

check("猎犬页有路径列表容器", 'id="hound-path-list"' in HTML)
check("查重页有路径列表容器", 'id="dedup-path-list"' in HTML)
check("有添加按钮", HTML.count("scan-path-add") >= 2, HTML.count("scan-path-add"))
check("旧 textarea 已移除", 'id="hound-paths"' not in HTML and 'id="dedup-paths"' not in HTML)
check("JS 有 addScanPathRow", "function addScanPathRow" in JS)
check("JS 有 makeScanPathRow", "function makeScanPathRow" in JS)
check("路径行有胶囊样式", ".scan-path-item" in CSS)
check("有删除按钮样式", ".scan-path-btn.danger" in CSS)

# 导航与页面命名
check("导航名为「洗版权重」", "洗版权重</span>" in HTML)
check("旧名「洗版权重与路径」已移除", "洗版权重与路径" not in HTML)
check("洗版权重在洗板中心组", HTML.find("洗板中心") < HTML.find('data-view="washcfg"'))

# 存储路径位置
i_cfg = HTML.find('id="view-config"')
cfg_block = HTML[i_cfg : HTML.find("</section>", i_cfg)]
check("存储路径在运行配置页", "💾 存储路径" in cfg_block)
i_wash = HTML.find('id="view-washcfg"')
wash_block = HTML[i_wash : HTML.find("</section>", i_wash)]
check("洗版权重页不再有存储路径", "存储路径" not in wash_block)
check("洗版权重页仍有权重卡片", "洗板权重顺序" in wash_block)

# 下拉框不重叠
check("select 有右侧内边距", "padding-right: 32px" in CSS)
check("select 关闭原生外观", "appearance: none" in CSS)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
