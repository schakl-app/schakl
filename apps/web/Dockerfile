# schakl web (SvelteKit SSR via adapter-node). Build context is the REPO ROOT
# (needs project.inlang + shared messages/ for the Paraglide compile).
# Published to ghcr.io/schakl-app/schakl-web.
#
# The builder is pinned to $BUILDPLATFORM — it always runs natively, never emulated, and
# runs ONCE no matter how many platforms we publish. adapter-node emits a self-contained
# bundle of pure JavaScript (no .node/.so, and no node_modules in the runtime stage), so
# the artefact is identical whichever CPU produced it. Building it a second time under
# QEMU cost 14.5 minutes per release to arrive at the same bytes.
FROM --platform=$BUILDPLATFORM node:22-slim AS builder

RUN corepack enable
WORKDIR /repo

# Install deps first (cached until manifests change).
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
COPY apps/web/package.json apps/web/
RUN pnpm install --frozen-lockfile

# Shared i18n sources + app source, then build (compiles Paraglide + Vite).
COPY project.inlang/ ./project.inlang/
COPY messages/ ./messages/
COPY apps/web/ ./apps/web/
RUN pnpm --filter @schakl/web build

FROM node:22-slim AS runtime
WORKDIR /app

# adapter-node emits a self-contained server in build/.
COPY --from=builder /repo/apps/web/build ./build
COPY --from=builder /repo/apps/web/package.json ./package.json

# BODY_SIZE_LIMIT: adapter-node refuses any request body over 512 kB by default, *before* a
# route runs — and the one request this server proxies that carries real bytes is a dictated
# clip (base64 in JSON, `routes/(app)/ai/[...path]`). A five-minute task dictation is ~1.6 MB
# encoded and met that default as a bare 413 with no sentence on screen. Sized to admit what
# the API itself admits (`MAX_AUDIO_BYTES` in `app/core/ai/audio.py`, 24 MB decoded → 32 MB
# encoded) so the two limits can never disagree in the browser's disfavour. Overridable per
# deployment; see docs/DEPLOY.md.
ENV NODE_ENV=production \
    PORT=3000 \
    PROTOCOL_HEADER=x-forwarded-proto \
    HOST_HEADER=x-forwarded-host \
    ADDRESS_HEADER=x-forwarded-for \
    BODY_SIZE_LIMIT=40M

EXPOSE 3000
CMD ["node", "build"]

# --- Build stamp -------------------------------------------------------------------------
# Last, so changing it never invalidates the layers above. The web app displays the *API's*
# version (one source of truth, and the two images are always released together), so these
# exist for `docker inspect` parity rather than to be read at runtime.
ARG SCHAKL_VERSION=0.0.0+dev
ARG SCHAKL_GIT_SHA=unknown
ARG SCHAKL_BUILT_AT=""

LABEL org.opencontainers.image.title="schakl-web" \
      org.opencontainers.image.source="https://github.com/schakl-app/schakl" \
      org.opencontainers.image.version="${SCHAKL_VERSION}" \
      org.opencontainers.image.revision="${SCHAKL_GIT_SHA}" \
      org.opencontainers.image.created="${SCHAKL_BUILT_AT}"
