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

# --- Build stamp -------------------------------------------------------------------------
# Last, so changing it never invalidates the layers above. Read back by the app as
# VLOTR_VERSION / VLOTR_GIT_SHA / VLOTR_BUILT_AT (app/config.py) and surfaced on
# Settings → System. The defaults mark an unstamped build so the update check stays quiet.
# CI passes these from .github/workflows/release.yml; the OCI labels there override ours.
ARG VLOTR_VERSION=0.0.0+dev
ARG VLOTR_GIT_SHA=unknown
ARG VLOTR_BUILT_AT=""

ENV VLOTR_VERSION=${VLOTR_VERSION} \
    VLOTR_GIT_SHA=${VLOTR_GIT_SHA} \
    VLOTR_BUILT_AT=${VLOTR_BUILT_AT}

LABEL org.opencontainers.image.title="vlotr-api" \
      org.opencontainers.image.source="https://github.com/vlotr-crm/vlotr" \
      org.opencontainers.image.version="${VLOTR_VERSION}" \
      org.opencontainers.image.revision="${VLOTR_GIT_SHA}" \
      org.opencontainers.image.created="${VLOTR_BUILT_AT}"
