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

# 无连字符写法：厂牌字母紧接数字，如 katu00118 / ABP00123 / SSIS888
# 数字后允许紧跟字母后缀（katu00118hhb 中的 hhb），但要求后缀不是分辨率/编码类词，
# 且不能继续接数字（避免把 2024.0105 这类日期串误吞）。
_NO_SEP_RE = re.compile(
    r"(?<![a-z0-9])([a-z]{2,10})(\d{2,6})([a-z]{0,6})(?![a-z0-9])",
    re.IGNORECASE,
)

# 数字后缀里出现这些词，说明是分辨率/编码而非番号后缀，整体判为非番号
_BAD_SUFFIX_RE = re.compile(
    r"^(?:\d{3,4}p|[0-9]+(?:p|k)|x?26[45]|hevc|avc|web|rip|hdr|uhd|fps)$",
    re.IGNORECASE,
)

# 这些「字母」其实是常见发布站/编码前缀的一部分，不该当成厂牌
_BAD_STUDIO = {
    "www", "http", "https", "com", "net", "org", "la", "cc", "vim", "xyz",
    "hhd", "hhd800", "hevc", "x264", "x265", "h264", "h265", "mp4", "mkv",
    "avi", "wmv", "flv", "vol", "part", "disc", "disk", "sample", "trailer",
    "season", "episode", "ep", "movie", "video", "clip", "full", "hd", "fhd",
    "uhd", "sd", "raw", "uncensored", "restored", "remastered", "sub", "subs",
}

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


def _is_plausible_no_sep(word: str, digits: str, suffix: str) -> bool:
    """判断「无连字符写法」是否像真实番号。

    规则：
    - 厂牌至少 2 位字母，且不在已知的伪厂牌表里
    - 数字 2~6 位（katu00118 的 00118 会被保留原样）
    - 后缀若有，不能是 1080p / 4k / x264 这类分辨率编码
    """
    w = word.lower()
    if len(w) < 2 or w in _BAD_STUDIO:
        return False
    if w.isdigit():
        return False
    if not (2 <= len(digits) <= 6):
        return False
    if suffix and _BAD_SUFFIX_RE.match(suffix):
        return False
    return True


