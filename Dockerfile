# syntax=docker/dockerfile:1
# EchoMind 智能客服系统 — Docker 多阶段构建
# 目标：生产镜像尽量精简，开发镜像包含调试工具

# ── 阶段 1：基础环境 ──────────────────────────────────────────────────────────
FROM python:3.12-slim AS base

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_CACHE_DIR=/root/.cache/pip \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONPATH=/app

# curl 用于健康检查；不再需要 gcc/g++（已移除本地 ML 模型）
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ── 阶段 2：安装 Python 依赖 ──────────────────────────────────────────────────
FROM base AS dependencies

# 国内构建建议走 PyPI 镜像；可用 --build-arg PIP_INDEX_URL=... 覆盖
ARG PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple

COPY requirements.txt .
# --mount=type=cache 复用 pip 的 wheel 缓存：requirements 不变时该步骤秒级完成
RUN --mount=type=cache,target=/root/.cache/pip \
    pip install --index-url "$PIP_INDEX_URL" --upgrade pip && \
    pip install --index-url "$PIP_INDEX_URL" -r requirements.txt

# ONNX embedding 模型不再在构建期下载（避免每次构建都从 S3 拉 79MB，导致构建卡住/超时）。
# 运行时通过 docker-compose 挂载 ./data/models/chroma-cache:/home/echomind/.cache/chroma 提供。

# ── 阶段 3：生产镜像 ──────────────────────────────────────────────────────────
FROM base AS production

# 非 root 用户运行。先创建用户，后续 COPY 直接带 owner，避免 chown -R 复制出额外大层。
RUN useradd -m -u 1000 echomind

# 从依赖阶段复制已安装的包
COPY --from=dependencies /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=dependencies /usr/local/bin /usr/local/bin

# 复制应用代码
COPY --chown=echomind:echomind . .

# 创建必要目录，只调整运行期需要写入的目录权限，避免递归 chown 整个应用。
RUN mkdir -p /app/data/chroma /app/logs /app/config && \
    chown echomind:echomind /app/data /app/data/chroma /app/logs /app/config
USER echomind

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ── 阶段 4：开发镜像 ──────────────────────────────────────────────────────────
FROM dependencies AS development

COPY . .

RUN mkdir -p /app/data/chroma /app/logs /app/config /app/tests && \
    chmod -R 777 /app/data /app/logs

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
