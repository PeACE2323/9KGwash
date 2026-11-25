"""对外 HTTP 唤醒：通知 CMS 重新生成 STRM。

落盘缓冲（防抖）
----------------
CD2 是逐文件推送的：一部影片刚入库，紧随其后可能还有同番号的其他版本、
字幕、甚至相邻的几个番号。如果每收到一个文件就立刻催 CMS 扫描，CMS 会在
文件还没落齐时开跑，扫到一半又要重来。

所以唤醒不再「等固定 N 秒然后发请求」，而是走一个防抖窗口：

* 第一次唤醒 -> 开窗，``CMS_DEBOUNCE_SEC`` 秒后真正发请求；
* 窗口内又来了新的入库通知 -> **取消旧窗口，重新计时**；
* 窗口自然结束 -> 合并成一个请求发出去，窗口内累计 N 次只发 1 次。

调用方（洗板线程）因此不再被 ``time.sleep`` 阻塞，可以立刻返回。
"""

from __future__ import annotations

import threading
import time

import requests

from .. import config
from ..logging_setup import logger

# 落盘缓冲（防抖）窗口，单位秒。窗口内每来一次新通知就整体重置。
CMS_DEBOUNCE_SEC = 10.0

_LOCK = threading.RLock()
_timer: threading.Timer | None = None
_deadline = 0.0
_pending = 0
_last_reason = ""


# ----------------------------------------------------------------------
# 状态查询
# ----------------------------------------------------------------------
def debounce_seconds() -> float:
    """当前防抖窗口长度（秒）。"""
    return CMS_DEBOUNCE_SEC


def pending_count() -> int:
    """窗口内累计待发送的唤醒次数。"""
    with _LOCK:
        return _pending


def is_pending() -> bool:
    """是否有一个已开窗、尚未发出的唤醒。"""
    with _LOCK:
        return _timer is not None


def pending_seconds() -> float:
    """距离当前窗口结束还有多少秒；没有待发请求时返回 0。"""
    with _LOCK:
        if _timer is None:
            return 0.0
        return max(0.0, _deadline - time.monotonic())


# ----------------------------------------------------------------------
# 内部
# ----------------------------------------------------------------------
def _cancel_locked() -> None:
    global _timer
    if _timer is not None:
        try:
            _timer.cancel()
        except Exception:  # noqa: BLE001
            pass
        _timer = None


def _fire() -> None:
    """窗口结束：发一次真实请求。"""
    global _timer, _pending, _last_reason, _deadline
    with _LOCK:
        count = _pending
        _pending = 0
        _last_reason = ""
        _deadline = 0.0
        _timer = None

    webhook = config.get_strm_webhook()
    if not webhook:
        return
    if config.get_dry_run():
        logger.info("   [🔔 主动唤醒] [Dry Run] 防抖窗口结束，跳过真实请求发送。")
        return

    if count > 1:
        logger.info(f"   [-] ⏱️ 落盘缓冲窗口内累计 {count} 次入库通知，合并为 1 次唤醒。")
    try:
        r = requests.get(webhook, timeout=5)
        logger.info(f"   [-] ✅ CMS 唤醒成功，状态码: {r.status_code}")
    except Exception as e:  # noqa: BLE001
        logger.error(f"   [-] ❌ CMS 唤醒失败: {e}")


# ----------------------------------------------------------------------
# 对外入口
# ----------------------------------------------------------------------
def trigger_cms(reason: str = "触发 STRM 生成") -> None:
    """请求唤醒 CMS；走落盘缓冲窗口，窗口内重复调用会重新计时。"""
    global _timer, _pending, _last_reason, _deadline

    webhook = config.get_strm_webhook()
    if not webhook:
        return
    if config.get_dry_run():
        logger.info(f"   [🔔 主动唤醒] [Dry Run] 模拟 {reason}，跳过真实请求发送。")
        return

    with _LOCK:
        _pending += 1
        _last_reason = reason
        count = _pending
        reset = _timer is not None
        _cancel_locked()
        _deadline = time.monotonic() + CMS_DEBOUNCE_SEC
        timer = threading.Timer(CMS_DEBOUNCE_SEC, _fire)
        timer.daemon = True
        _timer = timer
        timer.start()

    wait = f"{CMS_DEBOUNCE_SEC:g}"
    if reset:
        logger.info(
            f"   [🔔 主动唤醒] {reason}，落盘缓冲窗口内又来了新通知，"
            f"重新计时 {wait} 秒（本窗口已累计 {count} 次）。"
        )
    else:
        logger.info(
            f"   [🔔 主动唤醒] {reason}，等待 {wait} 秒落盘缓冲后向 CMS 发送指令"
            f"（窗口内再来新文件会重新计时）..."
        )


def flush_now() -> bool:
    """立刻结束当前窗口并发送（测试与优雅退出用）。返回此前是否有待发请求。"""
    with _LOCK:
        had = _timer is not None
        _cancel_locked()
    if had:
        _fire()
    return had


def reset() -> None:
    """清空内部状态（测试用）。"""
    global _timer, _pending, _last_reason, _deadline
    with _LOCK:
        _cancel_locked()
        _pending = 0
        _last_reason = ""
        _deadline = 0.0
