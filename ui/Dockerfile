# Build context: the repository root (docker-compose.yml sets it).
FROM node:22-alpine AS frontend

WORKDIR /frontend
COPY ui/frontend/package.json ui/frontend/package-lock.json ./
RUN npm ci
COPY ui/frontend ./
RUN npm run build


FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/ui/backend/src:/app/agent/mcp-server/src \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
# The ui and mcp dependency groups (the UI reuses sherpa_mcp's catalog and SQL guard); the project itself is not
# installed, PYTHONPATH finds the packages.
RUN uv sync --frozen --no-default-groups --group ui --group mcp --no-install-project

COPY ui/backend/src ui/backend/src
COPY agent/mcp-server/src agent/mcp-server/src
COPY --from=frontend /frontend/dist /app/static

RUN useradd --system --no-create-home sherpa
USER sherpa

EXPOSE 8090
CMD ["uvicorn", "sherpa_ui.app:container_app", "--host", "0.0.0.0", "--port", "8090", "--workers", "1"]
