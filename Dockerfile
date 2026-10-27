FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ARG MIHOMO_VERSION=v1.19.33
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 ghelper && useradd --uid 10001 --gid ghelper --no-create-home ghelper \
    && mkdir /data && chown ghelper:ghelper /data
# mihomo 内核用于真实测速（端口连通不等于代理可用）；校验和取自官方 release 资产
# 架构取自镜像自身：ARG TARGETARCH 一旦写了默认值，多架构构建时 arm64 镜像也会装进 amd64 内核
RUN set -eu; \
    arch="$(dpkg --print-architecture)"; \
    case "$arch" in \
      amd64) sha=132f6f3edf9a65e62f04853aed3358472e0b2b7666daa8419edfa2ddf5bb6323 ;; \
      arm64) sha=ba408b317080ee265c46cb923a50018c1b82cbd708551cdd46a1e3e307396187 ;; \
      *) echo "不支持的架构：$arch" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /tmp/mihomo.gz \
      "https://github.com/MetaCubeX/mihomo/releases/download/${MIHOMO_VERSION}/mihomo-linux-${arch}-${MIHOMO_VERSION}.gz"; \
    echo "${sha}  /tmp/mihomo.gz" | sha256sum -c -; \
    gzip -d /tmp/mihomo.gz; \
    install -m 0755 /tmp/mihomo /usr/local/bin/mihomo; \
    rm -f /tmp/mihomo
COPY requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock
COPY app/ app/
COPY migrations/ migrations/
COPY alembic.ini ./
COPY --from=frontend /build/dist frontend/dist/
USER ghelper
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 CMD curl -fsS http://127.0.0.1:8000/readyz || exit 1
CMD ["python", "-m", "uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
