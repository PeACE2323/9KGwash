"""投递配置持久化 + 后台监控线程。

配置独立存于 ``/config/deliver.json``，与洗板用的 ``config.json`` 分离，
避免两类配置互相污染，也便于单独备份/迁移。
"""

from __future__ import annotations

import json
import os
import threading
import time

from .. import config as app_config
from ..logging_setup import logger
from . import deliver

DELIVER_CONFIG_FILE = os.path.join(app_config.CONFIG_DIR, "deliver.json")

_lock = threading.RLock()
_cfg: dict | None = None

# 后台监控线程状态
_watcher_thread: threading.Thread | None = None
_watcher_stop = threading.Event()
_watcher_state = {"running": False, "last_run": 0, "last_stats": None, "last_error": ""}


# ---------------------------------------------------------------------------
# 配置读写
# ---------------------------------------------------------------------------
def load_deliver_config() -> dict:
    global _cfg
    with _lock:
        if _cfg is not None:
            return _cfg
        raw = {}
        if os.path.exists(DELIVER_CONFIG_FILE):
            try:
                with open(DELIVER_CONFIG_FILE, "r", encoding="utf-8-sig") as f:
                    raw = json.load(f)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"📦 [投递] 配置文件读取失败: {e}，使用默认配置。")
                raw = {}
        _cfg = deliver.normalize_deliver_config(raw)
        return _cfg


def save_deliver_config(raw: dict) -> tuple[bool, dict | str]:
    global _cfg
    with _lock:
        cfg = deliver.normalize_deliver_config(raw)
        try:
            os.makedirs(app_config.CONFIG_DIR, exist_ok=True)
            with open(DELIVER_CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=4)
            _cfg = cfg
            return True, cfg
        except Exception as e:  # noqa: BLE001
            return False, str(e)


def current_deliver_config() -> dict:
    return load_deliver_config()


# ---------------------------------------------------------------------------
# 后台监控
# ---------------------------------------------------------------------------
def _watch_loop():
    """轮询监控目录，按 WATCH_INTERVAL 执行投递。"""
    logger.info("📦 [投递监控] 后台线程已启动。")
    while not _watcher_stop.is_set():
        cfg = load_deliver_config()
        interval = max(10, int(cfg.get("WATCH_INTERVAL", 60)))

        if cfg.get("ENABLED") and cfg.get("SCAN_DIR"):
            try:
                stats = deliver.run_deliver(cfg, dry_run=app_config.get_dry_run(), only_recent=True)
                _watcher_state["last_run"] = time.time()
                _watcher_state["last_stats"] = stats
                _watcher_state["last_error"] = "" if stats.get("success") else str(stats.get("error", ""))
            except Exception as e:  # noqa: BLE001
                _watcher_state["last_error"] = str(e)
                logger.error(f"📦 [投递监控] 执行异常: {e}")

        _watcher_stop.wait(interval)

    _watcher_state["running"] = False
    logger.info("📦 [投递监控] 后台线程已停止。")


def start_watcher():
    global _watcher_thread
    with _lock:
        if _watcher_thread and _watcher_thread.is_alive():
            return
        _watcher_stop.clear()
        _watcher_thread = threading.Thread(target=_watch_loop, name="deliver-watcher", daemon=True)
        _watcher_thread.start()
        _watcher_state["running"] = True


def stop_watcher():
    _watcher_stop.set()


def watcher_status() -> dict:
    cfg = load_deliver_config()
    return {
        "thread_running": bool(_watcher_thread and _watcher_thread.is_alive()),
        "enabled": bool(cfg.get("ENABLED")),
        "scan_dir": cfg.get("SCAN_DIR", ""),
        "interval": cfg.get("WATCH_INTERVAL", 60),
        "last_run": _watcher_state.get("last_run", 0),
        "last_error": _watcher_state.get("last_error", ""),
        "last_stats": _watcher_state.get("last_stats"),
    }
