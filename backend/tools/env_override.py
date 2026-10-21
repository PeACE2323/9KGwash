"""入口脚本用：通过环境变量覆盖 config.json 中的结构性路径。

支持：
    MOUNT_BASE_PATH       网盘挂载路径，如 /media/CloudDrive
    METADATA_BASE_PATH    元数据路径，如 /metadata
    WATCHED_DIRS          逗号分隔的监听目录，如 "/115open/9KG/STRM,/115open/xx"
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, "/app")

from app import config  # noqa: E402


def main() -> int:
    patch = {}
    if os.environ.get("MOUNT_BASE_PATH"):
        patch["MOUNT_BASE_PATH"] = os.environ["MOUNT_BASE_PATH"]
    if os.environ.get("METADATA_BASE_PATH"):
        patch["METADATA_BASE_PATH"] = os.environ["METADATA_BASE_PATH"]
    if os.environ.get("WATCHED_DIRS"):
        patch["WATCHED_DIRS"] = [w.strip() for w in os.environ["WATCHED_DIRS"].split(",") if w.strip()]

    if not patch:
        return 0

    ok, res = config.save_config(patch)
    if ok:
        print(f"[entrypoint] 已应用环境变量覆盖: {patch}")
        return 0
    print(f"[entrypoint] 环境变量覆盖失败: {res}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