def find_fanhao(name: str) -> dict | None:
    """在文件名中智能定位番号。

    支持两种写法，优先取「带连字符」的规范写法：
        1. 规范写法  DASS-787 / ABP-159-C / 259LUXU-1900
        2. 无连字符  katu00118 / ABP00123 / katu00118hhb（数字后字母视作后缀）

    返回 dict:
        prefix     番号之前的全部文本（例如 ``www.98T.la@``）
        fanhao     番号原文（保留原大小写与原写法），如 ``DASS-787`` / ``katu00118``
        start/end  番号在该名称中的下标区间（不含后缀字母）
        suffix     无连字符写法里数字后紧跟的字母后缀（如 ``hhb``），规范写法为空
        has_leading_digits  是否形如 259LUXU-1900（前置数字厂牌）

    未找到返回 None。
    """
    if not name:
        return None

    # 先剥离常见发布站前缀，在其之后的文本里找番号
    consumed, rest = strip_publish_prefix(name)
    offset = len(consumed)

    def scan(text: str, base: int):
        """在 text 中扫描两种写法，返回候选列表。"""
        cands = []

        # 1) 规范写法（带连字符）—— 优先级最高
        for m in _FIND_RE.finditer(text):
            leading, word, digits = m.group(1), m.group(2), m.group(3)
            if word.lower() in _FALSE_POSITIVE_WORDS:
                continue
            cands.append(
                {
                    "start": base + m.start(),
                    "end": base + m.end(),
                    "text": m.group(0),
                    "word": word,
                    "digits": digits,
                    "suffix": "",
                    "has_leading": bool(leading),
                    "rank": 0,  # 规范写法优先
                }
            )

        # 2) 无连字符写法
        for m in _NO_SEP_RE.finditer(text):
            word, digits, suffix = m.group(1), m.group(2), m.group(3)
            if word.lower() in _FALSE_POSITIVE_WORDS:
                continue
            if not _is_plausible_no_sep(word, digits, suffix):
                continue
            if suffix and _BAD_SUFFIX_RE.match(suffix):
                continue
            # 番号只到数字结束，后缀字母不计入番号本体
            body = m.group(1) + m.group(2)
            cands.append(
                {
                    "start": base + m.start(),
                    "end": base + m.start() + len(body),
                    "text": body,
                    "word": word,
                    "digits": digits,
                    "suffix": suffix,
                    "has_leading": False,
                    "rank": 1,
                }
            )
        return cands

    candidates = scan(rest, offset)

    if not candidates:
        # 兜底：若剥离后找不到，回到原文全文寻找（应对前缀规则未覆盖的情况）
        candidates = scan(name, 0)

    if not candidates:
        return None

    # 排序：规范写法优先，其次按出现位置靠前
    candidates.sort(key=lambda c: (c["rank"], c["start"]))
    best = candidates[0]

    return {
        "prefix": name[: best["start"]],
        "fanhao": best["text"],
        "start": best["start"],
        "end": best["end"],
        "suffix": best["suffix"],
        "has_leading_digits": best["has_leading"],
        "style": "hyphen" if best["rank"] == 0 else "plain",
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


# ---------------------------------------------------------------------------
# 标记位置：前 / 后
# ---------------------------------------------------------------------------
MARK_POSITIONS = ("after", "before")


def _normalize_before_mark(mark: str) -> str:
    """前置标记统一收尾为「标记 + 连字符」，便于紧贴番号。

    ``U``    -> ``U-``
    ``U-``   -> ``U-``
    ``-U``   -> ``U-``（去掉前置连字符，避免出现 ``-U-DASS``）
    ``[U]``  -> ``[U]``（自带收尾符号时原样使用）
    """
    m = (mark or "").strip()
    if not m:
        return m
    # 去掉开头多余的分隔符
    m = m.lstrip("-_")
    if not m:
        return m
    if m[-1] in "-_.[]（）()【】":
        return m
    return m + "-"


def insert_mark_before_fanhao(name: str, mark: str) -> str:
    """在番号**前面**插入标记，番号及之后内容原样保留。

    例：``www.98T.la@DASS-787無防備...mp4`` + mark ``U``
        => ``www.98T.la@U-DASS-787無防備...mp4``
        （标记紧贴番号前，发布站前缀仍在最前）

    幂等：若番号前已经是该标记则不重复添加。
    """
    if not mark:
        return name
    info = find_fanhao(name)
    if not info:
        return name

    start = info["start"]
    prefix = name[:start]
    pre_mark = _normalize_before_mark(mark)

    # 幂等判断：番号前已带同一标记（只比较紧邻番号的那一段，避免误判）
    core = pre_mark.rstrip("-_.")
    if core:
        # 匹配「标记 + 可选分隔符」正好贴在番号前
        pattern = re.escape(core) + r"[-_. ]*$"
        if re.search(pattern, prefix, re.IGNORECASE):
            return name

    return prefix + pre_mark + name[start:]


def apply_mark_to_fanhao(name: str, mark: str, position: str = "after") -> str:
    """按指定位置给含番号的文件名打标记。"""
    if not mark:
        return name
    if str(position).lower() == "before":
        return insert_mark_before_fanhao(name, mark)
    return insert_mark_after_fanhao(name, mark)


def insert_mark_to_plain_name(name: str, mark: str, position: str = "after") -> str:
    """给「不含番号」的普通文件打标记（用于普通关键字规则）。

    - after : ``Some.Video.mp4`` + ``_restored`` => ``Some.Video_restored.mp4``
    - before: ``Some.Video.mp4`` + ``U-``        => ``U-Some.Video.mp4``

    保留扩展名位置，前后两种方式都不会破坏扩展名。
    """
    if not mark:
        return name
    base, ext = os.path.splitext(name)
    m = mark.strip()

    if str(position).lower() == "before":
        pre = m if m[-1] in "-_.[]（）()【】" else m + "-"
        if base.startswith(pre):
            return name
        return pre + name

    # 默认 after：追加到主干名末尾，扩展名保持最末
    if base.endswith(m):
        return name
    sep = "" if m[:1] in "-_. []（）()【】" else "_"
    return base + sep + m + ext


def apply_mark(name: str, mark: str, position: str = "after", prefer_fanhao: bool = True) -> str:
    """通用打标入口。

    prefer_fanhao=True 且文件名含番号时按番号前/后插入；
    否则按普通文件名追加/前置处理。
    """
    if not mark:
        return name
    if prefer_fanhao and find_fanhao(name):
        return apply_mark_to_fanhao(name, mark, position)
    return insert_mark_to_plain_name(name, mark, position)


def split_name_ext(filename: str) -> tuple[str, str]:
    base, ext = os.path.splitext(filename)
    return base, ext


# ---------------------------------------------------------------------------
# 画质 / 编码标识：这些片段出现在文件名里时，关键字不应在其内部命中。
# 例：关键字 FH 不应命中 RKI413.FHD 里的 FH（.FHD 是画质）
# ---------------------------------------------------------------------------
_QUALITY_TOKENS = (
    "fhd", "uhd", "qhd", "hd", "sd", "4k", "8k", "2k", "1080p", "720p",
    "2160p", "480p", "360p", "hevc", "avc", "x264", "x265", "h264", "h265",
    "hdr", "sdr", "webrip", "web-dl", "webdl", "bluray", "bdrip", "hdtv",
    "dvdrip", "aac", "ac3", "dts", "10bit", "8bit",
)
# 按长度倒序，保证匹配时优先命中更长的标识（如 1080p 先于 1080）
_QUALITY_RE = re.compile(
    r"(?<![a-z0-9])(?:"
    + "|".join(re.escape(t) for t in sorted(_QUALITY_TOKENS, key=len, reverse=True))
    + r")(?![a-z0-9])",
    re.IGNORECASE,
)
# 便于 O(1) 判断「关键字是否正好是某个画质标识」
_QUALITY_TOKEN_SET = {t.lower() for t in _QUALITY_TOKENS}


def strip_quality_tokens(text: str) -> str:
    """把画质/编码标识从文本中抹掉（替换为下划线），供关键字匹配使用。

    这样关键字只在「真正的内容」里匹配，不会被 .FHD / 1080p 这类片段误触发。
    只抹掉独立出现的标识，避免误伤正常单词。
    """
    if not text:
        return ""
    return _QUALITY_RE.sub("_", text)


def match_any_keyword(text: str, keywords: list[str]) -> str | None:
    """不分大小写地判断文本是否命中任一关键字，返回命中的第一个关键字。

    匹配前会先抹掉画质/编码标识（FHD、1080p、x264 等），
    避免关键字（如 FH）被画质标识（.FHD）误触发。
    """
    low = strip_quality_tokens(text or "").lower()
    raw_low = (text or "").lower()
    for kw in keywords or []:
        k = str(kw).strip()
        if not k:
            continue
        kl = k.lower()
        if kl in low:
            return k
        # 仅当关键字「整体就是」某个画质标识、且用户确实想按画质筛选时回退。
        # 但 FHD/HD 这类极易误伤的短词，只有在文件名里以独立词出现才放行。
        if kl in _QUALITY_TOKEN_SET and _QUALITY_RE.search(text or ""):
            if re.search(r"(?<![a-z0-9])" + re.escape(kl) + r"(?![a-z0-9])", raw_low):
                return k
    return None


# ---------------------------------------------------------------------------
# 欧美番号：厂牌.年.月.日
#   例：BlackedRaw.26.09.23.Delilah.Dagger...XXX.1080p.MP4-P2P[XC]
#       Tushy.2024.01.05.xxx.mp4
# 年份支持 2 位（26）与 4 位（2026）；月/日必须 2 位。
# ---------------------------------------------------------------------------
_WESTERN_DATE_RE = re.compile(r"(?<![0-9])(\d{2}|\d{4})\.(\d{2})\.(\d{2})(?!\d)")


def find_western(name: str) -> dict | None:
    """识别欧美番号（厂牌.年.月.日）。

    返回 dict:
        prefix      日期之前的全部文本（含厂牌与点号），如 ``BlackedRaw.``
        studio      厂牌名（日期前最后一段），如 ``BlackedRaw``
        date        完整日期串，如 ``26.09.23``
        start/end   日期在名称中的下标区间
    未找到返回 None。
    """
    if not name:
        return None

    # 先剥离发布站前缀，避免前缀里的数字干扰日期识别
    consumed, rest = strip_publish_prefix(name)
    offset = len(consumed)

    m = _WESTERN_DATE_RE.search(rest)
    if not m:
        # 兜底：在全文里找
        m = _WESTERN_DATE_RE.search(name)
        offset = 0
    if not m:
        return None

    start, end = offset + m.start(), offset + m.end()
    before = name[:start]

    # 厂牌 = 紧邻日期前、以点号分隔的最后一段
    studio_match = re.search(r"([A-Za-z0-9]+)\.$", before)
    studio = studio_match.group(1) if studio_match else ""

    return {
        "prefix": before,
        "studio": studio,
        "date": m.group(0),
        "start": start,
        "end": end,
    }


def insert_mark_after_western(name: str, mark: str) -> str:
    """在欧美番号的日期后追加标记，其余部分不变。

    例：``BlackedRaw.26.09.23.Delilah...mp4`` + ``-U``
        => ``BlackedRaw.26.09.23-U.Delilah...mp4``

    若无标记则原样返回；已存在该标记则幂等返回。
    """
    if not mark:
        return name
    info = find_western(name)
    if not info:
        return name

    normalized = mark if mark.startswith("-") else "-" + mark
    end = info["end"]
    tail = name[end:]

    if tail.startswith(normalized):
        return name
    return name[:end] + normalized + tail


def match_studio(name: str, studios: list[str]) -> str | None:
    """不分大小写地判断文件名是否含某个厂牌关键字。"""
    return match_any_keyword(name, studios)
