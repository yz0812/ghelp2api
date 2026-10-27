FROM node:24-bookworm-slim AS frontend
WORKDIR /build
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 DATA_DIR=/data
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 10001 ghelper && useradd --uid 10001 --gid ghelper --no-create-home ghelper \
    && mkdir /data && chown ghelper:ghelper /data
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
