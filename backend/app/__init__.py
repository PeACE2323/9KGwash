"""9KGwash —— 9KG 媒体库自动洗板网关（容器化版本）。"""

import os

# 基础语义版本号：功能有迭代时手动递增
BASE_VERSION = "9.16.0"

__version__ = BASE_VERSION


def _build_info() -> dict:
    """读取构建期注入的信息（commit、构建时间）。

    镜像构建时通过环境变量写入：
        XIBAN_BUILD_COMMIT     git 短 commit
        XIBAN_BUILD_TIME       ISO 时间
    本地开发时读不到，返回空值，界面只显示版本号。
    """
    return {
        "commit": (os.environ.get("XIBAN_BUILD_COMMIT") or "").strip(),
        "build_time": (os.environ.get("XIBAN_BUILD_TIME") or "").strip(),
    }


def version_full() -> str:
    """完整版本字符串，用于界面右下角展示。

    形如： v9.16.0+1b2ab11 · 2026-11-02
    没有构建信息时退化为 v9.16.0。
    """
    info = _build_info()
    commit = info["commit"]
    btime = info["build_time"]

    ver = f"v{__version__}"
    if commit:
        ver += f"+{commit[:7]}"

    # 构建时间只取日期部分，避免太长
    date = btime[:10] if btime else ""
    if date:
        return f"{ver} · {date}"
    return ver


def create_app():
    """延迟导入，避免 ``python -m app`` 与包 ``app`` 同名冲突。"""
    from .factory import create_app as _create_app

    return _create_app()
