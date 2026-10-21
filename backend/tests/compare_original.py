"""对照原脚本 xiban_server.py 的行为，确认「覆盖件保留 / 元数据残留」的真实规则。

做法：把原脚本复制到临时目录，仅替换两处硬编码路径（/config、app.run），
其余逻辑一字不改，然后用同样场景跑一遍，打印结果供比对。
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# 原脚本默认取仓库内的 legacy/，可用 XIBAN_ORIG_SRC 覆盖
_DEFAULT_SRC = os.path.join(os.path.dirname(ROOT), "legacy", "xiban_server.py")
SRC = os.environ.get("XIBAN_ORIG_SRC", _DEFAULT_SRC)

# 本地开发时优先使用 .venv 中的依赖（CI/系统环境下自动忽略）
_venv_sp = os.path.join(ROOT, ".venv", "Lib", "site-packages")
if os.path.isdir(_venv_sp):
    sys.path.insert(0, _venv_sp)

TMP_BASE = os.path.join(ROOT, "tests", "_tmp")
work = os.path.join(TMP_BASE, "orig_cmp")
shutil.rmtree(work, ignore_errors=True)
os.makedirs(work, exist_ok=True)
config_dir = os.path.join(work, "config")
os.makedirs(config_dir)
mount = os.path.join(work, "CloudDrive")
meta = os.path.join(work, "metadata")
os.makedirs(mount)
os.makedirs(meta)

import json  # noqa: E402

with open(os.path.join(config_dir, "config.json"), "w", encoding="utf-8") as f:
    json.dump(
        {
            "MOUNT_BASE_PATH": mount.replace("\\", "/"),
            "METADATA_BASE_PATH": meta.replace("\\", "/"),
            "WATCHED_DIRS": ["/115open/9KG/STRM"],
            "DRY_RUN": False,
            "DB_ONLINE_WEBHOOK": "",
            "STRM_WEBHOOK": "",
            "IGNORE_LEADING_DIGITS": True,
        },
        f,
    )

code = open(SRC, encoding="utf-8").read()
code = code.replace("CONFIG_PATH = '/config/config.json'", f"CONFIG_PATH = {os.path.join(config_dir, 'config.json').replace(chr(92), '/')!r}")
code = code.replace("WASH_HISTORY_FILE = '/config/wash_history.json'", f"WASH_HISTORY_FILE = {os.path.join(config_dir, 'wash_history.json').replace(chr(92), '/')!r}")
code = code.replace("LOG_DIR = '/config/logs'", f"LOG_DIR = {os.path.join(config_dir, 'logs').replace(chr(92), '/')!r}")
code = code.replace("app.run(host='0.0.0.0', port=5000)", "pass")
module_path = os.path.join(work, "orig_server.py")
open(module_path, "w", encoding="utf-8").write(code)

sys.path.insert(0, work)
import orig_server as orig  # noqa: E402

client = orig.app.test_client()


def touch(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write("x")
    return path


def show(title):
    print(f"\n### {title}")
    for base in (mount, meta):
        for root, dirs, files in os.walk(base):
            for f in files:
                print("   FILE", os.path.relpath(os.path.join(root, f), base).replace("\\", "/"))
            if not files and not dirs:
                print("   DIR ", os.path.relpath(root, base).replace("\\", "/") + "/ (empty)")


strm_m = os.path.join(mount, "115open", "9KG", "STRM")
strm_k = os.path.join(meta, "9KG", "STRM")

# 场景 1：同目录下两个版本 -> only_delete_json 分支
touch(os.path.join(strm_k, "STU-300", "STU-300.strm"))
touch(os.path.join(strm_k, "STU-300", "STU-300-C.strm"))
touch(os.path.join(strm_k, "STU-300", "STU-300.nfo"))
touch(os.path.join(strm_k, "STU-300", "STU-300.json"))
touch(os.path.join(strm_m, "STU-300", "STU-300.mp4"))
touch(os.path.join(strm_m, "STU-300", "STU-300.nfo"))
print("原脚本 扫描结果:")
for g in client.get("/api/scan_duplicates").get_json()["duplicates"]:
    for f in g["files"]:
        print("   ", f["name"], "weight=", f["weight"], "winner=", f["is_winner"])
data = client.get("/api/scan_duplicates").get_json()
res = client.post("/api/resolve_duplicates", json={"duplicates": data["duplicates"]}).get_json()
print("原脚本 同目录降维 返回:", res)
show("场景1 同目录降维后（原脚本）")

# 场景 2：跨目录，输家元数据目录只剩 poster.jpg
shutil.rmtree(mount)
shutil.rmtree(meta)
os.makedirs(mount)
os.makedirs(meta)
touch(os.path.join(strm_k, "ABF-391", "ABF-391.strm"))
touch(os.path.join(strm_k, "ABF-391", "poster.jpg"))
touch(os.path.join(strm_k, "ABF-391-C", "ABF-391-C.strm"))
touch(os.path.join(strm_m, "ABF-391", "ABF-391.mp4"))
touch(os.path.join(strm_m, "ABF-391-C", "ABF-391-C.mp4"))
data = client.get("/api/scan_duplicates").get_json()
res = client.post("/api/resolve_duplicates", json={"duplicates": data["duplicates"]}).get_json()
print("\n原脚本 跨目录粉碎 返回:", res)
show("场景2 跨目录粉碎后（原脚本）")

# 场景 3：洗板对决，输家元数据只剩 poster.jpg
shutil.rmtree(mount)
shutil.rmtree(meta)
os.makedirs(mount)
os.makedirs(meta)
touch(os.path.join(strm_m, "CCC-900", "CCC-900.mp4"))
touch(os.path.join(strm_m, "CCC-900-C", "CCC-900-C.mp4"))
touch(os.path.join(strm_k, "CCC-900", "CCC-900.nfo"))
touch(os.path.join(strm_k, "CCC-900", "poster.jpg"))
client.post("/webhook", json={"data": [{"action": "create", "is_dir": "false",
            "destination_file": "/115open/9KG/STRM/CCC-900-C/CCC-900-C.mp4"}]})
show("场景3 洗板对决后（原脚本）")

shutil.rmtree(work, ignore_errors=True)
