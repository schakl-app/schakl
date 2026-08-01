# schakl API image — build context is the REPO ROOT (needs apps/api + shared messages/).
# Published to ghcr.io/schakl-app/schakl-api.
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

# curl for the container healthcheck; Inter for the document renderer.
#
# Inter is the face a modern CRM reads in, so the document matches the product. Without it on
# disk the PDF falls back to whatever fontconfig finds, which is a document that looks nothing
# like the app the client was shown. Only the three weights the designs ask for are kept; the
# package ships ~21 MB of weights/italics. The prune is best-effort: a future package layout
# change must cost image size, never the build — the designs name a fallback stack, so a
# missing face degrades a glyph rather than an invoice.
# WeasyPrint prints the document HTML; it is a Python wheel but binds native text and
# raster stacks (pango shapes the text, cairo/gdk-pixbuf draw it). They are not optional —
# without them `import weasyprint` raises at startup, so they belong in the runtime image
# next to the font it shapes.
RUN apt-get update && apt-get install -y --no-install-recommends curl fonts-inter \
       libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libgdk-pixbuf-2.0-0 libcairo2 \
    && find /usr/share/fonts/opentype/inter -type f \
       ! -name 'Inter-Regular.otf' ! -name 'Inter-Medium.otf' ! -name 'Inter-SemiBold.otf' \
       -delete 2>/dev/null || true \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY --from=builder /app /app

ENV PATH="/app/.venv/bin:${PATH}" \
    SCHAKL_MESSAGES_DIR=/app/messages \
    PYTHONUNBUFFERED=1

EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["api"]

# --- Build stamp -------------------------------------------------------------------------
# Last, so changing it never invalidates the layers above. Read back by the app as
# SCHAKL_VERSION / SCHAKL_GIT_SHA / SCHAKL_BUILT_AT (app/config.py) and surfaced on
# Settings → System. The defaults mark an unstamped build so the update check stays quiet.
# CI passes these from .github/workflows/release.yml; the OCI labels there override ours.
ARG SCHAKL_VERSION=0.0.0+dev
ARG SCHAKL_GIT_SHA=unknown
ARG SCHAKL_BUILT_AT=""

ENV SCHAKL_VERSION=${SCHAKL_VERSION} \
    SCHAKL_GIT_SHA=${SCHAKL_GIT_SHA} \
    SCHAKL_BUILT_AT=${SCHAKL_BUILT_AT}

LABEL org.opencontainers.image.title="schakl-api" \
      org.opencontainers.image.source="https://github.com/schakl-app/schakl" \
      org.opencontainers.image.version="${SCHAKL_VERSION}" \
      org.opencontainers.image.revision="${SCHAKL_GIT_SHA}" \
      org.opencontainers.image.created="${SCHAKL_BUILT_AT}"
