"""洗板档案（wash_history.json）。

记录被网关主动清理过的特征主干，用于防御 Emby / 115 的延迟删除通知。
条目保留 7 天（604800 秒），过期自动剔除。
"""

from __future__ import annotations

import json
import os
import threading
import time

from .. import config

HISTORY_TTL = 604800  # 7 天

_lock = threading.RLock()


def load_wash_history() -> dict:
    if os.path.exists(config.WASH_HISTORY_FILE):
        try:
            with open(config.WASH_HISTORY_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:  # noqa: BLE001 - 与原脚本一致，静默回退
            pass
    return {}


def save_wash_history(history: dict) -> None:
    """保存洗板档案（原子写入）。

    档案是 Emby 幽灵删除防御的唯一依据：如果文件写坏，
    重启后防御失效，Emby 的延迟删除通知会被当成用户真删而转发给 CMS，
    可能引发整片误删。因此这里用「临时文件 + fsync + 原子替换」，
    保证任何时刻磁盘上的文件都是完整可解析的。
    """
    tmp_path = config.WASH_HISTORY_FILE + ".tmp"
    try:
        os.makedirs(os.path.dirname(config.WASH_HISTORY_FILE), exist_ok=True)
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(history, f, ensure_ascii=False, indent=4)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, config.WASH_HISTORY_FILE)
    except Exception:  # noqa: BLE001
        try:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)
        except OSError:
            pass


def record(keys) -> None:
    """把一批特征主干写入档案，并剔除超过 7 天的旧记录。"""
    if not keys:
        return
    with _lock:
        history = load_wash_history()
        now = time.time()
        for k in keys:
            history[k] = now
        history = {k: v for k, v in history.items() if now - v < HISTORY_TTL}
        save_wash_history(history)


def purge_expired() -> dict:
    """返回清洗过期条目后的档案（供 Emby 判断引用）。"""
    with _lock:
        history = load_wash_history()
        now = time.time()
        expired = [k for k, v in history.items() if now - v > HISTORY_TTL]
        if expired:
            for k in expired:
                del history[k]
            save_wash_history(history)
        return history
