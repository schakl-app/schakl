# vlotr web (SvelteKit SSR via adapter-node). Build context is the REPO ROOT
# (needs project.inlang + shared messages/ for the Paraglide compile).
# Published to ghcr.io/vlotr-crm/vlotr-web.
FROM node:22-slim AS builder

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
RUN pnpm --filter @vlotr/web build

FROM node:22-slim AS runtime
WORKDIR /app

# adapter-node emits a self-contained server in build/.
COPY --from=builder /repo/apps/web/build ./build
COPY --from=builder /repo/apps/web/package.json ./package.json

ENV NODE_ENV=production \
    PORT=3000 \
    PROTOCOL_HEADER=x-forwarded-proto \
    HOST_HEADER=x-forwarded-host \
    ADDRESS_HEADER=x-forwarded-for

EXPOSE 3000
CMD ["node", "build"]
