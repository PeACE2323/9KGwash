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
    # 可包含内置档位（UC / C / U / NORMAL）与自定义档位（custom:<id>）
    "WEIGHT_ORDER": ["UC", "C", "U", "NORMAL"],
    # 自定义权重档位：[{"id":"xxx","name":"4K","keywords":["4K","2160p"]}]
    "WEIGHT_CUSTOM": [],
    # 空间猎犬扫描路径（空 = 使用元数据侧监听目录）
    "HOUND_SCAN_PATHS": [],
    # 影片查重扫描路径（空 = 使用元数据侧监听目录）
    "DEDUP_SCAN_PATHS": [],
    # 元数据子路径覆盖：当网盘侧与元数据侧层级不一致时使用。
    # 例：网盘侧 /115open/9KG/STRM 而元数据侧只有 /STRM，
    #     则设 "META_SUBDIR_OVERRIDE": "/STRM"
    "META_SUBDIR_OVERRIDE": "",
}

# ---------------------------------------------------------------------------
# 洗板权重档位
# ---------------------------------------------------------------------------
# 内置档位固定 4 个，含义不可变，只有「先后顺序」可调。
# 自定义档位（带关键字的）可以和内置档位一起混排。
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

# 自定义档位在 WEIGHT_ORDER 里的前缀，便于与内置档位区分
CUSTOM_TIER_PREFIX = "custom:"

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


def normalize_weight_custom(raw) -> list[dict]:
    """规范化自定义权重档位。

    每个档位：
        {"id": "...", "name": "4K", "keywords": ["4K", "2160p"]}
    - id 自动生成，保证唯一
    - name 为空时用「自定义N」
    - keywords 统一为去重后的字符串列表（不分大小写去重）
    - 没有任何关键字或名字的档位直接丢弃
    """
    if not isinstance(raw, (list, tuple)):
        return []

    out: list[dict] = []
    used_ids: set[str] = set()
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            continue

        name = str(item.get("name") or "").strip()

        kws = item.get("keywords")
        if isinstance(kws, str):
            kws = [x for x in kws.replace("，", ",").replace("、", ",").replace("\n", ",").split(",")]
        if not isinstance(kws, (list, tuple)):
            kws = []

        clean: list[str] = []
        seen: set[str] = set()
        for k in kws:
            v = str(k).strip()
            if not v:
                continue
            key = v.lower()
            if key in seen:
                continue
            seen.add(key)
            clean.append(v)

        if not clean:
            continue
        if not name:
            name = f"自定义{idx + 1}"

        cid = str(item.get("id") or "").strip()
        # 生成稳定且唯一的 id
        if not cid or cid in used_ids:
            base = f"c{idx + 1}"
            cid = base
            n = 2
            while cid in used_ids:
                cid = f"{base}_{n}"
                n += 1
        used_ids.add(cid)

        out.append({"id": cid, "name": name, "keywords": clean})

    return out


def _all_valid_tiers(custom: list[dict]) -> list[str]:
    """当前全部合法档位：内置 4 档 + 自定义档。"""
    return list(WEIGHT_TIERS) + [CUSTOM_TIER_PREFIX + c["id"] for c in custom]


def normalize_weight_order(raw, custom: list[dict] | None = None) -> list[str]:
    """把任意输入规范为合法且完整的档位顺序（高 -> 低）。

    - 只保留合法档位，去重
    - 缺失的内置档位按默认顺序补到末尾（保证 4 个内置档始终都在）
    - 自定义档位若没出现在顺序里，按定义顺序追加到最后
    """
    if custom is None:
        custom = normalize_weight_custom(current_config().get("WEIGHT_CUSTOM"))

    valid = _all_valid_tiers(custom)

    if isinstance(raw, str):
        raw = [x.strip() for x in raw.replace("，", ",").split(",")]
    order: list[str] = []
    for t in raw or []:
        key = str(t).strip()
        # 内置档位统一大写；自定义档位保持原样（前缀已小写）
        if not key.lower().startswith(CUSTOM_TIER_PREFIX):
            key = key.upper()
        if key in valid and key not in order:
            order.append(key)

    for t in DEFAULT_WEIGHT_ORDER:
        if t not in order:
            order.append(t)

    for c in custom:
        t = CUSTOM_TIER_PREFIX + c["id"]
        if t not in order:
            order.append(t)

    return order


def _build_weight_map(order: list[str]) -> dict:
    """顺序 -> 权重：第一个（最优先）权重最高。

    仅 4 内置档时为 3 / 2 / 1 / 0，与原有 UC>C>U>普通 完全等价。
    加入 n 个自定义档后，总档位数 N，最高档权重 N-1，最低 0。
    """
    n = len(order)
    return {tier: (n - 1 - idx) for idx, tier in enumerate(order)}


