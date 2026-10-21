"""配置加载 / 保存 / 热更新。

与原脚本保持完全一致的语义：
- 配置文件固定位于 ``CONFIG_PATH``（容器内 ``/config/config.json``）；
- ``load_config`` 在默认值基础上 update 文件内容，文件损坏时回退默认安全配置；
- ``save_config`` 先合并当前配置再整体写回（indent=4, ensure_ascii=False）；
- 运行期可变项（DRY_RUN / 两个 Webhook / IGNORE_LEADING_DIGITS）支持网页热更新。
"""

from __future__ import annotations

import json
import os
import threading

# --- 固定路径（与容器卷一一对应） ---
CONFIG_DIR = os.environ.get("XIBAN_CONFIG_DIR", "/config")
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
WASH_HISTORY_FILE = os.path.join(CONFIG_DIR, "wash_history.json")
LOG_DIR = os.path.join(CONFIG_DIR, "logs")
LOG_FILE = os.path.join(LOG_DIR, "xiban.log")

DEFAULT_CONFIG = {
    "MOUNT_BASE_PATH": "/media/CloudDrive",
    "METADATA_BASE_PATH": "/metadata",
    "WATCHED_DIRS": ["/115open/9KG/STRM"],
    "DRY_RUN": True,
    "DB_ONLINE_WEBHOOK": "",
    "STRM_WEBHOOK": "",
    "IGNORE_LEADING_DIGITS": True,
}

# 只读路径项：这些只能通过 config.json / 环境变量调整，不在 WebUI 热更新范围内
STRUCTURAL_KEYS = ("MOUNT_BASE_PATH", "METADATA_BASE_PATH", "WATCHED_DIRS")

_lock = threading.RLock()
_config: dict = {}
# 运行期热更新状态（完全对应原脚本的全局变量）
_runtime = {
    "DRY_RUN": DEFAULT_CONFIG["DRY_RUN"],
    "DB_ONLINE_WEBHOOK": DEFAULT_CONFIG["DB_ONLINE_WEBHOOK"],
    "STRM_WEBHOOK": DEFAULT_CONFIG["STRM_WEBHOOK"],
    "IGNORE_LEADING_DIGITS": DEFAULT_CONFIG["IGNORE_LEADING_DIGITS"],
}


def _apply_runtime(conf: dict) -> None:
    _runtime["DRY_RUN"] = bool(conf.get("DRY_RUN", True))
    _runtime["DB_ONLINE_WEBHOOK"] = conf.get("DB_ONLINE_WEBHOOK", "") or ""
    _runtime["STRM_WEBHOOK"] = conf.get("STRM_WEBHOOK", "") or ""
    _runtime["IGNORE_LEADING_DIGITS"] = bool(conf.get("IGNORE_LEADING_DIGITS", True))


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    if os.path.exists(CONFIG_PATH):
        try:
            # utf-8-sig：兼容 Windows 记事本等工具写出的 BOM 头文件
            with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
                loaded = json.load(f)
                cfg.update(loaded)
        except Exception as e:  # noqa: BLE001 - 与原脚本一致：任何异常都回退默认值
            print(f"配置文件加载失败: {e}，使用默认安全配置。")
    return cfg


def init_config() -> dict:
    """进程启动时调用一次，完成「加载 -> 首次落盘 -> 应用环境变量覆盖」。"""
    global _config
    with _lock:
        _config = load_config()
        os.makedirs(CONFIG_DIR, exist_ok=True)
        # 配置文件不存在时立即生成一份，方便宿主直接编辑（原脚本只有保存时才写）
        if not os.path.exists(CONFIG_PATH):
            save_config({})
        _apply_runtime(_config)
        return _config


def reload_config() -> dict:
    """外部直接编辑 config.json 后，可调用此方法重新加载（容器内可选能力）。"""
    global _config
    with _lock:
        _config = load_config()
        _apply_runtime(_config)
        return _config


def save_config(new_config_data: dict) -> tuple[bool, dict | str]:
    global _config
    with _lock:
        current = load_config()
        current.update(new_config_data)
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(current, f, ensure_ascii=False, indent=4)
            return True, current
        except Exception as e:  # noqa: BLE001
            return False, str(e)


def current_config() -> dict:
    with _lock:
        return dict(_config)


# --- 运行期参数访问器（与原脚本全局变量一一对应） ---
def get_dry_run() -> bool:
    return _runtime["DRY_RUN"]


def get_db_webhook() -> str:
    return _runtime["DB_ONLINE_WEBHOOK"]


def get_strm_webhook() -> str:
    return _runtime["STRM_WEBHOOK"]


def get_ignore_leading_digits() -> bool:
    return _runtime["IGNORE_LEADING_DIGITS"]


def update_runtime(**kwargs) -> None:
    with _lock:
        _runtime.update(kwargs)


def mount_base_path() -> str:
    return current_config().get("MOUNT_BASE_PATH", "/media")


def metadata_base_path() -> str:
    return current_config().get("METADATA_BASE_PATH", "/metadata")


def watched_dirs() -> list[str]:
    return list(current_config().get("WATCHED_DIRS", []) or [])
