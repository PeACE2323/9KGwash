"""CMS 唤醒的落盘缓冲（防抖）测试。

背景
----
CD2 是逐文件推送的：一部影片入库后往往还跟着字幕、同番号的其他版本、
相邻的几个番号。原来的实现是「固定等 2 秒，然后立刻发请求」，有两个问题：

  ① 2 秒常常不够，文件还在落盘时 CMS 就被催着开跑；
  ② 每个文件各发一次，一批 5 个文件就催了 CMS 5 次。

现在的实现是一个 10 秒的**防抖窗口**：

  第一次请求开窗 -> 窗口内再来新通知就整体重新计时
  -> 窗口自然结束后合并成「一次」请求发出去。

调用方（洗板线程）不再被 ``time.sleep`` 阻塞，触发完就返回。
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(BASE, "backend"))

TMP = os.environ.get("XIBAN_TEST_TMP") or os.path.join(BASE, "tests", "_tmp_cms_debounce")
shutil.rmtree(TMP, ignore_errors=True)
CFG = os.path.join(TMP, "cfg")
os.makedirs(CFG)

os.environ["XIBAN_CONFIG_DIR"] = CFG
os.environ["XIBAN_PRINT_BANNER"] = "0"
os.environ["XIBAN_DISABLE_WATCHER"] = "1"

URL = "http://127.0.0.1:9/api/trigger"

BASE_CFG = {
    "MOUNT_BASE_PATH": "/mnt/cloud/115open",
    "METADATA_BASE_PATH": "/mnt/meta",
    "WATCHED_DIRS": ["/9KG/STRM"],
    "STRM_WEBHOOK": URL,
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
from app.services import cms, meta_backup  # noqa: E402

config.init_config()
config.reload_config()

PASS = FAIL = 0


def check(name, cond, extra=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        print(f"  ❌ {name} {extra}")


def read_text(path):
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------------
# 假的 requests.get：记录调用，不真的发网络请求
# ----------------------------------------------------------------------
CALLS = []


class _FakeResp:
    status_code = 200


class _FakeRequests:
    @staticmethod
    def get(url, timeout=None, **_kw):
        CALLS.append({"url": url, "timeout": timeout})
        return _FakeResp()


_REAL_REQUESTS = cms.requests


def use_fake():
    cms.requests = _FakeRequests


def use_real():
    cms.requests = _REAL_REQUESTS


# ----------------------------------------------------------------------
# 日志捕获
# ----------------------------------------------------------------------
class Capture(logging.Handler):
    def __init__(self):
        super().__init__(level=logging.DEBUG)
        self.lines = []

    def emit(self, record):
        try:
            self.lines.append(record.getMessage())
        except Exception:  # noqa: BLE001
            pass

    def find(self, needle):
        return [ln for ln in self.lines if needle in ln]


CAP = Capture()
cms.logger.addHandler(CAP)
# banner 关闭时日志级别可能被调到 WARNING，这里临时放开，才能捕获 INFO 文案
_OLD_LEVEL = cms.logger.level
cms.logger.setLevel(logging.DEBUG)

W = 0.4  # 测试用的短窗口，真实值是 10 秒


def set_window(v):
    cms.CMS_DEBOUNCE_SEC = v
    cms.reset()


def use_cfg(extra=None):
    write_cfg(extra)
    config.reload_config()


# ======================================================================
print("\n[A] 默认窗口长度与实现方式")
SRC = read_text(os.path.join(BASE, "app", "services", "cms.py"))

check("CMS_DEBOUNCE_SEC 默认 10 秒", cms.CMS_DEBOUNCE_SEC == 10.0, cms.CMS_DEBOUNCE_SEC)
check("debounce_seconds() 返回 10", cms.debounce_seconds() == 10.0)
check("源码里已无 time.sleep(2)", "time.sleep(2)" not in SRC)
check("源码里已完全没有阻塞式 sleep", "time.sleep(" not in SRC, "仍然存在 time.sleep(...)")
check("源码声明 10 秒窗口常量", "CMS_DEBOUNCE_SEC = 10.0" in SRC)
check("源码用 threading.Timer 实现", "threading.Timer" in SRC)
check("有 flush_now 供测试/退出使用", "def flush_now" in SRC)
check("有 reset 清理状态", "def reset" in SRC)

# ======================================================================
print("\n[B] 没有 webhook / Dry Run 时不开窗")
use_fake()
use_cfg({"STRM_WEBHOOK": ""})
set_window(W)
CALLS.clear()
cms.trigger_cms("测试")
check("未配置 webhook 不开窗", not cms.is_pending())
check("未配置 webhook pending_count 为 0", cms.pending_count() == 0)
time.sleep(W + 0.15)
check("未配置 webhook 不发请求", CALLS == [], CALLS)

use_cfg({"STRM_WEBHOOK": URL, "DRY_RUN": True})
set_window(W)
CALLS.clear()
CAP.lines.clear()
cms.trigger_cms("测试")
check("Dry Run 不开窗", not cms.is_pending())
time.sleep(W + 0.15)
check("Dry Run 不发请求", CALLS == [], CALLS)
check("Dry Run 有明确的模拟日志", bool(CAP.find("Dry Run")))

# ======================================================================
print("\n[C] 单次触发：窗口结束后只发一次")
use_cfg({"STRM_WEBHOOK": URL, "DRY_RUN": False})
set_window(W)
CALLS.clear()
CAP.lines.clear()

t0 = time.monotonic()
cms.trigger_cms("发现新文件落盘(仅单队列)")
spent = time.monotonic() - t0

check("trigger_cms 立即返回（不阻塞调用方）", spent < 0.15, f"{spent:.3f}s")
check("触发后进入 pending 状态", cms.is_pending())
check("窗口未结束时还没有请求", CALLS == [], CALLS)
time.sleep(W * 0.5)
check("窗口过半仍未发请求", CALLS == [], CALLS)
time.sleep(W * 0.7)

check("窗口结束后发出 1 次请求", len(CALLS) == 1, CALLS)
check("请求地址正确", CALLS and CALLS[0]["url"] == URL, CALLS)
check("请求带 5 秒超时", CALLS and CALLS[0]["timeout"] == 5, CALLS)
check("日志出现唤醒成功", bool(CAP.find("CMS 唤醒成功")))
check("结束后退出 pending 状态", not cms.is_pending())

# ======================================================================
print("\n[D] 窗口内来了新文件 -> 整体重新计时")
set_window(0.5)
CALLS.clear()
CAP.lines.clear()

cms.trigger_cms("发现新文件落盘(仅单队列)")
time.sleep(0.3)
cms.trigger_cms("洗板判定与清理流程结束")  # 重置到 t=0.8
time.sleep(0.3)  # t=0.6，旧窗口本应在此结束

check("重置后旧窗口不会提前触发", CALLS == [], CALLS)
time.sleep(0.35)  # t≈0.95
check("新窗口结束后只发 1 次", len(CALLS) == 1, CALLS)
check("多了一次通知但只用 1 个请求", len(CALLS) == 1)

# ======================================================================
print("\n[E] 日志把「重置」和「合并」说清楚")
set_window(W)
CALLS.clear()
CAP.lines.clear()

cms.trigger_cms("发现新文件落盘(仅单队列)")
sleep_line1 = list(CAP.lines)
time.sleep(0.1)
cms.trigger_cms("洗板判定与清理流程结束")
sleep_line2 = list(CAP.lines)

check("首次触发说明等待窗口", any("落盘缓冲后向 CMS 发送指令" in l for l in sleep_line1))
check("首次触发提示窗口内会重新计时", any("窗口内再来新文件会重新计时" in l for l in sleep_line1))
check("二次触发说明重新计时", any("又来了新通知" in l for l in sleep_line2))
check("二次触发写明累计次数", any("已累计 2 次" in l for l in sleep_line2))
cms.flush_now()
check("窗口结束后说明合并", bool(CAP.find("合并为 1 次唤醒")))

# ======================================================================
print("\n[F] 状态查询")
set_window(1.0)
CALLS.clear()
cms.trigger_cms("A")
check("pending_count = 1", cms.pending_count() == 1, cms.pending_count())
cms.trigger_cms("B")
check("pending_count 累计到 2", cms.pending_count() == 2, cms.pending_count())
p = cms.pending_seconds()
check("pending_seconds 在窗口范围内", 0 < p <= 1.0, p)
check("debounce_seconds 跟随当前窗口", cms.debounce_seconds() == 1.0)
cms.reset()
check("reset 后不再 pending", not cms.is_pending())
check("reset 后 pending_seconds 归零", cms.pending_seconds() == 0.0)
check("reset 后 pending_count 归零", cms.pending_count() == 0)

# ======================================================================
print("\n[G] flush_now 立即发送")
set_window(5.0)
CALLS.clear()
CAP.lines.clear()
cms.trigger_cms("A")
t0 = time.monotonic()
had = cms.flush_now()
spent = time.monotonic() - t0

check("flush_now 返回 True", had is True)
check("flush_now 立即发出请求", len(CALLS) == 1, CALLS)
check("flush_now 不必等满 5 秒窗口", spent < 0.2, f"{spent:.3f}s")
check("flush 后退出 pending", not cms.is_pending())
check("空窗口 flush 返回 False", cms.flush_now() is False)

# ======================================================================
print("\n[H] 连续 5 次入库只唤醒一次")
set_window(0.3)
CALLS.clear()
for i in range(5):
    cms.trigger_cms(f"第 {i+1} 个新文件")
    time.sleep(0.03)
time.sleep(0.55)
check("5 次连续触发只发 1 个请求", len(CALLS) == 1, len(CALLS))

# ======================================================================
print("\n[I] 各洗板入口仍然会请求唤醒")
WASHER = read_text(os.path.join(BASE, "app", "services", "washer.py"))
LIB = read_text(os.path.join(BASE, "app", "services", "library.py"))
README = read_text(os.path.join(BASE, "..", "README.md"))

check("单队列新文件仍唤醒", 'cms.trigger_cms("发现新文件落盘(仅单队列)")' in WASHER)
check("洗板结束仍唤醒", 'cms.trigger_cms("洗板判定与清理流程结束")' in WASHER)
check("查重洗板结束仍唤醒", 'cms.trigger_cms("查重洗板执行完毕")' in LIB)
check("元数据备份延迟已顺延到 45 秒", meta_backup.BACKUP_DELAY_SEC == 45, meta_backup.BACKUP_DELAY_SEC)
check("README 写明 10 秒缓冲", "10 秒" in README and "防抖" in README)

# ======================================================================
use_real()
cms.reset()
cms.CMS_DEBOUNCE_SEC = 10.0
cms.logger.removeHandler(CAP)
cms.logger.setLevel(_OLD_LEVEL)

print("\n" + "=" * 50)
print(f"结果: {PASS} 通过, {FAIL} 失败")
print("=" * 50)
shutil.rmtree(TMP, ignore_errors=True)
sys.exit(1 if FAIL else 0)