def _apply_runtime(conf: dict) -> None:
    _runtime["DRY_RUN"] = bool(conf.get("DRY_RUN", True))
    _runtime["DB_ONLINE_WEBHOOK"] = conf.get("DB_ONLINE_WEBHOOK", "") or ""
    _runtime["STRM_WEBHOOK"] = conf.get("STRM_WEBHOOK", "") or ""
    _runtime["IGNORE_LEADING_DIGITS"] = bool(conf.get("IGNORE_LEADING_DIGITS", True))

    custom = conf.get("WEIGHT_CUSTOM")
    if not isinstance(custom, list):
        custom = normalize_weight_custom(custom)
    order = normalize_weight_order(conf.get("WEIGHT_ORDER"), custom)
    _runtime["WEIGHT_MAP"] = _build_weight_map(order)
    _runtime["WEIGHT_ORDER"] = order
    _runtime["WEIGHT_CUSTOM"] = custom


def get_weight_order() -> list[str]:
    """当前生效的权重档位顺序（高 -> 低，含自定义档）。"""
    with _lock:
        order = _runtime.get("WEIGHT_ORDER")
        if order:
            return list(order)
        return list(_config.get("WEIGHT_ORDER") or DEFAULT_WEIGHT_ORDER)


def get_weight_custom() -> list[dict]:
    """当前生效的自定义权重档位。"""
    with _lock:
        custom = _runtime.get("WEIGHT_CUSTOM")
        if custom is None:
            custom = _config.get("WEIGHT_CUSTOM") or []
        return [dict(c) for c in custom]


def get_weight_map() -> dict:
    with _lock:
        m = _runtime.get("WEIGHT_MAP")
        if not m:
            m = _build_weight_map(DEFAULT_WEIGHT_ORDER)
        return dict(m)


def get_weight_for_tier(tier: str) -> int:
    """取某档位的当前权重值；未知档位回退为最低。

    注意：内置档位键是大写的（UC/C/U/NORMAL），
    自定义档位键是 ``custom:<id>``（小写前缀），
    不能统一 upper()，否则自定义档永远查不到（会误判成最低权重）。
    """
    key = str(tier or "").strip()
    if not key.lower().startswith(CUSTOM_TIER_PREFIX):
        key = key.upper()
    m = get_weight_map()
    return m.get(key, 0)


def normalize_config(cfg: dict) -> dict:
    """对配置做统一的规范化处理（默认值合并 + 字段校验）。

    内存缓存与落盘都应经过这里，避免「文件里的值与内存里的值不一致」。

    监听目录统一存成「相对网盘根」的形式：
    无论用户填 `/9KG/STRM` 还是 `/media/115open/9KG/STRM`，
    都会归一成 `/9KG/STRM`，避免网盘根被重复拼接。
    """
    out = dict(DEFAULT_CONFIG)
    out.update(cfg or {})

    # 先规范自定义档位，再据此补全顺序（顺序里可能含 custom:<id>）
    out["WEIGHT_CUSTOM"] = normalize_weight_custom(out.get("WEIGHT_CUSTOM"))
    out["WEIGHT_ORDER"] = normalize_weight_order(out.get("WEIGHT_ORDER"), out["WEIGHT_CUSTOM"])

    mount = _norm_slash(out.get("MOUNT_BASE_PATH") or "")
    raw_dirs = out.get("WATCHED_DIRS") or []
    if isinstance(raw_dirs, str):
        raw_dirs = [x for x in raw_dirs.replace("\n", ",").split(",")]

    normed: list[str] = []
    seen: set[str] = set()
    for raw in raw_dirs:
        v = _norm_slash(raw)
        if not v:
            continue
        rel, err = resolve_watch_dir(v, mount)
        if err or not rel:
            continue
        if rel in seen:
            continue
        seen.add(rel)
        normed.append(rel)
    out["WATCHED_DIRS"] = normed

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
        # 先归一（监听目录转相对路径、权重档位校验等），
        # 保证「内存里的值」与「文件里的值」完全一致。
        normalized = normalize_config(current)
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(CONFIG_PATH, "w", encoding="utf-8") as f:
                json.dump(normalized, f, ensure_ascii=False, indent=4)
            # 同步更新内存缓存，否则 current_config() 仍读旧值，
            # 导致「保存成功但必须重启才生效」。
            _config = normalized
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


def _norm_path_list(raw) -> list[str]:
    """把路径列表规范化为去重后的字符串列表（保留用户原样顺序）。"""
    if raw is None:
        return []
    items = raw if isinstance(raw, (list, tuple)) else str(raw).replace("\n", ",").split(",")
    out, seen = [], set()
    for it in items:
        v = _norm_slash(it)
        if not v or v == "/":
            continue
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def hound_scan_paths() -> list[str]:
    """空间猎犬的扫描路径；未配置时回退到元数据侧监听目录。"""
    paths = _norm_path_list(current_config().get("HOUND_SCAN_PATHS"))
    return paths or default_meta_scan_paths()


def dedup_scan_paths() -> list[str]:
    """影片查重的扫描路径；未配置时回退到元数据侧监听目录。"""
    paths = _norm_path_list(current_config().get("DEDUP_SCAN_PATHS"))
    return paths or default_meta_scan_paths()


