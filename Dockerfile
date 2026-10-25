# syntax=docker/dockerfile:1
# --- build: resolve deps from uv.lock into /app/.venv (no dev group, project itself not installed)
FROM ghcr.io/astral-sh/uv:0.12.17 AS uv

FROM python:3.14-slim AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-group checker --no-install-project

# --- runtime: venv + source only, non-root. The code runs from /app (config.ROOT = parent of app/).
FROM python:3.14-slim
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
# ffmpeg: the live-audio capture of the speech-to-text worker (apt build: uses the system CA certificates for HTTPS HLS)
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg && rm -rf /var/lib/apt/lists/*
RUN useradd --system --uid 10001 --no-create-home --shell /usr/sbin/nologin app
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY app ./app
COPY corpus ./corpus
COPY migrations ./migrations
COPY deploy/seed ./deploy/seed
COPY docker/entrypoint.sh /entrypoint.sh
USER app
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=3s --start-period=20s --retries=5 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=2).status == 200 else 1)"]
ENTRYPOINT ["sh", "/entrypoint.sh"]
