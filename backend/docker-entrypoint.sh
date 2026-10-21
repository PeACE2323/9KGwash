#!/bin/sh
# 9KGwash 容器入口：准备配置目录 -> 生成默认配置 -> 启动 gunicorn
set -e

CONFIG_DIR="${XIBAN_CONFIG_DIR:-/config}"
mkdir -p "$CONFIG_DIR/logs" /media /metadata 2>/dev/null || true

# 首次启动时把镜像内置的示例配置复制过去（不覆盖已有文件）
if [ ! -f "$CONFIG_DIR/config.json" ] && [ -f /app/config.default.json ]; then
  cp /app/config.default.json "$CONFIG_DIR/config.json"
  echo "[entrypoint] 已生成默认配置文件: $CONFIG_DIR/config.json"
fi

# 支持通过环境变量覆盖「结构性路径」（可选，便于不改文件即可迁移）
if [ -n "$MOUNT_BASE_PATH" ] || [ -n "$METADATA_BASE_PATH" ] || [ -n "$WATCHED_DIRS" ]; then
  python /app/tools/env_override.py
fi

echo "[entrypoint] 启动 9KGwash (workers=${GUNICORN_WORKERS:-1}, threads=${GUNICORN_THREADS:-8}, port=${PORT:-5000})"

exec gunicorn \
  --chdir /app \
  --bind "0.0.0.0:${PORT:-5000}" \
  --workers "${GUNICORN_WORKERS:-1}" \
  --threads "${GUNICORN_THREADS:-8}" \
  --timeout "${GUNICORN_TIMEOUT:-300}" \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile - \
  --capture-output \
  "app.factory:create_app()"
