"""番号智能定位与标记插入。

需求要点（来自用户）：
    www.98T.la@DASS-787無防備に黒パンスト..._restored_wm.mp4
    含关键字 restored -> 在番号后插入标记 -U
    => www.98T.la@DASS-787-U無防備に黒パンスト..._restored_wm.mp4

难点：文件名开头常有 ``www.98T.la@`` / ``[ThZu.Cc]`` / ``hhd800.com@`` 之类发布站前缀，
其中 ``98T``、``800`` 等片段可能被误判成番号。因此需要先「跳过常见前缀」再定位番号。
"""

from __future__ import annotations

import os
import re

# ---------------------------------------------------------------------------
# 常见发布站 / 水印前缀。匹配后从该位置之后开始寻找番号。
# 全部按「不分大小写」处理（用户要求）。
# ---------------------------------------------------------------------------
_PREFIX_PATTERNS = [
    # www.98T.la@  /  www.hhd800.com@  /  98t.la@  /  xxx.cc@
    r"^\s*(?:www\.)?[a-z0-9][a-z0-9\-]*\.[a-z]{2,6}\s*@\s*",
    # [ThZu.Cc]  /  【xxx】  /  (xxx)
    r"^\s*[\[【(（][^\]】)）]{1,40}[\]】)）]\s*",
    # hhd800.com@  这类无 www 前缀的域名@
    r"^\s*[a-z0-9][a-z0-9\-]*\.[a-z0-9\-]+\.[a-z]{2,6}\s*@\s*",
    # 开头的 @ 残留
    r"^\s*@+\s*",
]

# 番号主体：连续字母 + "-" + 连续数字。
# 允许 2-10 位字母（如 DASS / ABP / START / LUXU）。
# 前置可选数字厂牌（259LUXU-1900 中的 259），用 \d* 直接并入匹配，
# 这样「259LUXU-1900」会被整体识别为一个番号，而不是只认出 LUXU-1900。
_FIND_RE = re.compile(r"(?<![a-z0-9])(\d*)([a-z]{2,10})-(\d{2,6})(?!\d)", re.IGNORECASE)

# 已知需要排除的伪番号片段（出现在前缀/编码里，非真实番号）
_FALSE_POSITIVE_WORDS = {
    "cd", "dvd", "hd", "fhd", "uhd", "sd", "part", "vol", "disc", "disk",
    "x", "h", "uhd", "hdtv", "web", "webrip", "bdrip", "hdr", "hevc", "avc",
    "aac", "ac3", "dts", "mp", "ts", "mkv", "mp4",
}


def strip_publish_prefix(name: str) -> tuple[str, str]:
    """剥离常见发布站前缀，返回 (前缀, 剩余部分)。

    循环剥离，直到没有可匹配的前缀为止（例如 ``[xxx]www.98t.la@ABP-123``）。
    """
    rest = name or ""
    consumed = ""
    # 最多剥 3 层，防止构造异常时死循环
    for _ in range(3):
        matched = False
        for pat in _PREFIX_PATTERNS:
            m = re.match(pat, rest, re.IGNORECASE)
            if m and m.end() > 0:
                consumed += rest[: m.end()]
                rest = rest[m.end():]
                matched = True
                break
        if not matched:
            break
    return consumed, rest


def find_fanhao(name: str) -> dict | None:
    """在文件名中智能定位番号。

    返回 dict:
        prefix     番号之前的全部文本（例如 ``www.98T.la@``）
        fanhao     番号原文（保留原大小写），如 ``DASS-787``
        start/end  番号在该名称中的下标区间
        has_leading_digits  是否形如 259LUXU-1900（前置数字厂牌）

    未找到返回 None。
    """
    if not name:
        return None

    # 先剥离常见发布站前缀，在其之后的文本里找番号
    consumed, rest = strip_publish_prefix(name)
    offset = len(consumed)

    candidates = []
    for m in _FIND_RE.finditer(rest):
        leading, word, digits = m.group(1), m.group(2), m.group(3)
        if word.lower() in _FALSE_POSITIVE_WORDS:
            continue
        candidates.append(
            (
                offset + m.start(),
                offset + m.end(),
                m.group(0),
                word,
                digits,
                bool(leading),
            )
        )

    if not candidates:
        # 兜底：若剥离后找不到，回到原文全文寻找（应对前缀规则未覆盖的情况）
        for m in _FIND_RE.finditer(name):
            leading, word, digits = m.group(1), m.group(2), m.group(3)
            if word.lower() in _FALSE_POSITIVE_WORDS:
                continue
            candidates.append((m.start(), m.end(), m.group(0), word, digits, bool(leading)))

    if not candidates:
        return None

    start, end, text, word, digits, has_leading = candidates[0]

    return {
        "prefix": name[:start],
        "fanhao": text,
        "start": start,
        "end": end,
        "has_leading_digits": has_leading,
    }


def insert_mark_after_fanhao(name: str, mark: str) -> str:
    """在番号后插入标记，其余部分原样保留。

    例：``...@DASS-787無防備..._restored_wm.mp4`` + mark ``-U``
        => ``...@DASS-787-U無防備..._restored_wm.mp4``

    若已存在该标记则原样返回（幂等，避免重复投递时叠加）。
    """
    if not mark:
        return name
    info = find_fanhao(name)
    if not info:
        return name

    end = info["end"]
    tail = name[end:]

    # 幂等：番号后已经紧跟这个标记
    if tail.startswith(mark):
        return name
    # 兼容「已带其它 -X 标记」的情况：只要标记主体已存在就不重复加
    normalized = mark if mark.startswith("-") else "-" + mark
    if re.match(r"^-[A-Z0-9]{1,4}(?![A-Z0-9])", tail, re.IGNORECASE):
        existing = re.match(r"^-[A-Z0-9]{1,4}", tail, re.IGNORECASE).group(0)
        if existing.upper() == normalized.upper():
            return name

    return name[:end] + normalized + tail


def is_jav_filename(name: str) -> bool:
    """文件名是否包含可识别的日本番号。"""
    return find_fanhao(name) is not None


def split_name_ext(filename: str) -> tuple[str, str]:
    base, ext = os.path.splitext(filename)
    return base, ext


def match_any_keyword(text: str, keywords: list[str]) -> str | None:
    """不分大小写地判断文本是否命中任一关键字，返回命中的第一个关键字。"""
    low = (text or "").lower()
    for kw in keywords or []:
        k = str(kw).strip()
        if k and k.lower() in low:
            return k
    return None
