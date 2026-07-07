# vlotr API image — build context is the REPO ROOT (needs apps/api + shared messages/).
# Published to ghcr.io/vlotr-crm/vlotr-api.
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Install dependencies first (cached until the lockfile changes).
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

# App source + shared i18n catalogs.
COPY apps/api/ ./
COPY messages/ ./messages/
RUN uv sync --frozen --no-dev

FROM python:3.12-slim AS runtime

# curl for the container healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:${PATH}" \
    VLOTR_MESSAGES_DIR=/app/messages \
    PYTHONUNBUFFERED=1

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["api"]
