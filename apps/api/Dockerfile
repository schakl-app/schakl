# schakl API image — build context is the REPO ROOT (needs apps/api + shared messages/).
# Published to ghcr.io/schakl-app/schakl-api.

# `COPY --from=<image>` resolves that image for the TARGET platform, so an arm64 build pulls
# an arm64 uv — which the native amd64 builder below cannot exec ("Exec format error").
# Naming it as its own $BUILDPLATFORM stage is what pins the tool to the machine running it.
FROM --platform=$BUILDPLATFORM ghcr.io/astral-sh/uv:0.11 AS uv

# The builder is pinned to $BUILDPLATFORM so it always runs natively, never under QEMU, and
# `uv` is told the *target* platform instead. Emulating the wheel install cost 120s per
# release against 16s native — for work that is only downloading the right prebuilt wheels
# and compiling bytecode, and .pyc is architecture-independent. Nothing here is compiled
# from source, so nothing needs to run on the target CPU. The runtime stage below is NOT
# pinned: it stays per-target, and its venv interpreter (/app/.venv/bin/python is a symlink
# to /usr/local/bin/python3) therefore resolves to the target image's own Python.
FROM --platform=$BUILDPLATFORM python:3.12-slim AS builder

COPY --from=uv /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# BuildKit hands us TARGETARCH as Docker's name (amd64/arm64); uv wants the target triple.
# Resolved once, so the two `uv sync` calls below can never disagree and quietly leave the
# venv half-populated with the build host's wheels. An unknown arch fails the build rather
# than silently falling back to native — a wrong-arch image only fails at container start.
ARG TARGETARCH
RUN case "$TARGETARCH" in \
      amd64) echo x86_64-unknown-linux-gnu ;; \
      arm64) echo aarch64-unknown-linux-gnu ;; \
      *) echo "Dockerfile: unsupported TARGETARCH=$TARGETARCH" >&2; exit 1 ;; \
    esac > /uv-platform

# Install dependencies first (cached until the lockfile changes).
COPY apps/api/pyproject.toml apps/api/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project \
      --python-platform "$(cat /uv-platform)"

# App source + shared i18n catalogs.
COPY apps/api/ ./
COPY messages/ ./messages/
RUN uv sync --frozen --no-dev --python-platform "$(cat /uv-platform)"

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
