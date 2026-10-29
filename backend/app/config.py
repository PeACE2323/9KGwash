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
    # 洗板权重档位顺序（高 -> 低），可在前端拖动调整
    "WEIGHT_ORDER": ["UC", "C", "U", "NORMAL"],
}

# ---------------------------------------------------------------------------
# 洗板权重档位
# ---------------------------------------------------------------------------
# 档位固定 4 个，含义不可变，只有「先后顺序」可调。
WEIGHT_TIERS = ("UC", "U", "C", "NORMAL")
DEFAULT_WEIGHT_ORDER = ["UC", "C", "U", "NORMAL"]
WEIGHT_TIER_LABELS = {
    "UC": "UC · 中字+无码",
    "C": "C · 中字",
    "U": "U · 无码/破解",
    "NORMAL": "普通",
}
WEIGHT_TIER_DESC = {
    "UC": "文件名同时含中字(C)与无码/破解(U)标记",
    "C": "仅含中字标记（中字/字幕/-C）",
    "U": "仅含无码/破解标记（无码/破解/流出/-U）",
    "NORMAL": "不含上述标记的普通版本",
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
    # 档位 -> 权重 的映射（数字越大越优先）
    "WEIGHT_MAP": {},
}


def normalize_weight_order(raw) -> list[str]:
    """把任意输入规范为合法且完整的 4 档顺序。

    - 只保留合法档位，去重
    - 缺失的档位按默认顺序补到末尾（保证永远 4 个）
    """
    if isinstance(raw, str):
        raw = [x.strip() for x in raw.replace("，", ",").split(",")]
    order = []
    for t in raw or []:
        key = str(t).strip().upper()
        if key in WEIGHT_TIERS and key not in order:
            order.append(key)
    for t in DEFAULT_WEIGHT_ORDER:
        if t not in order:
            order.append(t)
    return order


def _build_weight_map(order: list[str]) -> dict:
    """顺序 -> 权重：第一个（最优先）权重最高。

    4 档时为 3 / 2 / 1 / 0，与原有 UC>C>U>普通 完全等价。
    """
    n = len(order)
    return {tier: (n - 1 - idx) for idx, tier in enumerate(order)}


def _apply_runtime(conf: dict) -> None:
    _runtime["DRY_RUN"] = bool(conf.get("DRY_RUN", True))
    _runtime["DB_ONLINE_WEBHOOK"] = conf.get("DB_ONLINE_WEBHOOK", "") or ""
    _runtime["STRM_WEBHOOK"] = conf.get("STRM_WEBHOOK", "") or ""
    _runtime["IGNORE_LEADING_DIGITS"] = bool(conf.get("IGNORE_LEADING_DIGITS", True))
    order = normalize_weight_order(conf.get("WEIGHT_ORDER"))
    _runtime["WEIGHT_MAP"] = _build_weight_map(order)


def get_weight_order() -> list[str]:
    """当前生效的权重档位顺序（高 -> 低）。"""
    with _lock:
        return list(_config.get("WEIGHT_ORDER") or DEFAULT_WEIGHT_ORDER)


def get_weight_map() -> dict:
    with _lock:
        m = _runtime.get("WEIGHT_MAP")
        if not m:
            m = _build_weight_map(DEFAULT_WEIGHT_ORDER)
        return dict(m)


def get_weight_for_tier(tier: str) -> int:
    """取某档位的当前权重值；未知档位回退为最低。"""
    m = get_weight_map()
    return m.get(str(tier).upper(), 0)


def normalize_config(cfg: dict) -> dict:
    """对配置做统一的规范化处理（默认值合并 + 字段校验）。

    内存缓存与落盘都应经过这里，避免「文件里的值与内存里的值不一致」。
    """
    out = dict(DEFAULT_CONFIG)
    out.update(cfg or {})
    out["WEIGHT_ORDER"] = normalize_weight_order(out.get("WEIGHT_ORDER"))
    return out


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
    return normalize_config(cfg)


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
            # 关键：同步更新内存缓存，否则 current_config() 仍读旧值，
            # 导致「保存成功但必须重启才生效」。
            _config = normalize_config(current)
            return True, dict(_config)
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


