"""番号解析 / 画质权重 / 版本聚类 / 主番号复制。

本模块是纯函数层，逻辑与原脚本逐行对齐（正则、大小写、边界条件完全一致），
便于单元测试与后续功能扩展。
"""

from __future__ import annotations

import os
import re

from .. import config

# 番号主体：字母数字 + "-" + 数字
_FANHAO_RE = re.compile(r"([a-zA-Z0-9]+-\d+)")
_LEADING_DIGITS_RE = re.compile(r"^\d+([a-zA-Z])")
_JAV_RE = re.compile(r"[a-zA-Z0-9]+-\d+")

# 分盘标记（CD1 / PARTA / -A / _B ...）
_PART_RE = re.compile(r"(CD[1-9]|PART[A-E]|[-_][AB](?![A-Z0-9]))")
# 中文字幕 / 破解等偏码标记
_HAS_C_RE = re.compile(r"([-_]C(?![A-Z0-9])|[-_]UC(?![A-Z0-9])|[-_]CU(?![A-Z0-9])|中字|字幕)")
_HAS_U_RE = re.compile(r"([-_]U(?![A-Z0-9])|[-_]UC(?![A-Z0-9])|[-_]CU(?![A-Z0-9])|无码|破解|流出)")
# 分盘后缀剥离
_PART_STRIP_RE = re.compile(
    r"[-_]?(CD[1-9]|PART[A-E])(?![a-zA-Z0-9])$|[-_][AB](?![a-zA-Z0-9])$", re.IGNORECASE
)

VIDEO_EXTS = (".mp4", ".mkv", ".avi", ".ts", ".wmv", ".iso", ".rmvb", ".flv", ".m2ts")
LOCAL_MEDIA_EXTS = (".strm",) + VIDEO_EXTS
MOVIE_AUX_DIRS = {
    "extrafanart",
    "extrathumbs",
    ".actors",
    "theme-videos",
    "theme-music",
    "trailers",
    "sample",
    "subs",
    "subtitles",
}


# ---------------------------------------------------------------------------
# 番号解析
# ---------------------------------------------------------------------------
def is_jav(text: str | None) -> bool:
    if not text:
        return False
    return bool(_JAV_RE.search(text))


def parse_fanhao_info(text: str | None):
    """返回 (canonical, raw, has_leading)。

    - raw            原样大写番号，如 ``100ABC-123``
    - canonical      去掉前置数字后的标准番号，如 ``ABC-123``
    - has_leading    是否存在前置数字
    """
    if not text:
        return None, None, False
    match = _FANHAO_RE.search(text)
    if match:
        raw = match.group(1).upper()
        stripped = _LEADING_DIGITS_RE.sub(r"\1", raw)
        has_leading = stripped != raw
        canonical = stripped if has_leading else raw
        return canonical, raw, has_leading
    return None, None, False


def extract_pure_fanhao(text: str, apply_ignore: bool = False) -> str:
    canonical, raw, _ = parse_fanhao_info(text)
    if not canonical:
        return (text or "").strip()
    if apply_ignore and config.get_ignore_leading_digits():
        return canonical
    return raw


def get_weight_and_fanhao(filename: str):
    """返回 (root_fanhao, weight, is_part)。

    权重由「档位顺序」决定，可在前端拖动调整。
    默认档位（高 -> 低）：UC > C > U > NORMAL，对应权重 3 / 2 / 1 / 0。

    档位含义：
        UC      = 中字 + 无码/破解（同时含 C 与 U 标记）
        C       = 仅中字
        U       = 仅无码/破解
        NORMAL  = 普通，无上述标记
    """
    basename = os.path.splitext(filename)[0]
    canonical, raw, _ = parse_fanhao_info(basename)

    if canonical:
        upper_base = basename.upper()
        is_part = bool(_PART_RE.search(upper_base))
        has_c = bool(_HAS_C_RE.search(upper_base))
        has_u = bool(_HAS_U_RE.search(upper_base))

        # 判定所属档位
        if has_c and has_u:
            tier = "UC"
        elif has_c:
            tier = "C"
        elif has_u:
            tier = "U"
        else:
            tier = "NORMAL"

        weight = config.get_weight_for_tier(tier)

        root_fanhao = canonical if config.get_ignore_leading_digits() else raw
        return root_fanhao, weight, is_part
    # 无番号：不参与洗板对决，权重固定为最低，不受档位拖动影响
    return basename.strip(), -1, False


def version_hash(file_info: dict) -> str:
    """同一「版本」的特征键：主干名 | 扩展名 | 权重 | 是否分盘。"""
    path = file_info.get("path") or file_info.get("name") or ""
    ext = os.path.splitext(path)[1].lower()
    if not file_info["is_part"]:
        stripped = file_info["basename"]
    else:
        stripped = _PART_STRIP_RE.sub("", file_info["basename"])
    return f"{stripped}|{ext}|{file_info['weight']}|{file_info['is_part']}"


def part_stripped_basename(basename: str) -> str:
    return _PART_STRIP_RE.sub("", basename)


def sort_key(file_info: dict):
    """王者排序键：权重 > 文件夹时间 > 文件时间（均为降序）。"""
    return (file_info["weight"], file_info["folder_mtime"], file_info["file_mtime"])


def is_aux_dir(name: str) -> bool:
    return name.lower() in MOVIE_AUX_DIRS


# ---------------------------------------------------------------------------
# 主番号（合轴）一键复制：把番号、番号-破解、番号-C 等全部归一为「主番号」
# ---------------------------------------------------------------------------
def main_fanhao(text: str | None) -> str:
    """从任意文件名 / 描述中提取「主番号」。

    例：``ABF-391-破解`` -> ``ABF-391``；``SSNI-497-C`` -> ``SSNI-497``；
    ``100ABC-123`` -> ``ABC-123``（受 IGNORE_LEADING_DIGITS 控制）。
    非 JAV 文本则原样（去扩展名、去首尾空白）返回。
    """
    if not text:
        return ""
    base = os.path.splitext(os.path.basename(str(text).strip()))[0]
    canonical, raw, _ = parse_fanhao_info(base)
    if not canonical:
        return base.strip()
    root = canonical if config.get_ignore_leading_digits() else raw
    # 主番号只保留 根番号-数字 这一段，丢弃其后的一切标签
    m = _FANHAO_RE.match(root)
    return m.group(1) if m else root