def default_meta_scan_paths() -> list[str]:
    """默认扫描路径：各监听目录对应的元数据侧路径。"""
    out = []
    for w in watched_dirs():
        p = meta_path_for(w)
        if p and p not in out:
            out.append(p)
    return out


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


def resolve_watch_dir(raw: str, mount_base: str | None = None) -> tuple[str, str | None]:
    """把监听目录输入解析为 (相对路径, 提示信息)。

    支持两种填法，避免用户混淆：

      A) 相对路径（历史写法）
         /115open/9KG/STRM
         → 原样作为相对路径

      B) 完整路径（易读、不易填错）
         /media/115open/9KG/STRM 且 网盘根 = /media
         → 自动剥离网盘根，得到 /115open/9KG/STRM

    关键防错：如果用户填的完整路径里已经包含网盘根，
    绝不再拼接一次，否则会出现
        /media/115open + /media/115open/9KG/STRM
        = /media/115open/media/115open/9KG/STRM
    这种重复两遍的畸形路径。
    """
    w = _norm_slash(raw)
    if not w or w == "/":
        return "", "监听目录不能为空"

    mb = _norm_slash(mount_base if mount_base is not None else mount_base_path())
    mb = mb.rstrip("/")

    # B) 完整路径：以网盘根开头 -> 剥离出相对部分
    if mb and mb != "/" and (w == mb or w.startswith(mb + "/")):
        rel = w[len(mb):]
        if not rel or rel == "/":
            return "", "监听目录不能等于网盘根目录本身"
        return rel, None

    # A) 相对路径：原样
    return w, None


def watch_dir_display(raw: str, mount_base: str | None = None) -> str:
    """返回监听目录的「完整路径」，用于界面展示（避免用户困惑相对/绝对）。"""
    rel, err = resolve_watch_dir(raw, mount_base)
    if err or not rel:
        return _norm_slash(raw)
    return _join(mount_base if mount_base is not None else mount_base_path(), rel)


def meta_subdir_for(watch_dir: str, meta_base: str | None = None) -> str:
    """由监听目录推导它在「元数据根」下的相对子路径（以 / 开头）。

    推导顺序：
      1) WATCHED_META_SUBDIRS 里对该监听目录的显式映射（最优先）
      2) 全局 META_SUBDIR_OVERRIDE（所有监听目录统一用这个子路径）
      3) 归一掉网盘根后，去掉 /115open 这一层
      4) 其余原样保留

    之所以需要 2)：用户的 Docker 映射可能让网盘侧和元数据侧层级不一致，例如
        网盘侧 /clouddrive/115open/9KG/STRM
        元数据侧 /media/STRM
    这时监听目录 /9KG/STRM 在元数据侧实际应为 /STRM，
    只能靠显式配置表达。

    刻意不做「首层同名就去掉」这类启发式猜测：
    元数据侧往往也存在 9KG 这样的同名目录，猜测会把路径改错，
    反而导致元数据清理找不到文件。
    """
    # 先归一：若填的是完整路径，剥离网盘根后再推导
    w, _err = resolve_watch_dir(watch_dir)
    if not w:
        w = _norm_slash(watch_dir)
    if w == "/":
        return "/"

    cfg = current_config()

    # 1) 针对具体监听目录的显式覆盖（同时兼容原始写法与归一后的写法）
    overrides = cfg.get("WATCHED_META_SUBDIRS") or {}
    if isinstance(overrides, dict):
        for key in (watch_dir, w):
            if key in overrides:
                return _norm_slash(overrides[key]) or "/"

    # 2) 全局统一覆盖
    global_sub = cfg.get("META_SUBDIR_OVERRIDE")
    if global_sub not in (None, ""):
        sub = _norm_slash(global_sub)
        # 允许写成 "/STRM" 或 "STRM"
        if sub and sub != "/":
            return sub

    # 3) 历史约定：/115open/... 去掉这层（元数据侧不存在 115open）
    for prefix in LEGACY_MOUNT_ONLY_PREFIXES:
        if w == prefix:
            return "/"
        if w.startswith(prefix + "/"):
            return w[len(prefix):]

    # 4) 原样
    return w


def meta_path_for(watch_dir: str) -> str:
    """监听目录对应的元数据绝对路径。"""
    mb = _norm_slash(metadata_base_path())
    sub = meta_subdir_for(watch_dir, mb)
    return _join(mb, sub)


def mount_path_for(watch_dir: str) -> str:
    """监听目录对应的网盘绝对路径。

    自动兼容「相对路径」与「已含网盘根的完整路径」两种写法，
    不会把网盘根重复拼接两遍。
    """
    mb = _norm_slash(mount_base_path())
    rel, err = resolve_watch_dir(watch_dir, mb)
    if err or not rel:
        return _norm_slash(watch_dir)
    return _join(mb, rel)
