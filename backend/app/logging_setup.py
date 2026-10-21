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
