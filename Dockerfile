# ---------- 构建阶段：安装依赖 ----------
FROM python:3.12-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build
COPY requirements.txt .
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ---------- 运行阶段 ----------
FROM python:3.12-slim

LABEL org.opencontainers.image.title="9KGwash" \
      org.opencontainers.image.description="9KG 媒体库自动洗板网关（空间猎犬 / 全库查重 / Emby 幽灵防御）" \
      org.opencontainers.image.version="9.15.0"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    LC_ALL=C.UTF-8 \
    TZ=Asia/Shanghai \
    PORT=5000 \
    GUNICORN_WORKERS=1 \
    GUNICORN_THREADS=8 \
    GUNICORN_TIMEOUT=300 \
    XIBAN_CONFIG_DIR=/config

# tzdata：保证日志时间与宿主机一致（可用 TZ 覆盖）；ca-certificates：Webhook HTTPS 调用
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app
COPY backend/ /app/

# 配置 / 日志 / 档案目录（通常挂载为卷）
RUN mkdir -p /config/logs /media /metadata \
    && chmod +x /app/docker-entrypoint.sh

# gunicorn：单 worker + 多线程，保证进程内洗板互斥与档案写入一致
EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5000/healthz', timeout=4)"

ENTRYPOINT ["/app/docker-entrypoint.sh"]