# ---------------------------------------------------------------------------
# 路径映射（网盘根 <-> 元数据根）
#
# 历史实现把 "/115open/" 写死在各处做字符串替换，一旦网盘目录不叫 115open
# 推导就全部失效。这里改为「按监听目录自适应推导」：
#
#   网盘侧:  MOUNT_BASE_PATH  + WATCHED_DIR[i]
#   元数据侧: METADATA_BASE_PATH + <推导出的相对子路径>
#
# 推导规则（按优先级）：
#   1) 若监听目录以 /115open/ 开头（历史约定）-> 去掉这层
#      /115open/9KG/STRM  ->  /9KG/STRM
#   2) 若监听目录首层在元数据侧真实存在（如 /9KG 存在）-> 去掉首层
#      /9KG/STRM  ->  /STRM   （极少见，仅当 /9KG 在元数据根下存在时）
#   3) 否则原样保留
#      /9KG/STRM  ->  /9KG/STRM
#
# 也可以在配置里直接指定 WATCHED_META_SUBDIRS 覆盖推导结果。
# ---------------------------------------------------------------------------

# 历史约定的「网盘专用」首层目录名，元数据侧不存在这一层
LEGACY_MOUNT_ONLY_PREFIXES = ("/115open",)


def _norm_slash(p: str) -> str:
    """规范化路径：统一用 /，去掉重复与尾部斜杠。

    注意：不能无条件补前导 "/"。
    容器内（Linux）路径天然以 / 开头；但开发/测试在 Windows 上，
    路径形如 ``E:/workspace/media``，若强行补 "/" 会变成
    ``/E:/workspace/media``，导致路径彻底失效。
    """
    q = str(p or "").strip()
    if not q:
        return q
    q = q.replace("\\", "/")
    while "//" in q:
        q = q.replace("//", "/")
    # Windows 盘符（如 E:/）或 UNC 路径不补前导斜杠
    is_windows = len(q) >= 2 and q[1] == ":"
    if not is_windows and not q.startswith("/"):
        q = "/" + q
    return q.rstrip("/") or ("/" if not is_windows else q)


def _join(base: str, sub: str) -> str:
    """把相对子路径拼到根目录上（不做任何前导斜杠假设）。"""
    b = _norm_slash(base)
    s = _norm_slash(sub)
    if s in ("", "/"):
        return b
    return (b + s).rstrip("/")


def meta_subdir_for(watch_dir: str, meta_base: str | None = None) -> str:
    """由监听目录推导它在「元数据根」下的相对子路径（以 / 开头）。

    推导顺序：
      1) 用户显式配置的 WATCHED_META_SUBDIRS 覆盖
      2) 历史约定：/115open/... 去掉这层
      3) 其余原样保留

    刻意不做「首层同名就去掉」这类启发式猜测：
    元数据侧往往也存在 9KG 这样的同名目录，猜测会把路径改错，
    反而导致元数据清理找不到文件。需要特殊映射时用配置显式指定。
    """
    w = _norm_slash(watch_dir)
    if w == "/":
        return "/"

    # 1) 用户显式覆盖
    cfg = current_config()
    overrides = cfg.get("WATCHED_META_SUBDIRS") or {}
    if isinstance(overrides, dict) and watch_dir in overrides:
        return _norm_slash(overrides[watch_dir])

    # 2) 历史约定：/115open/... 去掉这层（元数据侧不存在 115open）
    for prefix in LEGACY_MOUNT_ONLY_PREFIXES:
        if w == prefix:
            return "/"
        if w.startswith(prefix + "/"):
            return w[len(prefix):]

    # 3) 原样
    return w


def meta_path_for(watch_dir: str) -> str:
    """监听目录对应的元数据绝对路径。"""
    mb = _norm_slash(metadata_base_path())
    sub = meta_subdir_for(watch_dir, mb)
    return _join(mb, sub)


def mount_path_for(watch_dir: str) -> str:
    """监听目录对应的网盘绝对路径。"""
    mb = _norm_slash(mount_base_path())
    w = _norm_slash(watch_dir)
    return _join(mb, w)
