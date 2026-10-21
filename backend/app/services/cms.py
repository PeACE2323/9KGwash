"""对外 HTTP 唤醒：通知 CMS 重新生成 STRM。"""

from __future__ import annotations

import time

import requests

from .. import config
from ..logging_setup import logger


def trigger_cms(reason: str = "触发 STRM 生成") -> None:
    webhook = config.get_strm_webhook()
    if not webhook:
        return
    if config.get_dry_run():
        logger.info(f"   [🔔 主动唤醒] [Dry Run] 模拟 {reason}，跳过真实请求发送。")
        return

    logger.info(f"   [🔔 主动唤醒] {reason}，等待 2 秒落盘缓冲后向 CMS 发送指令...")
    time.sleep(2)
    try:
        r = requests.get(webhook, timeout=5)
        logger.info(f"   [-] ✅ CMS 唤醒成功，状态码: {r.status_code}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"   [-] ❌ CMS 唤醒失败: {e}")
