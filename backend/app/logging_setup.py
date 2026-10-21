"""日志系统：控制台 + 按天切割的滚动文件（保留 3 天）。

完全复刻原 ``CleanRotatingFileHandler`` 的命名与清理规则：
- 滚动后文件名形如 ``xiban-2025-10-21.log``（把 ``.log.`` 替换为 ``-``）；
- 只删除匹配 ``xiban-YYYY-MM-DD.log`` 的过期备份，最多保留 backupCount 个。
"""

from __future__ import annotations

import logging
import os
import re
from logging.handlers import TimedRotatingFileHandler

from . import config

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class CleanRotatingFileHandler(TimedRotatingFileHandler):
    def __init__(self, filename, **kwargs):
        super().__init__(filename, **kwargs)
        self.namer = lambda name: name.replace(".log.", "-") + ".log" if ".log." in name else name

    def getFilesToDelete(self):  # noqa: N802 - 与 logging 内部命名保持一致
        dirName, baseName = os.path.split(self.baseFilename)
        try:
            fileNames = os.listdir(dirName)
        except OSError:
            return []
        result = []
        prefix = baseName.replace(".log", "-")
        for fileName in fileNames:
            if fileName.startswith(prefix) and fileName.endswith(".log"):
                suffix = fileName[len(prefix):-4]
                if re.compile(r"^\d{4}-\d{2}-\d{2}$").match(suffix):
                    result.append(os.path.join(dirName, fileName))
        if len(result) < self.backupCount:
            result = []
        else:
            result.sort()
            result = result[: len(result) - self.backupCount]
        return result


logger = logging.getLogger("9KGwash")
_logging_ready = False

# ---------------------------------------------------------------------------
# 日志分类（供前端「活动日志」标签页筛选）
# ---------------------------------------------------------------------------
CATEGORY_ALL = "all"
CATEGORY_LABELS = {
    "all": "全部",
    "wash": "洗板",
    "hound": "空间猎犬",
    "dedup": "影片查重",
    "deliver": "文件投递",
    "emby": "Emby 中转",
    "system": "系统",
}
VALID_CATEGORIES = set(CATEGORY_LABELS)

# 日志正文中的分类标签形如 "#wash#"
_CATEGORY_TAG_RE = re.compile(r"#(wash|hound|dedup|deliver|emby|system)#")


def tag(category: str, msg: str) -> str:
    """给日志正文加分类前缀，便于前端按标签页归类。

    保持原有日志格式（时间 [级别] 正文）不变，只在正文前插入 ``#分类#``，
    因此旧的日志解析、grep 习惯都不受影响。
    """
    if category not in VALID_CATEGORIES or category == CATEGORY_ALL:
        return msg
    return f"#{category}#{msg}"


def parse_category(message: str) -> str:
    """从日志正文中解析分类，未匹配返回 system。"""
    m = _CATEGORY_TAG_RE.match(message or "")
    return m.group(1) if m else "system"


def strip_category(message: str) -> str:
    """去掉分类标签，返回纯正文。"""
    return _CATEGORY_TAG_RE.sub("", message or "", count=1)


class CategorizedLogger:
    """给某个业务模块专用、自动附带分类标签的 logger 包装。

    用法::

        logger = logging_setup.categorized("deliver")
        logger.info("已移动 xxx")      # 实际写入 "#deliver#已移动 xxx"

    这样各业务模块不必逐条改日志文案，新增日志也自动带上分类。
    """

    __slots__ = ("_cat", "_logger")

    def __init__(self, category: str, base: logging.Logger | None = None):
        self._cat = category if category in VALID_CATEGORIES else "system"
        self._logger = base or logger

    def _emit(self, level: int, msg, *args, **kwargs):
        try:
            text = msg % args if args else str(msg)
        except Exception:  # noqa: BLE001 - 格式异常时回退原文
            text = str(msg)
        self._logger.log(level, tag(self._cat, text), **kwargs)

    def info(self, msg, *args, **kwargs):
        self._emit(logging.INFO, msg, *args, **kwargs)

    def warning(self, msg, *args, **kwargs):
        self._emit(logging.WARNING, msg, *args, **kwargs)

    def error(self, msg, *args, **kwargs):
        self._emit(logging.ERROR, msg, *args, **kwargs)

    def debug(self, msg, *args, **kwargs):
        self._emit(logging.DEBUG, msg, *args, **kwargs)

    def exception(self, msg, *args, **kwargs):
        self._emit(logging.ERROR, msg, *args, **kwargs)


def categorized(category: str) -> CategorizedLogger:
    """取得一个自动打分类标签的 logger。"""
    return CategorizedLogger(category)


def setup_logging() -> logging.Logger:
    """配置全局 logger（幂等，可在 gunicorn 多 worker 下重复调用）。"""
    global _logging_ready
    if _logging_ready:
        return logger

    os.makedirs(config.LOG_DIR, exist_ok=True)
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)

    if not any(isinstance(h, logging.StreamHandler) and not isinstance(h, CleanRotatingFileHandler)
               for h in logger.handlers):
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    if not any(isinstance(h, CleanRotatingFileHandler) for h in logger.handlers):
        try:
            file_handler = CleanRotatingFileHandler(
                config.LOG_FILE, when="midnight", interval=1, backupCount=3, encoding="utf-8"
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)
        except OSError as e:  # 只读卷等极端情况下保证服务仍可启动
            logger.warning("日志文件不可写（%s），仅输出到控制台。", e)

    logger.propagate = False
    logging.getLogger("werkzeug").setLevel(logging.ERROR)
    _logging_ready = True
    return logger
