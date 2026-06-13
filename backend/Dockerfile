# RailMind backend — always-on FastAPI (WebSocket + sim tick loop).
# Build context is the repo root (see render.yaml dockerContext: .).

FROM python:3.12-slim

# uv (resolver/installer) + git (needed for the hive-agent git dependency)
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/
RUN apt-get update \
    && apt-get install -y --no-install-recommends git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/data \
    DB_PATH=/tmp/railmind.sqlite \
    AGENT_LLM=off \
    PORT=8000

# Dependencies first for layer caching.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App code + seed corridor.
COPY backend/app ./app
COPY data /data

EXPOSE 8000
# sh -c so $PORT (injected by Render) is expanded.
CMD ["sh", "-c", "uv run uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
