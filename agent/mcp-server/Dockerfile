# Build context: the repository root (docker-compose.yml sets it).
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12 /uv /usr/local/bin/uv

ENV UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH=/app/agent/mcp-server/src \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY pyproject.toml uv.lock ./
# Only the mcp dependency group; the project itself is not installed, PYTHONPATH finds the package.
RUN uv sync --frozen --no-default-groups --group mcp --no-install-project

COPY agent/mcp-server/src agent/mcp-server/src

RUN useradd --system --no-create-home sherpa
USER sherpa

EXPOSE 8765
CMD ["python", "-m", "sherpa_mcp.server"]
